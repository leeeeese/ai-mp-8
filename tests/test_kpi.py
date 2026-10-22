import json

from impact_agent import config
from impact_agent.agents import KpiAgent
from impact_agent.agents.kpi import load_answer_key
from impact_agent.schemas import (
    AgentRunRecord,
    AnswerKey,
    Area,
    AreaImpact,
    Complexity,
    Confidence,
    Difficulty,
    Evidence,
    Feasibility,
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
)
from impact_agent.state import AgentState

KEY = AnswerKey(req_id="T-1", expected_nodes=["a", "b", "c", "d"], optional_nodes=["o"], distractor_nodes=["x"])


def make_state(complexity, found, answer_key=KEY, tool_calls=3):
    state = AgentState(user_input=UserInput(request_text="r", sm_part=SmPart.PRODUCT), answer_key=answer_key)
    state.planner = PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=complexity,
        complexity_reason="x",
        plan=[],
    )
    if complexity is Complexity.COMPLEX:
        hops = [PathHop(node=n, relation="r", impact="i", confidence=Confidence.CONFIRMED) for n in found]
        state.simulation = SimulationOutput(area_impacts=[AreaImpact(
            area=Area.DB, impact_type=ImpactType.MODIFY, paths=[ImpactPath(path_id="p", hops=hops)],
            feasibility=Feasibility.FEASIBLE, difficulty=Difficulty.MINOR, difficulty_basis="b",
            confidence=Confidence.CONFIRMED,
        )])
    else:
        state.retriever = RetrieverOutput(evidence=[
            Evidence(node_id=n, file_path="f", summary="s", source_kind="source") for n in found
        ])
    state.agent_runs = [AgentRunRecord(agent="explorer", latency_ms=10, tool_calls=tool_calls)]
    return state


def metrics(out):
    return {m.name: m.value for m in out.metrics}


def test_recall_precision_from_answer_key():
    for complexity in Complexity:
        out = KpiAgent().run(make_state(complexity, found=["a", "b", "c", "o", "x"]))
        m = metrics(out)
        assert m[KpiMetricName.RECALL] == 0.75
        assert m[KpiMetricName.MISS_RATE] == 0.25
        assert m[KpiMetricName.PRECISION] == 0.8  # a, b, c, o 는 허용 / x 는 오탐
        assert out.missed_nodes == ["d"] and out.extra_nodes == ["x"]
        assert out.req_id == "T-1"


def test_without_answer_key_only_performance():
    out = KpiAgent().run(make_state(Complexity.SIMPLE, found=["a"], answer_key=None))
    m = metrics(out)
    assert KpiMetricName.RECALL not in m and KpiMetricName.LATENCY_MS in m
    assert out.req_id is None


def test_failures_produce_warnings():
    out = KpiAgent().run(make_state(Complexity.SIMPLE, found=["x"], tool_calls=config.MAX_TOOL_CALLS + 1))
    failed = {m.name for m in out.metrics if m.passed is False}
    assert failed == {KpiMetricName.RECALL, KpiMetricName.PRECISION, KpiMetricName.TOOL_CALLS}
    assert not out.all_passed and len(out.warnings) == 3


def test_load_answer_key(tmp_path):
    gt = {
        "req_id": "L-9",
        "expected": {
            "impacted": [{"node_id": "a", "must_find": True}, {"node_id": "b", "must_find": False}],
            "reference_only": ["r"],
        },
        "distractors": [{"node_id": "x", "why_confusing": "-"}],
    }
    path = tmp_path / "L-9.json"
    path.write_text(json.dumps(gt), encoding="utf-8")
    key = load_answer_key(path)
    assert key.expected_nodes == ["a"] and key.optional_nodes == ["b", "r"] and key.distractor_nodes == ["x"]
