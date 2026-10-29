"""Simulator 테스트 (가짜 LLM / LLM 없음). 설계: docs/SE_agent_design.md 1장."""

import pytest

from impact_agent.agents import KpiAgent, SimulatorAgent, Supervisor
from impact_agent.agents.simulator import (
    DraftAlternative,
    DraftArea,
    DraftDrop,
    DraftHop,
    SimulatorDraft,
    requirement_difficulty,
)
from impact_agent.llm import LLMError, Usage
from impact_agent.rules import build_impact_paths, path_areas
from impact_agent.schemas import (
    AgentName,
    AnswerKey,
    AnswerNode,
    Area,
    ChangeType,
    Complexity,
    ComposerOutput,
    Confidence,
    Difficulty,
    Direction,
    Domain,
    Evidence,
    ExploreItem,
    ExplorePath,
    ExplorerOutput,
    Feasibility,
    GraphEdge,
    GraphNode,
    ImpactType,
    ItemExploreResult,
    Mention,
    MentionKind,
    NodeRole,
    PlannerOutput,
    PlanStep,
    RequestType,
    RetrieverOutput,
    SmPart,
    StopReason,
    StructuredRequest,
    UserInput,
    VerifierOutput,
    VerifyResult,
)
from impact_agent.state import AgentState

R = NodeRole


def node(nid, label, role, domain=Domain.TOS, **props):
    return GraphNode(node_id=nid, name=nid.split(":", 1)[1], label=label, role=role, domain=domain, properties=props)


# tos: 캠페인 화면 → Controller → Service → SQL → 테이블 → 컬럼 / 전송 배치(인터페이스) → SQL → 테이블
TOS = [
    node("SCR:CMP01", "Screen", R.ENTRY), node("CTL:CmpCtl.save", "Controller", R.ENTRY_HANDLER),
    node("SVC:CmpSvc.save", "Service", R.LOGIC), node("SQL:CmpMapper.insert", "SQL", R.DATA_ACCESS),
    node("TBL:TCP_CMP", "Table", R.TABLE), node("COL:TCP_CMP.ADVT_YN", "Column", R.COLUMN),
    node("BAT:TCMP100", "Batch", R.ENTRY, interfaces=["IF-CMP-001"]), node("SQL:tcmp100.C_SEL", "SQL", R.DATA_ACCESS),
]
TOS_EDGES = [
    ("SCR:CMP01", "CTL:CmpCtl.save", "CALLS"), ("CTL:CmpCtl.save", "SVC:CmpSvc.save", "INVOKES"),
    ("SVC:CmpSvc.save", "SQL:CmpMapper.insert", "EXECUTES"), ("SQL:CmpMapper.insert", "TBL:TCP_CMP", "WRITES"),
    ("TBL:TCP_CMP", "COL:TCP_CMP.ADVT_YN", "HAS_COLUMN"), ("BAT:TCMP100", "SQL:tcmp100.C_SEL", "EXECUTES"),
    ("SQL:tcmp100.C_SEL", "TBL:TCP_CMP", "READS"),
]
P_SCREEN = ["SCR:CMP01", "CTL:CmpCtl.save", "SVC:CmpSvc.save", "SQL:CmpMapper.insert", "TBL:TCP_CMP", "COL:TCP_CMP.ADVT_YN"]
P_BATCH = ["BAT:TCMP100", "SQL:tcmp100.C_SEL", "TBL:TCP_CMP"]
EVIDENCE_NODES = ["SCR:CMP01", "CTL:CmpCtl.save", "SVC:CmpSvc.save", "SQL:CmpMapper.insert", "TBL:TCP_CMP",
                  "COL:TCP_CMP.ADVT_YN", "BAT:TCMP100", "SQL:tcmp100.C_SEL"]


def ev(nid, kind="source"):
    return Evidence(node_id=nid, file_path=f"src/{nid.split(':', 1)[1]}", location="L1-9", summary=f"{nid} 요약",
                    source_kind=kind, content=f"{nid} 원문")


