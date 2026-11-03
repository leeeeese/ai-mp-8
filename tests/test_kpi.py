import json

import pytest

from impact_agent import config
from impact_agent.agents import KpiAgent
from impact_agent.agents.kpi import infer_system, judge, load_answer_key, node_type, stage_of
from impact_agent.schemas import (
    AgentRunRecord,
    AnswerKey,
    AnswerNode,
    Area,
    AreaImpact,
    Complexity,
    ComposerOutput,
    Confidence,
    Difficulty,
    Direction,
    Domain,
    Evidence,
    ExcludedNode,
    ExplorePath,
    ExplorerOutput,
    Feasibility,
    ImpactPath,
    ImpactType,
    KpiMetricName,
    NodeSeverity,
    NodeVerdict,
    PathHop,
    PlannerOutput,
    RequestType,
    RetrieverOutput,
    SimpleAnswer,
    SimulationOutput,
    SmPart,
    StopReason,
    StructuredRequest,
    UserInput,
)
from impact_agent.state import AgentState

C, M, m = NodeSeverity.CRITICAL, NodeSeverity.MAJOR, NodeSeverity.MINOR


def answer(node_id, stage, severity=M):
    return AnswerNode(node_id=node_id, type=node_type(node_id), stage=stage, severity=severity)


# 설계서 8장 판정표 예시 (swing): 정답 8, 출력 7
KEY = AnswerKey(
    req_id="T-1",
    answer_nodes=[
        answer("SCR:S1", 0), answer("TP:TP1", 1), answer("MOD:M1", 2), answer("MOD:M2", 2, m),
        answer("DBIO:D1", 3, C), answer("TBL:T1", 4, C), answer("TBL:T2", 4), answer("COL:C1", 5, C),
    ],
    distractor_nodes=["TBL:T9"],
)
OUTPUT = ["SCR:S1", "TP:TP1", "MOD:M1", "DBIO:D1", "TBL:T1", "MOD:M9", "TBL:T9"]


def make_state(output, answer_key=KEY, complexity=Complexity.COMPLEX, tool_calls=3):
    state = AgentState(user_input=UserInput(request_text="r", sm_part=SmPart.PRODUCT), answer_key=answer_key)
    state.planner = PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=complexity,
        complexity_reason="x",
        domains=[],
        items=[],
        plan=[],
    )
    if output is not None and complexity is Complexity.COMPLEX:
        hops = [PathHop(node=n, relation="r", impact="i", confidence=Confidence.CONFIRMED) for n in output]
        state.simulation = SimulationOutput(area_impacts=[AreaImpact(
            area=Area.DB, impact_type=ImpactType.MODIFY, paths=[ImpactPath(path_id="p", hops=hops)],
            feasibility=Feasibility.FEASIBLE, difficulty=Difficulty.MINOR, difficulty_basis="b",
            confidence=Confidence.CONFIRMED)])
    elif output is not None:  # 단순: Composer 단순 응답 경로
        hops = [PathHop(node=n, relation="r", impact="i", confidence=Confidence.CONFIRMED) for n in output]
        state.composer = ComposerOutput(complexity=complexity, markdown="m", simple_answer=SimpleAnswer(
            conclusion_lines=["a", "b", "c"], evidence_table=[], impact_paths=[ImpactPath(path_id="p", hops=hops)]))
    state.agent_runs = [AgentRunRecord(agent="explorer", latency_ms=10, tool_calls=tool_calls)]
    return state


def metrics(out):
    return {x.name: x.value for x in out.metrics}


