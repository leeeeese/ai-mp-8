"""설계 기술 반영 테스트: ReAct(Explorer·Retriever), Reflexion(재계획 메시지), LangGraph 후속 입력 라우팅. 가짜 LLM 사용."""

from impact_agent.agents import ExplorerAgent, RetrieverAgent, Supervisor
from impact_agent.agents.explorer import ExplorerDecision
from impact_agent.agents.planner import user_message
from impact_agent.agents.retriever import RetrieverDecision
from impact_agent.llm import Usage
from impact_agent.schemas import (
    ChangeType,
    Complexity,
    ComposerOutput,
    Direction,
    FollowupInput,
    FollowupType,
    Mention,
    MentionKind,
    NodeRole,
    ReplanFeedback,
    SmPart,
    UserInput,
)
from impact_agent.state import AgentState
from test_explore import Fixed, analyze, item, plan

NULLS = dict(item_id=None, terms=None, labels=None, node_ids=None, direction=None, target_role=None)
R_NULLS = dict(item_id=None, node_ids=None, context_lines=None, target_role=None, direction=None, reason=None,
               key_evidence=None)


class ScriptLLM:
    """정해 둔 결정을 순서대로 돌려주고, 받은 user 메시지를 기록한다."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.users = []

    def parse(self, system, user, schema):
        self.users.append(user)
        d = self.decisions.pop(0)
        assert isinstance(d, schema)
        return d, Usage(10, 5)


def sup_with(env, planner_output, explorer_llm=None, retriever_llm=None):
    store, roots = env
    sup = Supervisor()
    sup.planner = Fixed(planner_output)
    sup.explorer = ExplorerAgent(store=store, llm=explorer_llm)
    sup.retriever = RetrieverAgent(store=store, roots=roots, llm=retriever_llm)
    return sup


def test_explorer_react_recovers_unmatched_mention(env):
    # 규칙 단계: "광고 구분" 은 매칭 실패 → ReAct 가 다른 표현으로 검색하고 그 컬럼에서 역방향 탐색
    mentions = [Mention(kind=MentionKind.COLUMN, phrase="없는표현")]
    llm = ScriptLLM([
        ExplorerDecision(thought="매칭 실패라 다른 표현으로 찾는다", action="search_graph_nodes",
                         **{**NULLS, "terms": ["광고여부"], "labels": ["Column"]}),
        ExplorerDecision(thought="찾은 컬럼에서 진입점까지", action="traverse_graph",
                         **{**NULLS, "item_id": 1, "node_ids": ["COL:TOR_ORD.ADVT_YN"], "direction": "reverse",
                            "target_role": NodeRole.ENTRY}),
        ExplorerDecision(thought="충분", action="finish", **NULLS),
    ])
    state = analyze(sup_with(env, plan([item(ChangeType.COLUMN_SPEC_CHANGE, mentions, Direction.REVERSE, NodeRole.ENTRY)]),
                             explorer_llm=llm))
    trace = state.explorer.react_trace
    assert [s.action for s in trace] == ["search_graph_nodes", "traverse_graph", "finish"]
    assert "COL:TOR_ORD.ADVT_YN" in trace[0].observation
    assert any(p.nodes[0] == "SCR:ORD01" and p.nodes[-1] == "COL:TOR_ORD.ADVT_YN" for p in state.explorer.paths)
    assert "매칭 실패" in llm.users[0] and "Observation 1" in llm.users[1]  # 조회 결과를 다음 입력에 붙임
    assert state.explorer.tool_call_count == 3  # 규칙 검색 1 + ReAct 2
    run = next(r for r in state.agent_runs if r.agent == "explorer")
    assert run.input_tokens == 30


def test_explorer_react_stops_after_invalid_actions(env):
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    bad = ExplorerDecision(thought="?", action="traverse_graph", **{**NULLS, "item_id": 1, "node_ids": ["SCR:NONE"]})
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)]),
                             explorer_llm=ScriptLLM([bad, bad])))
    trace = state.explorer.react_trace
    assert trace[0].observation.startswith("오류") and trace[-1].action == "finish"


def test_retriever_react_reads_more_and_requests_next_target(env):
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    llm = ScriptLLM([
        RetrieverDecision(thought="범위 밖은 직접 못 읽음", action="read_source", **{**R_NULLS, "node_ids": ["BAT:TORD100"]}),
        RetrieverDecision(thought="저장 SQL 원문 더 보기", action="read_source",
                          **{**R_NULLS, "node_ids": ["SQL:OrdMapper.insert"], "context_lines": 3}),
        RetrieverDecision(thought="같은 테이블을 읽는 배치 확인 필요", action="request_reexplore",
                          **{**R_NULLS, "item_id": 1, "node_ids": ["SQL:tord100.C_ORD"], "target_role": NodeRole.ENTRY,
                             "direction": "reverse", "reason": "TOR_ORD 를 읽는 배치 SQL"}),
        RetrieverDecision(thought="끝", action="finish", **R_NULLS),
    ] + [RetrieverDecision(thought="끝", action="finish", **R_NULLS)])
    sup = sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)]), retriever_llm=llm)
    state = analyze(sup)
    first = llm.users[0]
    assert "[경로 밖 연결]" in first and "SQL:tord100.C_ORD -READS-> TBL:TOR_ORD" in first
    run1 = state.agent_runs[2]
    assert run1.agent == "retriever" and run1.tool_calls == 2  # read_source 1 (규칙) + 1 (ReAct, 범위 밖 요청은 실행 안 함)
    # next_target 요청으로 Explorer 가 다시 돌아 배치까지 역방향 경로를 찾음
    assert state.explore_loop_count == 2
    assert any(p.nodes == ["BAT:TORD100", "SQL:tord100.C_ORD"] for p in state.explorer.paths)
    assert "[이미 요청한 재탐색]" in llm.users[-1] and "next_target ['SQL:tord100.C_ORD']" in llm.users[-1]


def test_replan_message_carries_previous_plan_and_feedback():
    out = plan([item(ChangeType.DATA_ADD, [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")], Direction.FORWARD,
                     NodeRole.TABLE)])
    state = AgentState(user_input=UserInput(request_text="요청", sm_part=SmPart.MARKETING), planner=out,
                       replan_feedback=[ReplanFeedback(round=1, reason="배치 영역 누락", additional_scope=["주문 전송 배치"]),
                                        ReplanFeedback(round=2, reason="연계 누락", additional_scope=["IF-ORD-001"])])
    msg = user_message(state)
    assert "[이전 탐색 명세]" in msg and "Screen:주문 등록" in msg
    assert "1차: 배치 영역 누락" in msg and "2차: 연계 누락" in msg


def test_followup_routes_to_composer_and_keeps_window(env):
    sup = sup_with(env, plan([]))
    seen = []
    sup.composer = Fixed(ComposerOutput(complexity=Complexity.SIMPLE, markdown="ok"))
    sup.composer.run = lambda s: (seen.append(len(s.followups)), ComposerOutput(complexity=Complexity.SIMPLE, markdown="ok"))[1]
    state = sup.analyze(UserInput(request_text="r", sm_part=SmPart.MARKETING))
    sid = sup.last_session_id
    planner_runs = sum(r.agent == "planner" for r in state.agent_runs)
    for i in range(7):
        state = sup.followup(sid, FollowupInput(type=FollowupType.QUERY_RESULT, content=f"결과 {i}"))
    assert sum(r.agent == "planner" for r in state.agent_runs) == planner_runs  # 조회 결과는 Planner 를 다시 돌리지 않음
    assert len(state.followups) == 5 and state.followups[-1].content == "결과 6"
    state = sup.followup(sid, FollowupInput(type=FollowupType.CHANGE_ANALYSIS, content="변경 분석 요청"))
    assert sum(r.agent == "planner" for r in state.agent_runs) == planner_runs + 1


def test_explorer_skips_react_without_signals(env):
    # 목표 도달·진입점 있음·값 언급 없음·공통 제외 없음 → 점검 신호가 없어 LLM 을 부르지 않는다
    mentions = [Mention(kind=MentionKind.BATCH, phrase="주문 전송 배치")]
    llm = ScriptLLM([])
    state = analyze(sup_with(env, plan([item(ChangeType.BATCH_CHANGE, mentions, Direction.FORWARD, NodeRole.DATA_ACCESS)]),
                             explorer_llm=llm))
    assert llm.users == [] and state.explorer.react_trace == []


def test_explorer_react_gets_signals(env):
    mentions = [Mention(kind=MentionKind.COLUMN, phrase="없는표현")]
    llm = ScriptLLM([ExplorerDecision(thought="끝", action="finish", **NULLS)])
    analyze(sup_with(env, plan([item(ChangeType.COLUMN_SPEC_CHANGE, mentions, Direction.REVERSE, NodeRole.ENTRY)]),
                     explorer_llm=llm))
    assert "[점검 신호]" in llm.users[0] and "① 항목 1: 매칭 실패 ['없는표현']" in llm.users[0]


def test_retriever_rereads_continue_and_stop_at_end(env, monkeypatch):
    import impact_agent.agents.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "CONTEXT_LINES_PER_EVIDENCE", 1)
    monkeypatch.setattr(retriever_mod, "SINGLE_NODE_OBSERVATION_CHARS", 20)
    read = RetrieverDecision(thought="이어서 읽기", action="read_source", **{**R_NULLS, "node_ids": ["SVC:OrdService.save"]})
    llm = ScriptLLM([read, read, read, RetrieverDecision(thought="끝", action="finish", **R_NULLS)])
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)]),
                             retriever_llm=llm))
    trace = state.retriever.react_trace
    assert "원문 2~2줄" in trace[0].observation and "saveItem();" in trace[0].observation
    assert "원문 3~3줄" in trace[1].observation and "끝까지 읽음" in trace[1].observation
    assert trace[2].observation.startswith("오류") and "이미 끝까지" in trace[2].observation
    assert state.retriever.tool_call_count == 3  # 규칙 1 + 이어 읽기 2 (끝까지 읽은 노드는 tool 을 부르지 않음)
    # 경로 테이블의 컬럼(경로에 없는 것)을 입력에 보여 줌
    assert "- TBL:TOR_ORD: ★ADVT_YN(광고여부(Y:광고,N:비광고)), ★ORD_ID(주문ID)" in llm.users[0]  # 저장 SQL 원문에 나옴


def test_retriever_keeps_evidence_across_reexplore_rounds(env):
    # hop 상한 → continue 재탐색. 두 번째 Retriever 는 새 노드만 읽고 첫 라운드 근거를 이어받는다
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE, cap=2)])))
    assert state.explore_loop_count == 2
    ids = [e.node_id for e in state.retriever.evidence]
    assert len(ids) == len(set(ids))
    assert {"SCR:ORD01", "CTL:OrdController.save", "SQL:OrdMapper.insert", "TBL:TOR_ORD"} <= set(ids)
    runs = [r for r in state.agent_runs if r.agent == "retriever"]
    assert [r.tool_calls for r in runs] == [1, 1]


def test_retriever_context_lines_shows_new_lines_first(env, monkeypatch):
    import impact_agent.agents.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "CONTEXT_LINES_PER_EVIDENCE", 1)
    llm = ScriptLLM([
        RetrieverDecision(thought="앞뒤 함께", action="read_source",
                          **{**R_NULLS, "node_ids": ["SVC:OrdService.save"], "context_lines": 1}),
        RetrieverDecision(thought="끝", action="finish", **R_NULLS),
    ])
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)]),
                             retriever_llm=llm))
    obs = state.retriever.react_trace[0].observation
    assert "L1-5 (원문 1~4줄" in obs and "class OrdService {" in obs  # 새로 붙은 L1 부터
    assert [e.location for e in state.retriever.evidence if e.node_id == "SVC:OrdService.save"] == ["L1-5"]  # 넓은 범위 하나로


def test_reexplore_round_does_not_repeat_explorer_tokens(env):
    # 1라운드 ReAct 사용량이 재탐색 라운드(LLM 미사용) 실행 로그에 다시 기록되지 않는다
    mentions = [Mention(kind=MentionKind.COLUMN, phrase="없는표현"), Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    llm = ScriptLLM([ExplorerDecision(thought="끝", action="finish", **NULLS)])
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE, cap=2)]),
                             explorer_llm=llm))
    runs = [r for r in state.agent_runs if r.agent == "explorer"]
    assert len(runs) == 2 and runs[0].input_tokens == 10 and runs[1].input_tokens is None


def test_retriever_key_evidence_goes_first(env):
    # finish 에서 고른 핵심 근거: 원문에 있는 줄이면 그 앞뒤를 잘라 [핵심] 근거로 맨 앞에, 원문에 없는 인용은 버림
    from impact_agent.agents.retriever import KeyEvidence

    finish = RetrieverDecision(thought="끝", action="finish", **{**R_NULLS, "key_evidence": [
        KeyEvidence(node_id="SVC:OrdService.save", quote="saveItem();", note="저장은 saveItem 으로 이어진다"),
        KeyEvidence(node_id="SVC:OrdService.save", quote="deleteAll(); // 원문에 없음", note="지어낸 인용")]})
    mentions = [Mention(kind=MentionKind.SCREEN, phrase="주문 등록")]
    state = analyze(sup_with(env, plan([item(ChangeType.DATA_ADD, mentions, Direction.FORWARD, NodeRole.TABLE)]),
                             retriever_llm=ScriptLLM([finish])))
    first = state.retriever.evidence[0]
    assert first.summary == "[핵심] 저장은 saveItem 으로 이어진다" and first.node_id == "SVC:OrdService.save"
    assert first.location == "L2-4" and "saveItem();" in first.content
    assert sum(e.summary.startswith("[핵심]") for e in state.retriever.evidence) == 1
    assert sum(e.node_id == "SVC:OrdService.save" for e in state.retriever.evidence) == 2  # 원래 근거도 유지
