"""Simulator: (복잡) 영역별 영향 경로를 구성하고 구현 가능성·난이도·대안을 판정한다. 설계: docs/SE_agent_design.md 1장

1. 경로 골격 (규칙): Explorer 경로 → ImpactPath (엣지 유형·근거 위치·확신도), rules.build_impact_paths
2. 영역 배정 (규칙): 역할 기준 screen / db / interface / batch (rules.path_areas). 영역이 없는 경로(진입 노드 없이
   Logic 만 있는 경로)는 결과에서 빠지지 않게 Explorer 엣지의 호출 노드 영역 → 그래프 역방향의 호출 진입점(Screen·Batch) 영역
   → 둘 다 없으면 screen 순으로 정하고 분석 한계에 남긴다 (assign_orphan_areas)
3. 영향 판정 (LLM, 있을 때): 노드별 영향(노드당 한 번, [변경] 은 모두 · [참조] 는 필요한 노드만, 나머지는 코드가 [참조]),
   영역별 영향 유형·가능성·난이도·대안, 요청안/대안 비교, 무관 경로 제외 제안
4. 판정 보정 (규칙): 골격 밖 노드 무시, 근거 없는 영역은 review, 사유·대안 없는 hard 는 review, 경로 제외 가드,
   공유 노드(진입점 2개 이상·공통)를 [변경] 하는 영역은 최소 Medium (설계서 1-3 (c))
5. 분석 한계: unresolved · 목표 미도달 · hop 상한 · 매칭 실패 · 근거 없음 · 공통 제외 → unverified_segments
LLM 이 없거나 실패하면 3단계를 건너뛰고 규칙 판정(review, 난이도 기준표)으로 낸다. 경로는 제외하지 않는다.
"""

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from ..llm import LLM_FAILURES, LLMClient, Usage, analysis_llm
from ..rules import (
    AREA_ORDER,
    ENTRY_AREAS,
    build_impact_paths,
    interface_node_ids,
    node_areas,
    node_role,
    path_areas,
    shared_nodes,
)
from ..schemas import (
    Alternative,
    Area,
    AreaImpact,
    ChangeType,
    Confidence,
    Difficulty,
    DroppedPath,
    ExplorePath,
    Feasibility,
    FollowupType,
    GraphEdge,
    GraphNode,
    ImpactPath,
    ImpactType,
    NodeRole,
    SimulationOutput,
    StopReason,
)
from ..state import AgentState
from ..tools import GraphStore, get_graph_store
from ..trace import clip, trace
from .base import BaseAgent

DEFAULT_IMPACT = "영향 검토 필요"
# 상세 실행 로그 섹션 (api/agent_log.py: "동작 · …" 은 동작 묶음). 결과만이 아니라 LLM 판정 → 규칙 검증·보정 과정을 남긴다
SKELETON_SECTION = "동작 · 경로 골격 (규칙)"
LLM_SECTION = "동작 · LLM 판정"
NODE_SECTION = "동작 · 노드별 [변경]/[참조] 판정 (LLM)"
CHECK_SECTION = "동작 · 규칙 검증·보정 (LLM 판정 → 최종)"
DROP_SECTION = "동작 · 경로 제외 가드"
MAX_CONTEXT_PATHS = 40  # LLM 입력에 보여 줄 경로 수
CONTEXT_LINES_PER_EVIDENCE = 12  # 노드당 원문 줄 수
MAX_CONTEXT_CHARS = 16000  # 원문 블록 전체 문자 수 상한
# LLM 이 없을 때 db 영역 난이도 (설계서 1-2): 컬럼 길이·타입 변경 Major, 저장 데이터 추가(기존 테이블 컬럼 추가) Medium
DB_DIFFICULTY_BY_CHANGE: dict[ChangeType, tuple[Difficulty, str]] = {
    ChangeType.COLUMN_SPEC_CHANGE: (Difficulty.MAJOR, "(규칙) 컬럼 길이·타입 변경"),
    ChangeType.DATA_ADD: (Difficulty.MEDIUM, "(규칙) 기존 테이블에 컬럼 추가 가능"),
}
MAX_SCHEDULE_FACTORS = 3  # 영역당 일정 영향 요인 수
DIFFICULTY_RANK = [Difficulty.MINOR, Difficulty.MEDIUM, Difficulty.MAJOR]
CHANGE_TAG = "[변경]"  # hop 영향 설명의 변경 / 참조 표시 (prompts/simulator.md)
REFERENCE_TAG = "[참조]"
REFERENCE_IMPACT = "경로상 호출·조회 (이 요건으로 바뀌지 않음)"  # LLM 이 쓰지 않은 노드의 [참조] 문장 (Composer 단순 응답도 같음)
# [노드 메타] 에 넣을 노드 속성 (설계서 1-3 (a) "LLM 에 넘길 속성"). code·search_text 같은 큰 값은 넣지 않는다
META_KEYS = ["images", "events", "cycle", "cron", "program", "steps", "interfaces", "url", "http_method", "stmt_type",
             "dbio_type", "data_type", "nullable", "pk", "code_grp", "code_values"]
