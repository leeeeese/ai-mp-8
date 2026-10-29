"""KPI 판정 품질 지표 · swing 노드 판정 · 배치 평가 집계/리포트/비교 테스트. 설계: docs/SE_kpi-agent-design.md 1-4, 2장."""

import importlib.util
import json
from pathlib import Path

import pytest

from impact_agent.agents import ComposerAgent, KpiAgent, SimulatorAgent, VerifierAgent
from impact_agent.agents.kpi import infer_system, judge, load_answer_key, node_type, stage_of
from impact_agent.schemas import (
    AnswerKey,
    AnswerNode,
    Area,
    Complexity,
    Difficulty,
    Feasibility,
    FollowupType,
    ImpactType,
    KpiCategory,
    KpiMetricName,
    NodeVerdict,
    StopReason,
)
from test_simulator import (
    CHANGE_HOPS,
    SHARED_PATHS,
    SW_SCREEN,
    DraftHop,
    ScriptLLM,
    area,
    draft,
    make_state,
    make_swing_state,
    with_followups,
)

ROOT = Path(__file__).resolve().parents[1]
M = KpiMetricName

# 예시 4 판정: Module 상수 변경 → screen·batch modify (공유 노드라 Medium), db·interface 는 참조만
EXAMPLE4_AREAS = [area(Area.SCREEN), area(Area.DB, impact=ImpactType.REFERENCE_ONLY),
                  area(Area.INTERFACE, impact=ImpactType.REFERENCE_ONLY), area(Area.BATCH)]


def run_complex(state, sim_areas=None, hops=None):
    """실제 Simulator(가짜 LLM 판정) → Verifier → Composer 를 돈 상태."""
    llm = ScriptLLM(draft(areas=sim_areas, hops=hops)) if sim_areas or hops else None
    state.simulation = SimulatorAgent(llm=llm, use_llm=llm is not None).run(state)
    state.verifier = VerifierAgent(use_llm=False).run(state)
    state.composer = ComposerAgent(use_llm=False).run(state)
    return state


def swing_key(**kw):
    base = dict(
        req_id="T-SW", complexity=Complexity.COMPLEX, answer_nodes=[AnswerNode(node_id="MOD:PntSvc.checkLimit", type="Module")],
        expected_difficulty=Difficulty.MEDIUM, expected_feasibility=Feasibility.FEASIBLE,
        expected_areas={Area.SCREEN: ImpactType.MODIFY, Area.DB: ImpactType.REFERENCE_ONLY,
                        Area.INTERFACE: ImpactType.REFERENCE_ONLY, Area.BATCH: ImpactType.MODIFY},
        change_nodes=["MOD:PntSvc.checkLimit"], reference_nodes=["TP:PntSvc.savePnt", "DBIO:PntSql.selectMonthPnt"])
    return AnswerKey(**(base | kw))


def kpi_of(state, key):
    state.answer_key = key
    return KpiAgent().run(state)


def values(kpi, category=KpiCategory.JUDGEMENT):
    return {m.name: m.value for m in kpi.metrics if m.category is category}


# --- 판정 품질: 복잡 flow ---------------------------------------------------------------------------


def test_judgement_all_match_swing():
    state = run_complex(make_swing_state(SHARED_PATHS), EXAMPLE4_AREAS, CHANGE_HOPS)
    kpi = kpi_of(state, swing_key())
    assert values(kpi) == {M.COMPLEXITY_MATCH: 1.0, M.DIFFICULTY_MATCH: 1.0, M.FEASIBILITY_MATCH: 1.0,
                           M.AREA_IMPACT_MATCH: 1.0, M.CHANGE_TAG_ACCURACY: 1.0, M.COMPLEX_MINOR: 0.0,
                           M.VERIFIER_UNRESOLVED: 0.0}
    assert kpi.judgement_notes == [] and not kpi.reanalyzed
    assert all(m.threshold is None for m in kpi.metrics if m.category is KpiCategory.JUDGEMENT)   # 측정만
    assert kpi.all_passed == KpiAgent().run(state).all_passed                                         # 기준 판정에 영향 없음


