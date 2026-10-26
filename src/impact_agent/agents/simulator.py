"""Simulator: (복잡) 영역별 영향 경로를 구성하고 구현 가능성·난이도·대안을 판정한다. 설계: docs/SE_agent_design.md 1장

1. 경로 골격 (규칙): Explorer 경로 → ImpactPath (엣지 유형·근거 위치·확신도), rules.build_impact_paths
2. 영역 배정 (규칙): 역할 기준 screen / db / interface / batch (rules.path_areas)
3. 영향 판정 (LLM, 있을 때): hop 별 영향, 영역별 영향 유형·가능성·난이도·대안, 요청안/대안 비교, 무관 경로 제외 제안
4. 판정 보정 (규칙): 골격 밖 노드 무시, 근거 없는 영역은 review, 사유·대안 없는 hard 는 review, 경로 제외 가드
5. 분석 한계: unresolved · 목표 미도달 · hop 상한 · 매칭 실패 · 근거 없음 · 공통 제외 → unverified_segments
LLM 이 없거나 실패하면 3단계를 건너뛰고 규칙 판정(review, 난이도 기준표)으로 낸다. 경로는 제외하지 않는다.
"""

from pydantic import BaseModel, Field

from ..llm import LLM_FAILURES, LLMClient, Usage, analysis_llm
from ..rules import AREA_ORDER, build_impact_paths, interface_node_ids, path_areas
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
    GraphNode,
    ImpactPath,
    ImpactType,
    SimulationOutput,
    StopReason,
)
from ..state import AgentState
from .base import BaseAgent

DEFAULT_IMPACT = "영향 검토 필요"
MAX_CONTEXT_PATHS = 40  # LLM 입력에 보여 줄 경로 수
CONTEXT_LINES_PER_EVIDENCE = 12  # 노드당 원문 줄 수
MAX_CONTEXT_CHARS = 16000  # 원문 블록 전체 문자 수 상한
SCHEMA_CHANGE_TYPES = {ChangeType.COLUMN_SPEC_CHANGE, ChangeType.DATA_ADD}


# --- LLM 응답 형식 (Structured Outputs 호환: 기본값 없이 모든 필드 명시) ---------------------


class DraftHop(BaseModel):
    path_id: str
    node: str = Field(description="경로에 있는 노드 ID 그대로")
    impact: str = Field(description="이 노드에서 바뀌는 것 / 확인만 필요한 것 (한 문장)")


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
    schedule_factors: list[str] = Field(description="일정에 영향을 주는 요인. 없으면 []")
    infeasible_reason: str | None = Field(description="feasibility=hard 일 때 사유. 아니면 null")
    alternatives: list[DraftAlternative] = Field(description="feasibility=hard 이거나 요청안보다 나은 방법이 있을 때. 없으면 []")


class DraftDrop(BaseModel):
    path_id: str
    reason: str = Field(description="요건과 무관하다고 본 근거")


class SimulatorDraft(BaseModel):
    hops: list[DraftHop]
    areas: list[DraftArea] = Field(description="screen / db / interface / batch 4개 모두")
    requested_vs_alternative: str | None = Field(description="요청안과 대안이 갈리면 비교 요약. 아니면 null")
    drop_paths: list[DraftDrop] = Field(description="요건과 무관한 경로만. 없으면 []")


# --- 에이전트 ------------------------------------------------------------------------------------


class SimulatorAgent(BaseAgent[SimulationOutput]):
    name = "simulator"
    output_model = SimulationOutput

    def __init__(self, llm: LLMClient | None = None, use_llm: bool = True) -> None:
        self._llm = llm
        self._llm_checked = llm is not None or not use_llm
        self.last_usage: Usage | None = None

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
        limits = unverified_segments(state, [p for p, _ in built])

        draft = None
        if built and self.llm is not None:
            try:
                draft, self.last_usage = self.llm.parse(
                    self.system_prompt, user_message(state, [p for p, _ in built], areas_of, nodes), SimulatorDraft)
            except LLM_FAILURES as exc:  # LLM 실패는 규칙 판정으로 대체 (분석 흐름을 끊지 않음)
                limits.append(f"LLM 판정 실패로 규칙 판정 사용: {exc}")

        if draft is not None:
            apply_hop_impacts(draft, [p for p, _ in built])
        kept, dropped = apply_drops(draft, built, state)
        return SimulationOutput(
            area_impacts=judge_areas(draft, kept, areas_of, state, limits),
            requested_vs_alternative=draft.requested_vs_alternative if draft else None,
            unverified_segments=limits,
            dropped_paths=dropped,
        )


# --- 판정 보정 (규칙) ----------------------------------------------------------------------------


def apply_hop_impacts(draft: SimulatorDraft, paths: list[ImpactPath]) -> None:
    """LLM 의 hop 영향 설명을 골격 hop 에만 반영한다 (골격 밖 노드·경로는 무시)."""
    impacts = {(h.path_id, h.node): h.impact for h in draft.hops if h.impact.strip()}
    for p in paths:
        for h in p.hops:
            h.impact = impacts.get((p.path_id, h.node), h.impact)