MAX_META_VALUE = 120  # 속성 값 하나의 글자 수 상한
# 영역 없는 경로의 호출 진입점 찾기 (그래프 역방향): 호출 엣지만 따라가고, hop·방문 노드 수 상한을 둔다
CALLER_EDGES = {"CALLS", "INVOKES", "EXECUTES", "OPENS"}
MAX_ENTRY_HOPS = 4
MAX_ENTRY_VISITS = 50
MAX_ENTRY_NAMES = 3  # 분석 한계 문장에 적을 진입점 수
# 재분석 사유 문장 안의 그래프 노드 ID (<접두어>:<키>, docs/mock_data.md 노드 ID 규칙)
NODE_ID_IN_TEXT = re.compile(r"\b(?:SCR|CTL|TP|BAT|SVC|MOD|DBIO|SQL|TBL|COL):[A-Za-z0-9_.#$]+")
NOTE_PREFIX = "참고:"  # 재분석 입력에서 참고 신호(Composer detail_analysis_notes, 약한 신호) 줄의 머리


# --- LLM 응답 형식 (Structured Outputs 호환: 기본값 없이 모든 필드 명시) ---------------------


class DraftNode(BaseModel):
    node: str = Field(description="[경로 노드] 에 있는 노드 ID 그대로")
    change: bool = Field(description="이 요건으로 코드·데이터·구조를 고치면 true ([변경]), 확인만 하면 false ([참조])")
    impact: str = Field(description="무엇을 어떻게 고치는지 / 왜 확인만 하면 되는지 한 문장 + 원문 근거 ([변경]·[참조] 표시 없이)")


class DraftAlternative(BaseModel):
    description: str
    feasibility: Feasibility
    difficulty: Difficulty
    pros_cons: str


class DraftArea(BaseModel):
    area: Area
    impact_type: ImpactType
    feasibility: Feasibility
    difficulty: Difficulty
    difficulty_basis: str = Field(description="난이도 기준표에서 적용한 항목")
    schedule_factors: list[str] = Field(description="일정에 영향을 주는 요인 3개 이내, 짧게. 없으면 []")
    infeasible_reason: str | None = Field(description="feasibility=hard 일 때 사유. 아니면 null")
    alternatives: list[DraftAlternative] = Field(description="feasibility=hard 이거나 요청안보다 나은 방법이 있을 때. 없으면 []")


class DraftDrop(BaseModel):
    path_id: str
    reason: str = Field(description="요건과 무관하다고 본 근거")


class SimulatorDraft(BaseModel):
    nodes: list[DraftNode] = Field(description="노드당 한 번. 바뀌는 노드는 빠짐없이, 확인만 하는 노드는 재분석 요청 노드·판단 근거가 필요한 노드만")
    areas: list[DraftArea] = Field(description="screen / db / interface / batch 4개 모두")
    requested_vs_alternative: str | None = Field(description="요청안과 대안이 갈리면 비교 요약. 아니면 null")
    drop_paths: list[DraftDrop] = Field(description="요건과 무관한 경로만. 없으면 []")


# --- 에이전트 ------------------------------------------------------------------------------------