@pytest.mark.parametrize("complexity", list(Complexity))
def test_design_example(complexity):
    out = KpiAgent().run(make_state(OUTPUT, complexity=complexity))
    v = metrics(out)
    assert v[KpiMetricName.NODE_RECALL] == pytest.approx(5 / 8)
    assert v[KpiMetricName.NODE_MISS_RATE] == pytest.approx(3 / 8)
    assert v[KpiMetricName.CRITICAL_MISS_RATE] == pytest.approx(1 / 3)   # C1
    assert v[KpiMetricName.NODE_PRECISION] == pytest.approx(5 / 7)
    assert out.missed_nodes == ["MOD:M2", "TBL:T2", "COL:C1"]
    assert out.false_positive_nodes == ["MOD:M9", "TBL:T9"]
    assert out.distractors_found == ["TBL:T9"]
    recall = {g.key: (g.tp, g.tp + g.fn) for g in out.by_type}
    assert recall == {"Screen": (1, 1), "TP": (1, 1), "Module": (1, 2), "DBIO": (1, 1), "Table": (1, 2), "Column": (0, 1)}
    assert [g.key for g in out.by_type] == ["Screen", "TP", "Module", "DBIO", "Table", "Column"]
    assert {g.key: g.recall for g in out.by_stage}["4"] == 0.5


def test_excluded_and_out_of_scope():
    key = KEY.model_copy(update={
        "excluded_nodes": [ExcludedNode(node_id="MOD:M9", reason="reference_only")],
        "eval_target_labels": ["Screen", "TP", "Module", "DBIO", "Table"],   # Column 제외
    })
    out = KpiAgent().run(make_state(OUTPUT + ["COL:X"], answer_key=key))
    verdict = {r.node_id: r.verdict for r in out.judgements}
    assert verdict["MOD:M9"] is NodeVerdict.EXCLUDED                       # 출력돼도 오탐 아님
    assert verdict["COL:C1"] is NodeVerdict.OUT_OF_SCOPE and verdict["COL:X"] is NodeVerdict.OUT_OF_SCOPE
    v = metrics(out)
    assert v[KpiMetricName.NODE_RECALL] == pytest.approx(5 / 7)            # C1 은 분모에서 빠짐
    assert v[KpiMetricName.NODE_PRECISION] == pytest.approx(5 / 6)         # M9 제외, T9 만 오탐
    assert (v[KpiMetricName.EXCLUDED_NODES], v[KpiMetricName.OUT_OF_SCOPE_NODES]) == (1, 2)
    assert KpiMetricName.CRITICAL_MISS_RATE in v and v[KpiMetricName.CRITICAL_MISS_RATE] == 0  # 범위 안 Critical 2건 모두 탐지


def test_thresholds_and_warnings():
    out = KpiAgent().run(make_state(["SCR:Z"], tool_calls=config.MAX_TOOL_CALLS + 1))
    v = metrics(out)
    assert v[KpiMetricName.NODE_RECALL] == 0 and v[KpiMetricName.CRITICAL_MISS_RATE] == 1
    failed = {x.name for x in out.metrics if x.passed is False}
    assert failed == {KpiMetricName.NODE_RECALL, KpiMetricName.NODE_PRECISION, KpiMetricName.TOOL_CALLS}
    assert not out.all_passed and len(out.warnings) == 3


def tag_hops(state, changed):
    """Simulator 경로 hop 에 [변경]/[참조] 표시를 붙인다 (changed 노드만 [변경])."""
    for h in state.simulation.area_impacts[0].paths[0].hops:
        h.impact = "[변경] x" if h.node in changed else "[참조] y"
    return state


def test_change_node_precision():
    state = tag_hops(make_state(OUTPUT), {"SCR:S1", "TP:TP1", "MOD:M9"})
    v = metrics(KpiAgent().run(state))
    assert v[KpiMetricName.NODE_PRECISION] == pytest.approx(5 / 7)         # [참조] 노드 T9 도 오탐
    assert v[KpiMetricName.CHANGE_NODE_PRECISION] == pytest.approx(2 / 3)  # [변경] 3개 중 M9 만 오탐
    m = next(x for x in KpiAgent().run(state).metrics if x.name is KpiMetricName.CHANGE_NODE_PRECISION)
    assert m.threshold is None and m.passed is None                       # 측정만

    key = KEY.model_copy(update={"excluded_nodes": [ExcludedNode(node_id="MOD:M9", reason="reference_only")]})
    state.answer_key = key                                                # 평가 제외 노드는 노드 정밀도와 같이 세지 않음
    assert metrics(KpiAgent().run(state))[KpiMetricName.CHANGE_NODE_PRECISION] == 1.0

    assert KpiMetricName.CHANGE_NODE_PRECISION not in metrics(KpiAgent().run(make_state(OUTPUT)))   # 표시 없음 → 생략


