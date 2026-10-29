"""Planner → Explorer → Retriever 연계 테스트 (작은 가짜 그래프·소스, LLM·Neo4j 없음)."""

import csv
import json

import pytest

from impact_agent.agents import ExplorerAgent, RetrieverAgent, Supervisor
from impact_agent.agents.planner import DraftItem, DraftMention, PlannerAgent, PlannerDraft
from impact_agent.llm import Usage
from impact_agent.schemas import (
    ChangeType,
    Complexity,
    ConfirmedNode,
    Direction,
    Domain,
    ExploreItem,
    Mention,
    MentionKind,
    NodeRole,
    PlannerOutput,
    RequestType,
    SearchGraphNodesInput,
    SmPart,
    StopReason,
    StructuredRequest,
    TraverseGraphInput,
    UserInput,
)
from impact_agent.tools import CsvGraphStore, SourceRoots, search_graph_nodes, traverse_graph

# 주문 화면 → 저장 Controller → Service(자기 호출) → SQL → 주문 테이블 → 광고여부 컬럼, 주문 배치 → 조회 SQL → 주문 테이블
NODES = [
    ("SCR:ORD01", "Screen", "Entry", "주문 등록", "web/ORD01.jsp", 1, {"desc": "주문 등록 화면"}),
    ("CTL:OrdController.save", "Controller", "EntryHandler", "OrdController.save", "java/OrdController.java", 3, {}),
    ("SVC:OrdService.save", "Service", "Logic", "OrdService.save", "java/OrdService.java", 2, {}),
    ("SVC:OrdService.saveItem", "Service", "Logic", "OrdService.saveItem", "java/OrdService.java", 6, {}),
    ("SQL:OrdMapper.insert", "SQL", "DataAccess", "OrdMapper.insert", "mapper/OrdMapper.xml", 1, {}),
    ("TBL:TOR_ORD", "Table", "Table", "주문", "ddl/TOR_ORD.sql", 1, {}),
    ("COL:TOR_ORD.ADVT_YN", "Column", "Column", "ADVT_YN", "ddl/TOR_ORD.sql", 3, {"table": "TOR_ORD", "comment": "광고여부(Y:광고,N:비광고)"}),
    ("COL:TOR_ORD.ORD_ID", "Column", "Column", "ORD_ID", "ddl/TOR_ORD.sql", 2, {"table": "TOR_ORD", "comment": "주문ID"}),
    ("BAT:TORD100", "Batch", "Entry", "주문 전송 배치", "job/TORD100.xml", 1, {"interfaces": ["IF-ORD-001 주문 전송"]}),
    ("SQL:tord100.C_ORD", "SQL", "DataAccess", "tord100.C_ORD", "job/TORD100.xml", 2, {}),
    ("CTL:CmmCdController.get", "Controller", "EntryHandler", "CmmCdController.get", "java/Cmm.java", 1, {"is_common": True}),
]
EDGES = [
    ("SCR:ORD01", "CTL:OrdController.save", "CALLS", {"event": "save"}),
    ("SCR:ORD01", "CTL:CmmCdController.get", "CALLS", {}),
    ("CTL:OrdController.save", "SVC:OrdService.save", "INVOKES", {}),
    ("SVC:OrdService.save", "SVC:OrdService.saveItem", "INVOKES", {}),
    ("SVC:OrdService.saveItem", "SQL:OrdMapper.insert", "EXECUTES", {}),
    ("SQL:OrdMapper.insert", "TBL:TOR_ORD", "WRITES", {"columns": "ORD_ID,ADVT_YN"}),
    ("TBL:TOR_ORD", "COL:TOR_ORD.ADVT_YN", "HAS_COLUMN", {}),
    ("TBL:TOR_ORD", "COL:TOR_ORD.ORD_ID", "HAS_COLUMN", {}),
    ("BAT:TORD100", "SQL:tord100.C_ORD", "EXECUTES", {}),
    ("SQL:tord100.C_ORD", "TBL:TOR_ORD", "READS", {"columns": "ORD_ID"}),
]
SOURCES = {
    "web/ORD01.jsp": "<%-- 화면ID: ORD01 --%>\n<input id=\"chkAdvt\">",
    "java/OrdController.java": "class OrdController {\n\n    @PostMapping(\"/save.do\")\n    public void save() {}\n}",
    "java/OrdService.java": "class OrdService {\n    public void save() {\n        saveItem();\n    }\n\n    private void saveItem() {}\n}",
    "mapper/OrdMapper.xml": "<insert id=\"insert\">INSERT INTO TOR_ORD (ORD_ID, ADVT_YN)</insert>",
    "ddl/TOR_ORD.sql": "CREATE TABLE TOR_ORD (\n    ORD_ID VARCHAR2(10),\n    ADVT_YN CHAR(1)\n);",
    "job/TORD100.xml": "<job id=\"TORD100\">\n<sql>SELECT ORD_ID FROM TOR_ORD</sql>\n</job>",
}
CODE_LINES = {"CTL:OrdController.save": 2, "SVC:OrdService.save": 3, "SVC:OrdService.saveItem": 1}