class SimulatorAgent(BaseAgent[SimulationOutput]):
    name = "simulator"
    output_model = SimulationOutput

    def __init__(self, llm: LLMClient | None = None, use_llm: bool = True, store: GraphStore | None = None) -> None:
        self._llm = llm
        self._llm_checked = llm is not None or not use_llm
        self._store = store
        self._store_checked = store is not None
        self.last_usage: Usage | None = None

    @property
    def store(self) -> GraphStore | None:
        """영역 없는 경로의 호출 진입점을 찾을 그래프 (Explorer 와 같은 저장소). 연결할 수 없으면 None (screen 기본값)."""
        if not self._store_checked:
            try:
                self._store = get_graph_store()
            except Exception:  # noqa: BLE001  설정·연결 실패는 분석을 막지 않는다
                self._store = None
            self._store_checked = True
        return self._store

    @property
    def llm(self) -> LLMClient | None:
        """판정 LLM. 주입하지 않았으면 .env 설정으로 한 번 만든다 (키가 없으면 None → 규칙 판정만)."""
        if not self._llm_checked:
            self._llm, self._llm_checked = analysis_llm(), True
        return self._llm

    def run(self, state: AgentState) -> SimulationOutput:
        self.last_usage = None
        exp, ret = state.explorer, state.retriever
        nodes = {n.node_id: n for n in exp.nodes} if exp else {}
        evidence = ret.evidence if ret else []

        built = build_impact_paths(exp.paths if exp else [], exp.edges if exp else [], evidence, DEFAULT_IMPACT)
        for path, _ in built:
            path.cross_domain = len({nodes[h.node].domain for h in path.hops if h.node in nodes and nodes[h.node].domain}) > 1
        iface = interface_node_ids(nodes, evidence)
        areas_of = {p.path_id: path_areas([h.node for h in p.hops], nodes, iface) for p, _ in built}
        hop_areas = {(p.path_id, i): a for p, _ in built for i, a in enumerate(node_areas([h.node for h in p.hops], nodes, iface))}
        shared = shared_nodes(exp.paths if exp else [], nodes)
        limits = unverified_segments(state, [p for p, _ in built])
        limits += assign_orphan_areas([p for p, _ in built], areas_of, hop_areas, exp.edges if exp else [],
                                      lambda: self.store)
        trace(f"Explorer 경로 {len(exp.paths) if exp else 0} → 영향 경로 {len(built)}개 (같은 노드 순서는 하나로, 근거 있는 hop 은 confirmed)",
              section=SKELETON_SECTION, label="골격")
        for area in AREA_ORDER:
            if ids := [p.path_id for p, _ in built if area in areas_of[p.path_id]]:
                trace(f"경로 {len(ids)}개: {', '.join(ids[:8])}{' …' if len(ids) > 8 else ''}", section=SKELETON_SECTION, label=area.value)
        if shared:
            trace("; ".join(f"{n} ({why})" for n, why in shared.items()), section=SKELETON_SECTION, label="공유 노드")
        on_paths = {h.node for p, _ in built for h in p.hops}
        if missing := [n for n in reanalysis_nodes(state) if n not in on_paths]:
            asked = set(reanalysis_nodes(state, notes=False))
            if req := [n for n in missing if n in asked]:
                limits.append(f"재분석 요청 노드 미탐색: {', '.join(req)} (이번 탐색 경로에 없어 판정하지 못함)")
            if ref := [n for n in missing if n not in asked]:
                limits.append(f"재분석 참고 노드 미탐색: {', '.join(ref)} (참고 신호, 이번 탐색 경로에 없어 판정하지 못함)")

        draft = None
        if not built:
            trace("영향 경로 없음 → LLM 판정 생략", section=LLM_SECTION, label="생략")
        elif self.llm is None:
            trace("LLM 없음 → 규칙 판정 (review · 난이도 기준표)", section=LLM_SECTION, label="생략")
        if built and self.llm is not None:
            trace(f"경로 {min(len(built), MAX_CONTEXT_PATHS)}/{len(built)}개 · 근거 원문 노드당 {CONTEXT_LINES_PER_EVIDENCE}줄 · "
                  f"전체 {MAX_CONTEXT_CHARS:,}자까지 · 공유 노드 {len(shared)} → SimulatorDraft (Structured Output, Few-shot)",
                  section=LLM_SECTION, label="입력")
            try:
                draft, self.last_usage = self.llm.parse(
                    self.system_prompt, user_message(state, [p for p, _ in built], areas_of, nodes, shared), SimulatorDraft)
            except LLM_FAILURES as exc:  # LLM 실패는 규칙 판정으로 대체 (분석 흐름을 끊지 않음)
                limits.append(f"LLM 판정 실패로 규칙 판정 사용: {exc}")
                trace(f"실패 → 규칙 판정으로 대체: {clip(exc, 150)}", section=LLM_SECTION, label="대체")
        if draft is not None:
            trace(f"노드 판정 {len(draft.nodes)}건 ([변경] {sum(d.change for d in draft.nodes)}) · 영역 {len(draft.areas)} · "
                  f"제외 제안 {len(draft.drop_paths)} · 요청안/대안 {'있음' if draft.requested_vs_alternative else '없음'}",
                  section=LLM_SECTION, label="응답")
            for d in draft.nodes:
                trace(f"{d.node} — {clip(d.impact.strip().removeprefix(CHANGE_TAG).removeprefix(REFERENCE_TAG).strip(), 240)}",
                      section=NODE_SECTION, label=CHANGE_TAG if d.change else REFERENCE_TAG)

        if draft is not None:  # LLM 에 보여 준 경로(MAX_CONTEXT_PATHS)의 노드만 판정된 것으로 본다
            shown = {h.node for p, _ in built[:MAX_CONTEXT_PATHS] for h in p.hops}
            apply_node_impacts(draft, [p for p, _ in built], shown)
            written = {d.node for d in draft.nodes}
            if skipped := [n for _, ns in target_nodes(state, shown) for n in ns if n not in written]:
                trace(f"요건 대상인데 LLM 이 판정하지 않음 → [참조] 기본값: {', '.join(dict.fromkeys(skipped))}",
                      section=NODE_SECTION, label="누락 보정")
                limits.append(f"요건 대상 노드 판정 누락: {', '.join(dict.fromkeys(skipped))} ([참조] 기본값으로 둠, 변경 여부 확인 필요)")
        kept, dropped = apply_drops(draft, built, state, shared)
        return SimulationOutput(
            area_impacts=judge_areas(draft, kept, areas_of, state, limits, (shared, hop_areas)),
            requested_vs_alternative=draft.requested_vs_alternative if draft else None,
            unverified_segments=limits,
            dropped_paths=dropped,
        )


