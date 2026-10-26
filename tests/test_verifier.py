"""Verifier 테스트 (가짜 LLM / LLM 없음). 설계: docs/SE_agent_design.md 2장."""

from impact_agent.agents import SimulatorAgent, Supervisor, VerifierAgent
from impact_agent.agents.verifier import DraftAltJudgement, VerifierDraft
from impact_agent.llm import LLMError, Usage
from impact_agent.schemas import (
    AgentName,
    Alternative,
    Area,
    Complexity,
    ComposerOutput,
    Confidence,
    Difficulty,
    Domain,
    Feasibility,
    ItemExploreResult,
    NodeRole,
    PlanStep,
    ReplanFeedback,
    StopReason,
    VerifyResult,
)
from test_simulator import P_BATCH, P_SCREEN, TOS, Fixed, make_state, node

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
                                             "alternative_basis"]
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