def apply_drops(
    draft: SimulatorDraft | None, built: list[tuple[ImpactPath, list[ExplorePath]]], state: AgentState
) -> tuple[list[ImpactPath], list[DroppedPath]]:
    """LLM 의 경로 제외 제안 중 가드 규칙을 통과한 것만 제외한다."""
    if draft is None:
        return [p for p, _ in built], []
    reasons = {d.path_id: d.reason for d in draft.drop_paths}
    confirmed = {m.confirmed_node for it in (state.planner.items if state.planner else [])
                 for m in it.mentions if m.confirmed_node}
    kept = list(built)
    dropped = []
    for path, sources in built:
        reason = reasons.get(path.path_id)
        if reason and not drop_guarded(path, sources, confirmed, kept):
            kept.remove((path, sources))
            dropped.append(DroppedPath(path=path, reason=reason))
    return [p for p, _ in kept], dropped


def drop_guarded(
    path: ImpactPath, sources: list[ExplorePath], confirmed: set[str], kept: list[tuple[ImpactPath, list[ExplorePath]]]
) -> bool:
    """제외하면 안 되는 경로인지 (설계서 1장 경로 제외 가드)."""
    if any(s.stop_reason is StopReason.TARGET_REACHED for s in sources) and path.hops[-1].confidence is Confidence.CONFIRMED:
        return True  # 목표 역할에 도달했고 마지막 노드에 근거가 있음
    if any(h.node in confirmed for h in path.hops):
        return True  # 사용자가 확정한 노드가 있음
    others = {s.item_id for p, ss in kept if p is not path for s in ss}
    return any(s.item_id not in others for s in sources)  # 빼면 그 요건 항목의 경로가 없어짐


def rule_difficulty(area: Area, paths: list[ImpactPath], changed: list[Area], state: AgentState) -> tuple[Difficulty, str]:
    """난이도 기준표의 규칙 판정 (LLM 이 없을 때). 설계서 1장."""
    if not paths:
        return Difficulty.MINOR, "변경 경로 없음"
    types = {it.change_type for it in state.planner.items} if state.planner else set()
    if area is Area.DB and types & SCHEMA_CHANGE_TYPES:
        return Difficulty.MAJOR, "(규칙) DB 스키마 변경 가능 (컬럼 추가·길이·타입)"
    if area is Area.INTERFACE:
        return Difficulty.MAJOR, "(규칙) 인터페이스 연계 변경"
    if len(changed) >= 3:
        return Difficulty.MAJOR, "(규칙) 변경 영역 3개 이상"
    if any(p.cross_domain for p in paths):
        return Difficulty.MAJOR, "(규칙) 도메인 간 연계"
    if len(changed) == 2:
        return Difficulty.MEDIUM, "(규칙) 변경 영역 2개"
    return Difficulty.MINOR, "(규칙) 단일 영역 변경"


def judge_areas(
    draft: SimulatorDraft | None, paths: list[ImpactPath], areas_of: dict[str, list[Area]], state: AgentState,
    limits: list[str],
) -> list[AreaImpact]:
    """영역 4개의 판정. LLM 판정이 있으면 쓰고 규칙으로 보정, 없으면 규칙 판정."""
    by_area = {a: [p for p in paths if a in areas_of[p.path_id]] for a in AREA_ORDER}
    changed = [a for a in AREA_ORDER if by_area[a]]
    drafts = {d.area: d for d in draft.areas} if draft else {}
    out = []
    for area in AREA_ORDER:
        ps = by_area[area]
        hop_conf = [h.confidence for p in ps for h in p.hops]
        all_confirmed = bool(hop_conf) and all(c is Confidence.CONFIRMED for c in hop_conf)
        any_confirmed = Confidence.CONFIRMED in hop_conf
        difficulty, basis = rule_difficulty(area, ps, changed, state)
        values = {
            "impact_type": ImpactType.MODIFY if ps else ImpactType.NONE, "feasibility": Feasibility.REVIEW,
            "difficulty": difficulty, "difficulty_basis": basis, "schedule_factors": [], "infeasible_reason": None,
            "alternatives": [],
        }
        if (d := drafts.get(area)) is not None:
            values.update(
                impact_type=d.impact_type, feasibility=d.feasibility, difficulty=d.difficulty,
                difficulty_basis=d.difficulty_basis.strip() or basis, schedule_factors=d.schedule_factors,
                infeasible_reason=d.infeasible_reason,
                alternatives=[Alternative(**a.model_dump()) for a in d.alternatives],
            )
            changes = values["impact_type"] in (ImpactType.ADD, ImpactType.MODIFY)
            if values["feasibility"] is Feasibility.FEASIBLE and changes and not any_confirmed:
                values["feasibility"] = Feasibility.REVIEW  # 근거(원문)가 하나도 없으면 가능 여부를 확정하지 않음
            if values["feasibility"] is Feasibility.HARD and not (values["infeasible_reason"] and values["alternatives"]):
                values["feasibility"] = Feasibility.REVIEW
                limits.append(f"[{area.value}] hard 판정의 사유·대안이 없어 review 로 둠")
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


def user_message(state: AgentState, paths: list[ImpactPath], areas_of: dict[str, list[Area]], nodes: dict[str, GraphNode]) -> str:
    lines = ["[요청]", state.user_input.request_text.strip(), "", "[요건 항목]"]
    if state.planner:
        lines += [f"- 항목 {it.item_id} ({it.change_type.value}) {it.text}" for it in state.planner.items]

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

    lines += ["", "[노드 원문]"]
    used = 0
    evidence = state.retriever.evidence if state.retriever else []
    path_nodes = {h.node for p in shown for h in p.hops}
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