def assign_orphan_areas(
    paths: list[ImpactPath], areas_of: dict[str, list[Area]], hop_areas: dict[tuple[str, int], set[Area]],
    edges: list[GraphEdge], store: Callable[[], GraphStore | None] = lambda: None,
) -> list[str]:
    """영역이 없는 경로(예: 시작 노드가 곧 목표 역할인 Module 하나짜리 경로)에 영역을 정한다. 분석 한계 문장을 돌려준다.

    영역이 없으면 어느 영역 경로에도 들어가지 않아 결과·레포트·KPI 에서 기록 없이 빠진다. 아래 순서로 정한다.
    1. 경로 첫 노드를 호출하는 노드(Explorer 엣지)가 다른 경로에서 가진 영역
    2. 그래프 역방향으로 찾은 호출 진입점(Screen·Batch)의 영역 (entry_areas_in_graph). Explorer 가 시작 노드에서 바로
       목표 역할에 닿으면 호출 쪽 엣지를 남기지 않아서 1 로는 못 찾는 경우가 대부분이다
    3. 둘 다 없으면 screen (rules.node_areas 의 "진입 노드 없이 EntryHandler 에서 시작한 경로는 screen" 과 같은 기준)
    store 는 그래프 저장소를 돌려주는 함수 (영역 없는 경로가 있을 때만 부른다). areas_of · hop_areas 를 그 자리에서 고친다.
    """
    known: dict[str, set[Area]] = {}
    for p in paths:
        for i, h in enumerate(p.hops):
            known.setdefault(h.node, set()).update(hop_areas[(p.path_id, i)])
    limits = []
    for p in paths:
        if areas_of[p.path_id] or not p.hops:
            continue
        first = p.hops[0].node
        callers = [e.from_id for e in edges if e.to_id == first]
        inherited = set().union(*(known.get(c, set()) for c in callers))
        found = None if inherited else entry_areas_in_graph(first, store())  # None = 그래프 조회 못함
        areas = inherited or set((found or {}).values()) or {Area.SCREEN}
        areas_of[p.path_id] = [a for a in AREA_ORDER if a in areas]
        for i in range(len(p.hops)):
            hop_areas[(p.path_id, i)] = hop_areas[(p.path_id, i)] | areas
        names = ", ".join(a.value for a in areas_of[p.path_id])
        if inherited:
            how = f"호출 노드의 영역({names})을 따름"
        elif found:
            entries = list(found)
            more = f" 외 {len(entries) - MAX_ENTRY_NAMES}개" if len(entries) > MAX_ENTRY_NAMES else ""
            how = f"그래프의 호출 진입점({', '.join(entries[:MAX_ENTRY_NAMES])}{more}) 기준 {names}"
        elif found is None:
            how = "screen 으로 둠 (그래프를 조회하지 못함, 진입 영역 확인 필요)"
        else:
            how = f"screen 으로 둠 (그래프 {MAX_ENTRY_HOPS} hop 안에 호출 진입점 없음, 진입 영역 확인 필요)"
        limits.append(f"영역 추정: {p.path_id} ({' → '.join(h.node for h in p.hops)}) 진입점이 없어 {how}")
    return limits


def entry_areas_in_graph(node_id: str, store: GraphStore | None) -> dict[str, Area] | None:
    """node_id 를 호출하는 진입점(Screen·Batch) → 영역. 호출 엣지(CALLER_EDGES)를 역방향으로 MAX_ENTRY_HOPS 까지 따라간다.

    진입점까지 가지 못하고 EntryHandler(TP·Controller)에서 끊기면 그 노드를 screen 진입으로 본다 (rules.node_areas 와 같은 기준).
    그래프가 없거나 조회에 실패하면 None.
    """
    if store is None:
        return None
    found: dict[str, Area] = {}
    seen, frontier = {node_id}, [node_id]
    try:
        for _ in range(MAX_ENTRY_HOPS):
            nxt = []
            for nid in frontier:
                for e in store.in_edges(nid):
                    if e.type not in CALLER_EDGES or e.from_id in seen or len(seen) >= MAX_ENTRY_VISITS:
                        continue
                    seen.add(e.from_id)
                    caller = store.node(e.from_id)
                    if node_role(caller) is NodeRole.ENTRY:
                        found.setdefault(e.from_id, ENTRY_AREAS.get(caller.label, Area.SCREEN))
                    else:
                        nxt.append(e.from_id)
                        if node_role(caller) is NodeRole.ENTRY_HANDLER and not store.in_edges(e.from_id):
                            found.setdefault(e.from_id, Area.SCREEN)
            frontier = nxt
    except Exception:  # noqa: BLE001  그래프 조회 실패는 screen 기본값으로 (분석을 막지 않는다)
        return None
    return found


# --- 판정 보정 (규칙) ----------------------------------------------------------------------------


def apply_node_impacts(draft: SimulatorDraft, paths: list[ImpactPath], seen: set[str]) -> None:
    """LLM 의 노드별 영향을 골격 hop 에 반영한다 (노드당 한 문장을 그 노드가 지나는 모든 경로에 같이 씀).

    LLM 이 쓰지 않은 노드는 [참조] + REFERENCE_IMPACT. 단 LLM 에 보여 주지 않은 노드(seen 밖, 경로 수 상한으로 잘림)는
    판정하지 않았으므로 기본값(DEFAULT_IMPACT, 표시 없음)으로 둔다. 골격 밖 노드는 무시. 같은 노드가 여러 번 오면
    [변경] 을 우선한다. LLM 이 문장 앞에 표시를 붙여 와도 한 번만 붙게 뗀다.
    """
    by_node: dict[str, tuple[bool, str]] = {}
    for d in draft.nodes:
        text = d.impact.strip().removeprefix(CHANGE_TAG).removeprefix(REFERENCE_TAG).strip()
        if text and (d.node not in by_node or (d.change and not by_node[d.node][0])):
            by_node[d.node] = (d.change, text)
    for p in paths:
        for h in p.hops:
            if h.node in by_node or h.node in seen:
                change, text = by_node.get(h.node, (False, REFERENCE_IMPACT))
                h.impact = f"{CHANGE_TAG if change else REFERENCE_TAG} {text}"