def test_judgement_mismatches_are_explained():
    state = run_complex(make_swing_state(SHARED_PATHS), EXAMPLE4_AREAS, CHANGE_HOPS)
    key = swing_key(expected_difficulty=Difficulty.MAJOR,
                    expected_areas={Area.SCREEN: ImpactType.MODIFY, Area.DB: ImpactType.MODIFY,
                                    Area.INTERFACE: ImpactType.REFERENCE_ONLY, Area.BATCH: ImpactType.MODIFY},
                    change_nodes=["MOD:PntSvc.checkLimit", "DBIO:PntSql.selectMonthPnt"],   # DBIO 는 [참조] 로 판정됨
                    reference_nodes=["TP:PntSvc.savePnt"])
    kpi = kpi_of(state, key)
    v = values(kpi)
    assert v[M.DIFFICULTY_MATCH] == 0.0 and v[M.AREA_IMPACT_MATCH] == 0.75 and v[M.CHANGE_TAG_ACCURACY] == 2 / 3
    assert kpi.judgement_notes == [
        "요건 전체 난이도: 정답 Major / 실행 Medium",
        "영역 영향 유형 불일치: db 정답 modify/실행 reference_only",
        "[변경]/[참조] 판정 불일치: DBIO:PntSql.selectMonthPnt(정답 변경)",
    ]


def test_wrong_path_adopted_tos():
    state = make_state()          # tos 화면 경로 + 배치 경로
    hops = [DraftHop(path_id="P2-1", node="SQL:tcmp100.C_SEL", impact="[변경] 조회 컬럼 추가")]
    kpi = kpi_of(run_complex(state, hops=hops),
                 AnswerKey(req_id="T-TOS", complexity=Complexity.COMPLEX, answer_nodes=[AnswerNode(node_id="BAT:TCMP100", type="Batch")],
                           wrong_paths=[["SQL:tcmp100.C_SEL"], ["SQL:other.X"]]))
    assert values(kpi)[M.WRONG_PATH_ADOPTED] == 1.0
    assert kpi.judgement_notes == ["오답 경로 채택: SQL:tcmp100.C_SEL 을 [변경] 으로 판정"]


def test_untagged_simulation_skips_tag_metrics():
    state = run_complex(make_swing_state(SHARED_PATHS))        # Simulator LLM 없음 → [변경]/[참조] 표시 없음
    v = values(kpi_of(state, swing_key(wrong_paths=[["SQL:x"]])))
    assert M.CHANGE_TAG_ACCURACY not in v and M.WRONG_PATH_ADOPTED not in v
    assert v[M.COMPLEX_MINOR] == 0.0                           # 규칙 판정: interface(TP 연계) Major, 변경 영역 4개 → Major
    assert v[M.FEASIBILITY_MATCH] == 0.0                       # 규칙 판정 review vs 정답 feasible


def test_complex_minor_flag():
    db_only = [area(Area.SCREEN, impact=ImpactType.REFERENCE_ONLY), area(Area.DB),
               area(Area.INTERFACE, impact=ImpactType.REFERENCE_ONLY), area(Area.BATCH, impact=ImpactType.REFERENCE_ONLY)]
    hop = [DraftHop(path_id="P1-1", node="DBIO:PntSql.selectMonthPnt", impact="[변경] 조회 조건 변경")]   # 공유 아님
    v = values(kpi_of(run_complex(make_swing_state(SHARED_PATHS), db_only, hop), swing_key()))
    assert v[M.COMPLEX_MINOR] == 1.0 and v[M.DIFFICULTY_MATCH] == 0.0     # 정답 Medium / 실행 Minor


def test_reanalyzed_flag_and_verifier_unresolved():
    state = with_followups(make_swing_state(SHARED_PATHS), (FollowupType.REANALYSIS, "CTL:Nowhere.x 확인"))
    kpi = kpi_of(run_complex(state, EXAMPLE4_AREAS, CHANGE_HOPS), swing_key())
    assert kpi.reanalyzed
    assert values(kpi)[M.VERIFIER_UNRESOLVED] == 1.0           # 요청 노드가 경로에 없어 보완 필요가 남음
    assert any(n.startswith("Verifier 보완 필요 미해결: 재분석 요청 노드가 영향 경로에 없음") for n in kpi.judgement_notes)


