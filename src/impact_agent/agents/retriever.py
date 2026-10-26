"""Retriever: Explorer 경로 노드의 소스·정의서 원문을 직접 읽어 근거(Evidence)로 Simulator 에 넘기고, 필요하면 재탐색을 요청한다.

- read_source 한 번으로 시작 노드 + 경로 노드를 읽는다 (인터페이스가 걸린 노드는 정의서도 함께)
- 재탐색 요청 (reexplore_request, Supervisor 가 MAX_EXPLORE_LOOP 안에서 Explorer 를 다시 돌림)
    continue  hop 상한에 걸린 경로 → 끊긴 노드(frontier)에서 같은 목표로 이어서 탐색
    reverse   정방향 항목인데 시작 노드가 모두 목표 역할보다 DB 쪽이라 경로가 없음 → 진입점까지 역방향
- 원문을 못 찾은 노드는 nodes_without_evidence (근거 없음으로 보고서에 남김)
- ReAct 보완 (Yao et al. 2023, LLM 이 있을 때): 읽은 원문과 경로 밖 연결을 Observation 으로 주고,
  LLM 이 Thought → Action(read_source / request_reexplore / finish) 을 고른다.
  탐색 범위(Explorer 노드) 안은 직접 더 읽고, 범위 밖 연결은 next_target 재탐색 요청으로만 넘긴다.
근거 요약은 노드 설명·코멘트를 쓴다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from ..llm import LLMClient, Usage, react_llm
from ..rules import ROLE_RANK
from ..schemas import (
    Direction,
    Evidence,
    GraphNode,
    NodeRole,
    ReadSourceInput,
    ReadSourceItem,
    ReexploreMode,
    ReexploreRequest,
    RetrieverOutput,
)
from ..state import AgentState
from ..tools import GraphStore, SourceRoots, get_graph_store, read_source
from .base import BaseAgent
from .react import InvalidAction, run_react

MAX_READ_NODES = 80  # 한 번에 읽을 노드 수 상한 (토큰 보호)
CONTEXT_LINES_PER_EVIDENCE = 10  # ReAct 입력에 보여 줄 근거별 원문 줄 수 (더 필요하면 read_source)
MAX_CONTEXT_CHARS = 10000  # ReAct 입력의 근거 원문 총 길이 (단계마다 다시 보내므로 토큰 비용에 직결)
MAX_BOUNDARY = 30  # ReAct 입력에 보여 줄 경로 밖 연결 수


class RetrieverDecision(BaseModel):
    """ReAct 한 단계 (Structured Output: 기본값 없이 모든 필드 명시)."""

    thought: str = Field(description="원문에서 무엇을 확인했고 무엇이 부족한지, 다음 행동을 왜 고르는지")
    action: Literal["read_source", "request_reexplore", "finish"]
    item_id: int | None = Field(description="request_reexplore: 대상 탐색 명세 항목")
    node_ids: list[str] | None = Field(description="read_source: 더 읽을 노드 / request_reexplore: 여기서부터 다시 탐색")
    context_lines: int | None = Field(description="read_source: 노드 범위 앞뒤로 더 읽을 줄 수. 없으면 null")
    target_role: NodeRole | None = Field(description="request_reexplore: 다음 목표 역할")
    direction: Literal["forward", "reverse"] | None = Field(description="request_reexplore: 탐색 방향")
    reason: str | None = Field(description="request_reexplore: 원문에서 본 연결 근거 (파일·위치)")


class RetrieverAgent(BaseAgent[RetrieverOutput]):
    name = "retriever"
    output_model = RetrieverOutput

    def __init__(self, store: GraphStore | None = None, roots: SourceRoots | None = None,
                 llm: LLMClient | None = None) -> None:
        self._store = store
        self.roots = roots
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

    def run(self, state: AgentState) -> RetrieverOutput:
        exp = state.explorer
        if exp is None:
            return RetrieverOutput(evidence=[])
        node_ids = list(dict.fromkeys([s.node_id for s in exp.start_nodes] + [n for p in exp.paths for n in p.nodes]))
        skipped = node_ids[MAX_READ_NODES:]
        node_ids = node_ids[:MAX_READ_NODES]

        items: list[ReadSourceItem] = []
        calls = 0
        if node_ids and state.tool_call_count < config.MAX_TOOL_CALLS:  # Supervisor 가 Explorer 호출 수까지 누적한 값
            items = read_source(ReadSourceInput(node_ids=node_ids), self.store, self.roots)
            calls = 1

        evidence = [self._evidence(i) for i in items if i.found]
        found = {i.node_id for i in items if i.found}
        out = RetrieverOutput(
            evidence=evidence,
            nodes_without_evidence=[n for n in node_ids if n not in found] + skipped,
            reexplore_request=self._reexplore(state),
            tool_call_count=calls,
        )
        self.last_usage = None
        if self.llm is not None and evidence and state.tool_call_count + out.tool_call_count < config.MAX_TOOL_CALLS:
            self._react(state, out)
        return out

    def _evidence(self, item: ReadSourceItem) -> Evidence:
        node = self.store.node(item.node_id)
        return Evidence(
            node_id=item.node_id,
            file_path=item.file_path,
            symbol=node.name if node else None,
            location=f"L{item.start_line}-{item.end_line}" if item.start_line else None,
            summary=_summary(node, item),
            source_kind=item.source_kind,
            domain=node.domain if node else None,
            content=item.content,
        )

    def _reexplore(self, state: AgentState) -> list[ReexploreRequest]:
        exp, plan = state.explorer, state.planner
        items = {i.item_id: i for i in plan.items} if plan else {}
        requests = []
        for r in exp.item_results:
            if r.frontier:
                requests.append(ReexploreRequest(
                    item_id=r.item_id, mode=ReexploreMode.CONTINUE, node_ids=r.frontier,
                    reason=f"hop 상한에 걸려 목표 역할({r.target_role.value})까지 확인하지 못함"))
                continue
            item = items.get(r.item_id)
            has_paths = any(p.item_id == r.item_id for p in exp.paths)
            reversed_once = any(p.item_id == r.item_id and p.direction is Direction.REVERSE for p in exp.paths)
            if item and item.direction is Direction.FORWARD and not has_paths and r.start_node_ids and not reversed_once:
                target = ROLE_RANK[item.target_role]
                beyond = [n for n in r.start_node_ids if (node := self.store.node(n)) and ROLE_RANK[node.role] > target]
                if beyond:
                    requests.append(ReexploreRequest(
                        item_id=r.item_id, mode=ReexploreMode.REVERSE, node_ids=beyond, target_role=NodeRole.ENTRY,
                        reason="시작 노드가 모두 목표 역할보다 DB 쪽이라 정방향 경로가 없음 → 진입점까지 역방향 확인"))
        return requests


    # --- ReAct 보완 ------------------------------------------------------------------

    def _react(self, state: AgentState, out: RetrieverOutput) -> None:
        base = out.tool_call_count
        added = []  # 이번 라운드 ReAct 가 낸 재탐색 요청 (tool 호출은 아니지만 단계 수를 같은 한도로 묶음)

        def budget() -> int:
            return config.MAX_TOOL_CALLS - state.tool_call_count - out.tool_call_count - len(added)

        trace, self.last_usage = run_react(
            self.llm, self.system_prompt, self._react_context(state, out), RetrieverDecision,
            lambda d: self._react_execute(d, state, out, added), budget)
        out.react_trace = trace
        out.tool_call_count = max(out.tool_call_count, base)

    def _react_context(self, state: AgentState, out: RetrieverOutput) -> str:
        exp, plan = state.explorer, state.planner
        lines = ["[탐색 명세]"]
        lines += [f"- 항목 {it.item_id} {it.change_type.value} → 목표 {it.target_role.value} | {it.text}" for it in plan.items]
        lines.append("[Explorer 경로] (진입 → DB)")
        lines += [f"- [{p.item_id}] {p.stop_reason.value}: {' > '.join(p.nodes)}" for p in exp.paths[:40]] or ["- 없음"]
        if exp.unresolved:
            lines += ["[Explorer 미해결]"] + [f"- {u}" for u in exp.unresolved]
        lines.append("[근거 원문] (노드별 앞부분)")
        used = 0
        for e in out.evidence:
            body = "\n".join((e.content or "").splitlines()[:CONTEXT_LINES_PER_EVIDENCE])
            if used + len(body) > MAX_CONTEXT_CHARS:
                lines.append(f"### {e.node_id} | {e.file_path} {e.location or ''} | {e.summary} (원문 생략: 입력 길이 한도)")
                continue
            used += len(body)
            lines.append(f"### {e.node_id} | {e.file_path} {e.location or ''} | {e.summary}\n{body}")
        if out.nodes_without_evidence:
            lines.append(f"[원문 없음] {out.nodes_without_evidence}")
        explored = {n.node_id for n in exp.nodes}
        boundary = []
        for nid in dict.fromkeys(n for p in exp.paths for n in p.nodes):
            for e in self.store.out_edges(nid) + self.store.in_edges(nid):
                other = e.to_id if e.from_id == nid else e.from_id
                if other not in explored and e.type != "HAS_COLUMN":
                    boundary.append(f"- {e.from_id} -{e.type}-> {e.to_id}")
        lines.append("[경로 밖 연결] (탐색 범위 밖. 필요하면 request_reexplore)")
        lines += list(dict.fromkeys(boundary))[:MAX_BOUNDARY] or ["- 없음"]
        prev = state.retriever.reexplore_request if state.retriever else []
        requested = prev + out.reexplore_request
        if requested:
            lines += ["[이미 요청한 재탐색]"] + [f"- [{r.item_id}] {r.mode.value} {r.node_ids}" for r in requested]
        return "\n".join(lines)

    def _react_execute(self, d: RetrieverDecision, state: AgentState, out: RetrieverOutput, added: list) -> str:
        if d.action == "read_source":
            explored = {n.node_id for n in state.explorer.nodes}
            ids = [n for n in d.node_ids or [] if n in explored]
            outside = [n for n in d.node_ids or [] if n not in explored]
            if not ids:
                raise InvalidAction(f"탐색 범위 안 노드만 읽을 수 있습니다 (범위 밖 {outside} 는 request_reexplore)")
            items = read_source(ReadSourceInput(node_ids=ids, context_lines=max(d.context_lines or 0, 0)), self.store, self.roots)
            out.tool_call_count += 1
            known = {(e.node_id, e.file_path, e.location) for e in out.evidence}
            for i in items:
                ev = self._evidence(i) if i.found else None
                if ev and (ev.node_id, ev.file_path, ev.location) not in known:
                    out.evidence.append(ev)
                    out.nodes_without_evidence = [n for n in out.nodes_without_evidence if n != i.node_id]
            note = f" (범위 밖 {outside} 는 읽지 않음)" if outside else ""
            return "\n\n".join(f"### {i.node_id} {i.file_path} L{i.start_line}-{i.end_line}\n{i.content}"
                                for i in items if i.found)[:2400] + note or "원문 없음"

        # request_reexplore
        ids = [n for n in d.node_ids or [] if self.store.node(n)]
        if not ids or not d.reason:
            raise InvalidAction("request_reexplore 에는 그래프에 있는 node_ids 와 reason 이 필요합니다")
        prev = state.retriever.reexplore_request if state.retriever else []
        if any(sorted(r.node_ids) == sorted(ids) for r in prev + out.reexplore_request):
            raise InvalidAction(f"{ids} 는 이미 재탐색을 요청했습니다")
        req = ReexploreRequest(item_id=d.item_id, mode=ReexploreMode.NEXT_TARGET, node_ids=ids, target_role=d.target_role,
                               direction=Direction(d.direction) if d.direction else None, reason=d.reason)
        out.reexplore_request.append(req)
        added.append(req)
        return f"재탐색 요청 등록: {ids} → {d.target_role.value if d.target_role else '항목 목표'} ({d.direction or 'forward'})"


def _summary(node: GraphNode | None, item: ReadSourceItem) -> str:
    if item.source_kind == "interface_spec":
        first = next((ln.strip("# ").strip() for ln in item.content.splitlines() if ln.strip()), "")
        return f"인터페이스 정의서: {first}"
    if node is None:
        return item.file_path
    p = node.properties
    desc = p.get("desc") or p.get("comment") or p.get("program_name") or ""
    head = f"{node.label}({node.role.value if node.role else '-'}) {node.name}"
    return f"{head} - {desc}" if desc else head
