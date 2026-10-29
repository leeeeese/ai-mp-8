"""Verifier 테스트 (가짜 LLM / LLM 없음). 설계: docs/SE_agent_design.md 2장."""

from impact_agent.agents import SimulatorAgent, Supervisor, VerifierAgent
from impact_agent.agents.verifier import DraftAltJudgement, VerifierDraft
from impact_agent.llm import LLMError, Usage
from impact_agent.schemas import (
    AgentName,
    Alternative,
    Area,
    ChangeType,
    Complexity,
    ComposerOutput,
    Confidence,
    Difficulty,
    Direction,
    Domain,
    ExploreItem,
    Feasibility,
    FollowupType,
    ItemExploreResult,
    Mention,
    MentionKind,
    NodeRole,
    PlanStep,
    ReplanFeedback,
    StopReason,
    VerifyResult,
)
from test_simulator import (
    CHANGE_HOPS,
    P_BATCH,
    P_SCREEN,
    SHARED_PATHS,
    SW_SCREEN,
    SWING_IDS,
    TOS,
    DraftHop,
    Fixed,
    make_state,
    make_swing_state,
    node,
    with_followups,
)
from test_simulator import ScriptLLM as SimLLM
from test_simulator import draft as sim_draft

NO_IFACE = [n if n.node_id != "BAT:TCMP100" else node("BAT:TCMP100", "Batch", NodeRole.ENTRY) for n in TOS]


def simulate(state):
    state.simulation = SimulatorAgent(use_llm=False).run(state)
    return state


def verify(state, llm=None):
    agent = VerifierAgent(llm=llm, use_llm=llm is not None)
    return agent.run(state), agent


def failing(out, check=None):
    return [c for c in out.checks if c.result is VerifyResult.NEEDS_SUPPLEMENT and (check is None or c.check == check)]


class ScriptLLM:
    def __init__(self, draft=None, error=None):
        self.draft, self.error, self.users = draft, error, []

    def parse(self, system, user, schema):
        self.users.append(user)
        if self.error:
            raise self.error
        assert schema is VerifierDraft and "Verifier" in system
        return self.draft, Usage(50, 10)


def test_all_pass_lists_every_check():
    out, _ = verify(simulate(make_state()))
    assert out.result is VerifyResult.PASS and out.reason is None and out.additional_scope == []
    assert [c.check for c in out.checks] == ["area_path_missing", "evidence_missing", "cross_domain_link", "redundant_path",
                                             "alternative_basis", "reanalysis_coverage"]
    assert all(c.note == "이상 없음" for c in out.checks)


# --- 영역 누락 ------------------------------------------------------------------------------------


def test_required_area_missing():
    out, _ = verify(simulate(make_state(nodes=NO_IFACE)))   # 항목 2 interface_field_add 인데 interface 경로 없음
    (c,) = failing(out)
    assert c.check == "area_path_missing" and c.area is Area.INTERFACE
    assert out.result is VerifyResult.NEEDS_SUPPLEMENT and "[항목 2] interface_field_add 인데 interface 영역 경로 없음" in out.reason
    assert out.additional_scope == ["area:interface", "keyword:전송 파일"]


def test_interface_out_of_graph_counts_as_marked():
    out, _ = verify(simulate(make_state(nodes=NO_IFACE, unresolved=["[항목 2] 그래프 밖: 수신 시스템 전문"])))
    assert out.result is VerifyResult.PASS


def test_item_without_any_path():
    state = make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED)],
                       item_results=[ItemExploreResult(item_id=2, target_role=NodeRole.ENTRY, direction="reverse",
                                                       reached=False, frontier=["SQL:tcmp100.C_SEL"])])
    out, _ = verify(simulate(state))
    (c,) = failing(out)
    assert c.note.startswith("[항목 2] 영향 경로 없음")
    assert out.additional_scope == ["node:SQL:tcmp100.C_SEL", "keyword:전송 파일"]


# --- 근거 검증 ------------------------------------------------------------------------------------


