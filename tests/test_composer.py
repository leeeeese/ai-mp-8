"""Composer 테스트 (가짜 LLM / LLM 없음). 설계: docs/SE_agent_design.md 3장."""

from impact_agent.agents import ComposerAgent, SimulatorAgent, Supervisor, VerifierAgent
from impact_agent.agents.composer import (
    ComplexDraft,
    DraftChecklistItem,
    DraftClaim,
    DraftMail,
    DraftQuery,
    DraftQuerySummary,
    FollowupDraft,
    SimpleDraft,
)
from impact_agent.agents.simulator import DraftDrop, DraftHop, SimulatorDraft
from impact_agent.agents.verifier import decide
from impact_agent.llm import LLMError, Usage
from impact_agent.schemas import (
    AgentName,
    Alternative,
    AnswerKey,
    AnswerNode,
    Area,
    ChecklistOwner,
    ChecklistStatus,
    Complexity,
    Difficulty,
    Feasibility,
    FollowupInput,
    FollowupType,
    OpenItem,
    PlanStep,
    SaveReportOutput,
    StopReason,
)
from test_simulator import P_BATCH, P_SCREEN, Fixed, make_state
from test_simulator import area as sim_area
from test_verifier import NO_IFACE

SIMPLE_PATH = [(1, P_SCREEN, StopReason.TARGET_REACHED)]


class ScriptLLM:
    def __init__(self, *drafts, error=None):
        self.drafts, self.error, self.users, self.schemas = list(drafts), error, [], []

    def parse(self, system, user, schema):
        self.users.append(user)
        self.schemas.append(schema)
        if self.error:
            raise self.error
        assert "Composer" in system
        d = self.drafts.pop(0)
        assert isinstance(d, schema)
        return d, Usage(30, 8)


class Saver:
    def __init__(self, fail_pdf=False):
        self.fail_pdf, self.calls = fail_pdf, []

    def __call__(self, params):
        self.calls.append(params)
        if params.format == "pdf" and self.fail_pdf:
            raise ImportError("No module named 'weasyprint'")
        return SaveReportOutput(file_path=f"outputs/reports/r.{params.format}")


def composer(llm=None, saver=None):
    return ComposerAgent(llm=llm, use_llm=llm is not None, saver=saver or Saver())


def simple_state(**kw):
    state = make_state(paths=SIMPLE_PATH, **kw)
    state.planner.complexity = Complexity.SIMPLE
    return state


def complex_state(open_items=(), sim_llm=None, nodes=None):
    state = make_state(**({"nodes": nodes} if nodes else {}))
    state.planner.structured_request.open_items = [OpenItem(item=i, reason="요건서에 없음") for i in open_items]
    state.simulation = SimulatorAgent(llm=sim_llm, use_llm=sim_llm is not None).run(state)
    state.verifier = VerifierAgent(use_llm=False).run(state)
    return state


# --- 단순 -----------------------------------------------------------------------------------------


def test_simple_rule_only():
    state = simple_state(evidence_nodes=[n for n in P_SCREEN if n != "TBL:TCP_CMP"])
    out = composer().run(state)
    a = out.simple_answer
    assert out.complexity is Complexity.SIMPLE and out.complex_report is None
    assert 3 <= len(a.conclusion_lines) <= 5 and a.conclusion_lines[1] == "영향 경로 1개, 노드 6개 중 원문 근거 확보 5개"
    assert [p.path_id for p in a.impact_paths] == ["P1-1"] and [h.node for h in a.impact_paths[0].hops] == P_SCREEN
    assert len(a.evidence_table) == 5 and a.evidence_table[0].claim == "SCR:CMP01 요약"
    assert a.detail_analysis_recommended          # 항목 1 이 data_add
    assert "근거(원문) 없음: TBL:TCP_CMP" in a.analysis_limits
    assert "## 결론" in out.markdown and "- P1-1: SCR:CMP01 → CTL:CmpCtl.save" in out.markdown
    assert out.mail_draft is None and out.report_file_path is None