@pytest.fixture
def env(tmp_path):
    gdir = tmp_path / "graph"
    gdir.mkdir()
    with open(gdir / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "label", "role", "name", "domain", "source_file", "source_line", "properties"])
        for nid, label, role, name, src, line, props in NODES:
            props = {"is_common": False, **props, "code": "\n".join(["x"] * CODE_LINES.get(nid, 1)),
                     "search_text": " ".join([nid.split(":", 1)[1], name, props.get("desc", ""), props.get("comment", "")])}
            w.writerow([nid, label, role, name, "tos", src, line, json.dumps(props, ensure_ascii=False)])
    with open(gdir / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "type", "properties"])
        for a, b, t, p in EDGES:
            w.writerow([a, b, t, json.dumps(p)])
    src_root = tmp_path / "data" / "source" / "tos"
    for rel, text in SOURCES.items():
        (src_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (src_root / rel).write_text(text, encoding="utf-8")
    doc = tmp_path / "data" / "docs" / "tos" / "interface" / "IF-ORD-001.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# IF-ORD-001 주문 전송\n레코드 100 byte", encoding="utf-8")
    roots = SourceRoots(data=tmp_path / "data", source={Domain.TOS: src_root, Domain.SWING: tmp_path / "none"},
                        docs={Domain.TOS: tmp_path / "data" / "docs" / "tos", Domain.SWING: tmp_path / "none"})
    return CsvGraphStore(gdir), roots


def traverse(store, start, direction, target, cap=5, **kw):
    return traverse_graph(TraverseGraphInput(start_ids=[start], direction=direction, target_role=target, hop_cap=cap, **kw), store)


# --- traverse_graph -------------------------------------------------------------------------


def test_forward_stops_at_target_and_skips_common(env):
    store, _ = env
    out = traverse(store, "SCR:ORD01", "forward", NodeRole.TABLE)
    reached = [p.nodes for p in out.paths if p.stop_reason is StopReason.TARGET_REACHED]
    assert reached == [["SCR:ORD01", "CTL:OrdController.save", "SVC:OrdService.save", "SVC:OrdService.saveItem",
                        "SQL:OrdMapper.insert", "TBL:TOR_ORD"]]
    assert out.excluded_common == ["CTL:CmmCdController.get"]
    assert out.hops_used == 4  # Service 자기 호출은 hop 으로 세지 않음


def test_nearest_role_when_target_skipped(env):
    store, _ = env
    out = traverse(store, "BAT:TORD100", "forward", NodeRole.LOGIC)
    assert [(p.nodes, p.stop_reason) for p in out.paths] == [(["BAT:TORD100", "SQL:tord100.C_ORD"], StopReason.NEAREST_ROLE)]


def test_reverse_from_column_follows_only_edges_using_column(env):
    store, _ = env
    out = traverse(store, "COL:TOR_ORD.ADVT_YN", "reverse", NodeRole.ENTRY)
    starts = {p.nodes[0] for p in out.paths if p.stop_reason is StopReason.TARGET_REACHED}
    assert starts == {"SCR:ORD01"}  # 배치 SQL 은 ADVT_YN 을 읽지 않으므로 제외
    out = traverse(store, "COL:TOR_ORD.ORD_ID", "reverse", NodeRole.ENTRY)
    assert {p.nodes[0] for p in out.paths} == {"SCR:ORD01", "BAT:TORD100"}