def test_confirmed_hop_with_unknown_evidence_ref():
    state = simulate(make_state())
    hop = state.simulation.area_impacts[0].paths[0].hops[1]
    hop.evidence_ref = "src/elsewhere#L1-2"
    out, _ = verify(state)
    (c,) = failing(out, "evidence_missing")        # screen·db 에 걸친 경로라도 한 번만
    assert c.note.endswith(": CTL:CmpCtl.save") and out.additional_scope == ["node:CTL:CmpCtl.save"]


def test_changed_area_without_any_evidence():
    state = simulate(make_state(evidence_nodes=[n for n in P_SCREEN if n != "TBL:TCP_CMP"]))
    out, _ = verify(state)
    # 배치 경로(P2-1)는 근거가 전혀 없고 interface·batch 영역에 걸침 / db 는 P1-1 근거가 있어 통과
    assert {c.area for c in failing(out, "evidence_missing")} == {Area.INTERFACE, Area.BATCH}
    assert all("원문 근거가 하나도 없음" in c.note for c in failing(out, "evidence_missing"))
    assert out.additional_scope == ["node:BAT:TCMP100", "node:SQL:tcmp100.C_SEL", "node:TBL:TCP_CMP"]


# --- 도메인 연계 · 중복 ----------------------------------------------------------------------------


def test_cross_domain_not_marked():
    nodes = [n if n.node_id != "TBL:TCP_CMP" else n.model_copy(update={"domain": Domain.SWING}) for n in TOS]
    state = simulate(make_state(nodes=nodes))
    assert all(p.cross_domain for a in state.simulation.area_impacts for p in a.paths)   # Simulator 는 표시함
    assert verify(state)[0].result is VerifyResult.PASS
    for a in state.simulation.area_impacts:
        for p in a.paths:
            p.cross_domain = False
    out, _ = verify(state)
    assert {c.note.split(" ")[0] for c in failing(out, "cross_domain_link")} == {"P1-1", "P2-1"}


def test_redundant_and_contained_paths_are_warnings_only():
    state = simulate(make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED), (1, P_SCREEN[:3], StopReason.DEAD_END),
                                       (2, P_BATCH, StopReason.TARGET_REACHED)]))
    out, _ = verify(state)
    assert out.result is VerifyResult.PASS
    notes = [c.note for c in out.checks if c.check == "redundant_path"]
    assert notes == ["(경고) P1-2 가 P1-1 에 포함됨"]


# --- 대안 근거 ------------------------------------------------------------------------------------

ALT = Alternative(description="기존 ADVT_YN 반전 사용", feasibility=Feasibility.FEASIBLE, difficulty=Difficulty.MINOR,
                  pros_cons="DB 변경 없음")


def with_hard(state, alternatives):
    db = next(a for a in state.simulation.area_impacts if a.area is Area.DB)
    db.feasibility, db.infeasible_reason, db.alternatives = Feasibility.HARD, "길이 제한", alternatives
    return state


def test_hard_without_alternative():
    out, _ = verify(with_hard(simulate(make_state()), []))
    (c,) = failing(out, "alternative_basis")
    assert c.area is Area.DB and "area:db" in out.additional_scope


def test_alternative_basis_judged_by_llm():
    state = with_hard(simulate(make_state()), [ALT, ALT.model_copy(update={"description": "공통코드 추가"})])
    llm = ScriptLLM(VerifierDraft(judgements=[
        DraftAltJudgement(area=Area.DB, index=0, has_basis=True, note="COL:TCP_CMP.ADVT_YN 근거"),
        DraftAltJudgement(area=Area.DB, index=1, has_basis=False, note="공통코드 테이블이 근거에 없음"),
        DraftAltJudgement(area=Area.SCREEN, index=5, has_basis=False, note="없는 대안")]))
    out, agent = verify(state, llm)
    (c,) = failing(out, "alternative_basis")
    assert c.note == "[db] 대안 2 근거 부족: 공통코드 테이블이 근거에 없음"
    assert agent.last_usage.input_tokens == 50
    assert "db / 1: 공통코드 추가" in llm.users[0] and "[근거 요약]" in llm.users[0]


def test_alternative_llm_failure_is_warning_and_no_llm_skips():
    state = with_hard(simulate(make_state()), [ALT])
    out, _ = verify(state, ScriptLLM(error=LLMError("timeout")))
    assert out.result is VerifyResult.PASS and any("LLM 판단 실패" in c.note for c in out.checks)
    out, agent = verify(state)            # LLM 없음 → 대안이 있으면 통과
    assert out.result is VerifyResult.PASS and agent.last_usage is None