def apply_drops(
    draft: SimulatorDraft | None, built: list[tuple[ImpactPath, list[ExplorePath]]], state: AgentState,
    shared: dict[str, str] | None = None,
) -> tuple[list[ImpactPath], list[DroppedPath]]:
    """LLM 의 경로 제외 제안 중 가드 규칙을 통과한 것만 제외한다 (hop 영향 설명을 반영한 뒤 부른다)."""
    if draft is None:
        return [p for p, _ in built], []
    reasons = {d.path_id: d.reason for d in draft.drop_paths}
    confirmed = {m.confirmed_node for it in (state.planner.items if state.planner else [])
                 for m in it.mentions if m.confirmed_node}
    requested = set(reanalysis_nodes(state))
    # 어느 경로에서든 [변경] 으로 판정된 공유 노드: 그 노드를 지나는 다른 진입점 경로도 함께 영향받는 범위다
    changed_shared = {h.node for p, _ in built for h in p.hops if h.node in (shared or {}) and h.impact.startswith(CHANGE_TAG)}
    kept = list(built)
    dropped = []
    for path, sources in built:
        reason = reasons.get(path.path_id)
        if not reason:
            continue
        if guard := drop_guard_reason(path, sources, confirmed, kept, requested, changed_shared):
            trace(f"{path.path_id} 제외 제안 거절 — 가드: {guard} (LLM 사유: {clip(reason, 120)})", section=DROP_SECTION, label="거절")
            continue
        kept.remove((path, sources))
        dropped.append(DroppedPath(path=path, reason=reason))
        trace(f"{path.path_id} 제외 ({' → '.join(h.node for h in path.hops)}) — {clip(reason, 160)}", section=DROP_SECTION, label="수용")
    if unknown := [pid for pid in reasons if pid not in {p.path_id for p, _ in built}]:
        trace(f"골격에 없는 경로 id 라 무시: {', '.join(unknown)}", section=DROP_SECTION, label="무시")
    return [p for p, _ in kept], dropped


def drop_guarded(
    path: ImpactPath, sources: list[ExplorePath], confirmed: set[str], kept: list[tuple[ImpactPath, list[ExplorePath]]],
    requested: set[str] | None = None, changed_shared: set[str] | None = None,
) -> bool:
    """제외하면 안 되는 경로인지 (설계서 1장 경로 제외 가드)."""
    return drop_guard_reason(path, sources, confirmed, kept, requested, changed_shared) is not None


def drop_guard_reason(
    path: ImpactPath, sources: list[ExplorePath], confirmed: set[str], kept: list[tuple[ImpactPath, list[ExplorePath]]],
    requested: set[str] | None = None, changed_shared: set[str] | None = None,
) -> str | None:
    """경로 제외를 막은 가드 (없으면 None). 실행 로그에 거절 사유로 남긴다."""
    if any(s.stop_reason is StopReason.TARGET_REACHED for s in sources) and path.hops[-1].confidence is Confidence.CONFIRMED:
        return "목표 역할에 도달했고 마지막 노드에 근거가 있음"
    if hit := [h.node for h in path.hops if h.node in confirmed]:
        return f"사용자가 확정한 노드 {', '.join(hit)}"
    if requested and (hit := [h.node for h in path.hops if h.node in requested]):
        return f"재분석으로 확인 요청한 노드 {', '.join(hit)} (설계서 1-4)"
    if changed_shared and (hit := [h.node for h in path.hops if h.node in changed_shared]):
        return f"[변경] 된 공유 노드 {', '.join(hit)} 를 지남 → 함께 영향받는 진입점 경로 (설계서 1-3 (c))"
    others = {s.item_id for p, ss in kept if p is not path for s in ss}
    if lone := [s.item_id for s in sources if s.item_id not in others]:
        return f"빼면 요건 항목 {', '.join(map(str, dict.fromkeys(lone)))} 의 경로가 하나도 남지 않음"
    return None


def rule_difficulty(area: Area, paths: list[ImpactPath], state: AgentState) -> tuple[Difficulty, str]:
    """영역 난이도의 규칙 판정 (LLM 이 없을 때). 영역 기준만 본다 (영역 수·도메인 연계는 requirement_difficulty)."""
    if not paths:
        return Difficulty.MINOR, "변경 경로 없음"
    types = {it.change_type for it in state.planner.items} if state.planner else set()
    if area is Area.DB and (rules := [DB_DIFFICULTY_BY_CHANGE[t] for t in types if t in DB_DIFFICULTY_BY_CHANGE]):
        return max(rules, key=lambda r: DIFFICULTY_RANK.index(r[0]))
    if area is Area.INTERFACE:
        return Difficulty.MAJOR, "(규칙) 인터페이스 연계 변경"
    return Difficulty.MINOR, "(규칙) 영역 기준 해당 없음 (상세 판단은 원문 확인 필요)"


def requirement_difficulty(areas: list[AreaImpact]) -> tuple[Difficulty, str]:
    """요건 전체 난이도 = max(영역 난이도, 요건 기준). 요건 기준: 변경 영역 3개 이상·도메인 연계 → Major.

    Minor 영역끼리는 개수로 올리지 않는다 (설계서 1-2, 정답셋 L-1·L-3: Minor 영역 2개 → Minor).
    """
    changed = [a for a in areas if a.impact_type in (ImpactType.ADD, ImpactType.MODIFY)]
    if not changed:
        return Difficulty.MINOR, "변경 영역 없음"
    top = max(changed, key=lambda a: DIFFICULTY_RANK.index(a.difficulty))
    level, basis = top.difficulty, f"영역 중 최고 ({top.area.value}: {top.difficulty_basis})"
    if len(changed) >= 3:
        return Difficulty.MAJOR, f"변경 영역 {len(changed)}개 (3개 이상)"
    if any(p.cross_domain for a in changed for p in a.paths):
        return Difficulty.MAJOR, "도메인 간 연계"
    return level, basis


