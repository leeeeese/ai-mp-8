import json

import pytest

from impact_agent import config
from impact_agent.agents import KpiAgent
from impact_agent.agents.kpi import load_answer_key, matched_prefix_len, stage_of
from impact_agent.schemas import (
    AgentRunRecord,
    AnswerKey,
    AnswerPath,
    AnswerPathStatus,
    Complexity,
    ComplexReport,
    ComposerOutput,
    Confidence,
    ImpactPath,
    KpiMetricName,
    PathDetection,
    PathHop,
    PlannerOutput,
    RequestType,
    SimpleAnswer,
    SmPart,
    StructuredRequest,
    UserInput,
)
from impact_agent.state import AgentState

# 정답 경로: 확정 4 (Critical 3) + 미확정 1
KEY = AnswerKey(
    req_id="T-1",
    paths=[
        AnswerPath(path_id="P1", nodes=["SCR:a", "TP:b", "MOD:c", "DBIO:d"], critical=True),
        AnswerPath(path_id="P2", nodes=["SCR:a", "TP:x", "DBIO:y"], critical=True),
        AnswerPath(path_id="P3", nodes=["BAT:j", "DBIO:k"]),
        AnswerPath(path_id="P4", nodes=["SCR:m", "TP:n", "TBL:o"], critical=True),
        AnswerPath(path_id="P5", nodes=["SCR:p", "TP:q"], status=AnswerPathStatus.PENDING),
    ],
    expected_nodes=["DBIO:d", "DBIO:y", "DBIO:k", "TBL:o"],
)
# Agent 제시 경로
PREDICTED = [
    ["SCR:a", "TP:b", "TP:b2", "MOD:c", "DBIO:d", "TBL:t"],  # P1 정확 (중간 추가·끝 이후 연장 허용)
    ["SCR:a", "TP:b", "TP:b2", "MOD:c", "DBIO:d", "TBL:t"],  # 중복 → 한 번만
    ["SCR:a", "TP:x"],                                      # P2 부분 (2/3)
    ["BAT:j", "DBIO:k"],                                    # P3 정확
    ["SCR:m"],                                              # P4 초기
    ["SCR:p", "TP:q"],                                      # P5 미확정 → 제외
    ["SCR:z", "TP:z"],                                      # 오탐
]


def make_state(predicted, answer_key=KEY, complexity=Complexity.COMPLEX, tool_calls=3):
    state = AgentState(user_input=UserInput(request_text="r", sm_part=SmPart.PRODUCT), answer_key=answer_key)
    state.planner = PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=complexity, complexity_reason="x", plan=[],
    )
    paths = [
        ImpactPath(path_id=f"p{i}", hops=[PathHop(node=n, relation="r", impact="i", confidence=Confidence.CONFIRMED) for n in p])
        for i, p in enumerate(predicted)
    ]
    if complexity is Complexity.COMPLEX:
        report = ComplexReport(summary_conclusion="s", area_verdicts=[], schedule_factors=[], path_details=paths,
                               analysis_limits=[], checklist=[])
        state.composer = ComposerOutput(complexity=complexity, complex_report=report, markdown="m")
    else:
        answer = SimpleAnswer(conclusion_lines=["a", "b", "c"], impact_paths=paths, evidence_table=[])
        state.composer = ComposerOutput(complexity=complexity, simple_answer=answer, markdown="m")
    state.agent_runs = [AgentRunRecord(agent="explorer", latency_ms=10, tool_calls=tool_calls)]
    return state


def metrics(out):
    return {m.name: m.value for m in out.metrics}


@pytest.mark.parametrize("complexity", list(Complexity))
def test_path_kpis(complexity):
    out = KpiAgent().run(make_state(PREDICTED, complexity=complexity))
    m = metrics(out)
    assert m[KpiMetricName.PATH_RECALL] == 0.5                              # 정확 2 / 확정 4
    assert m[KpiMetricName.CRITICAL_MISS_RATE] == pytest.approx(2 / 3)      # P2 부분 · P4 초기 = 미탐
    assert m[KpiMetricName.PATH_PRECISION] == pytest.approx(2 / 5)          # 정확 2 / (정확 2 + 부분·초기 2 + 오탐 1)
    assert m[KpiMetricName.PATH_PRECISION_EXCL_PARTIAL] == pytest.approx(2 / 3)  # 정확 2 / (정확 2 + 오탐 1)
    assert (m[KpiMetricName.PARTIAL_PATHS], m[KpiMetricName.INITIAL_PATHS], m[KpiMetricName.PENDING_PATHS]) == (1, 1, 1)
    assert out.false_positive_paths == [["SCR:z", "TP:z"]]