# --- 반복 사유 · 재계획 루프 ------------------------------------------------------------------------


def test_repeated_reason_passes_with_note():
    state = simulate(make_state(nodes=NO_IFACE))
    first, _ = verify(state)
    state.replan_feedback = [ReplanFeedback(round=1, reason=first.reason, additional_scope=first.additional_scope)]
    out, _ = verify(state)
    assert out.result is VerifyResult.PASS and out.additional_scope == []
    (c,) = [c for c in out.checks if c.check == "area_path_missing"]
    assert c.note.startswith("(반복) 재계획으로 해결되지 않음: [항목 2]")


def test_supervisor_replans_once_then_passes_on_repeat():
    base = make_state(nodes=NO_IFACE)
    plan = [PlanStep(agent=a, purpose="p") for a in (AgentName.EXPLORER, AgentName.RETRIEVER, AgentName.SIMULATOR,
                                                     AgentName.VERIFIER)]
    sup = Supervisor()
    sup.planner = Fixed(base.planner.model_copy(update={"plan": plan}))
    sup.explorer, sup.retriever = Fixed(base.explorer), Fixed(base.retriever)
    sup.simulator, sup.verifier = SimulatorAgent(use_llm=False), VerifierAgent(use_llm=False)
    sup.composer = Fixed(ComposerOutput(complexity=Complexity.COMPLEX, markdown="ok"))
    sup.run(base.user_input)
    st = sup.last_state
    assert [r.agent for r in st.agent_runs].count("planner") == 2       # 1차 보완 필요 → 재계획 1회 → 같은 사유 반복 → 통과
    assert st.replan_count == 1 and len(st.replan_feedback) == 1
    assert st.replan_feedback[0].additional_scope == ["area:interface", "keyword:전송 파일"]
    assert st.verifier.result is VerifyResult.PASS and st.composer.markdown == "ok"
    assert st.simulation.area_impacts[0].confidence is Confidence.CONFIRMED


# --- 재분석 요청 점검 (설계서 1-4, 2장) — swing 경로 + 실제 Simulator 출력 -----------------------------


def reanalysis_state(reason, simulator_llm=None, **kw):
    """화면·배치가 TP·Module 을 공유하는 swing 경로에, 사용자의 재분석 사유를 붙여 Simulator 까지 돌린 상태."""
    state = with_followups(make_swing_state(SHARED_PATHS, **kw), (FollowupType.REANALYSIS, reason))
    state.simulation = SimulatorAgent(llm=simulator_llm, use_llm=simulator_llm is not None).run(state)
    return state


def coverage(out):
    return [c for c in out.checks if c.check == "reanalysis_coverage"]


def test_reanalysis_node_missing_needs_supplement():
    out, _ = verify(reanalysis_state("공통 모듈 CTL:CmmCdController.selectCdList 영향 확인 / MOD:PntSvc.checkLimit 확인"))
    assert out.result is VerifyResult.NEEDS_SUPPLEMENT
    (c,) = coverage(out)
    assert c.result is VerifyResult.NEEDS_SUPPLEMENT
    assert c.note == "재분석 요청 노드가 영향 경로에 없음: CTL:CmmCdController.selectCdList"   # 경로에 있는 노드는 제외
    assert out.additional_scope == ["node:CTL:CmmCdController.selectCdList"]                  # Planner 재계획 입력


def test_reanalysis_missing_common_node_says_excluded():
    state = reanalysis_state("공통 모듈 MOD:BatchLauncher.runJob 이 탐색에서 제외되어 영향 확인 필요")
    state.explorer.excluded_common = ["MOD:BatchLauncher.runJob"]
    (c,) = coverage(verify(state)[0])
    assert c.note == "재분석 요청 노드가 영향 경로에 없음: MOD:BatchLauncher.runJob (공통 모듈이라 탐색에서 제외됨)"