def make_state(paths=None, evidence_nodes=EVIDENCE_NODES, items=None, nodes=TOS, edges=TOS_EDGES, **exp_kw):
    paths = paths if paths is not None else [(1, P_SCREEN, StopReason.TARGET_REACHED), (2, P_BATCH, StopReason.TARGET_REACHED)]
    items = items or [
        ExploreItem(item_id=1, text="캠페인 화면에 비광고 여부 저장", change_type=ChangeType.DATA_ADD,
                    mentions=[Mention(kind=MentionKind.SCREEN, phrase="캠페인 화면")], target_role=R.TABLE,
                    direction=Direction.FORWARD_THEN_REVERSE, hop_cap=10),
        ExploreItem(item_id=2, text="전송 파일에 비광고 여부 추가", change_type=ChangeType.INTERFACE_FIELD_ADD,
                    mentions=[Mention(kind=MentionKind.INTERFACE, phrase="전송 파일")], target_role=R.ENTRY,
                    direction=Direction.REVERSE, hop_cap=5),
    ]
    state = AgentState(user_input=UserInput(request_text="캠페인 비광고 여부 전송", sm_part=SmPart.MARKETING))
    state.planner = PlannerOutput(
        structured_request=StructuredRequest(target="t", action="a", request_type=RequestType.CHANGE),
        complexity=Complexity.COMPLEX, complexity_reason="x", domains=[Domain.TOS], items=items, plan=[])
    state.explorer = ExplorerOutput(
        start_nodes=[], nodes=nodes, edges=[GraphEdge(from_id=a, to_id=b, type=t) for a, b, t in edges],
        paths=[ExplorePath(item_id=i, nodes=ns, direction=Direction.FORWARD, stop_reason=s) for i, ns, s in paths],
        **exp_kw)
    state.retriever = RetrieverOutput(evidence=[ev(n) for n in evidence_nodes],
                                      nodes_without_evidence=[n for n in EVIDENCE_NODES if n not in evidence_nodes])
    return state


class ScriptLLM:
    def __init__(self, draft=None, error=None):
        self.draft, self.error, self.users = draft, error, []

    def parse(self, system, user, schema):
        self.users.append(user)
        if self.error:
            raise self.error
        assert schema is SimulatorDraft and "Simulator" in system
        return self.draft, Usage(100, 20)


def area(a, impact=ImpactType.MODIFY, feas=Feasibility.FEASIBLE, diff=Difficulty.MINOR, **kw):
    return DraftArea(**{"area": a, "impact_type": impact, "feasibility": feas, "difficulty": diff, "difficulty_basis": "b",
                        "schedule_factors": [], "infeasible_reason": None, "alternatives": [], **kw})


def draft(areas=None, hops=None, drops=None, rva=None):
    areas = areas or [area(Area.SCREEN), area(Area.DB), area(Area.INTERFACE, diff=Difficulty.MAJOR), area(Area.BATCH)]
    return SimulatorDraft(hops=hops or [], areas=areas, requested_vs_alternative=rva, drop_paths=drops or [])


def by_area(out):
    return {a.area: a for a in out.area_impacts}


# --- 골격 · 영역 ----------------------------------------------------------------------------------


def test_skeleton_relations_evidence_and_ids():
    state = make_state(evidence_nodes=[n for n in EVIDENCE_NODES if n != "SVC:CmpSvc.save"],
                       paths=[(1, P_SCREEN, StopReason.TARGET_REACHED), (1, P_SCREEN, StopReason.HOP_CAP),
                              (2, P_BATCH, StopReason.TARGET_REACHED)])
    built = build_impact_paths(state.explorer.paths, state.explorer.edges, state.retriever.evidence)
    assert [p.path_id for p, _ in built] == ["P1-1", "P2-1"]          # 같은 노드 순서는 합침
    assert len(built[0][1]) == 2
    hops = built[0][0].hops
    assert [h.relation for h in hops] == ["ENTRY", "CALLS", "INVOKES", "EXECUTES", "WRITES", "HAS_COLUMN"]
    assert hops[0].evidence_ref == "src/CMP01#L1-9" and hops[0].confidence is Confidence.CONFIRMED
    assert hops[2].evidence_ref is None and hops[2].confidence is Confidence.INFERRED