def test_path_results_show_how_far():
    r = {x.path_id: x for x in KpiAgent().run(make_state(PREDICTED)).path_results}
    assert r["P1"].detection is PathDetection.EXACT and r["P1"].reached_stage == "D"
    assert r["P2"].detection is PathDetection.PARTIAL and (r["P2"].reached_node, r["P2"].reached_stage) == ("TP:x", "B")
    assert r["P4"].detection is PathDetection.INITIAL and r["P4"].reached_stage == "A"
    assert r["P5"].detection is PathDetection.PENDING


def test_missed_and_thresholds():
    out = KpiAgent().run(make_state([["SCR:z"]], tool_calls=config.MAX_TOOL_CALLS + 1))
    m = metrics(out)
    assert m[KpiMetricName.PATH_RECALL] == 0 and m[KpiMetricName.CRITICAL_MISS_RATE] == 1
    failed = {x.name for x in out.metrics if x.passed is False}
    assert failed == {KpiMetricName.PATH_RECALL, KpiMetricName.PATH_PRECISION, KpiMetricName.TOOL_CALLS}
    assert not out.all_passed and len(out.warnings) == 3
    assert out.missed_nodes == sorted(KEY.expected_nodes)


def test_without_answer_key_only_performance():
    out = KpiAgent().run(make_state(PREDICTED, answer_key=None))
    m = metrics(out)
    assert KpiMetricName.PATH_RECALL not in m and KpiMetricName.LATENCY_MS in m
    assert out.req_id is None and out.path_results == []


def test_helpers():
    assert matched_prefix_len(["a", "b", "c"], ["a", "x", "b"]) == 2
    assert matched_prefix_len(["a", "b"], ["b", "a"]) == 1
    assert stage_of(["BAT:x", "DBIO:y"], 1) == "C"   # Batch 직접 호출 DBIO
    assert stage_of(["TP:x", "MOD:y", "DBIO:z", "TBL:t", "COL:c"], 2) == "D"  # Module 경유 DBIO
    assert stage_of(["TBL:t", "COL:c"], 1) == "F"


def test_load_answer_key(tmp_path):
    gt = {
        "req_id": "L-9",
        "expected": {
            "impacted": [{"node_id": "a", "must_find": True}, {"node_id": "b", "must_find": False}],
            "reference_only": ["r"],
            "paths": [{"path_id": "P1", "nodes": ["SCR:s", "a"], "critical": True}],
        },
        "distractors": [{"node_id": "x", "why_confusing": "-"}],
    }
    path = tmp_path / "L-9.json"
    path.write_text(json.dumps(gt), encoding="utf-8")
    key = load_answer_key(path)
    assert key.expected_nodes == ["a"] and key.optional_nodes == ["b", "r"] and key.distractor_nodes == ["x"]
    assert key.paths[0].critical and key.paths[0].status is AnswerPathStatus.CONFIRMED


def test_shared_start_with_pending_still_counts_as_partial():
    # P2 를 2노드 따라갔고, 미확정 P5 와는 무관 → 부분 (보류로 빠지면 안 됨)
    key = AnswerKey(req_id="T-2", expected_nodes=[], paths=[
        AnswerPath(path_id="C", nodes=["SCR:s", "TP:a", "DBIO:b"]),
        AnswerPath(path_id="P", nodes=["SCR:s", "TP:z"], status=AnswerPathStatus.PENDING),
    ])
    m = metrics(KpiAgent().run(make_state([["SCR:s", "TP:a"]], answer_key=key)))
    assert m[KpiMetricName.PATH_PRECISION] == 0.0  # 부분 1건이 분모에 포함
    assert KpiMetricName.PATH_PRECISION_EXCL_PARTIAL in m