def test_column_expansion_uses_focus(env):
    store, _ = env
    out = traverse(store, "SCR:ORD01", "forward", NodeRole.COLUMN, focus_ids=["COL:TOR_ORD.ADVT_YN"])
    assert {p.nodes[-1] for p in out.paths if p.stop_reason is StopReason.TARGET_REACHED} == {"COL:TOR_ORD.ADVT_YN"}


def test_hop_cap_sets_frontier(env):
    store, _ = env
    out = traverse(store, "SCR:ORD01", "forward", NodeRole.TABLE, cap=2)
    assert out.frontier == ["SVC:OrdService.save"]


def test_search_prefers_phrase_and_longer_synonyms(env):
    store, _ = env
    hits = search_graph_nodes(SearchGraphNodesInput(terms=["비마케팅 여부", "광고여부", "주문"], labels=["Column"]), store)
    assert hits[0].node_id == "COL:TOR_ORD.ADVT_YN" and hits[0].matched_term == "광고여부"


# --- Planner ---------------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, draft):
        self.draft = draft
        self.calls = []

    def parse(self, system, user, schema):
        self.calls.append((system, user, schema))
        return self.draft, Usage(10, 5)


def test_planner_applies_rules():
    draft = PlannerDraft(
        target="t", action="a", condition=None, policy=None, scope=None, request_type=RequestType.CHANGE,
        business_terms=[], open_items=[], complexity=Complexity.COMPLEX, complexity_reason="r", exclude_common=True,
        items=[DraftItem(text="저장", change_type=ChangeType.DATA_ADD, out_of_graph=[], mentions=[
            DraftMention(kind=MentionKind.COLUMN, phrase="비마케팅 여부", maybe_new=True, synonyms=[], meaning=None, area=None)]),
            DraftItem(text="전송", change_type=ChangeType.INTERFACE_FIELD_ADD, out_of_graph=["채널 전문"], mentions=[])],
    )
    llm = FakeLLM(draft)
    from impact_agent.state import AgentState

    out = PlannerAgent(llm=llm).run(AgentState(user_input=UserInput(request_text="요청", sm_part=SmPart.MARKETING)))
    assert out.domains == [Domain.TOS]
    first, second = out.items
    assert (first.target_role, first.direction, first.hop_cap) == (NodeRole.TABLE, Direction.FORWARD_THEN_REVERSE, 10)
    assert (second.target_role, second.direction, second.hop_cap) == (NodeRole.ENTRY, Direction.REVERSE, 5)
    assert [s.agent.value for s in out.plan] == ["explorer", "retriever", "simulator", "verifier"]
    assert "[요청]" in llm.calls[0][1]


# --- Explorer ⇄ Retriever (Supervisor) ---------------------------------------------------------


def plan(items, is_common=True):
    return PlannerOutput(structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
                         complexity=Complexity.SIMPLE, complexity_reason="r", domains=[Domain.TOS], items=items,
                         exclude={"is_common": is_common},
                         plan=[{"agent": "explorer", "purpose": "p"}, {"agent": "retriever", "purpose": "p"}])


def item(change_type, mentions, direction, target, cap=5, out_of_graph=()):
    return ExploreItem(item_id=1, text="x", change_type=change_type, mentions=mentions, target_role=target,
                       direction=direction, hop_cap=cap, out_of_graph=list(out_of_graph))


class Fixed:
    def __init__(self, output):
        self.output = output

    def run(self, state):
        return self.output.model_copy(deep=True)


def supervisor(env, planner_output):
    store, roots = env
    sup = Supervisor()
    sup.planner = Fixed(planner_output)
    sup.explorer = ExplorerAgent(store=store)
    sup.retriever = RetrieverAgent(store=store, roots=roots)
    return sup


def analyze(sup, confirmed=()):
    return sup.analyze(UserInput(request_text="r", sm_part=SmPart.MARKETING, confirmed_nodes=list(confirmed)), until="retriever")


