from impact_agent import config
from impact_agent.agents import KpiAgent
from impact_agent.schemas import (
    AgentRunRecord,
    Area,
    AreaImpact,
    Complexity,
    Confidence,
    Difficulty,
    ExplorerOutput,
    Feasibility,
    GraphNode,
    ImpactPath,
    ImpactType,
    KpiMetricName,
    PathHop,
    PlannerOutput,
    RequestType,
    RetrieverOutput,
    SimulationOutput,
    SmPart,
    StructuredRequest,
    UserInput,
    VerifierOutput,
    VerifyResult,
)
from impact_agent.state import AgentState


def make_state(complexity, nodes=4, missing=0, confirmed=2, inferred=0, verify=VerifyResult.PASS, tool_calls=3):
    state = AgentState(user_input=UserInput(request_text="r", sm_part=SmPart.PRODUCT))
    state.planner = PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=complexity,
        complexity_reason="x",
        plan=[],
    )
    state.explorer = ExplorerOutput(
        start_nodes=[], edges=[], hops_used=1,
        nodes=[GraphNode(node_id=f"n{i}", name="n", label="TP") for i in range(nodes)],
    )
    state.retriever = RetrieverOutput(evidence=[], nodes_without_evidence=[f"n{i}" for i in range(missing)])
    hops = [PathHop(node="n", relation="r", impact="i", confidence=Confidence.CONFIRMED)] * confirmed
    hops += [PathHop(node="n", relation="r", impact="i", confidence=Confidence.INFERRED)] * inferred
    state.simulation = SimulationOutput(area_impacts=[AreaImpact(
        area=Area.DB, impact_type=ImpactType.MODIFY, paths=[ImpactPath(path_id="p", hops=hops)],
        feasibility=Feasibility.FEASIBLE, difficulty=Difficulty.MINOR, difficulty_basis="b",
        confidence=Confidence.CONFIRMED,
    )])
    state.verifier = VerifierOutput(result=verify, checks=[])
    state.agent_runs = [AgentRunRecord(agent="explorer", latency_ms=10, tool_calls=tool_calls)]
    return state


def metrics(out):
    return {m.name: m for m in out.metrics}


def test_simple_has_no_complex_only_metrics():
    out = KpiAgent().run(make_state(Complexity.SIMPLE))
    m = metrics(out)
    assert KpiMetricName.EVIDENCE_COVERAGE in m
    assert KpiMetricName.CONFIRMED_RATIO not in m and KpiMetricName.VERIFY_PASSED not in m
    assert out.all_passed and not out.warnings


def test_complex_all_pass():
    out = KpiAgent().run(make_state(Complexity.COMPLEX))
    m = metrics(out)
    assert m[KpiMetricName.CONFIRMED_RATIO].value == 1.0
    assert m[KpiMetricName.AREA_COVERAGE].value == 0.25
    assert out.all_passed


def test_failures_produce_warnings():
    state = make_state(
        Complexity.COMPLEX, nodes=4, missing=2, confirmed=1, inferred=3,
        verify=VerifyResult.NEEDS_SUPPLEMENT, tool_calls=config.MAX_TOOL_CALLS + 1,
    )
    out = KpiAgent().run(state)
    failed = {m.name for m in out.metrics if m.passed is False}
    assert failed == {
        KpiMetricName.EVIDENCE_COVERAGE,
        KpiMetricName.CONFIRMED_RATIO,
        KpiMetricName.VERIFY_PASSED,
        KpiMetricName.TOOL_CALLS,
    }
    assert not out.all_passed and len(out.warnings) == 4