# --- 판정 품질: 단순 flow (상세 분석 권장) ------------------------------------------------------------


def run_simple(state):
    state.planner.complexity = Complexity.SIMPLE
    state.composer = ComposerAgent(use_llm=False).run(state)
    return state


@pytest.mark.parametrize(("paths", "gt", "recommended", "note"), [
    (SHARED_PATHS, Complexity.COMPLEX, 1.0, None),                                         # 놓친 복잡 요건을 붙잡음
    ([(1, SW_SCREEN, StopReason.TARGET_REACHED)], Complexity.COMPLEX, 0.0, "놓친 복잡 요건"),  # 신호 없음
    (SHARED_PATHS, Complexity.SIMPLE, 1.0, "정답 simple 인데 상세 분석 권장"),               # 과한 권장
], ids=["catch", "miss", "over"])
def test_detail_recommended_simple_flow(paths, gt, recommended, note):
    state = run_simple(make_swing_state(paths))
    kpi = kpi_of(state, swing_key(complexity=gt))
    v = values(kpi)
    assert v[M.DETAIL_RECOMMENDED] == recommended
    assert v[M.COMPLEXITY_MATCH] == (1.0 if gt is Complexity.SIMPLE else 0.0)
    assert M.DIFFICULTY_MATCH not in v and M.AREA_IMPACT_MATCH not in v                   # 단순 flow 는 Simulator 없음
    assert any(note in n for n in kpi.judgement_notes) if note else kpi.judgement_notes == [f"복잡도: 정답 {gt.value} / 실행 simple"]


# --- 정답셋 로드 ---------------------------------------------------------------------------------


def test_load_answer_key_comparison_values():
    k = load_answer_key(ROOT / "eval/ground_truth/SC-001.json")
    assert k.expected_difficulty is Difficulty.MAJOR and k.expected_feasibility is Feasibility.FEASIBLE
    assert k.expected_areas[Area.INTERFACE] is ImpactType.MODIFY and len(k.expected_areas) == 4
    assert k.wrong_paths == [["SQL:tbch1010.C_SND"]]          # 정답 경로와 겹치는 노드는 뺀 고유 노드만
    assert not set(k.change_nodes) & set(k.reference_nodes)


def test_change_node_takes_priority_over_reference(tmp_path):
    gt = json.loads((ROOT / "eval/ground_truth/L-3.json").read_text(encoding="utf-8"))
    gt["expected"]["reference_only"].append("DBIO:RomProcHstSql.selectProcHstList")   # 변경 노드를 참조에도 넣음
    f = tmp_path / "L-3.json"
    f.write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")
    k = load_answer_key(f)
    assert "DBIO:RomProcHstSql.selectProcHstList" in k.change_nodes
    assert "DBIO:RomProcHstSql.selectProcHstList" not in k.reference_nodes


# --- swing 노드 판정 · 단계 ---------------------------------------------------------------------


def test_swing_types_stages_and_system():
    assert [node_type(n) for n in ("SCR:Z01", "TP:A.b", "MOD:A.c", "DBIO:S.d", "BAT:ZB01")] == \
        ["Screen", "TP", "Module", "DBIO", "Batch"]
    assert stage_of(["SCR:Z01", "TP:A.b", "DBIO:S.d"], 2) == 2              # TP 직접 호출
    assert stage_of(["SCR:Z01", "TP:A.b", "MOD:A.c", "DBIO:S.d"], 3) == 3   # Module 경유
    assert stage_of(["BAT:ZB01", "DBIO:S.d"], 1) == 2                        # 배치 직접 호출
    assert infer_system(["TP:A.b", "MOD:A.c"]).value == "swing"