def test_single_answer_precision_threshold_not_applied():
    key = AnswerKey(req_id="T-1", answer_nodes=[answer("TBL:T1", 4)])
    out = KpiAgent().run(make_state(["SCR:S1", "TP:TP1", "TBL:T1"], answer_key=key))
    m = next(x for x in out.metrics if x.name is KpiMetricName.NODE_PRECISION)
    assert m.value == pytest.approx(1 / 3) and m.threshold is None and m.passed is None   # 기록만, 미달 아님
    assert out.all_passed and out.warnings == []


def test_zero_denominator_is_not_applicable():
    key = AnswerKey(req_id="T-0", answer_nodes=[])
    v = metrics(KpiAgent().run(make_state([], answer_key=key, complexity=Complexity.COMPLEX)))
    assert KpiMetricName.NODE_RECALL not in v and KpiMetricName.NODE_PRECISION not in v


def test_without_answer_key_only_performance():
    out = KpiAgent().run(make_state(OUTPUT, answer_key=None))
    v = metrics(out)
    assert KpiMetricName.NODE_RECALL not in v and KpiMetricName.LATENCY_MS in v
    assert out.req_id is None and not out.evaluable and out.judgements == [] and out.warnings == []


@pytest.mark.parametrize("complexity", list(Complexity))
def test_missing_final_nodes_is_not_evaluable(complexity):
    out = KpiAgent().run(make_state(None, complexity=complexity))
    assert not out.evaluable and out.req_id == "T-1" and "final_nodes" in out.skip_reason
    assert out.warnings and KpiMetricName.NODE_RECALL not in metrics(out)


def test_simple_final_nodes_from_composer_paths():
    state = make_state(["SCR:S1", "TP:TP1", "TP:TP1", "MOD:M1"], complexity=Complexity.SIMPLE)
    # Retriever 근거만 있고 경로에 없는 노드(사용자 답변에 안 보임)·Explorer 결과는 세지 않음, 중복 제외
    state.retriever = RetrieverOutput(evidence=[Evidence(node_id="MOD:OFF", file_path="g", summary="s", source_kind="source")],
                                      nodes_without_evidence=["TBL:OFF"])
    state.explorer = ExplorerOutput(start_nodes=[], nodes=[], edges=[], paths=[
        ExplorePath(item_id=1, nodes=["SCR:OTHER"], direction=Direction.FORWARD, stop_reason=StopReason.TARGET_REACHED)])
    assert KpiAgent.final_nodes(state) == ["SCR:S1", "TP:TP1", "MOD:M1"]


def test_tool_calls_per_round_and_explore_loops_total():
    # 재계획 1회: 라운드마다 tool 한도를 새로 주므로 한도 비교는 라운드별 최댓값, 합계는 따로. 재탐색은 Retriever 실행 수 합계
    state = make_state(OUTPUT)
    n = config.MAX_TOOL_CALLS
    state.agent_runs = [AgentRunRecord(agent=a, latency_ms=1, tool_calls=t) for a, t in [
        ("planner", 0), ("explorer", n - 5), ("retriever", 3), ("explorer", 1), ("retriever", 1), ("simulator", 0),
        ("planner", 0), ("explorer", n - 2), ("retriever", 2), ("composer", 0)]]
    state.explore_loop_count = 1                                              # 마지막 라운드 값 (쓰지 않음)
    out = KpiAgent().run(state)
    v = metrics(out)
    assert v[KpiMetricName.TOOL_CALLS] == n and v[KpiMetricName.TOOL_CALLS_TOTAL] == 2 * n
    assert v[KpiMetricName.EXPLORE_LOOPS] == 3
    assert KpiMetricName.TOOL_CALLS not in {x.name for x in out.metrics if x.passed is False}   # 라운드마다 한도 안


