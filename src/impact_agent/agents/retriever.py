"""Retriever: Explorer 경로 노드의 소스·정의서 원문을 직접 읽어 근거(Evidence)로 Simulator 에 넘기고, 필요하면 재탐색을 요청한다.

- read_source 한 번으로 시작 노드 + 경로 노드를 읽는다 (인터페이스가 걸린 노드는 정의서도 함께)
- 재탐색 라운드는 이전 라운드 근거를 그대로 이어받고, 근거가 없는 새 노드만 읽는다 (근거 누적, 덮어쓰지 않음)
- 같은 노드·파일의 근거는 가장 넓은 범위 하나로 합친다
- 재탐색 요청 (reexplore_request, Supervisor 가 MAX_EXPLORE_LOOP 안에서 Explorer 를 다시 돌림)
    continue  hop 상한에 걸린 경로 → 끊긴 노드(frontier)에서 같은 목표로 이어서 탐색
    reverse   정방향 항목인데 시작 노드가 모두 목표 역할보다 DB 쪽이라 경로가 없음 → 진입점까지 역방향
- 원문을 못 찾은 노드는 nodes_without_evidence (근거 없음으로 보고서에 남김)
- ReAct 보완 (Yao et al. 2023, LLM 이 있을 때): 읽은 원문과 경로 밖 연결을 Observation 으로 주고,
  LLM 이 Thought → Action(read_source / request_reexplore / finish) 을 고른다.
  탐색 범위(Explorer 노드) 안은 직접 더 읽고, 범위 밖 연결은 next_target 재탐색 요청으로만 넘긴다.
  같은 노드를 다시 읽으면 이미 보여 준 줄 다음부터 이어서 보여 주고, 끝까지 본 노드는 tool 을 부르지 않고 오류로 돌려준다.
  경로 테이블의 컬럼(경로에 없는 것)을 입력에 함께 보여 줘, 원문이 쓰는 컬럼을 재탐색으로 그래프 경로에 되돌린다.
- 핵심 근거: ReAct 를 끝낼 때 LLM 이 판단에 꼭 필요한 원문 위치(노드 + 원문에서 그대로 옮긴 코드 한 줄 + 사실 한 문장)를 고르면,
  그 줄 앞뒤만 잘라 "[핵심]" 근거로 만들어 근거 목록 맨 앞에 둔다 (뒤 단계는 근거마다 앞부분만 보므로, 깊은 곳의 근거도 전달되게).
근거 요약은 노드 설명·코멘트를 쓴다.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLMClient, Usage, react_llm
from ..rules import MIN_CALLS_FOR_REEXPLORE, RETRIEVER_MAX_REACT_STEPS, ROLE_RANK
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
MAX_TABLE_COLUMNS_CHARS = 2500  # ReAct 입력의 경로 테이블 컬럼 목록 길이
OBSERVATION_CHARS = 2400  # read_source 결과 길이 (노드 여러 개면 나눠 씀)
SINGLE_NODE_OBSERVATION_CHARS = 6000  # 노드 1개만 읽을 때
MAX_KEY_EVIDENCE = 5
KEY_BEFORE, KEY_AFTER = 3, 8  # 핵심 근거로 잘라 낼 줄 (찾은 줄 앞뒤)
KEY_TAG = "[핵심]"
ATTACHED = re.compile(r"^// ---- (.+?) ----$")


class KeyEvidence(BaseModel):
    node_id: str = Field(description="탐색 범위 안 노드")
    quote: str = Field(description="Observation 에 나온 원문 코드 한 줄을 그대로 (20~120자)")
    note: str = Field(description="그 줄로 확인한 사실 한 문장")


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
    key_evidence: list[KeyEvidence] | None = Field(description="finish: 뒤 단계가 꼭 봐야 할 원문 위치 1~5개. 그 외 null")


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
        prev = state.retriever  # 재탐색 라운드면 이전 라운드 결과 (첫 라운드는 Supervisor 가 None 으로 비움)
        if prev is None:
            self._requested: list[ReexploreRequest] = []
        self._shown: dict[tuple[str, str], tuple[int, int]] = {}  # (노드, 파일) → (보여 준 줄 수, 시작 줄)
        node_ids = list(dict.fromkeys([s.node_id for s in exp.start_nodes] + [n for p in exp.paths for n in p.nodes]))
        skipped = node_ids[MAX_READ_NODES:]
        node_ids = node_ids[:MAX_READ_NODES]
        evidence = list(prev.evidence) if prev else []
        have = {e.node_id for e in evidence}
        to_read = [n for n in node_ids if n not in have]

        calls = 0
        if to_read and state.tools_left() > 0:  # Supervisor 가 Explorer 호출 수까지 누적한 값
            items = read_source(ReadSourceInput(node_ids=to_read), self.store, self.roots)
            evidence += [self._evidence(i) for i in items if i.found]
            calls = 1
        found = {e.node_id for e in evidence}
        out = RetrieverOutput(
            evidence=_merge(evidence),
            nodes_without_evidence=[n for n in node_ids if n not in found] + skipped,
            reexplore_request=[r for r in self._reexplore(state) if not self._already(r.node_ids)],
            tool_call_count=calls,
        )
        self._requested += out.reexplore_request
        self.last_usage = None
        if self.llm is not None and out.evidence and state.tools_left() - out.tool_call_count > 0:
            self._react(state, out)
        out.evidence = _merge(out.evidence)
        return out

    def _already(self, node_ids: list[str]) -> bool:
        return any(sorted(r.node_ids) == sorted(node_ids) for r in self._requested)

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

        def budget() -> int:  # 재탐색을 요청했으면 그 라운드 몫(MIN_CALLS_FOR_REEXPLORE)은 남겨 둔다
            return state.tools_left() - out.tool_call_count - (MIN_CALLS_FOR_REEXPLORE if added else 0)

        def observation_len(d: RetrieverDecision) -> int:  # _page 가 원문 길이를 맞추므로 머리줄·안내문 몫만 더함
            if d.action != "read_source":
                return OBSERVATION_CHARS
            body = SINGLE_NODE_OBSERVATION_CHARS if len(d.node_ids or []) == 1 else OBSERVATION_CHARS
            return body + 300 * max(len(d.node_ids or []), 1)

        trace, self.last_usage = run_react(
            self.llm, self.system_prompt, self._react_context(state, out), RetrieverDecision,
            lambda d: self._react_execute(d, state, out, added), budget,
            max_steps=RETRIEVER_MAX_REACT_STEPS, max_observation=observation_len)
        out.react_trace = trace
        out.tool_call_count = max(out.tool_call_count, base)
        keys = [k for k in (trace[-1].args.get("key_evidence") or []) if isinstance(k, dict)] if trace else []
        explored = {n.node_id for n in state.explorer.nodes}
        found = [ev for k in keys[:MAX_KEY_EVIDENCE] if k.get("node_id") in explored
                 and (ev := self._key_evidence(k, out.evidence)) is not None]
        out.evidence = found + out.evidence

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
            self._shown[(e.node_id, e.file_path)] = (min(CONTEXT_LINES_PER_EVIDENCE, len((e.content or "").splitlines())),
                                                     _start_line(e))
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
        lines.append("[경로 테이블의 컬럼] (경로에 없는 컬럼. ★ = 원문 SQL·프로그램·화면에 이름이 나옴. 요건 값을 이 컬럼으로 다루면 request_reexplore)")
        lines += self._table_columns(exp, out.evidence) or ["- 없음"]
        if self._requested:
            lines += ["[이미 요청한 재탐색]"] + [f"- [{r.item_id}] {r.mode.value} {r.node_ids}" for r in self._requested]
        return "\n".join(lines)

    def _table_columns(self, exp, evidence: list[Evidence]) -> list[str]:
        """경로 테이블의 컬럼 중 경로에 없는 것. 테이블 DDL 이 아닌 원문(SQL·프로그램·화면)에 이름이 나오면 ★ 를 붙여 앞에 둔다."""
        on_path = {n for p in exp.paths for n in p.nodes}
        text = "\n".join(e.content or "" for e in evidence if not (e.node_id or "").startswith(("TBL:", "COL:"))).upper()
        lines, used = [], 0
        for tid in sorted(n for n in on_path if n.startswith("TBL:")):
            cols = []
            for e in self.store.out_edges(tid):
                if e.type != "HAS_COLUMN" or e.to_id in on_path:
                    continue
                name = e.to_id.split(".", 1)[-1]
                col = self.store.node(e.to_id)
                comment = col.properties.get("comment") if col else None
                star = re.search(rf"\b{re.escape(name.upper())}\b", text) is not None
                cols.append((not star, f"{'★' if star else ''}{name}({comment})" if comment else f"{'★' if star else ''}{name}"))
            if not cols:
                continue
            line = f"- {tid}: {', '.join(c for _, c in sorted(cols, key=lambda c: c[0]))}"
            if used + len(line) > MAX_TABLE_COLUMNS_CHARS:
                lines.append("- … 외 테이블 생략 (입력 길이 한도)")
                break
            used += len(line)
            lines.append(line)
        return lines

    def _key_evidence(self, key: dict, evidence: list[Evidence]) -> Evidence | None:
        """quote 가 나오는 줄 앞뒤를 잘라 [핵심] 근거로. 원문에 없는 quote(지어낸 인용)는 버린다."""
        quote = " ".join(str(key.get("quote") or "").split())
        if len(quote) < 8:
            return None
        for e in evidence:
            if e.node_id != key["node_id"] or e.summary.startswith(KEY_TAG):
                continue
            # 근거 원문 = 본문 + "// ---- <파일> ----" 로 붙은 파일들. 줄 번호는 파일별로 센다
            part_file, part_start, lines = e.file_path, _start_line(e), (e.content or "").splitlines()
            prefix = e.file_path[: -len(node.source_file)] if (node := self.store.node(e.node_id)) and node.source_file \
                and e.file_path.endswith(node.source_file) else ""
            offset = 0
            for i, ln in enumerate(lines):
                if (m := ATTACHED.match(ln)):
                    part_file, part_start, offset = prefix + m.group(1), 1, i + 1
                    continue
                if quote in " ".join(ln.split()):
                    end = next((j - 1 for j in range(i + 1, len(lines)) if ATTACHED.match(lines[j])), len(lines) - 1)
                    lo, hi = max(i - KEY_BEFORE, offset), min(i + KEY_AFTER, end)
                    return Evidence(node_id=e.node_id, file_path=part_file,
                                    location=f"L{part_start + lo - offset}-{part_start + hi - offset}",
                                    summary=f"{KEY_TAG} {key.get('note') or ''}".strip(), symbol=e.symbol,
                                    source_kind=e.source_kind, domain=e.domain, content="\n".join(lines[lo:hi + 1]))
        return None

    def _fully_shown(self, node_id: str, out: RetrieverOutput) -> bool:
        evs = [e for e in out.evidence if e.node_id == node_id]
        return bool(evs) and all(
            (k := (e.node_id, e.file_path)) in self._shown and self._shown[k][0] >= len((e.content or "").splitlines())
            for e in evs)

    def _page(self, item: ReadSourceItem, chars: int) -> str:
        """이미 보여 준 줄 다음부터 chars 길이만큼. 범위가 앞으로 넓어졌으면(context_lines) 새로 붙은 앞부분부터 보여 준다."""
        lines = item.content.splitlines()
        shown, start = self._shown.get((item.node_id, item.file_path), (0, item.start_line or 1))
        new_start = item.start_line or start
        offset = 0 if new_start < start else min(max(shown - (new_start - start), 0), len(lines))
        body, end = [], offset
        for ln in lines[offset:]:
            if sum(len(b) + 1 for b in body) + len(ln) > chars and body:
                break
            body.append(ln)
            end += 1
        self._shown[(item.node_id, item.file_path)] = (end, item.start_line or 1)
        head = f"### {item.node_id} {item.file_path} L{item.start_line}-{item.end_line} (원문 {offset + 1}~{end}줄 / 전체 {len(lines)}줄"
        head += ", 끝까지 읽음)" if end >= len(lines) else f", 다시 읽으면 {end + 1}줄부터)"
        return head + "\n" + "\n".join(body)

    def _react_execute(self, d: RetrieverDecision, state: AgentState, out: RetrieverOutput, added: list) -> str:
        if d.action == "read_source":
            explored = {n.node_id for n in state.explorer.nodes}
            ids = [n for n in d.node_ids or [] if n in explored]
            outside = [n for n in d.node_ids or [] if n not in explored]
            if not ids:
                raise InvalidAction(f"탐색 범위 안 노드만 읽을 수 있습니다 (범위 밖 {outside} 는 request_reexplore)")
            context = max(d.context_lines or 0, 0)
            done = [n for n in ids if not context and self._fully_shown(n, out)]
            ids = [n for n in ids if n not in done]
            if not ids:
                raise InvalidAction(f"{done} 는 이미 끝까지 읽었습니다. 범위 밖 내용이 필요하면 context_lines 를 주거나 request_reexplore")
            items = read_source(ReadSourceInput(node_ids=ids, context_lines=context), self.store, self.roots)
            out.tool_call_count += 1
            for i in items:
                if i.found:
                    out.evidence.append(self._evidence(i))
                    out.nodes_without_evidence = [n for n in out.nodes_without_evidence if n != i.node_id]
            found = [i for i in items if i.found]
            per = (SINGLE_NODE_OBSERVATION_CHARS if len(ids) == 1 else OBSERVATION_CHARS) // max(len(found), 1)
            note = (f" (범위 밖 {outside} 는 읽지 않음)" if outside else "") + (f" ({done} 는 이미 끝까지 읽음)" if done else "")
            return "\n\n".join(self._page(i, per) for i in found) + note if found else "원문 없음" + note

        # request_reexplore
        ids = [n for n in d.node_ids or [] if self.store.node(n)]
        if not ids or not d.reason:
            raise InvalidAction("request_reexplore 에는 그래프에 있는 node_ids 와 reason 이 필요합니다")
        if self._already(ids):
            raise InvalidAction(f"{ids} 는 이미 재탐색을 요청했습니다")
        if state.tools_left() - out.tool_call_count < MIN_CALLS_FOR_REEXPLORE:
            raise InvalidAction(f"남은 tool 호출이 {MIN_CALLS_FOR_REEXPLORE}회 미만이라 재탐색을 진행할 수 없습니다. finish 로 미확인 내용을 남기세요")
        req = ReexploreRequest(item_id=d.item_id, mode=ReexploreMode.NEXT_TARGET, node_ids=ids, target_role=d.target_role,
                               direction=Direction(d.direction) if d.direction else None, reason=d.reason)
        out.reexplore_request.append(req)
        self._requested.append(req)
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


def _start_line(e: Evidence) -> int:
    return int(e.location[1:].split("-")[0]) if e.location and e.location.startswith("L") else 1


def _merge(evidence: list[Evidence]) -> list[Evidence]:
    """같은 노드·파일·종류의 근거는 가장 넓은 범위(원문이 긴 것) 하나만 남긴다 ([핵심] 근거는 위치별로 따로). 처음 나온 순서 유지."""
    best: dict[tuple, Evidence] = {}
    for e in evidence:
        k = (e.node_id, e.file_path, e.source_kind, e.location if e.summary.startswith(KEY_TAG) else None)
        if k not in best or len(e.content or "") > len(best[k].content or ""):
            best[k] = e
    order = list(dict.fromkeys((e.node_id, e.file_path, e.source_kind, e.location if e.summary.startswith(KEY_TAG) else None)
                               for e in evidence))
    return [best[k] for k in order]