def test_simple_llm_sentences_queries_and_mail():
    state = simple_state()
    state.user_input.output_options = ["mail_draft"]
    llm = ScriptLLM(SimpleDraft(
        conclusion_lines=["비광고 여부는 TCP_CMP.ADVT_YN 반전으로 저장 가능합니다."],   # 1줄 → 규칙 문구로 3줄 채움
        hops=[DraftHop(path_id="P1-1", node="SQL:CmpMapper.insert", impact="INSERT 컬럼 추가")],
        claims=[DraftClaim(node_id="COL:TCP_CMP.ADVT_YN", claim="광고여부 컬럼 존재")],
        check_queries=[DraftQuery(purpose="값 분포", sql="SELECT ADVT_YN, COUNT(*) FROM TCP_CMP GROUP BY ADVT_YN;"),
                       DraftQuery(purpose="수정", sql="UPDATE TCP_CMP SET ADVT_YN = 'N'"),
                       DraftQuery(purpose="여러 문장", sql="SELECT 1 FROM DUAL; DELETE FROM TCP_CMP"),
                       DraftQuery(purpose="숨은 쓰기", sql="WITH x AS (SELECT 1 FROM DUAL) SELECT * FROM x FOR UPDATE")],
        inquiry_contacts=["캠페인 운영 담당"], detail_analysis_recommended=False,
        mail=DraftMail(subject="[회신] 비광고 여부", body="본문")))
    agent = composer(llm)
    out = agent.run(state)
    a = out.simple_answer
    assert a.conclusion_lines[0].startswith("비광고 여부는") and len(a.conclusion_lines) == 3
    assert a.impact_paths[0].hops[3].impact == "INSERT 컬럼 추가"
    assert {r.claim for r in a.evidence_table} >= {"광고여부 컬럼 존재"}
    assert [q.purpose for q in a.check_queries] == ["값 분포"] and not a.check_queries[0].sql.endswith(";")
    assert a.inquiry_contacts == ["캠페인 운영 담당"] and "## 문의처" in out.markdown
    assert a.detail_analysis_recommended          # LLM 은 false 지만 규칙(data_add)으로 true
    assert out.mail_draft.subject == "[회신] 비광고 여부" and agent.last_usage.output_tokens == 8
    assert llm.schemas == [SimpleDraft] and "[출력 요청] mail_draft" in llm.users[0]


def test_simple_rule_mail_and_too_many_lines():
    state = simple_state()
    state.user_input.output_options = ["mail_draft"]
    out = composer(ScriptLLM(SimpleDraft(conclusion_lines=[f"줄 {i}" for i in range(7)], hops=[], claims=[], check_queries=[],
                                         inquiry_contacts=[], detail_analysis_recommended=True, mail=None))).run(state)
    assert out.simple_answer.conclusion_lines == [f"줄 {i}" for i in range(5)]
    assert out.mail_draft.subject == "[영향도 분석] t" and "[결론]\n- 줄 0" in out.mail_draft.body


# --- 복잡 -----------------------------------------------------------------------------------------


def test_complex_rule_only_report():
    state = complex_state(open_items=["B 상품코드 분리 여부"])
    db = next(a for a in state.simulation.area_impacts if a.area is Area.DB)
    db.alternatives = [Alternative(description="ADVT_YN 반전 사용", feasibility=Feasibility.FEASIBLE,
                                   difficulty=Difficulty.MINOR, pros_cons="DB 변경 없음")]
    out = composer().run(state)
    r = out.complex_report
    assert out.complexity is Complexity.COMPLEX and out.simple_answer is None and r.stage == "initial"
    assert [x.area for x in r.area_verdicts] == [Area.SCREEN, Area.DB, Area.INTERFACE, Area.BATCH]
    assert r.area_verdicts[1].alternative == "ADVT_YN 반전 사용" and r.area_verdicts[0].evidence_names[0] == "src/CMP01#L1-9"
    # 영역 난이도는 영역 기준만 (screen Minor), 요건 전체는 변경 영역 4개 → Major
    assert r.summary_conclusion.startswith("변경 영역: screen(modify·review·Minor)")
    assert "요건 전체 난이도 Major (변경 영역 4개 (3개 이상))" in r.summary_conclusion
    assert [p.path_id for p in r.path_details] == ["P1-1", "P2-1"]
    ids = {c.item_id: (c.owner, c.question) for c in r.checklist}
    assert ids["B-1"] == (ChecklistOwner.BUSINESS, "B 상품코드 분리 여부 (요건서에 없음)")
    assert ids["B-2"][1].startswith("db 영역: 요청안과 대안 중 선택")
    assert ids["D-1"][1] == "screen 영역 구현 가능 여부 확인 (원문 근거 부족)"
    assert "## 영역별 판정" in out.markdown and "## 검토 제외 경로" not in out.markdown


