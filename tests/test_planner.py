"""Planner 테스트 (가짜 LLM): 사용자 상세 분석 요청(reanalysis)이면 complex 강제 (docs/planner_detail_analysis_to-be.md)."""

from impact_agent.agents.planner import REANALYSIS_REASON, DraftItem, DraftMention, PlannerAgent, PlannerDraft
from impact_agent.llm import Usage
from impact_agent.schemas import (
    AgentName,
    ChangeType,
    Complexity,
    FollowupInput,
    FollowupType,
    MentionKind,
    RequestType,
    SmPart,
    UserInput,
)
from impact_agent.state import AgentState


class FakeLLM:
    def __init__(self, complexity=Complexity.SIMPLE):
        self.complexity = complexity
        self.users = []

    def parse(self, system, user, schema):
        self.users.append(user)
        return PlannerDraft(
            target="t", action="a", condition=None, policy=None, scope=None, request_type=RequestType.CHANGE,
            business_terms=[], open_items=[], complexity=self.complexity, complexity_reason='"문구를 바꿔 주세요" — 한 화면',
            exclude_common=True,
            items=[DraftItem(text="문구 변경", change_type=ChangeType.SCREEN_CHANGE, out_of_graph=[], mentions=[
                DraftMention(kind=MentionKind.SCREEN, phrase="안내 화면", maybe_new=False, synonyms=[], meaning=None, area=None)])],
        ), Usage(10, 5)


def state(followups=()):
    return AgentState(user_input=UserInput(request_text="안내 화면 문구를 바꿔 주세요", sm_part=SmPart.PRODUCT),
                      followups=list(followups))


def test_simple_stays_simple_without_reanalysis():
    out = PlannerAgent(llm=FakeLLM()).run(state())
    assert out.complexity is Complexity.SIMPLE
    assert [s.agent for s in out.plan] == [AgentName.EXPLORER, AgentName.RETRIEVER]


def test_reanalysis_forces_complex():
    llm = FakeLLM(Complexity.SIMPLE)
    fu = FollowupInput(type=FollowupType.REANALYSIS, content="공통 모듈 CTL:CmmCd 가 경로에 있어 영향 확인 필요")
    out = PlannerAgent(llm=llm).run(state([fu]))
    assert out.complexity is Complexity.COMPLEX
    assert [s.agent for s in out.plan] == [AgentName.EXPLORER, AgentName.RETRIEVER, AgentName.SIMULATOR, AgentName.VERIFIER]
    assert out.complexity_reason.startswith(f"{REANALYSIS_REASON}: 공통 모듈 CTL:CmmCd")
    assert "(reanalysis) 공통 모듈 CTL:CmmCd" in llm.users[0]  # 권장 사유가 Planner 입력에 들어감


def test_only_last_followup_counts():
    # 재분석 뒤에 다른 변경 분석 요청이 오면 LLM 판정을 따른다
    fus = [FollowupInput(type=FollowupType.REANALYSIS, content="r"), FollowupInput(type=FollowupType.CHANGE_ANALYSIS, content="c")]
    assert PlannerAgent(llm=FakeLLM()).run(state(fus)).complexity is Complexity.SIMPLE


def test_replan_keeps_complex():
    # Verifier 재계획 라운드: 이전 판정 complex 면 LLM 이 simple 을 내도 complex 유지
    from impact_agent.schemas import ReplanFeedback

    s = state()
    s.planner = PlannerAgent(llm=FakeLLM(Complexity.COMPLEX)).run(s)
    s.replan_feedback = [ReplanFeedback(round=1, reason="배치 영역 누락", additional_scope=["area:batch"])]
    out = PlannerAgent(llm=FakeLLM(Complexity.SIMPLE)).run(s)
    assert out.complexity is Complexity.COMPLEX and out.complexity_reason.startswith("재계획 (복잡 흐름 유지) 1차")
    assert AgentName.SIMULATOR in {p.agent for p in out.plan}
