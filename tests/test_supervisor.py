"""Supervisor 루프 제어 테스트 (에이전트는 가짜 구현으로 대체)."""

from impact_agent import config
from impact_agent.agents import Supervisor
from impact_agent.schemas import (
    AgentName,
    AnswerKey,
    Complexity,
    ComposerOutput,
    ExplorerOutput,
    KpiOutput,
    PlannerOutput,
    PlanStep,
    ReexploreRequest,
    RequestType,
    RetrieverOutput,
    SimulationOutput,
    SmPart,
    StructuredRequest,
    UserInput,
    VerifierOutput,
    VerifyResult,
)


class Fake:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def run(self, state):
        self.calls += 1
        return self.fn(state)


def make_supervisor(complexity, reexplore=False, verify=VerifyResult.PASS):
    agents = [AgentName.EXPLORER, AgentName.RETRIEVER]
    if complexity is Complexity.COMPLEX:
        agents += [AgentName.SIMULATOR, AgentName.VERIFIER]
    sup = Supervisor()
    sup.planner = Fake(lambda s: PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=complexity,
        complexity_reason="test",
        domains=[],
        items=[],
        plan=[PlanStep(agent=a, purpose="p") for a in agents],
    ))
    sup.explorer = Fake(lambda s: ExplorerOutput(start_nodes=[], nodes=[], edges=[], hops_used=1))
    sup.retriever = Fake(lambda s: RetrieverOutput(
        evidence=[], reexplore_request=[ReexploreRequest(node_ids=["n"], reason="r")] if reexplore else []
    ))
    sup.simulator = Fake(lambda s: SimulationOutput(area_impacts=[]))
    sup.verifier = Fake(lambda s: VerifierOutput(result=verify, checks=[], reason="r"))
    sup.seen_kpi = []
    sup.composer = Fake(lambda s: (sup.seen_kpi.append(s.kpi), ComposerOutput(complexity=complexity, markdown="ok"))[1])
    return sup


def run(sup):
    return sup.run(UserInput(request_text="요청", sm_part=SmPart.PRODUCT))


def test_simple_skips_simulation():
    sup = make_supervisor(Complexity.SIMPLE)
    assert run(sup).markdown == "ok"
    assert sup.simulator.calls == 0 and sup.verifier.calls == 0


def test_reexplore_is_bounded():
    sup = make_supervisor(Complexity.SIMPLE, reexplore=True)
    run(sup)
    assert sup.explorer.calls == config.MAX_EXPLORE_LOOP


def test_replan_is_bounded():
    sup = make_supervisor(Complexity.COMPLEX, verify=VerifyResult.NEEDS_SUPPLEMENT)
    run(sup)
    assert sup.planner.calls == config.MAX_REPLAN + 1
    assert sup.composer.calls == 1


def test_kpi_runs_after_composer():
    for complexity in Complexity:
        sup = make_supervisor(complexity)
        run(sup)
        assert sup.seen_kpi == [None]  # Composer 는 KPI 결과를 보지 않는다
        kpi = sup.last_state.kpi
        assert isinstance(kpi, KpiOutput)
        assert kpi.complexity is complexity
        agents = [r.agent for r in kpi.agent_runs]
        assert agents[0] == "planner" and agents[-1] == "composer"


def test_answer_key_reaches_kpi():
    sup = make_supervisor(Complexity.SIMPLE)
    sup.run(UserInput(request_text="요청", sm_part=SmPart.PRODUCT), AnswerKey(req_id="L-1", expected_nodes=["n"]))
    assert sup.last_state.kpi.req_id == "L-1"
    assert sup.last_state.kpi.missed_nodes == ["n"]