def test_hop_cap_continue_joins_path(env):
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(supervisor(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE, cap=2)])))
    assert state.explore_loop_count == 2  # hop 상한 → Retriever continue 요청 → Explorer 재실행
    paths = [p.nodes for p in state.explorer.paths if p.stop_reason is StopReason.TARGET_REACHED]
    assert paths == [["SCR:ORD01", "CTL:OrdController.save", "SVC:OrdService.save", "SVC:OrdService.saveItem",
                      "SQL:OrdMapper.insert", "TBL:TOR_ORD"]]
    assert not any(p.stop_reason is StopReason.HOP_CAP for p in state.explorer.paths)


def test_forward_without_entry_side_start_requests_reverse(env):
    mentions = [Mention(kind=MentionKind.COLUMN, phrase="광고여부")]
    sup = supervisor(env, plan([item(ChangeType.SCREEN_CHANGE, mentions, Direction.FORWARD, NodeRole.ENTRY_HANDLER)]))
    state = analyze(sup)
    assert state.explore_loop_count == 2
    assert any(p.direction is Direction.REVERSE and p.nodes[0] == "SCR:ORD01" for p in state.explorer.paths)


def test_retriever_reads_source_and_interface_doc(env):
    mentions = [Mention(kind=MentionKind.BATCH, phrase="주문 전송 배치")]
    state = analyze(supervisor(env, plan([item(ChangeType.BATCH_CHANGE, mentions, Direction.FORWARD, NodeRole.DATA_ACCESS)])))
    kinds = {(e.node_id, e.source_kind) for e in state.retriever.evidence}
    assert ("BAT:TORD100", "interface_spec") in kinds and ("SQL:tord100.C_ORD", "source") in kinds
    sql = next(e for e in state.retriever.evidence if e.node_id == "SQL:tord100.C_ORD")
    assert "SELECT ORD_ID FROM TOR_ORD" in sql.content and sql.location == "L2-2"
    assert state.retriever.reexplore_request == []


def test_confirmed_node_and_unresolved(env):
    mentions = [Mention(kind=MentionKind.COLUMN, phrase="비마케팅 여부", maybe_new=True),
                Mention(kind=MentionKind.TERM, phrase="없는 용어", maybe_new=True)]
    sup = supervisor(env, plan([item(ChangeType.INTERFACE_FIELD_ADD, mentions, Direction.REVERSE, NodeRole.ENTRY,
                                     out_of_graph=["채널 전문"])]))
    state = analyze(sup, [ConfirmedNode(phrase="비마케팅 여부", node_id="COL:TOR_ORD.ADVT_YN")])
    choice = next(s for s in state.explorer.start_nodes if s.node_id == "COL:TOR_ORD.ADVT_YN")
    assert choice.match_source.value == "confirmed"
    assert "[항목 1] 그래프 밖: 채널 전문" in state.explorer.unresolved
    assert "[항목 1] 신규 후보: 없는 용어 (Term)" in state.explorer.unresolved
    assert {p.nodes[0] for p in state.explorer.paths} == {"SCR:ORD01"}


def test_tool_budget_is_respected(env, monkeypatch):
    from impact_agent import config

    monkeypatch.setattr(config, "MAX_TOOL_CALLS", 2)
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(supervisor(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)])))
    assert state.tool_call_count == 2  # 검색 1회 후 탐색은 생략하고, 마지막 1회는 Retriever 원문 읽기 몫
    assert any("tool 호출 상한" in u for u in state.explorer.unresolved)
    assert [e.node_id for e in state.retriever.evidence] == ["SCR:ORD01"] and state.explore_loop_count == 1


def test_same_search_runs_once(env):
    # 두 항목이 같은 언급 표현을 가지면 검색 tool 은 한 번만 부른다
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    items = [item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE),
             item(ChangeType.SCREEN_CHANGE, mentions, Direction.FORWARD, NodeRole.ENTRY_HANDLER).model_copy(update={"item_id": 2})]
    state = analyze(supervisor(env, plan(items)))
    assert state.explorer.tool_call_count == 3  # 검색 1 + 항목별 탐색 2
    assert {s.item_id for s in state.explorer.start_nodes} == {1, 2}
