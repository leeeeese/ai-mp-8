"""Explorer: Planner 탐색 명세(items)의 언급 표현을 노드로 매칭하고, 목표 역할까지 경로를 탐색한다.

- 시작 노드: 확정 매핑(confirmed_node) → 용어사전·노드명·설명 검색 (search_graph_nodes, 같은 표현·라벨 검색은 한 번만)
- tool 호출: 규칙 단계·재탐색은 Retriever 원문 읽기 1회, ReAct 는 RESERVED_FOR_RETRIEVER 를 남긴다
- 탐색 (traverse_graph): direction 에 따라
    forward               목표 역할보다 진입 쪽 시작 노드에서 정방향
    reverse               목표 역할보다 DB 쪽 시작 노드에서 역방향
    forward_then_reverse  정방향으로 목표(수정 대상)를 찾고, 그 대상(언급된 컬럼이 있으면 그 컬럼)에서 진입점까지 역방향
- 재탐색: Retriever 의 reexplore_request 를 받아 이전 결과에 이어 붙인다 (hop 상한으로 끊긴 경로는 이어서 하나로 합침)
- out_of_graph·매칭 실패·신규 후보는 unresolved 로 넘긴다
- ReAct 보완 (Yao et al. 2023, LLM 이 있을 때 첫 라운드만): 규칙 탐색 결과를 Observation 으로 주고,
  LLM 이 Thought → Action(search_graph_nodes / traverse_graph / get_node_detail / finish) 을 골라 매칭 실패·끊긴 경로를 보완한다.
  규칙 결과에서 점검 신호(매칭 실패·목표 미도달·진입점 없음·언급 값의 컬럼 없음·공통 제외)를 먼저 계산해,
  신호가 없으면 ReAct 를 생략하고 있으면 그 신호만 입력에 표시한다. 단계 수는 EXPLORER_MAX_REACT_STEPS 까지.
  결과는 같은 출력에 합치고 단계 기록은 react_trace 에 남긴다.
요건 유형 판단은 하지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from ..llm import LLMClient, Usage, react_llm
from ..rules import EXPLORER_MAX_REACT_STEPS, MENTION_LABELS, RESERVED_FOR_RETRIEVER, ROLE_RANK
from ..schemas import (
    Direction,
    ExploreItem,
    ExplorePath,
    ExplorerOutput,
    GetNodeDetailInput,
    ItemExploreResult,
    MatchSource,
    MentionKind,
    NodeRole,
    PlannerOutput,
    ReexploreMode,
    ReexploreRequest,
    SearchGraphNodesInput,
    StartNodeChoice,
    StopReason,
    TraverseGraphInput,
    TraverseGraphOutput,
)
from ..state import AgentState
from ..tools import GraphStore, get_graph_store, get_node_detail, search_graph_nodes, traverse_graph
from .base import BaseAgent
from .react import InvalidAction, run_react

MIN_SCORE = 0.5  # 이보다 낮은 후보는 시작 노드로 쓰지 않음
RELATIVE_SCORE = 0.8  # 1등 점수 대비 이 비율 이상인 후보까지 시작 노드로 채택
MAX_START_PER_MENTION = 3
MAX_CONTEXT_PATHS = 40  # ReAct 입력에 보여 줄 경로 수
VALUE_MENTIONS = {MentionKind.COLUMN, MentionKind.TERM}  # 값·여부·코드·사유를 말하는 언급 (컬럼까지 확인 대상)


class ExplorerDecision(BaseModel):
    """ReAct 한 단계 (Structured Output: 기본값 없이 모든 필드 명시)."""

    thought: str = Field(description="지금까지의 조회 결과에서 무엇이 부족한지, 다음 행동을 왜 고르는지")
    action: Literal["search_graph_nodes", "traverse_graph", "get_node_detail", "finish"]
    item_id: int | None = Field(description="대상 탐색 명세 항목 (traverse_graph 필수)")
    terms: list[str] | None = Field(description="search_graph_nodes: 찾을 표현 (요건 표현·동의어·시스템 용어)")
    labels: list[str] | None = Field(description="search_graph_nodes: 노드 유형 제한 (예: Screen, Column). 제한 없으면 null")
    node_ids: list[str] | None = Field(description="traverse_graph: 시작 노드 / get_node_detail: 노드 1개. 조회 결과에 나온 ID 만")
    direction: Literal["forward", "reverse"] | None = Field(description="traverse_graph 방향")
    target_role: NodeRole | None = Field(description="traverse_graph 목표 역할. null 이면 항목의 목표 역할")


class ToolBudgetExceeded(Exception):
    pass


class ExplorerAgent(BaseAgent[ExplorerOutput]):
    name = "explorer"
    output_model = ExplorerOutput

    def __init__(self, store: GraphStore | None = None, llm: LLMClient | None = None) -> None:
        self._store = store
        self._llm = llm
        self._llm_checked = llm is not None
        self.last_usage: Usage | None = None

    @property
    def llm(self) -> LLMClient | None:
        """ReAct 단계 LLM. 주입하지 않았으면 .env 설정으로 한 번 만든다 (키가 없으면 None → 규칙 단계만)."""
        if not self._llm_checked:
            self._llm, self._llm_checked = react_llm(), True
        return self._llm

    @property
    def store(self) -> GraphStore:
        if self._store is None:
            self._store = get_graph_store()
        return self._store

    def run(self, state: AgentState) -> ExplorerOutput:
        plan = state.planner
        self._calls = 0
        self.last_usage = None  # 재탐색 라운드는 LLM 을 쓰지 않으므로 이전 라운드 사용량이 다시 기록되지 않게 비움
        self._budget = state.tools_left()
        self._reserve = 1  # 규칙 단계·재탐색도 Retriever 원문 읽기 1회는 남긴다
        self._searches: dict[tuple, list] = {}  # 같은 검색(표현·라벨)은 항목이 달라도 한 번만 호출
        requests = state.retriever.reexplore_request if state.retriever else []
        if state.explorer is not None and requests:
            out = state.explorer.model_copy(deep=True)
            for req in requests:
                self._guard(out, lambda: self._reexplore(req, plan, out))
        else:
            out = ExplorerOutput(start_nodes=[], nodes=[], edges=[])
            for item in plan.items:
                self._guard(out, lambda: self._explore_item(item, plan, out))
            signals = self._signals(plan, out)
            if self.llm is not None and signals and self._budget - self._calls > RESERVED_FOR_RETRIEVER:
                self._reserve = RESERVED_FOR_RETRIEVER
                self._guard(out, lambda: self._react(plan, out, signals))
        out.tool_call_count = self._calls
        out.hops_used = max((r.hops_used for r in out.item_results), default=0)
        return out

    # --- tool 호출 (호출 수 관리) -----------------------------------------------

    def _guard(self, out: ExplorerOutput, fn) -> None:
        try:
            fn()
        except ToolBudgetExceeded:
            note = f"tool 호출 상한({config.MAX_TOOL_CALLS}) 도달 - 이후 탐색 생략"
            if note not in out.unresolved:
                out.unresolved.append(note)

    def _tool(self, fn, params):
        if self._calls >= self._budget - self._reserve:
            raise ToolBudgetExceeded
        self._calls += 1
        return fn(params, self.store)

    # --- 항목 탐색 ---------------------------------------------------------------

    def _explore_item(self, item: ExploreItem, plan: PlannerOutput, out: ExplorerOutput) -> None:
        result = ItemExploreResult(item_id=item.item_id, target_role=item.target_role, direction=item.direction, reached=False)
        out.item_results.append(result)
        for area in item.out_of_graph:
            out.unresolved.append(f"[항목 {item.item_id}] 그래프 밖: {area}")

        starts = self._match_starts(item, plan, out, result)
        if not starts:
            return
        target = ROLE_RANK[item.target_role]
        rank = {nid: ROLE_RANK[self.store.node(nid).role] for nid in starts}
        low = [n for n in starts if rank[n] < target]
        high = [n for n in starts if rank[n] > target]
        at = [n for n in starts if rank[n] == target]
        columns = [n for n in starts if self.store.node(n).label == "Column"]
        exclude = plan.exclude.is_common

        for n in at:
            self._add_paths(out, item.item_id, Direction.FORWARD,
                            TraverseGraphOutput(nodes=[self.store.node(n)], edges=[], paths=[
                                {"nodes": [n], "reached_role": item.target_role, "stop_reason": StopReason.TARGET_REACHED}]))

        if item.direction is Direction.FORWARD and low:
            self._traverse(out, item.item_id, low, "forward", item.target_role, item.hop_cap, exclude, columns)
        elif item.direction is Direction.REVERSE:
            if high:
                self._traverse(out, item.item_id, high, "reverse", item.target_role, item.hop_cap, exclude, columns)
            entries = [n for n in at + low if self.store.node(n).role is NodeRole.ENTRY]
            if entries:  # 언급으로 찾은 진입점(예: 연계 배치)이 언급 컬럼의 테이블에 닿는지 정방향으로 확인
                self._traverse(out, item.item_id, entries, "forward", NodeRole.COLUMN if columns else NodeRole.TABLE,
                               item.hop_cap, exclude, columns)
        elif item.direction is Direction.FORWARD_THEN_REVERSE:
            fwd = self._traverse(out, item.item_id, low, "forward", item.target_role, item.hop_cap, exclude, columns) if low else None
            reached = [p.nodes[-1] for p in fwd.paths if p.stop_reason is StopReason.TARGET_REACHED] if fwd else []
            reached += at
            # 언급된 컬럼이 있으면 그 컬럼에서, 없으면 도달한 테이블·언급된 테이블에서 진입점까지
            rev_from = columns or list(dict.fromkeys(reached + high))
            remaining = max(item.hop_cap - (fwd.hops_used if fwd else 0), 1)
            if rev_from:
                self._traverse(out, item.item_id, rev_from, "reverse", NodeRole.ENTRY, remaining, exclude, [],
                               writes_only=True)
        self._update_result(out, result)

    def _match_starts(self, item: ExploreItem, plan: PlannerOutput, out: ExplorerOutput,
                      result: ItemExploreResult) -> list[str]:
        starts: list[str] = []
        for m in item.mentions:
            if m.confirmed_node and self.store.node(m.confirmed_node):
                starts.append(m.confirmed_node)
                out.start_nodes.append(StartNodeChoice(item_id=item.item_id, node_id=m.confirmed_node, matched_keyword=m.phrase,
                                                       match_source=MatchSource.CONFIRMED, score=1.0, reason="사용자 확정 매핑"))
                continue
            key = (m.phrase, *m.synonyms, "|", *(MENTION_LABELS[m.kind] or []))
            if key not in self._searches:
                self._searches[key] = self._tool(search_graph_nodes, SearchGraphNodesInput(
                    terms=[m.phrase, *m.synonyms], domains=plan.domains, labels=MENTION_LABELS[m.kind], top_k=5))
            hits = self._searches[key]
            good = [h for h in hits if h.score >= MIN_SCORE]
            if not good:
                result.unmatched_mentions.append(m.phrase)
                kind = "신규 후보" if m.maybe_new else "매칭 실패"
                out.unresolved.append(f"[항목 {item.item_id}] {kind}: {m.phrase} ({m.kind.value})")
                continue
            for h in [h for h in good if h.score >= good[0].score * RELATIVE_SCORE][:MAX_START_PER_MENTION]:
                starts.append(h.node_id)
                out.start_nodes.append(StartNodeChoice(
                    item_id=item.item_id, node_id=h.node_id, matched_keyword=h.matched_term or m.phrase,
                    match_source=h.match_source, score=h.score,
                    reason=f"'{h.matched_term}' ≈ {h.name} ({h.label}, {h.match_source.value} 점수 {h.score:g})"))
        starts = list(dict.fromkeys(starts))
        result.start_node_ids = starts
        return starts

    def _traverse(self, out, item_id, start_ids, direction, target, hop_cap, exclude, focus, writes_only=False,
                  join=False) -> TraverseGraphOutput:
        res = self._tool(traverse_graph, TraverseGraphInput(
            start_ids=start_ids, direction=direction, target_role=target, hop_cap=hop_cap, exclude_common=exclude,
            focus_ids=focus, writes_only=writes_only))
        self._add_paths(out, item_id, Direction(direction), res, join_from=start_ids if join else None)
        return res

    # --- 결과 합치기 ---------------------------------------------------------------

    def _add_paths(self, out: ExplorerOutput, item_id: int, direction: Direction, res: TraverseGraphOutput,
                   join_from: list[str] | None = None) -> None:
        known = {n.node_id for n in out.nodes}
        out.nodes += [n for n in res.nodes if n.node_id not in known]
        known_e = {(e.from_id, e.to_id, e.type) for e in out.edges}
        out.edges += [e for e in res.edges if (e.from_id, e.to_id, e.type) not in known_e]
        out.excluded_common = sorted(set(out.excluded_common) | set(res.excluded_common))

        # 재탐색(continue): hop 상한으로 끊긴 이전 경로를 빼고, 새 경로 앞(역방향이면 뒤)에 이어 붙인다
        cut = []
        if join_from:
            cut = [o for o in out.paths if o.item_id == item_id and o.stop_reason is StopReason.HOP_CAP
                   and _end(o) in set(join_from)]
            out.paths = [o for o in out.paths if o not in cut]
        for p in res.paths:
            nodes = list(p.nodes)
            for old in cut:
                if direction is Direction.FORWARD and old.nodes[-1] == nodes[0]:
                    nodes = old.nodes[:-1] + nodes
                    break
                if direction is Direction.REVERSE and old.nodes[0] == nodes[-1]:
                    nodes = nodes[:-1] + old.nodes
                    break
            if any(o.item_id == item_id and o.nodes == nodes for o in out.paths):
                continue
            out.paths.append(ExplorePath(item_id=item_id, nodes=nodes, direction=direction,
                                         reached_role=p.reached_role, stop_reason=p.stop_reason))

    def _update_result(self, out: ExplorerOutput, result: ItemExploreResult) -> None:
        mine = [p for p in out.paths if p.item_id == result.item_id]
        result.reached = any(p.stop_reason is StopReason.TARGET_REACHED for p in mine)
        result.frontier = sorted({_end(p) for p in mine if p.stop_reason is StopReason.HOP_CAP})
        roles = lambda path: [self.store.node(n).role for n in path]  # noqa: E731
        result.hops_used = max((sum(a is not b for a, b in zip(roles(p.nodes), roles(p.nodes)[1:])) for p in mine), default=0)

    # --- 재탐색 -------------------------------------------------------------------

    def _reexplore(self, req: ReexploreRequest, plan: PlannerOutput, out: ExplorerOutput) -> None:
        item = next((i for i in plan.items if i.item_id == req.item_id), None)
        item_id = req.item_id if req.item_id is not None else 0
        if req.direction is not None:
            direction = req.direction
        elif req.mode is ReexploreMode.REVERSE:
            direction = Direction.REVERSE
        elif item is not None and req.mode is ReexploreMode.CONTINUE:
            direction = Direction.REVERSE if item.direction is Direction.REVERSE else Direction.FORWARD
        else:
            direction = Direction.FORWARD
        target = req.target_role or (NodeRole.ENTRY if direction is Direction.REVERSE else
                                     item.target_role if item else NodeRole.TABLE)
        cap = item.hop_cap if item else config.MAX_HOPS_FORWARD
        columns = [n for n in req.node_ids if (node := self.store.node(n)) and node.label == "Column"]
        self._traverse(out, item_id, req.node_ids, direction.value, target, cap, plan.exclude.is_common, columns,
                       join=req.mode is ReexploreMode.CONTINUE)
        result = next((r for r in out.item_results if r.item_id == item_id), None)
        if result is None:
            result = ItemExploreResult(item_id=item_id, target_role=target, direction=direction, reached=False)
            out.item_results.append(result)
        self._update_result(out, result)


    # --- ReAct 보완 ------------------------------------------------------------------

    def _signals(self, plan: PlannerOutput, out: ExplorerOutput) -> list[str]:
        """규칙 결과에서 ReAct 로 보완할 신호 (프롬프트 점검 항목 번호와 맞춤). 없으면 ReAct 를 생략한다."""
        signals = []
        for it in plan.items:
            r = next((x for x in out.item_results if x.item_id == it.item_id), None)
            if r is None:
                continue
            mine = [p for p in out.paths if p.item_id == it.item_id]
            nodes = [self.store.node(n) for p in mine for n in p.nodes]
            if r.unmatched_mentions:
                signals.append(f"① 항목 {it.item_id}: 매칭 실패 {r.unmatched_mentions}")
            if not r.reached:
                signals.append(f"② 항목 {it.item_id}: 목표 역할({it.target_role.value}) 미도달")
            if mine and not any(self.store.node(p.nodes[0]).role is NodeRole.ENTRY for p in mine):
                signals.append(f"④ 항목 {it.item_id}: 경로에 진입점(화면·배치) 없음")
            if any(m.kind in VALUE_MENTIONS for m in it.mentions) and not any(n and n.label == "Column" for n in nodes):
                signals.append(f"⑤ 항목 {it.item_id}: 요건이 말한 값의 컬럼이 경로에 없음")
        if out.excluded_common:
            signals.append(f"⑥ 공통이라 확장 안 한 노드 {len(out.excluded_common)}개 (요건 대상인지 확인)")
        return signals

    def _react(self, plan: PlannerOutput, out: ExplorerOutput, signals: list[str]) -> None:
        context = self._react_context(plan, out) + "\n[점검 신호] (규칙 결과에서 계산. 이 항목만 확인)\n" + "\n".join(
            f"- {s}" for s in signals)
        trace, self.last_usage = run_react(
            self.llm, self.system_prompt, context, ExplorerDecision,
            lambda d: self._react_execute(d, plan, out),
            budget=lambda: self._budget - self._calls - self._reserve, max_steps=EXPLORER_MAX_REACT_STEPS)
        out.react_trace = trace

    def _react_context(self, plan: PlannerOutput, out: ExplorerOutput) -> str:
        lines = [f"[탐색 영역] {[d.value for d in plan.domains]} / 공통 제외 {plan.exclude.is_common}", "[탐색 명세]"]
        for it in plan.items:
            mentions = "; ".join(f"{m.kind.value}:{m.phrase}" + (f" (동의어 {', '.join(m.synonyms)})" if m.synonyms else "")
                                 + (" [신규 가능]" if m.maybe_new else "") for m in it.mentions)
            lines.append(f"- 항목 {it.item_id} {it.change_type.value} → 목표 {it.target_role.value}, {it.direction.value}, "
                         f"hop {it.hop_cap} | {it.text} | {mentions}")
        lines.append("[규칙 탐색 결과 - 시작 노드]")
        lines += [f"- [{s.item_id}] {s.node_id} ({s.reason})" for s in out.start_nodes] or ["- 없음"]
        lines.append("[규칙 탐색 결과 - 경로] (진입 → DB)")
        lines += [f"- [{p.item_id}] {p.stop_reason.value}: {' > '.join(p.nodes)}" for p in out.paths[:MAX_CONTEXT_PATHS]] or ["- 없음"]
        if len(out.paths) > MAX_CONTEXT_PATHS:
            lines.append(f"- ... 외 {len(out.paths) - MAX_CONTEXT_PATHS}개")
        lines.append("[항목별 결과]")
        lines += [f"- 항목 {r.item_id}: 목표 도달 {r.reached}, hop {r.hops_used}, 끊긴 노드 {r.frontier or '-'}, "
                  f"매칭 실패 {r.unmatched_mentions or '-'}" for r in out.item_results]
        if out.unresolved:
            lines += ["[미해결]"] + [f"- {u}" for u in out.unresolved]
        if out.excluded_common:
            lines.append(f"[공통이라 확장 안 함] {out.excluded_common}")
        return "\n".join(lines)

    def _react_execute(self, d: ExplorerDecision, plan: PlannerOutput, out: ExplorerOutput) -> str:
        if d.action == "search_graph_nodes":
            if not d.terms:
                raise InvalidAction("search_graph_nodes 에는 terms 가 필요합니다")
            hits = self._tool(search_graph_nodes, SearchGraphNodesInput(
                terms=d.terms, domains=plan.domains, labels=d.labels or None, top_k=8))
            return "\n".join(f"{h.node_id} | {h.label}({h.role.value}) | {h.name} | 점수 {h.score:g}" for h in hits) or "결과 없음"

        if d.action == "get_node_detail":
            if not d.node_ids:
                raise InvalidAction("get_node_detail 에는 node_ids[0] 이 필요합니다")
            node = self._tool(get_node_detail, GetNodeDetailInput(node_id=d.node_ids[0]))
            if node is None:
                return f"{d.node_ids[0]} 노드 없음"
            p = node.properties
            desc = p.get("desc") or p.get("comment") or p.get("program_name") or ""
            outs = [f"{e.type}→{e.to_id}" for e in self.store.out_edges(node.node_id)][:12]
            ins = [f"{e.from_id}→{e.type}" for e in self.store.in_edges(node.node_id)][:12]
            return (f"{node.node_id} | {node.label}({node.role.value if node.role else '-'}) | {node.name} | "
                    f"{node.source_file}:{node.source_line} | 공통 {p.get('is_common', False)} | {desc}\n"
                    f"나가는 엣지: {outs or '-'}\n들어오는 엣지: {ins or '-'}")

        # traverse_graph
        item = next((i for i in plan.items if i.item_id == d.item_id), None)
        if item is None:
            raise InvalidAction(f"item_id {d.item_id} 항목이 없습니다")
        ids = [n for n in d.node_ids or [] if self.store.node(n)]
        if not ids:
            raise InvalidAction("traverse_graph 에는 그래프에 있는 node_ids 가 필요합니다 (search_graph_nodes 결과의 ID)")
        before = len(out.paths)
        known = {(s.item_id, s.node_id) for s in out.start_nodes}
        for n in ids:
            if (item.item_id, n) not in known:
                out.start_nodes.append(StartNodeChoice(item_id=item.item_id, node_id=n, matched_keyword=n,
                                                       match_source=MatchSource.TEXT, reason=f"ReAct: {d.thought[:120]}"))
        columns = [n for n in ids if self.store.node(n).label == "Column"]
        self._traverse(out, item.item_id, ids, d.direction or "forward", d.target_role or item.target_role, item.hop_cap,
                       plan.exclude.is_common, columns)
        result = next(r for r in out.item_results if r.item_id == item.item_id)
        result.start_node_ids = list(dict.fromkeys(result.start_node_ids + ids))
        self._update_result(out, result)
        new = out.paths[before:]
        return "\n".join(f"{p.stop_reason.value}: {' > '.join(p.nodes)}" for p in new) or "새 경로 없음"


def _end(path: ExplorePath) -> str:
    """탐색이 멈춘 쪽 끝 노드 (정방향은 마지막, 역방향은 첫 노드)."""
    return path.nodes[-1] if path.direction is Direction.FORWARD else path.nodes[0]