def apply_shared_floor(
    values: dict, area: Area, paths: list[ImpactPath], shared: dict[str, str], hop_areas: dict[tuple[str, int], set[Area]]
) -> None:
    """공유 노드를 [변경] 하는 영역은 최소 Medium, 일정 영향 요인에 회귀 테스트 (LLM 판정 보정).

    그 hop 이 실제로 속한 영역만 본다 (경로가 interface·db 에도 걸쳐 있어도 Module 변경은 진입 영역).
    """
    if values["impact_type"] not in (ImpactType.ADD, ImpactType.MODIFY):
        return
    hit = list(dict.fromkeys(
        h.node for p in paths for i, h in enumerate(p.hops)
        if h.node in shared and h.impact.startswith(CHANGE_TAG) and area in hop_areas.get((p.path_id, i), set())))
    if not hit:
        return
    why = ", ".join(f"{n} ({shared[n]})" for n in hit)
    if values["difficulty"] is Difficulty.MINOR:
        values["difficulty"] = Difficulty.MEDIUM
        values["difficulty_basis"] = f"(보정) 공유 노드 변경: {why} / LLM: {values['difficulty_basis']}"
        trace(f"난이도 Minor → Medium: 공유 노드를 [변경] ({why})", section=CHECK_SECTION, label=area.value)
    factor = f"공유 노드 회귀 테스트: {why}"
    if factor not in values["schedule_factors"] and len(values["schedule_factors"]) < MAX_SCHEDULE_FACTORS:
        values["schedule_factors"].append(factor)


def tag_mismatch(
    area: Area, impact_type: ImpactType, paths: list[ImpactPath], hop_areas: dict[tuple[str, int], set[Area]]
) -> str | None:
    """hop [변경]/[참조] 표시와 영역 impact_type 이 어긋나면 경고 문구 (판정은 바꾸지 않음).

    그 영역에 속한 hop(rules.node_areas)만 본다: [변경] 이 없는데 modify/add, [변경] 이 있는데 reference_only/none.
    """
    changed = list(dict.fromkeys(
        h.node for p in paths for i, h in enumerate(p.hops)
        if h.impact.startswith(CHANGE_TAG) and area in hop_areas.get((p.path_id, i), set())))
    if impact_type in (ImpactType.ADD, ImpactType.MODIFY) and not changed:
        return f"[{area.value}] {impact_type.value} 판정이지만 이 영역에 [변경] hop 이 없음 (판정 확인 필요)"
    if impact_type in (ImpactType.REFERENCE_ONLY, ImpactType.NONE) and changed:
        return f"[{area.value}] {impact_type.value} 판정이지만 [변경] hop 이 있음: {', '.join(changed)} (판정 확인 필요)"
    return None


def judge_areas(
    draft: SimulatorDraft | None, paths: list[ImpactPath], areas_of: dict[str, list[Area]], state: AgentState,
    limits: list[str], shared: tuple[dict[str, str], dict[tuple[str, int], set[Area]]] | None = None,
) -> list[AreaImpact]:
    """영역 4개의 판정. LLM 판정이 있으면 쓰고 규칙으로 보정, 없으면 규칙 판정.

    shared = (공유 노드 → 사유, (path_id, hop 순번) → 그 hop 의 영역). 공유 노드 보정에 쓴다.
    """
    shared_by_node, hop_areas = shared or ({}, {})
    tagged = any(h.impact.startswith((CHANGE_TAG, REFERENCE_TAG)) for p in paths for h in p.hops)  # LLM 판정이 있었음
    by_area = {a: [p for p in paths if a in areas_of[p.path_id]] for a in AREA_ORDER}
    drafts = {d.area: d for d in draft.areas} if draft else {}
    out = []
    for area in AREA_ORDER:
        ps = by_area[area]
        hop_conf = [h.confidence for p in ps for h in p.hops]
        all_confirmed = bool(hop_conf) and all(c is Confidence.CONFIRMED for c in hop_conf)
        any_confirmed = Confidence.CONFIRMED in hop_conf
        difficulty, basis = rule_difficulty(area, ps, state)
        values = {
            "impact_type": ImpactType.MODIFY if ps else ImpactType.NONE, "feasibility": Feasibility.REVIEW,
            "difficulty": difficulty, "difficulty_basis": basis, "schedule_factors": [], "infeasible_reason": None,
            "alternatives": [],
        }
        if draft is not None and area not in drafts:
            trace(f"LLM 판정 없음 → 규칙 판정 {values['impact_type'].value} · review · {difficulty.value} ({basis})",
                  section=CHECK_SECTION, label=area.value)
        if (d := drafts.get(area)) is not None:
            llm_said = f"{d.impact_type.value} · {d.feasibility.value} · {d.difficulty.value}"
            values.update(
                impact_type=d.impact_type, feasibility=d.feasibility, difficulty=d.difficulty,
                difficulty_basis=d.difficulty_basis.strip() or basis,
                schedule_factors=[f.strip() for f in d.schedule_factors if f.strip()][:MAX_SCHEDULE_FACTORS],
                infeasible_reason=d.infeasible_reason,
                alternatives=[Alternative(**a.model_dump()) for a in d.alternatives],
            )
            changes = values["impact_type"] in (ImpactType.ADD, ImpactType.MODIFY)
            if values["feasibility"] is Feasibility.FEASIBLE and changes and not any_confirmed:
                values["feasibility"] = Feasibility.REVIEW  # 근거(원문)가 하나도 없으면 가능 여부를 확정하지 않음
                trace("가능 여부 feasible → review: 이 영역 경로에 원문 근거(confirmed hop)가 하나도 없음", section=CHECK_SECTION,
                      label=area.value)
            if values["feasibility"] is Feasibility.HARD and not (values["infeasible_reason"] and values["alternatives"]):
                values["feasibility"] = Feasibility.REVIEW
                limits.append(f"[{area.value}] hard 판정의 사유·대안이 없어 review 로 둠")
                trace("가능 여부 hard → review: 불가 사유·대안이 없음", section=CHECK_SECTION, label=area.value)
            apply_shared_floor(values, area, ps, shared_by_node, hop_areas)
            if tagged and (warning := tag_mismatch(area, values["impact_type"], ps, hop_areas)):
                limits.append(warning)
                trace(f"경고: {warning}", section=CHECK_SECTION, label=area.value)
            final = f"{values['impact_type'].value} · {values['feasibility'].value} · {values['difficulty'].value}"
            trace(f"LLM {llm_said} → 최종 {final}" + (" (유지)" if final == llm_said else " (보정)")
                  + f" | 근거 hop {sum(c is Confidence.CONFIRMED for c in hop_conf)}/{len(hop_conf)} · 난이도 근거: {clip(values['difficulty_basis'], 120)}",
                  section=CHECK_SECTION, label=area.value)
        out.append(AreaImpact(area=area, paths=ps, confidence=Confidence.CONFIRMED if all_confirmed else Confidence.INFERRED,
                              **values))
    return out