def test_complex_final_nodes_from_simulation_not_retriever():
    state = make_state(["TBL:T1"], complexity=Complexity.COMPLEX)
    state.retriever = RetrieverOutput(evidence=[], nodes_without_evidence=["TBL:R"])
    assert KpiAgent.final_nodes(state) == ["TBL:T1"]


def test_judge_order_and_output_flag():
    key = AnswerKey(req_id="T-3", answer_nodes=[answer("TBL:A", 4)], excluded_nodes=[ExcludedNode(node_id="TBL:E", reason="x")])
    rows = judge(key, ["TBL:B", "TBL:A", "TBL:B"])
    assert [(r.node_id, r.verdict, r.in_output) for r in rows] == [
        ("TBL:A", NodeVerdict.DETECTED, True), ("TBL:E", NodeVerdict.EXCLUDED, False), ("TBL:B", NodeVerdict.FALSE_POSITIVE, True)]


def test_helpers():
    assert stage_of(["BAT:x", "DBIO:y"], 1) == 2                          # Batch 직접 호출 DBIO
    assert stage_of(["TP:x", "MOD:y", "DBIO:z", "TBL:t"], 2) == 3          # Module 경유 DBIO
    assert stage_of(["TBL:t", "COL:c"], 1) == 5
    assert node_type("SVC:a.b") == "Service" and node_type("XYZ:a") == "XYZ"
    assert infer_system(["TP:a", "TBL:b"]) is Domain.SWING and infer_system(["SQL:a"]) is Domain.TOS
    assert infer_system(["TBL:a"]) is None and infer_system(["TP:a", "SQL:b"]) is None


def test_load_answer_key(tmp_path):
    gt = {
        "req_id": "L-9", "sm_part": "product", "complexity": "simple",
        "expected": {
            "impacted": [
                {"node_id": "DBIO:a", "must_find": True, "severity": "Critical"},
                {"node_id": "TBL:t", "must_find": True},
                {"node_id": "TBL:b", "must_find": False},
            ],
            "reference_only": ["TBL:r", "TBL:b"],
            "paths": [
                {"path_id": "P1", "nodes": ["SCR:s", "MOD:m", "DBIO:a", "TBL:t"], "critical": True},
                {"path_id": "P2", "nodes": ["SCR:s", "TP:p"], "status": "pending"},
            ],
        },
        "distractors": [{"node_id": "TBL:x", "why_confusing": "-"}],
    }
    path = tmp_path / "L-9.json"
    path.write_text(json.dumps(gt), encoding="utf-8")
    key = load_answer_key(path)
    assert [(a.node_id, a.type, a.stage, a.severity) for a in key.answer_nodes] == [
        ("DBIO:a", "DBIO", 3, C), ("TBL:t", "Table", 4, M)]
    assert {e.node_id: e.reason for e in key.excluded_nodes} == {
        "TBL:b": "must_find=false", "TBL:r": "reference_only",
        "SCR:s": "정답 경로 노드 (impacted 아님)", "MOD:m": "정답 경로 노드 (impacted 아님)", "TP:p": "미확정 경로 노드"}
    assert key.system is Domain.SWING and key.sm_part is SmPart.PRODUCT and key.complexity is Complexity.SIMPLE
    assert key.distractor_nodes == ["TBL:x"] and key.eval_target_labels == []


def test_ground_truth_files_load():
    from pathlib import Path

    files = sorted((Path(__file__).resolve().parents[1] / "eval" / "ground_truth").glob("*.json"))
    assert files
    for f in files:
        key = load_answer_key(f)
        assert key.answer_nodes, f.name
        assert not {a.node_id for a in key.answer_nodes} & {e.node_id for e in key.excluded_nodes}, f.name