def test_reanalysis_nodes_answered_pass():
    sim_llm = SimLLM(sim_draft(hops=CHANGE_HOPS))     # checkLimit [변경], savePnt·selectMonthPnt [참조]
    out, _ = verify(reanalysis_state("MOD:PntSvc.checkLimit 와 TP:PntSvc.savePnt 를 화면·배치 2곳에서 호출", sim_llm))
    (c,) = coverage(out)
    assert c.result is VerifyResult.PASS and c.note == "이상 없음"


def test_reanalysis_node_unanswered_is_warning_only():
    sim_llm = SimLLM(sim_draft(hops=CHANGE_HOPS))     # TBL:TB_PNT_HST 에는 판정 문장 없음
    out, _ = verify(reanalysis_state("TBL:TB_PNT_HST 와 MOD:PntSvc.checkLimit 영향 확인", sim_llm))
    (c,) = coverage(out)
    assert c.result is VerifyResult.PASS
    assert c.note == "(경고) 재분석 요청 노드의 판정 문장([변경]/[참조])이 없음: TBL:TB_PNT_HST"
    assert "node:TBL:TB_PNT_HST" not in out.additional_scope                       # 경고는 재계획 사유 아님


def test_reanalysis_untagged_simulation_skips_answer_check():
    out, _ = verify(reanalysis_state("TBL:TB_PNT_HST 영향 확인"))                    # Simulator LLM 없음 → 표시 없음
    (c,) = coverage(out)
    assert c.result is VerifyResult.PASS and c.note == "이상 없음"


def test_reanalysis_missing_repeated_passes():
    state = reanalysis_state("CTL:CmmCdController.selectCdList 영향 확인")
    first, _ = verify(state)
    state.replan_feedback = [ReplanFeedback(round=1, reason=first.reason, additional_scope=first.additional_scope)]
    out, _ = verify(state)
    (c,) = coverage(out)
    assert out.result is VerifyResult.PASS and c.result is VerifyResult.PASS
    assert c.note.startswith("(반복) 재계획으로 해결되지 않음: 재분석 요청 노드가 영향 경로에 없음")


def test_no_reanalysis_no_coverage_issue():
    state = make_swing_state(SHARED_PATHS)
    state.simulation = SimulatorAgent(use_llm=False).run(state)
    (c,) = coverage(verify(state)[0])
    assert c.result is VerifyResult.PASS and c.note == "이상 없음"


def test_reanalysis_coverage_tos():
    # tos 노드 ID(CTL·SQL·SVC)로도 같은 판정: 미탐색 공통 모듈 → 보완 필요, 판정 문장 없음 → 경고
    state = with_followups(make_state(), (FollowupType.REANALYSIS,
        "공통 모듈 CTL:CmmCdController.selectCdList 영향 확인 / SQL:tcmp100.C_SEL 확인 / SVC:CmpSvc.save 판정 필요"))
    state.explorer.excluded_common = ["CTL:CmmCdController.selectCdList"]
    hops = [DraftHop(path_id="P1-1", node="SVC:CmpSvc.save", impact="[변경] 저장 분기 추가")]
    state.simulation = SimulatorAgent(llm=SimLLM(sim_draft(hops=hops))).run(state)
    out, _ = verify(state)
    assert [c.note for c in coverage(out)] == [
        "재분석 요청 노드가 영향 경로에 없음: CTL:CmmCdController.selectCdList (공통 모듈이라 탐색에서 제외됨)",
        "(경고) 재분석 요청 노드의 판정 문장([변경]/[참조])이 없음: SQL:tcmp100.C_SEL"]
    assert out.additional_scope == ["node:CTL:CmmCdController.selectCdList"]


# --- 근거 점검: 변경 지점([변경] hop) 기준 (설계서 2장) ---------------------------------------------


def evidence_checks(out):
    return [c for c in out.checks if c.check == "evidence_missing" and c.result is VerifyResult.NEEDS_SUPPLEMENT]


def shared_state(hops, no_evidence=()):
    """swing 화면·배치 공유 경로. no_evidence 노드는 Retriever 근거 없음. Simulator 는 hops 판정을 쓴다."""
    state = make_swing_state(SHARED_PATHS, evidence_nodes=[n for n in SWING_IDS if n not in no_evidence])
    state.simulation = SimulatorAgent(llm=SimLLM(sim_draft(hops=hops))).run(state)
    return state