def unverified_segments(state: AgentState, paths: list[ImpactPath]) -> list[str]:
    """분석 한계: 그래프 밖·목표 미도달·hop 상한·매칭 실패·공통 제외·근거 없음."""
    exp = state.explorer
    out: list[str] = []
    if exp is not None:
        out += exp.unresolved
        for r in exp.item_results:
            if not r.reached:
                out.append(f"[항목 {r.item_id}] 목표 역할({r.target_role.value})에 닿은 경로 없음")
            if r.frontier:
                out.append(f"[항목 {r.item_id}] hop 상한으로 끊긴 노드: {', '.join(r.frontier)}")
            if r.unmatched_mentions:
                out.append(f"[항목 {r.item_id}] 노드를 찾지 못한 표현: {', '.join(r.unmatched_mentions)}")
        if exp.excluded_common:
            out.append(f"공통 노드라 확장하지 않음: {', '.join(exp.excluded_common)}")
    inferred = list(dict.fromkeys(h.node for p in paths for h in p.hops if h.confidence is Confidence.INFERRED))
    if inferred:
        out.append(f"근거(원문) 없음: {', '.join(inferred)}")
    return list(dict.fromkeys(out))


# --- LLM 입력 ------------------------------------------------------------------------------------


def _node_text(node_id: str, nodes: dict[str, GraphNode]) -> str:
    n = nodes.get(node_id)
    return f"{node_id}({n.label}, {n.name})" if n else node_id


def _meta_value(value) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text if len(text) <= MAX_META_VALUE else text[:MAX_META_VALUE] + "…"


def node_meta_lines(paths: list[ImpactPath], nodes: dict[str, GraphNode], edges: list[GraphEdge]) -> list[str]:
    """[노드 메타]: 경로 노드마다 시스템·역할 + META_KEYS 속성, 경로 위 READS/WRITES 엣지의 접근 컬럼 (설계서 1-3 (a))."""
    out = []
    for nid in dict.fromkeys(h.node for p in paths for h in p.hops):
        n = nodes.get(nid)
        if n is None:
            continue
        role = node_role(n)
        head = f"- {nid} ({n.domain.value if n.domain else '-'}·{role.value if role else n.label})"
        props = [f"{k}={_meta_value(n.properties[k])}" for k in META_KEYS if n.properties.get(k) not in (None, "", [])]
        if n.properties.get("is_common"):
            props.append("공통 모듈")
        out.append(f"{head} {' / '.join(props)}" if props else head)
    pairs = {(p.hops[i - 1].node, h.node) for p in paths for i, h in enumerate(p.hops) if i}
    for e in edges:
        if (e.from_id, e.to_id) in pairs and e.type in ("READS", "WRITES") and e.properties.get("columns"):
            out.append(f"- {e.from_id} -{e.type}-> {e.to_id} columns={_meta_value(e.properties['columns'])}")
    return out


def reanalysis_request(state: AgentState) -> str | None:
    """사용자가 단순 응답의 "상세 분석 권장"을 보고 요청한 재분석 사유 (가장 최근 reanalysis 후속 입력, 설계서 1-4)."""
    last = next((f for f in reversed(state.followups) if f.type is FollowupType.REANALYSIS), None)
    if last is None:
        return None
    return last.content.strip() or None


def reanalysis_nodes(state: AgentState, notes: bool = True) -> list[str]:
    """재분석 사유에 나온 노드 ID (문장 끝 마침표는 뗀다, 순서 유지·중복 제거).

    재분석 입력은 권장 사유 줄과 "참고:" 로 시작하는 참고 신호 줄로 온다 (줄바꿈으로 구분).
    notes=False 면 참고 신호 줄에만 나온 노드는 뺀다 (권장 사유 노드만 = Verifier 가 막는 대상).
    """
    reason = reanalysis_request(state)
    if reason is None:
        return []
    lines = [x for x in reason.splitlines() if notes or not x.strip().startswith(NOTE_PREFIX)]
    return list(dict.fromkeys(m.rstrip(".") for x in lines for m in NODE_ID_IN_TEXT.findall(x)))