def test_swing_node_judgement():
    key = AnswerKey(req_id="T", answer_nodes=[AnswerNode(node_id="MOD:PntSvc.checkLimit", type="Module"),
                                              AnswerNode(node_id="TBL:TB_X", type="Table")],
                    eval_target_labels=["TP", "Module", "Table"])
    rows = {r.node_id: r.verdict for r in judge(key, ["MOD:PntSvc.checkLimit", "TP:PntSvc.savePnt", "DBIO:PntSql.q"])}
    assert rows == {"MOD:PntSvc.checkLimit": NodeVerdict.DETECTED, "TBL:TB_X": NodeVerdict.MISSED,
                    "TP:PntSvc.savePnt": NodeVerdict.FALSE_POSITIVE, "DBIO:PntSql.q": NodeVerdict.OUT_OF_SCOPE}


# --- 배치 평가: 집계 · 리포트 · 버전 비교 (scripts/eval_kpi.py) ---------------------------------------


def eval_kpi():
    spec = importlib.util.spec_from_file_location("eval_kpi", ROOT / "scripts/eval_kpi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def case(case_id, flow, gt, state, key):
    kpi = kpi_of(state, key)
    return {"case_id": case_id, "system": "swing", "sm_part": "product", "flow": flow, "gt_complexity": gt,
            "kpi": kpi, "rows": kpi.judgements}


def batch_cases():
    return [
        case("C1", "complex", "complex", run_complex(make_swing_state(SHARED_PATHS), EXAMPLE4_AREAS, CHANGE_HOPS), swing_key()),
        case("S1", "simple", "complex", run_simple(make_swing_state(SHARED_PATHS)), swing_key()),                  # catch
        case("S2", "simple", "complex", run_simple(make_swing_state([(1, SW_SCREEN, StopReason.TARGET_REACHED)])), swing_key()),
        case("S3", "simple", "simple", run_simple(make_swing_state(SHARED_PATHS)), swing_key(complexity=Complexity.SIMPLE)),
    ]


def test_judgement_summary_rates():
    j = eval_kpi().judgement_summary(batch_cases())
    assert j["complexity_match_rate"] == 0.5                  # C1·S3 일치, S1·S2 불일치
    assert j["difficulty_match_rate"] == 1.0 and j["area_impact_match_avg"] == 1.0
    assert (j["detail_catch_cases"], j["detail_catch_rate"]) == (2, 0.5)
    assert (j["detail_over_cases"], j["detail_over_rate"]) == (1, 1.0)
    assert j["complex_minor"] == 0 and j["verifier_unresolved"] == 0 and j["reanalyzed"] == 0


def test_report_and_version_compare():
    ev = eval_kpi()
    cases = batch_cases()
    summary = {"agent_version": "v0.4", "skipped": [], "overall": ev.summarize(cases, None)["all"],
               "judgement": ev.judgement_summary(cases)}
    previous = {"agent_version": "v0.3", "overall": {"node_recall": 0.5}, "judgement": {"complexity_match_rate": 0.25}}
    md = ev.report_markdown(summary, cases, previous)
    assert md.startswith("# KPI 평가 리포트 (v0.4)")
    for section in ("## 노드 탐지", "## 판정 품질 (측정만)", "## 미탐 노드 (Critical 우선)", "## 판정 불일치", "## 버전 비교 (v0.3 → v0.4)"):
        assert section in md
    assert "- S2: 정답 complex 인데 단순 처리됐고 상세 분석 권장도 없음 (놓친 복잡 요건)" in md
    rows = {name: (before, after, diff) for name, before, after, diff in ev.compare_rows(previous, summary)}
    assert rows["judgement.complexity_match_rate"] == (0.25, 0.5, 0.25)
    assert rows["judgement.detail_catch_rate"][0] is None and rows["judgement.detail_catch_rate"][2] is None
    assert "| judgement.complexity_match_rate | 25.0% | 50.0% | +25.0% |" in md
    # 건수는 % 가 아니라 숫자로
    assert "| detail_catch_cases | 2 |" in md and "| detail_over_cases | 1 |" in md and "| wrong_path_adopted | 0 |" in md
    assert "| reanalyzed | 0 |" in md