def test_areas_by_role_tos_and_swing():
    nodes = {n.node_id: n for n in TOS}
    assert path_areas(P_SCREEN, nodes, set()) == [Area.SCREEN, Area.DB]
    assert path_areas(P_BATCH, nodes, {"BAT:TCMP100"}) == [Area.DB, Area.INTERFACE, Area.BATCH]
    swing = {n.node_id: n for n in [
        node("SCR:Z01", "Screen", R.ENTRY, Domain.SWING), node("TP:Svc.join", "TP", R.ENTRY_HANDLER, Domain.SWING),
        node("MOD:Mod.calc", "Module", R.LOGIC, Domain.SWING), node("DBIO:Sql.upd", "DBIO", R.DATA_ACCESS, Domain.SWING),
        node("BAT:ZB01", "Batch", R.ENTRY, Domain.SWING), node("TBL:TB_A", "Table", R.TABLE, Domain.SWING)]}
    assert path_areas(["SCR:Z01", "TP:Svc.join", "MOD:Mod.calc"], swing, set()) == [Area.SCREEN]
    assert path_areas(["BAT:ZB01", "MOD:Mod.calc", "DBIO:Sql.upd", "TBL:TB_A"], swing, set()) == [Area.DB, Area.BATCH]
    assert path_areas(["TP:Svc.join", "DBIO:Sql.upd"], swing, set()) == [Area.SCREEN, Area.DB]  # 진입 없이 TP 부터
    no_role = {"SCR:Z01": swing["SCR:Z01"].model_copy(update={"role": None})}
    assert path_areas(["SCR:Z01"], no_role, set()) == [Area.SCREEN]   # 역할이 비면 라벨로


# --- LLM 없음 -------------------------------------------------------------------------------------


def test_rule_only_output():
    state = make_state(evidence_nodes=[n for n in EVIDENCE_NODES if n != "SQL:tcmp100.C_SEL"],
                       unresolved=["[항목 2] 그래프 밖: 수신 시스템"], excluded_common=["CTL:CmmCd.get"],
                       item_results=[ItemExploreResult(item_id=2, target_role=R.ENTRY, direction=Direction.REVERSE,
                                                       reached=False, frontier=["TBL:TCP_CMP"], unmatched_mentions=["전송 파일"])])
    agent = SimulatorAgent(use_llm=False)
    out = agent.run(state)
    a = by_area(out)
    assert [x.area for x in out.area_impacts] == [Area.SCREEN, Area.DB, Area.INTERFACE, Area.BATCH]
    assert all(x.impact_type is ImpactType.MODIFY and x.feasibility is Feasibility.REVIEW for x in out.area_impacts)
    assert a[Area.DB].difficulty is Difficulty.MEDIUM and "컬럼 추가" in a[Area.DB].difficulty_basis  # data_add
    assert a[Area.INTERFACE].difficulty is Difficulty.MAJOR
    assert a[Area.SCREEN].confidence is Confidence.CONFIRMED and a[Area.BATCH].confidence is Confidence.INFERRED
    assert all(h.impact == "영향 검토 필요" for x in out.area_impacts for p in x.paths for h in p.hops)
    assert out.dropped_paths == [] and out.requested_vs_alternative is None and agent.last_usage is None
    limits = " | ".join(out.unverified_segments)
    for text in ["그래프 밖: 수신 시스템", "목표 역할(Entry)에 닿은 경로 없음", "hop 상한으로 끊긴 노드: TBL:TCP_CMP",
                 "노드를 찾지 못한 표현: 전송 파일", "공통 노드라 확장하지 않음: CTL:CmmCd.get", "근거(원문) 없음: SQL:tcmp100.C_SEL"]:
        assert text in limits


def test_rule_difficulty_by_area_count():
    items = [ExploreItem(item_id=1, text="화면 문구", change_type=ChangeType.SCREEN_CHANGE, mentions=[],
                         target_role=R.ENTRY_HANDLER, direction=Direction.FORWARD, hop_cap=5)]
    out = SimulatorAgent(use_llm=False).run(make_state(paths=[(1, P_SCREEN[:2], StopReason.TARGET_REACHED)], items=items))
    a = by_area(out)
    assert a[Area.SCREEN].difficulty is Difficulty.MINOR and a[Area.DB].impact_type is ImpactType.NONE
    assert requirement_difficulty(out.area_impacts) == (Difficulty.MINOR, "영역 중 최고 (screen: (규칙) 영역 기준 해당 없음 (상세 판단은 원문 확인 필요))")
    out = SimulatorAgent(use_llm=False).run(make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED)], items=items))
    assert by_area(out)[Area.SCREEN].difficulty is Difficulty.MINOR                 # 영역 난이도에 영역 수를 넣지 않음
    assert requirement_difficulty(out.area_impacts)[0] is Difficulty.MINOR          # Minor 영역 2개는 Minor (정답셋 L-1·L-3)