def target_nodes(state: AgentState, path_nodes: set[str]) -> list[tuple[str, list[str]]]:
    """요건 항목별 대상 노드 = Explorer 가 요건 언급 표현에서 찾은 탐색 시작 노드 중 경로에 있는 것. [(항목 머리, 노드)].

    LLM 이 이 노드를 쓰지 않으면 [참조] 기본값이 붙는데, 요건이 직접 가리킨 노드라 판정을 빠뜨리면 [변경] 이 조용히 빠진다.
    """
    exp = state.explorer
    items = {it.item_id: it for it in (state.planner.items if state.planner else [])}
    out = []
    for r in exp.item_results if exp else []:
        ns = [n for n in dict.fromkeys(r.start_node_ids) if n in path_nodes]
        if not ns:
            continue
        it = items.get(r.item_id)
        phrases = ", ".join(dict.fromkeys(m.phrase for m in it.mentions)) if it else ""
        out.append((f"항목 {r.item_id}" + (f" (언급: {phrases})" if phrases else ""), ns))
    return out


def user_message(
    state: AgentState, paths: list[ImpactPath], areas_of: dict[str, list[Area]], nodes: dict[str, GraphNode],
    shared: dict[str, str] | None = None,
) -> str:
    lines = ["[요청]", state.user_input.request_text.strip(), "", "[요건 항목]"]
    if state.planner:
        lines += [f"- 항목 {it.item_id} ({it.change_type.value}) {it.text}" for it in state.planner.items]
    if reason := reanalysis_request(state):
        lines += ["", "[재분석 요청] 사용자가 단순 응답을 보고 상세 분석을 요청했다. 아래 사유에 나온 노드·확인 사항마다 판정 결과를 쓴다. "
                  f"'{NOTE_PREFIX}' 로 시작하는 줄은 참고 신호다 (경로에 있으면 같은 방식으로 판정을 쓴다).",
                  reason]

    lines += ["", "[영역별 경로] path_id: 노드 -관계-> 노드 … (진입 → DB 순서, 근거 없는 노드는 *)"]
    shown = paths[:MAX_CONTEXT_PATHS]
    for area in AREA_ORDER:
        lines.append(f"### {area.value}")
        rows = [p for p in shown if area in areas_of[p.path_id]]
        for p in rows:
            hops = " ".join(
                (f"-{h.relation}-> " if i else "") + _node_text(h.node, nodes) + ("" if h.confidence is Confidence.CONFIRMED else "*")
                for i, h in enumerate(p.hops))
            lines.append(f"- {p.path_id}: {hops}")
        if not rows:
            lines.append("- 없음")
    if len(paths) > MAX_CONTEXT_PATHS:
        lines.append(f"- … 외 {len(paths) - MAX_CONTEXT_PATHS}개 (판정은 보이는 경로 기준)")

    path_nodes = {h.node for p in shown for h in p.hops}
    lines += ["", f"[경로 노드] {len(path_nodes)}개 (중복 없음, nodes 는 이 중에서 노드당 한 번)",
              ", ".join(dict.fromkeys(h.node for p in shown for h in p.hops)) or "-"]
    if targets := target_nodes(state, path_nodes):
        lines += ["", "[요건 대상 노드] 요건 언급 표현과 매칭된 탐색 시작 노드. 노드마다 nodes 에 판정(change true/false + 근거)을 쓴다"]
        lines += [f"- {head}: {', '.join(ns)}" for head, ns in targets]
    lines += ["", "[노드 메타] 노드 (시스템·역할) 판단용 속성 / 데이터 접근 엣지의 접근 컬럼"]
    lines += node_meta_lines(shown, nodes, state.explorer.edges if state.explorer else [])
    shown_shared = [n for n in dict.fromkeys(h.node for p in shown for h in p.hops) if n in (shared or {})]
    if shown_shared:
        lines += ["", "[공유 노드] 여러 진입점(Screen·Batch)에서 호출되거나 공통 모듈. 바꾸면 걸친 영역 모두 영향, 최소 Medium"]
        lines += [f"- {n}: {shared[n]}" for n in shown_shared]

    lines += ["", "[노드 원문]"]
    used = 0
    evidence = state.retriever.evidence if state.retriever else []
    for e in evidence:
        if e.node_id not in path_nodes:
            continue
        body = "\n".join((e.content or "").splitlines()[:CONTEXT_LINES_PER_EVIDENCE])
        block = f"### {e.node_id} {e.file_path} {e.location or ''} ({e.source_kind})\n{e.summary}\n{body}"
        if used + len(block) > MAX_CONTEXT_CHARS:
            lines.append("- … 원문 일부 생략 (길이 상한)")
            break
        lines.append(block)
        used += len(block)

    exp = state.explorer
    if exp and exp.unresolved:
        lines += ["", "[그래프 밖·미해결]"] + [f"- {u}" for u in exp.unresolved]
    if state.replan_feedback:
        lines += ["", "[이전 재계획 사유] 같은 누락이 반복되지 않게 판단하세요."]
        lines += [f"- {fb.round}차: {fb.reason}" for fb in state.replan_feedback]
    return "\n".join(lines)