def test_changed_hop_without_evidence_needs_supplement():
    # 영역의 다른 노드엔 근거가 있지만, 실제로 바꾸는 Module 에 근거가 없음 → 이전 기준으로는 통과, 지금은 보완 필요
    out, _ = verify(shared_state(CHANGE_HOPS, no_evidence=["MOD:PntSvc.checkLimit"]))
    assert [(c.area, c.note) for c in evidence_checks(out)] == [
        (Area.SCREEN, "[screen] 변경 지점([변경] hop)에 원문 근거가 없음: MOD:PntSvc.checkLimit"),
        (Area.BATCH, "[batch] 변경 지점([변경] hop)에 원문 근거가 없음: MOD:PntSvc.checkLimit")]
    assert out.additional_scope == ["node:MOD:PntSvc.checkLimit"]                  # db·interface 는 Module 소속 아님


def test_changed_hops_with_any_evidence_pass():
    assert evidence_checks(verify(shared_state(CHANGE_HOPS))[0]) == []          # 변경 지점에 근거 있음
    hops = CHANGE_HOPS + [DraftHop(path_id="P1-1", node="TP:PntSvc.savePnt", impact="[변경] 입력 검증 추가")]
    out, _ = verify(shared_state(hops, no_evidence=["MOD:PntSvc.checkLimit"]))
    assert evidence_checks(out) == []                                           # [변경] 2개 중 하나(TP)는 근거 있음


def test_untagged_simulation_keeps_area_wide_evidence_rule():
    state = make_swing_state(SHARED_PATHS, evidence_nodes=[n for n in SWING_IDS if n != "MOD:PntSvc.checkLimit"])
    state.simulation = SimulatorAgent(use_llm=False).run(state)               # [변경] 표시 없음
    assert evidence_checks(verify(state)[0]) == []                             # 영역에 근거 있는 hop 이 있어 통과 (이전 동작)


# --- swing 경로에서 기존 점검 (설계서 2장) ---------------------------------------------------------


def swing_item(change_type):
    return [ExploreItem(item_id=1, text="t", change_type=change_type, mentions=[Mention(kind=MentionKind.TERM, phrase="적립")],
                        target_role=NodeRole.LOGIC, direction=Direction.FORWARD, hop_cap=5)]


def test_swing_required_area_missing_and_met():
    state = make_swing_state([(1, SW_SCREEN, StopReason.TARGET_REACHED)], items=swing_item(ChangeType.BATCH_CHANGE))
    out, _ = verify(simulate(state))
    assert [c.note for c in failing(out, "area_path_missing")] == ["[항목 1] batch_change 인데 batch 영역 경로 없음"]
    state = make_swing_state(SHARED_PATHS, items=swing_item(ChangeType.BATCH_CHANGE))   # Batch→TP 재사용 경로
    assert failing(verify(simulate(state))[0], "area_path_missing") == []


def test_swing_interface_from_tp_property_counts():
    # swing 연계는 TP 속성 interfaces (IfClient.send). 화면 경로만 있어도 interface 영역 충족
    state = make_swing_state([(1, SW_SCREEN, StopReason.TARGET_REACHED)], items=swing_item(ChangeType.INTERFACE_FIELD_ADD))
    assert failing(verify(simulate(state))[0], "area_path_missing") == []


def test_swing_cross_domain_and_contained_path():
    state = simulate(make_swing_state(SHARED_PATHS + [(1, ["SCR:ZPNTSS01", "TP:PntSvc.savePnt"], StopReason.DEAD_END)]))
    out, _ = verify(state)
    assert failing(out, "cross_domain_link") == []                              # swing 단일 도메인
    warns = [c.note for c in out.checks if c.check == "redundant_path" and c.note != "이상 없음"]
    assert warns == ["(경고) P1-3 가 P1-1 에 포함됨"]
    state.explorer.nodes = [n.model_copy(update={"domain": Domain.TOS}) if n.node_id == "TBL:TB_PNT_HST" else n
                            for n in state.explorer.nodes]                       # 경로 중간에 tos 노드가 섞임
    assert [c.note for c in failing(verify(state)[0], "cross_domain_link")] == [
        "P1-1 경로가 swing·tos 에 걸치는데 도메인 연계로 표시되지 않음"]