def test_rule_difficulty_column_spec_change():
    items = [ExploreItem(item_id=1, text="컬럼 길이 변경", change_type=ChangeType.COLUMN_SPEC_CHANGE, mentions=[],
                         target_role=R.ENTRY, direction=Direction.REVERSE, hop_cap=5),
             ExploreItem(item_id=2, text="저장 항목 추가", change_type=ChangeType.DATA_ADD, mentions=[],
                         target_role=R.TABLE, direction=Direction.FORWARD_THEN_REVERSE, hop_cap=10)]
    out = SimulatorAgent(use_llm=False).run(make_state(items=items))
    assert by_area(out)[Area.DB].difficulty is Difficulty.MAJOR                     # 길이·타입 변경이 더 무거움
    assert "길이·타입" in by_area(out)[Area.DB].difficulty_basis


@pytest.mark.parametrize(("case", "levels", "expected"), [
    ("L-1 배치 주기", {Area.DB: Difficulty.MINOR, Area.BATCH: Difficulty.MINOR}, Difficulty.MINOR),
    ("L-3 그리드 항목", {Area.SCREEN: Difficulty.MINOR, Area.DB: Difficulty.MINOR}, Difficulty.MINOR),
    ("M-3 컬럼 추가", {Area.SCREEN: Difficulty.MINOR, Area.DB: Difficulty.MEDIUM}, Difficulty.MEDIUM),
    ("H-1 3개 영역", {Area.SCREEN: Difficulty.MEDIUM, Area.DB: Difficulty.MINOR, Area.BATCH: Difficulty.MEDIUM},
     Difficulty.MAJOR),
    ("SC-001 인터페이스", {Area.INTERFACE: Difficulty.MAJOR, Area.BATCH: Difficulty.MEDIUM}, Difficulty.MAJOR),
])
def test_requirement_difficulty_ground_truth_shapes(case, levels, expected):
    """정답셋 영역 판정 모양 (설계서 1-2 대조표). levels 에 없는 영역은 변경 없음."""
    out = SimulatorAgent(use_llm=False).run(make_state())
    for a in out.area_impacts:
        a.impact_type = ImpactType.MODIFY if a.area in levels else ImpactType.REFERENCE_ONLY
        a.difficulty = levels.get(a.area, Difficulty.MINOR)
    assert requirement_difficulty(out.area_impacts)[0] is expected, case


def test_requirement_difficulty():
    out = SimulatorAgent(use_llm=False).run(make_state())                 # 4개 영역 변경
    assert requirement_difficulty(out.area_impacts) == (Difficulty.MAJOR, "변경 영역 4개 (3개 이상)")
    for a in out.area_impacts:
        if a.area in (Area.INTERFACE, Area.BATCH):
            a.impact_type = ImpactType.NONE
        else:
            a.difficulty = Difficulty.MAJOR if a.area is Area.DB else Difficulty.MINOR
    assert requirement_difficulty(out.area_impacts)[0] is Difficulty.MAJOR   # 2개 영역이지만 db 가 Major
    for a in out.area_impacts:
        a.impact_type = ImpactType.NONE
    assert requirement_difficulty(out.area_impacts) == (Difficulty.MINOR, "변경 영역 없음")


def test_empty_explorer():
    state = make_state(paths=[])
    out = SimulatorAgent(llm=ScriptLLM(draft())).run(state)   # 경로가 없으면 LLM 을 부르지 않음
    assert all(x.impact_type is ImpactType.NONE and x.paths == [] for x in out.area_impacts)


# --- LLM 판정 · 보정 ------------------------------------------------------------------------------


def test_llm_judgement_applied_and_ghost_nodes_ignored():
    llm = ScriptLLM(draft(
        hops=[DraftHop(path_id="P1-1", node="SCR:CMP01", impact="비광고 여부 체크박스 추가"),
              DraftHop(path_id="P1-1", node="SCR:GHOST", impact="없는 노드")],
        areas=[area(Area.SCREEN, diff=Difficulty.MEDIUM), area(Area.DB, impact=ImpactType.REFERENCE_ONLY),
               area(Area.INTERFACE, diff=Difficulty.MAJOR, schedule_factors=["수신 시스템 협의"]), area(Area.BATCH)],
        rva="요청안: 신규 컬럼 / 대안: ADVT_YN 반전"))
    agent = SimulatorAgent(llm=llm)
    out = agent.run(make_state())
    a = by_area(out)
    hops = a[Area.SCREEN].paths[0].hops
    assert hops[0].impact == "비광고 여부 체크박스 추가" and all(h.node != "SCR:GHOST" for h in hops)
    assert a[Area.DB].impact_type is ImpactType.REFERENCE_ONLY and a[Area.SCREEN].difficulty is Difficulty.MEDIUM
    assert a[Area.INTERFACE].schedule_factors == ["수신 시스템 협의"] and a[Area.INTERFACE].feasibility is Feasibility.FEASIBLE
    assert out.requested_vs_alternative.startswith("요청안") and agent.last_usage.input_tokens == 100
    user = llm.users[0]
    assert "[영역별 경로]" in user and "P1-1: SCR:CMP01(Screen, CMP01) -CALLS->" in user and "SVC:CmpSvc.save 원문" in user