def test_complex_limits_from_verifier_and_dropped_paths():
    extra = ["SCR:CMP01", "CTL:CmpCtl.save"]
    sim_llm = type("L", (), {"parse": lambda self, s, u, schema: (SimulatorDraft(
        hops=[], areas=[], requested_vs_alternative=None, drop_paths=[DraftDrop(path_id="P1-2", reason="조회 진입만")]), Usage())})()
    state = make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED), (1, extra, StopReason.DEAD_END),
                              (2, P_BATCH, StopReason.TARGET_REACHED)], nodes=NO_IFACE)
    state.simulation = SimulatorAgent(llm=sim_llm).run(state)
    first = VerifierAgent(use_llm=False).run(state)                       # 인터페이스 영역 누락 → 보완 필요
    state.verifier = first                                               # 재계획 한도 초과로 Composer 에 옴
    out = composer().run(state)
    r = out.complex_report
    assert any(x.startswith("재계획 한도(2회) 안에서 보완하지 못함: [항목 2]") for x in r.analysis_limits)
    assert [d.path.path_id for d in r.dropped_paths] == ["P1-2"]
    assert "## 검토 제외 경로" in out.markdown and "| P1-2 | SCR:CMP01 → CTL:CmpCtl.save | 조회 진입만 |" in out.markdown
    assert any(c.question.startswith("검토 제외 경로 P1-2") for c in r.checklist if c.owner is ChecklistOwner.DEVELOPER)
    # 같은 사유 반복으로 통과한 점검은 분석 한계로
    state.verifier = decide([], [])
    state.verifier.checks[0].note = "(반복) 재계획으로 해결되지 않음: [항목 2] x"
    assert "(반복) 재계획으로 해결되지 않음: [항목 2] x" in composer().run(state).complex_report.analysis_limits


def test_complex_llm_summary_checklist_merge():
    state = complex_state(open_items=["B 상품코드 분리 여부"])
    llm = ScriptLLM(ComplexDraft(
        summary_conclusion="구현 가능하며 난이도는 Major 입니다.",
        checklist=[DraftChecklistItem(owner=ChecklistOwner.DEVELOPER, question="CmpSvc 하드코딩 위치 확인", related_area=Area.SCREEN),
                   DraftChecklistItem(owner=ChecklistOwner.BUSINESS, question="B 상품코드 분리 여부 (요건서에 없음)", related_area=None)],
        mail=None))
    out = composer(llm).run(state)
    r = out.complex_report
    assert r.summary_conclusion == "구현 가능하며 난이도는 Major 입니다."
    assert [(c.item_id, c.question) for c in r.checklist] == [
        ("B-1", "B 상품코드 분리 여부 (요건서에 없음)"), ("D-1", "CmpSvc 하드코딩 위치 확인")]   # 중복 질문 제거
    assert r.checklist[1].related_area is Area.SCREEN
    assert "[이미 포함된 확인 항목]" in llm.users[0] and llm.schemas == [ComplexDraft]


def test_llm_failure_uses_rules():
    out = composer(ScriptLLM(error=LLMError("timeout"))).run(complex_state())
    assert out.complex_report.summary_conclusion.startswith("변경 영역:")
    assert any("LLM 작성 실패" in x for x in out.complex_report.analysis_limits)


# --- 후속 답변 ------------------------------------------------------------------------------------


def test_followup_answers_checklist_and_stage():
    state = complex_state(open_items=["B 상품코드 분리 여부"])
    state.composer = composer().run(state)
    ids = [c.item_id for c in state.composer.complex_report.checklist]
    state.followups = [FollowupInput(type=FollowupType.BUSINESS_ANSWER, content="동일 상품코드 유지", target_item_ids=["B-1"])]
    out = composer().run(state)
    r = out.complex_report
    b1 = next(c for c in r.checklist if c.item_id == "B-1")
    assert (b1.answer, b1.status) == ("동일 상품코드 유지", ChecklistStatus.CONFIRMED) and r.stage == "updated"
    assert "| B-1 | business | B 상품코드 분리 여부 (요건서에 없음) | 동일 상품코드 유지 | confirmed |" in out.markdown
    state.composer = out
    state.followups = [FollowupInput(type=FollowupType.DEVELOPER_OPINION, content="확인", target_item_ids=ids)]
    assert composer().run(state).complex_report.stage == "final_for_dev"