def test_hop_impact_shared_node_filled_by_node():
    # 화면 경로(P1-1)와 배치 경로(P2-1)가 같은 SQL 을 지남. LLM 은 P1-1 에만 설명 → P2-1 에도 반영
    shared = ["BAT:TCMP100", "SQL:CmpMapper.insert", "TBL:TCP_CMP"]
    state = make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED), (2, shared, StopReason.TARGET_REACHED)],
                       edges=TOS_EDGES + [("BAT:TCMP100", "SQL:CmpMapper.insert", "EXECUTES")])
    out = SimulatorAgent(llm=ScriptLLM(draft(hops=[
        DraftHop(path_id="P1-1", node="SQL:CmpMapper.insert", impact="[변경] 컬럼 추가"),
        DraftHop(path_id="P2-1", node="TBL:TCP_CMP", impact="[참조] 구조 변경 없음"),
        DraftHop(path_id="P1-1", node="TBL:TCP_CMP", impact="[변경] 컬럼 추가 대상 테이블"),
    ]))).run(state)
    hops = {(p.path_id, h.node): h.impact for a in out.area_impacts for p in a.paths for h in p.hops}
    assert hops[("P2-1", "SQL:CmpMapper.insert")] == "[변경] 컬럼 추가"           # 노드 기준으로 채움
    assert hops[("P2-1", "TBL:TCP_CMP")] == "[참조] 구조 변경 없음"               # 경로별 설명이 있으면 그대로
    assert hops[("P1-1", "TBL:TCP_CMP")] == "[변경] 컬럼 추가 대상 테이블"
    assert hops[("P2-1", "BAT:TCMP100")] == "영향 검토 필요"                      # 설명이 없는 노드는 기본값


def test_hop_impact_prefers_change_for_same_node():
    out = SimulatorAgent(llm=ScriptLLM(draft(hops=[
        DraftHop(path_id="P9-9", node="SVC:CmpSvc.save", impact="[참조] 호출만"),     # 없는 경로 id
        DraftHop(path_id="P9-8", node="SVC:CmpSvc.save", impact="[변경] 저장 분기 추가"),
    ]))).run(make_state())
    hop = next(h for a in out.area_impacts for p in a.paths for h in p.hops if h.node == "SVC:CmpSvc.save")
    assert hop.impact == "[변경] 저장 분기 추가"


def test_feasible_without_evidence_becomes_review():
    # batch 경로(P2-1) 노드는 전부 근거 없음 → review / screen 경로는 일부만 근거 없음 → feasible 유지, 확신도는 inferred
    state = make_state(evidence_nodes=[n for n in EVIDENCE_NODES if n not in ("BAT:TCMP100", "SQL:tcmp100.C_SEL", "TBL:TCP_CMP")])
    out = SimulatorAgent(llm=ScriptLLM(draft())).run(state)
    a = by_area(out)
    assert a[Area.BATCH].feasibility is Feasibility.REVIEW
    assert a[Area.SCREEN].feasibility is Feasibility.FEASIBLE and a[Area.SCREEN].confidence is Confidence.INFERRED


def test_hard_without_alternative_becomes_review():
    alt = DraftAlternative(description="기존 컬럼 재사용", feasibility=Feasibility.FEASIBLE, difficulty=Difficulty.MINOR, pros_cons="p")
    out = SimulatorAgent(llm=ScriptLLM(draft(areas=[
        area(Area.SCREEN, feas=Feasibility.HARD, infeasible_reason="구조상 불가"),
        area(Area.DB, feas=Feasibility.HARD, infeasible_reason="길이 제한", alternatives=[alt]),
        area(Area.INTERFACE), area(Area.BATCH)]))).run(make_state())
    a = by_area(out)
    assert a[Area.SCREEN].feasibility is Feasibility.REVIEW and a[Area.DB].feasibility is Feasibility.HARD
    assert a[Area.DB].alternatives[0].description == "기존 컬럼 재사용"
    assert any("[screen] hard" in x for x in out.unverified_segments)