def test_followup_reanalysis_request():
    state = complex_state(open_items=["B 상품코드 분리 여부"])
    state.composer = composer().run(state)
    state.followups = [FollowupInput(type=FollowupType.BUSINESS_ANSWER, content="B 는 별도 상품코드로", target_item_ids=["B-1"])]
    llm = ScriptLLM(FollowupDraft(needs_reanalysis=True, reason="상품 데이터 경로를 다시 봐야 함",
                                  changed_assumptions=["B 별도 상품코드"], query_summary=None))
    out = composer(llm).run(state)
    assert out.reanalysis_request.reason == "상품 데이터 경로를 다시 봐야 함"
    assert "> 재분석 필요: 상품 데이터 경로를 다시 봐야 함 (바뀐 가정: B 별도 상품코드)" in out.markdown
    assert "[체크리스트]" in llm.users[0] and llm.schemas == [FollowupDraft]


def test_followup_query_result_on_simple_answer():
    state = simple_state()
    state.composer = composer().run(state)
    state.followups = [FollowupInput(type=FollowupType.QUERY_RESULT, content="ADVT_YN=Y 120건\nN 3건")]
    out = composer().run(state)                                         # LLM 없음
    assert out.simple_answer.query_result_summary.inquiry_items == ["조회 결과 검토 필요: ADVT_YN=Y 120건"]
    llm = ScriptLLM(FollowupDraft(needs_reanalysis=False, reason=None, changed_assumptions=[],
                                  query_summary=DraftQuerySummary(code_meanings=["Y: 광고"], main_causes=["기본값 Y"],
                                                                  inquiry_items=[])))
    out = composer(llm).run(state)
    assert out.simple_answer.query_result_summary.main_causes == ["기본값 Y"] and "## 조회 결과 요약" in out.markdown
    assert out.reanalysis_request is None


# --- PDF ------------------------------------------------------------------------------------------


def test_pdf_saved_or_falls_back_to_md():
    state = complex_state()
    state.user_input.output_options = ["pdf"]
    saver = Saver()
    out = composer(saver=saver).run(state)
    assert out.report_file_path == "outputs/reports/r.pdf" and [c.format for c in saver.calls] == ["pdf"]
    saver = Saver(fail_pdf=True)
    out = composer(saver=saver).run(state)
    assert out.report_file_path == "outputs/reports/r.md" and [c.format for c in saver.calls] == ["pdf", "md"]
    assert any(x.startswith("PDF 변환 불가로 MD 로 저장") for x in out.complex_report.analysis_limits)
    assert "PDF 변환 불가" in saver.calls[1].content


# --- Supervisor 전체 흐름 ---------------------------------------------------------------------------


def test_supervisor_end_to_end_and_followup():
    base = complex_state(open_items=["B 상품코드 분리 여부"])
    plan = [PlanStep(agent=a, purpose="p") for a in (AgentName.EXPLORER, AgentName.RETRIEVER, AgentName.SIMULATOR,
                                                     AgentName.VERIFIER)]
    sup = Supervisor()
    sup.planner = Fixed(base.planner.model_copy(update={"plan": plan}))
    sup.explorer, sup.retriever = Fixed(base.explorer), Fixed(base.retriever)
    sup.simulator, sup.verifier = SimulatorAgent(use_llm=False), VerifierAgent(use_llm=False)
    sup.composer = composer()
    key = AnswerKey(req_id="T", answer_nodes=[AnswerNode(node_id="BAT:TCMP100", type="Batch")])
    out = sup.run(base.user_input, key)
    assert out.complex_report.stage == "initial" and "# 영향도 분석 보고서 (initial)" in out.markdown
    assert sup.last_state.kpi.evaluable and [r.agent for r in sup.last_state.agent_runs][-1] == "composer"

    state = sup.followup(sup.last_session_id, FollowupInput(type=FollowupType.BUSINESS_ANSWER, content="유지", target_item_ids=["B-1"]))
    assert state.composer.complex_report.stage == "updated"
    assert [r.agent for r in state.agent_runs].count("planner") == 1     # 답변은 Planner 를 거치지 않음


def test_schedule_factors_capped():
    state = complex_state()
    llm = type("L", (), {"parse": lambda self, s, u, schema: (SimulatorDraft(hops=[], areas=[
        sim_area(a, schedule_factors=[f"{a.value} 요인 {i}" for i in range(5)])
        for a in (Area.SCREEN, Area.DB, Area.INTERFACE, Area.BATCH)], requested_vs_alternative=None, drop_paths=[]), Usage())})()
    state.simulation = SimulatorAgent(llm=llm).run(state)
    assert all(len(a.schedule_factors) == 3 for a in state.simulation.area_impacts)      # 영역당 3개
    r = composer().run(state).complex_report
    assert len(r.schedule_factors) == 9 and r.schedule_factors[-1] == "외 4건 (영역별 판정 참고)"  # 전체 8개 + 안내