def test_llm_failure_falls_back_to_rules():
    agent = SimulatorAgent(llm=ScriptLLM(error=LLMError("timeout")))
    out = agent.run(make_state())
    assert all(x.feasibility is Feasibility.REVIEW for x in out.area_impacts if x.paths)
    assert any("LLM 판정 실패" in x for x in out.unverified_segments)


# --- 경로 제외 가드 --------------------------------------------------------------------------------

EXTRA = ["SCR:CMP01", "CTL:CmpCtl.save"]  # 항목 1 의 두 번째 경로 (목표 미도달)


def test_drop_accepted_when_guards_pass():
    state = make_state(paths=[(1, P_SCREEN, StopReason.TARGET_REACHED), (1, EXTRA, StopReason.DEAD_END),
                              (2, P_BATCH, StopReason.TARGET_REACHED)])
    out = SimulatorAgent(llm=ScriptLLM(draft(drops=[DraftDrop(path_id="P1-2", reason="조회 진입만, 저장과 무관")]))).run(state)
    assert [d.path.path_id for d in out.dropped_paths] == ["P1-2"] and out.dropped_paths[0].reason.startswith("조회")
    assert all(p.path_id != "P1-2" for x in out.area_impacts for p in x.paths)
    # KPI 는 드롭된 경로를 평가하지 않는다
    state.simulation = out
    assert "P1-2" not in {p.path_id for x in out.area_impacts for p in x.paths}
    assert KpiAgent.final_nodes(state) == list(dict.fromkeys(P_SCREEN + P_BATCH))


@pytest.mark.parametrize("case", ["target_reached_with_evidence", "confirmed_node", "last_path_of_item"])
def test_drop_rejected_by_guard(case):
    paths = [(1, P_SCREEN, StopReason.TARGET_REACHED), (1, EXTRA, StopReason.DEAD_END), (2, P_BATCH, StopReason.TARGET_REACHED)]
    drop = "P1-2"
    state = make_state(paths=paths)
    if case == "target_reached_with_evidence":
        drop = "P1-1"                                   # 목표 도달 + 마지막 노드 근거 있음
    elif case == "confirmed_node":
        state.planner.items[0].mentions[0].confirmed_node = "CTL:CmpCtl.save"
    else:
        drop = "P2-1"                                   # 항목 2 의 유일한 경로
        state.explorer.paths[2].stop_reason = StopReason.DEAD_END
    out = SimulatorAgent(llm=ScriptLLM(draft(drops=[DraftDrop(path_id=drop, reason="무관")]))).run(state)
    assert out.dropped_paths == []


# --- Supervisor 연결 · KPI -------------------------------------------------------------------------


class Fixed:
    def __init__(self, output):
        self.output = output

    def run(self, state):
        return self.output.model_copy(deep=True)


def test_supervisor_complex_flow_reaches_kpi():
    base = make_state()
    planner = base.planner.model_copy(update={"plan": [PlanStep(agent=a, purpose="p") for a in
                                                       (AgentName.EXPLORER, AgentName.RETRIEVER, AgentName.SIMULATOR, AgentName.VERIFIER)]})
    sup = Supervisor()
    sup.planner, sup.explorer, sup.retriever = Fixed(planner), Fixed(base.explorer), Fixed(base.retriever)
    sup.simulator = SimulatorAgent(use_llm=False)
    sup.verifier = Fixed(VerifierOutput(result=VerifyResult.PASS, checks=[]))
    sup.composer = Fixed(ComposerOutput(complexity=Complexity.COMPLEX, markdown="ok"))
    key = AnswerKey(req_id="T", answer_nodes=[AnswerNode(node_id="COL:TCP_CMP.ADVT_YN", type="Column"),
                                              AnswerNode(node_id="BAT:TCMP100", type="Batch")])
    sup.run(base.user_input, key)
    kpi = sup.last_state.kpi
    assert kpi.evaluable and kpi.missed_nodes == []
    assert [r.agent for r in sup.last_state.agent_runs][3] == "simulator"
