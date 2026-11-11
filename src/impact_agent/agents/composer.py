"""Composer: 분석 결과를 사용자 결과물로 만든다. 설계: docs/SE_agent_design.md 3장

- 단순 (Retriever → Composer): SimpleAnswer = 요약 결론 · 영향 경로 · 근거 (+ 확인 쿼리, 분석 한계)
  + 상세 분석 권장과 사유 (강한 탐색 신호만) · 참고 신호 (약한 신호, 권장 안 함). 둘 다 노드 ID 포함 →
  사용자가 요청하면 함께 reanalysis 후속 입력이 된다 (설계서 3-1·1-4)
- 복잡 (Verifier → Composer): ComplexReport = 요약 · 영역별 판정 · 일정 요인 · 경로 상세 · 분석 한계 · 검토 제외 경로 · 체크리스트
- 후속 답변 (Supervisor 가 Composer 로 바로 보냄): 체크리스트 답변 반영·stage 갱신, 조회 결과 요약,
  답변 때문에 경로가 바뀌어야 하면 reanalysis_request (사용자가 확인 후 reanalysis 후속 입력으로 보냄)
- 요청 시 (UserInput.output_options): 회신 메일 초안, PDF 저장 (save_report, 실패하면 MD 저장)
구조화 항목(경로·근거·판정표·분석 한계)은 코드가, 문장(결론·요약·체크리스트 질문·메일)만 LLM 이 쓴다.
markdown 은 항상 templates/ 로 렌더링한다. LLM 이 없거나 실패하면 규칙 문구로 쓴다.
"""

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from .. import config
from ..llm import LLM_FAILURES, LLMClient, Usage, analysis_llm
from ..rules import (
    AREA_ORDER,
    build_impact_paths,
    evidence_ref,
    interface_node_ids,
    node_areas,
    node_role,
    shared_nodes,
)
from ..schemas import (
    Area,
    AreaImpact,
    AreaVerdictRow,
    ChangeType,
    ChecklistItem,
    ChecklistOwner,
    ChecklistStatus,
    Complexity,
    ComplexReport,
    ComposerOutput,
    Confidence,
    Difficulty,
    Evidence,
    ExplorerOutput,
    Feasibility,
    FollowupInput,
    FollowupType,
    GuidanceQuery,
    ImpactPath,
    ImpactType,
    MailDraft,
    NodeRole,
    QueryResultSummary,
    ReanalysisRequest,
    SaveReportInput,
    SaveReportOutput,
    SimpleAnswer,
    SimpleEvidenceRow,
    VerifyResult,
)
from ..state import AgentState
from ..templates import render_complex, render_simple
from ..tools import save_report
from ..trace import clip, trace
from .base import BaseAgent
from .simulator import (
    CHANGE_TAG,
    REFERENCE_IMPACT,
    REFERENCE_TAG,
    reanalysis_request,
    requirement_difficulty,
    unverified_segments,
)
from .verifier import REPEATED, WARNING, unique_paths

# Supervisor 가 Composer 로 바로 보내는 후속 입력 (supervisor.COMPOSER_FOLLOWUPS 와 같은 값)
ANSWER_FOLLOWUPS = {FollowupType.BUSINESS_ANSWER, FollowupType.DEVELOPER_OPINION, FollowupType.QUERY_RESULT}
DETAIL_CHANGE_TYPES = {ChangeType.DATA_ADD, ChangeType.COLUMN_SPEC_CHANGE, ChangeType.QUERY_FIELD_ADD,
                       ChangeType.INTERFACE_FIELD_ADD}
WRITE_SQL = re.compile(r"\b(insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|exec|execute|call)\b", re.IGNORECASE)
MAX_SCHEDULE_FACTORS = 8  # 레포트 전체 일정 영향 요인 수
MAX_PATH_SUMMARY = 3  # 판정표 한 칸에 보여 줄 경로 수
MAX_EVIDENCE_NAMES = 5
MAX_CONTEXT_EVIDENCE = 40
MAX_DETAIL_REASONS = 5  # 상세 분석 권장 사유 수
LLM_DETAIL_REASON = "원문에서 변경 요소가 보이나 탐색 신호가 없어 상세 분석은 권장하지 않음 (Agent 판단)"
NO_TAG_NOTE = "LLM 응답이 없어 노드별 [변경] 표시를 하지 못함 - 공통 모듈·여러 진입점 신호로는 권장 여부를 판단하지 못함"
MAX_CLAIMS = 10  # 단순 응답 LLM 이 쓰는 근거 문장 수
# 상세 실행 로그 섹션: LLM 초안 → 코드 검증·조립 과정(버린 것·출처·권장 판단)을 남긴다
LLM_SECTION = "동작 · LLM 작성"
GUARD_SECTION = "동작 · 출력 검증 (LLM 초안 → 코드 조립)"
DETAIL_SECTION = "동작 · 상세 분석 권장 판단"
CHECKLIST_SECTION = "동작 · 체크리스트 조립"


# --- LLM 응답 형식 (Structured Outputs 호환: 기본값 없이 모든 필드 명시) ---------------------


class DraftClaim(BaseModel):
    node_id: str
    claim: str = Field(description="이 근거로 확인되는 사실 한 문장")


class DraftQuery(BaseModel):
    purpose: str
    sql: str = Field(description="읽기 전용 SELECT 한 문장")


class DraftChecklistItem(BaseModel):
    owner: ChecklistOwner
    question: str
    related_area: Area | None


class DraftMail(BaseModel):
    subject: str
    body: str


class DraftChange(BaseModel):
    node: str = Field(description="[경로 노드] 에 있는 노드 ID 그대로")
    impact: str = Field(description="이 요건으로 이 노드에서 바뀌는 것 한 문장")


class SimpleDraft(BaseModel):
    conclusion_lines: list[str] = Field(description="3~5줄. 결론 먼저, 노드명·테이블명을 붙여 쓴다")
    changes: list[DraftChange] = Field(description="이 요건으로 코드·데이터를 고쳐야 하는 노드 빠짐없이, 노드당 한 번. 나머지는 [참조]")
    claims: list[DraftClaim] = Field(description="[변경] 노드와 결론에 쓴 노드의 근거만, 10개 이내")
    check_queries: list[DraftQuery] = Field(description="운영 문의일 때 확인용 SQL. 아니면 []")
    inquiry_contacts: list[str] = Field(description="문의가 필요한 담당·부서. 없으면 []")
    detail_analysis_recommended: bool = Field(description="원문에서 변경 요소가 보이면 true (참고 신호로만 쓰임)")
    mail: DraftMail | None = Field(description="[출력 요청] 에 mail_draft 가 있을 때만. 아니면 null")


class ComplexDraft(BaseModel):
    summary_conclusion: str = Field(description="가능 여부·난이도·핵심 영향 3~5문장")
    checklist: list[DraftChecklistItem] = Field(description="[이미 포함된 확인 항목] 과 겹치지 않게")
    mail: DraftMail | None = Field(description="[출력 요청] 에 mail_draft 가 있을 때만. 아니면 null")


class DraftQuerySummary(BaseModel):
    code_meanings: list[str]
    main_causes: list[str]
    inquiry_items: list[str]


class FollowupDraft(BaseModel):
    needs_reanalysis: bool = Field(description="답변 때문에 영향 경로 자체(탐색 대상·가정)가 바뀌어야 하면 true")
    reason: str | None
    changed_assumptions: list[str]
    query_summary: DraftQuerySummary | None = Field(description="조회 결과(query_result)일 때만. 아니면 null")


# --- 에이전트 ------------------------------------------------------------------------------------


class ComposerAgent(BaseAgent[ComposerOutput]):
    name = "composer"
    output_model = ComposerOutput

    def __init__(
        self,
        llm: LLMClient | None = None,
        use_llm: bool = True,
        saver: Callable[[SaveReportInput], SaveReportOutput] = save_report,
    ) -> None:
        self._llm = llm
        self._llm_checked = llm is not None or not use_llm
        self.saver = saver
        self.last_usage: Usage | None = None
        self.notes: list[str] = []

    @property
    def llm(self) -> LLMClient | None:
        """문장 작성 LLM. 주입하지 않았으면 .env 설정으로 한 번 만든다 (키가 없으면 None → 규칙 문구)."""
        if not self._llm_checked:
            self._llm, self._llm_checked = analysis_llm(), True
        return self._llm

    def run(self, state: AgentState) -> ComposerOutput:
        self.last_usage, self.notes = None, []
        last = state.followups[-1] if state.followups else None
        if last is not None and last.type in ANSWER_FOLLOWUPS and state.composer is not None:
            out = self._followup(state, last)
        elif state.planner and state.planner.complexity is Complexity.COMPLEX and state.simulation is not None:
            out = self._complex(state)
        else:
            out = self._simple(state)
        self._save_pdf(state, out)
        return out

    def _draft(self, schema: type[BaseModel], user: str) -> BaseModel | None:
        if self.llm is None:
            trace("LLM 없음 → 규칙 문구로 작성", section=LLM_SECTION, label="생략")
            return None
        trace(f"{schema.__name__} (Structured Output, 문장 필드만) · 입력 {len(user):,}자", section=LLM_SECTION, label="입력")
        try:
            draft, self.last_usage = self.llm.parse(self.system_prompt, user, schema)
        except LLM_FAILURES as exc:  # LLM 실패는 규칙 문구로 대체
            self.notes.append(f"LLM 작성 실패로 규칙 문구 사용: {exc}")
            trace(f"실패 → 규칙 문구로 대체: {clip(exc, 150)}", section=LLM_SECTION, label="대체")
            return None
        return draft

    # --- 단순 ------------------------------------------------------------------------------------

    def _simple(self, state: AgentState) -> ComposerOutput:
        exp, ret = state.explorer, state.retriever
        evidence = ret.evidence if ret else []
        paths = [p for p, _ in build_impact_paths(exp.paths if exp else [], exp.edges if exp else [], evidence)]
        draft: SimpleDraft | None = self._draft(SimpleDraft, simple_message(state, paths))
        if draft is not None:
            on_path = {h.node for p in paths for h in p.hops}
            for c in draft.changes:
                trace(f"{c.node} — {clip(c.impact.strip().removeprefix(CHANGE_TAG).strip(), 220)}"
                      + ("" if c.node in on_path else " → 경로 밖 노드라 버림"), section=GUARD_SECTION,
                      label=CHANGE_TAG if c.node in on_path else "버림")
            trace(f"경로 {len(paths)}개 · 노드 {len(on_path)}개 중 [변경] {len({c.node for c in draft.changes} & on_path)}개, 나머지 [참조]",
                  section=GUARD_SECTION, label="표시")
            apply_changes(draft.changes, paths)
            paths = changed_first(paths)
            if len(draft.claims) > MAX_CLAIMS:
                trace(f"근거 문장 {len(draft.claims)}개 중 앞 {MAX_CLAIMS}개만 사용", section=GUARD_SECTION, label="근거 문장")
        claims = {c.node_id: c.claim for c in draft.claims[:MAX_CLAIMS]} if draft else {}
        reasons, notes = detail_reasons(state, paths)
        if draft is None:  # [변경] 표시가 없으면 노드 신호가 조용히 빠지므로 알린다
            notes.append(NO_TAG_NOTE)
        elif not reasons and draft.detail_analysis_recommended:  # LLM 판단만으로는 권장하지 않음
            notes.append(LLM_DETAIL_REASON)
        for x in reasons:
            trace(x, section=DETAIL_SECTION, label="강한 신호")
        for x in notes:
            trace(x, section=DETAIL_SECTION, label="약한 신호")
        llm_said = f"LLM {'권장' if draft and draft.detail_analysis_recommended else '권장 안 함'}" if draft else "LLM 없음"
        trace(f"강한 신호 {len(reasons)} · 약한 신호 {len(notes)} · {llm_said} → "
              + ("권장 (강한 신호가 있어서)" if reasons else "권장 안 함 (강한 신호 없음, 약한 신호는 참고로만 표시)"),
              section=DETAIL_SECTION, label="결정")
        if draft is not None:
            raw = [x for x in draft.conclusion_lines if x.strip()]
            if len(raw) < 3:
                trace(f"LLM 결론 {len(raw)}줄 → 규칙 문구로 3줄까지 채움", section=GUARD_SECTION, label="결론")
        answer = SimpleAnswer(
            conclusion_lines=fit_lines(draft.conclusion_lines if draft else [], rule_conclusion(state, paths, evidence)),
            impact_paths=paths,
            evidence_table=evidence_rows(ordered_evidence(evidence, paths), claims),
            check_queries=safe_queries(draft.check_queries, log=True) if draft else [],
            inquiry_contacts=draft.inquiry_contacts if draft else [],
            analysis_limits=unverified_segments(state, paths) + self.notes,
            detail_analysis_recommended=bool(reasons),
            detail_analysis_reasons=reasons,
            detail_analysis_notes=notes,
        )
        return ComposerOutput(
            complexity=state.planner.complexity if state.planner else Complexity.SIMPLE,
            simple_answer=answer,
            mail_draft=mail_draft(state, draft.mail if draft else None, answer.conclusion_lines, [], []),
            markdown=render_simple(answer),
        )

    # --- 복잡 ------------------------------------------------------------------------------------

    def _complex(self, state: AgentState) -> ComposerOutput:
        sim = state.simulation
        base_items = required_checklist(state)
        draft: ComplexDraft | None = self._draft(ComplexDraft, complex_message(state, base_items))
        extra = [(d.owner, d.question, d.related_area) for d in draft.checklist] if draft else rule_checklist(sim.area_impacts)
        level, basis = requirement_difficulty(sim.area_impacts)
        trace(f"요건 전체 난이도 {level.value} ({clip(basis, 120)}) - 코드 계산값을 LLM 에 주고 요약에 그대로 쓰게 함",
              section=GUARD_SECTION, label="난이도")
        if draft is None or not draft.summary_conclusion.strip():
            trace("LLM 요약 없음 → 규칙 요약 (영역·가능 여부·난이도 나열)", section=GUARD_SECTION, label="요약")
        trace(f"규칙 {len(base_items)}건 (요건 미확정·검토 제외 경로·공유 노드·공통 모듈, LLM 과 무관하게 항상) + "
              f"{'LLM' if draft else '규칙 대체'} {len(extra)}건", section=CHECKLIST_SECTION, label="출처")
        for o, q, _ in base_items:
            trace(clip(q, 200), section=CHECKLIST_SECTION, label=f"규칙 {o.value}")
        for o, q, _ in extra:
            trace(clip(q, 200), section=CHECKLIST_SECTION, label=f"{'LLM' if draft else '규칙'} {o.value}")
        merged = number_checklist(base_items + extra)
        if dup := len([q for _, q, _ in base_items + extra if q.strip()]) - len(merged):
            trace(f"같은 질문 {dup}건 제거 → 업무 B-n · 개발 D-n 번호 {len(merged)}건", section=CHECKLIST_SECTION, label="정리")
        report = ComplexReport(
            summary_conclusion=with_flow_note(state, (draft.summary_conclusion.strip() if draft else "") or rule_summary(sim.area_impacts)),
            area_verdicts=area_rows(sim.area_impacts),
            schedule_factors=merge_schedule_factors(sim.area_impacts),
            path_details=unique_paths(state),
            analysis_limits=complex_limits(state) + self.notes,
            dropped_paths=sim.dropped_paths,
            checklist=merged,
        )
        business = [c.question for c in report.checklist if c.owner is ChecklistOwner.BUSINESS]
        return ComposerOutput(
            complexity=Complexity.COMPLEX,
            complex_report=report,
            mail_draft=mail_draft(state, draft.mail if draft else None, [report.summary_conclusion], report.area_verdicts,
                                  business),
            markdown=render_complex(report),
        )

    # --- 후속 답변 --------------------------------------------------------------------------------

    def _followup(self, state: AgentState, fu: FollowupInput) -> ComposerOutput:
        out = state.composer.model_copy(deep=True)
        draft: FollowupDraft | None = self._draft(FollowupDraft, followup_message(state, fu))
        if out.complex_report is not None:
            r = out.complex_report
            for c in r.checklist:
                if c.item_id in fu.target_item_ids:
                    c.answer, c.status = fu.content, ChecklistStatus.CONFIRMED
            done = bool(r.checklist) and all(c.status is ChecklistStatus.CONFIRMED for c in r.checklist)
            r.stage = "final_for_dev" if done else "updated"
            r.analysis_limits += self.notes
            out.markdown = render_complex(r)
        if out.simple_answer is not None:
            s = out.simple_answer
            if fu.type is FollowupType.QUERY_RESULT:
                s.query_result_summary = (QueryResultSummary(**draft.query_summary.model_dump()) if draft and draft.query_summary
                                          else QueryResultSummary(inquiry_items=[f"조회 결과 검토 필요: {fu.content.splitlines()[0]}"]))
            s.analysis_limits += self.notes
            out.markdown = render_simple(s)
        out.reanalysis_request = (ReanalysisRequest(reason=draft.reason, changed_assumptions=draft.changed_assumptions)
                                  if draft and draft.needs_reanalysis and draft.reason else None)
        if out.complex_report is not None:
            trace(f"답변 반영 {', '.join(fu.target_item_ids) or '-'} → 단계 {out.complex_report.stage}", section=GUARD_SECTION, label="체크리스트")
        trace(f"재분석 필요: {out.reanalysis_request.reason} (바뀐 가정 {out.reanalysis_request.changed_assumptions})"
              if out.reanalysis_request else "재분석 필요 없음 (답변이 기존 판단을 확정·보완)", section=GUARD_SECTION, label="재분석 판단")
        if out.reanalysis_request:
            out.markdown += (f"\n> 재분석 필요: {out.reanalysis_request.reason} "
                             f"(바뀐 가정: {', '.join(out.reanalysis_request.changed_assumptions) or '-'})\n")
        return out

    # --- PDF -------------------------------------------------------------------------------------

    def _save_pdf(self, state: AgentState, out: ComposerOutput) -> None:
        if "pdf" not in state.user_input.output_options:
            return
        try:
            out.report_file_path = self.saver(SaveReportInput(content=out.markdown, format="pdf")).file_path
        except (ImportError, OSError, NotImplementedError) as exc:  # 변환 패키지·폰트가 없으면 MD 로 저장
            note = f"PDF 변환 불가로 MD 로 저장: {exc}"
            if out.complex_report is not None:
                out.complex_report.analysis_limits.append(note)
                out.markdown = render_complex(out.complex_report)
            elif out.simple_answer is not None:
                out.simple_answer.analysis_limits.append(note)
                out.markdown = render_simple(out.simple_answer)
            out.report_file_path = self.saver(SaveReportInput(content=out.markdown, format="md")).file_path


# --- 구조화 항목 (코드) ----------------------------------------------------------------------------


def fit_lines(lines: list[str], fallback: list[str]) -> list[str]:
    """결론 3~5줄로 맞춘다 (모자라면 규칙 문구로 채움)."""
    lines = [x.strip() for x in lines if x.strip()]
    for x in fallback:
        if len(lines) >= 3:
            break
        if x not in lines:
            lines.append(x)
    return lines[:5]


def path_summary(p: ImpactPath) -> str:
    nodes = [h.node for h in p.hops]
    return " → ".join(nodes) if len(nodes) <= 3 else f"{nodes[0]} → … → {nodes[-1]}"


def rule_conclusion(state: AgentState, paths: list[ImpactPath], evidence: list[Evidence]) -> list[str]:
    req = state.planner.structured_request if state.planner else None
    nodes = {h.node for p in paths for h in p.hops}
    with_ev = nodes & {e.node_id for e in evidence}
    lines = [f"요청: {req.target} - {req.action}" if req else f"요청: {state.user_input.request_text.strip()[:80]}",
             f"영향 경로 {len(paths)}개, 노드 {len(nodes)}개 중 원문 근거 확보 {len(with_ev)}개"]
    lines.append(f"주요 경로: {'; '.join(path_summary(p) for p in paths[:2])}" if paths else "탐색된 영향 경로가 없습니다 (분석 한계 참고)")
    lines.append("상세 내용은 아래 영향 경로·근거를 확인해 주세요.")
    return lines


def apply_changes(changes: list[DraftChange], paths: list[ImpactPath]) -> None:
    """LLM 이 고른 노드는 [변경] + 영향 문장, 나머지 hop 은 [참조] 로 채운다 (노드당 한 문장을 모든 경로에 같이 씀).

    경로 밖 노드는 무시한다. LLM 이 문장 앞에 표시를 붙여 와도 한 번만 붙게 뗀다.
    """
    by_node = {c.node: c.impact.strip().removeprefix(CHANGE_TAG).strip() for c in changes if c.impact.strip()}
    for p in paths:
        for h in p.hops:
            h.impact = f"{CHANGE_TAG} {by_node[h.node]}" if h.node in by_node else f"{REFERENCE_TAG} {REFERENCE_IMPACT}"


def changed_first(paths: list[ImpactPath]) -> list[ImpactPath]:
    """[변경] 노드를 지나는 경로를 앞으로 (원래 순서 유지). 결과물은 앞쪽 경로만 펼쳐 보여 준다."""
    return sorted(paths, key=lambda p: not any(h.impact.startswith(CHANGE_TAG) for h in p.hops))


def ordered_evidence(evidence: list[Evidence], paths: list[ImpactPath]) -> list[Evidence]:
    """[변경] 노드 근거 → [변경] 경로 위 노드 근거 → 나머지 (원래 순서 유지)."""
    changed = {h.node for p in paths for h in p.hops if h.impact.startswith(CHANGE_TAG)}
    on_changed = {h.node for p in paths if any(h.node in changed for h in p.hops) for h in p.hops}
    return sorted(evidence, key=lambda e: 0 if e.node_id in changed else 1 if e.node_id in on_changed else 2)


def evidence_rows(evidence: list[Evidence], claims: dict[str, str]) -> list[SimpleEvidenceRow]:
    rows, seen = [], set()
    for e in evidence:
        key = (e.node_id, e.file_path, e.location)
        if key in seen:
            continue
        seen.add(key)
        rows.append(SimpleEvidenceRow(claim=claims.get(e.node_id or "", e.summary), evidence=e.file_path, location=e.location,
                                      confidence=Confidence.CONFIRMED))
    return rows


def safe_queries(queries: list[DraftQuery], log: bool = False) -> list[GuidanceQuery]:
    """읽기 전용 SELECT 한 문장만 통과 (쓰기·DDL·여러 문장은 버림). log 면 통과·버림을 실행 로그에 남긴다."""
    out = []
    for q in queries:
        sql = q.sql.strip().rstrip(";").strip()
        if re.match(r"(select|with)\b", sql, re.IGNORECASE) and ";" not in sql and not WRITE_SQL.search(sql):
            out.append(GuidanceQuery(purpose=q.purpose, sql=sql))
            if log:
                trace(f"{clip(q.purpose, 80)}: {clip(sql, 160)}", section=GUARD_SECTION, label="쿼리 통과")
        elif log:
            why = ("쓰기·DDL 키워드 " + WRITE_SQL.search(sql).group(0) if WRITE_SQL.search(sql)
                   else "여러 문장" if ";" in sql else "SELECT 로 시작하지 않음")
            trace(f"{clip(q.purpose, 80)}: {clip(sql, 120)} → 버림 ({why})", section=GUARD_SECTION, label="쿼리 차단")
    return out


def detail_reasons(state: AgentState, paths: list[ImpactPath]) -> tuple[list[str], list[str]]:
    """단순 응답의 상세 분석 권장 사유(강한 신호)와 참고 사항(약한 신호) (설계서 3-1).

    강한 신호가 있을 때만 권장하고, 약한 신호는 참고 신호(detail_analysis_notes)로 남긴다. 공통 모듈·여러 진입점 신호는
    [변경] 노드에서만 센다 ([참조] 노드는 거쳐 가기만 하므로 신호가 아님). 신호마다 노드 ID 를 그대로 넣는다: 사용자가
    "상세 분석 요청"을 누르면 사유와 참고 신호가 함께 reanalysis 후속 입력이 되고, Simulator·Verifier 가 문장 속 노드 ID 를
    확인 대상으로 쓴다 (simulator.reanalysis_nodes).

    강한 신호: [변경] 노드가 공통 모듈이거나 여러 진입점에서 호출됨 · 요건 항목 2개 이상이 2개 영역 이상을 변경 ·
              구조 변경 요건(DETAIL_CHANGE_TYPES)의 신규 후보·그래프 밖 연계
    약한 신호: 구조 변경 유형만 있음 · 탐색에서 제외된 공통 모듈 · 구조 변경이 아닌 요건의 신규 후보·그래프 밖 항목
    """
    exp = state.explorer
    nodes = {n.node_id: n for n in exp.nodes} if exp else {}
    items = state.planner.items if state.planner else []
    structural = [it for it in items if it.change_type in DETAIL_CHANGE_TYPES]
    changed = list(dict.fromkeys(h.node for p in paths for h in p.hops if h.impact.startswith(CHANGE_TAG)))
    shared = shared_nodes(exp.paths if exp else [], nodes)
    strong: list[str] = []
    weak = [f"[항목 {it.item_id}] 저장·조회·연계 항목이 바뀌는 요건 ({it.change_type.value})" for it in structural]
    for n in changed:
        if n in nodes and nodes[n].properties.get("is_common"):
            strong.append(f"공통 모듈 {n} 이 변경 대상이라 다른 화면·처리 영향 확인 필요")
        elif n in shared and shared[n].startswith("진입점"):
            strong.append(f"변경 대상 {n} 을 여러 진입점에서 호출 ({shared[n]})")
    areas = changed_areas(state, paths)
    if len(items) >= 2 and len(areas) >= 2:
        strong.append(f"요건 항목 {len(items)}개가 {'·'.join(a.value for a in areas)} 영역을 함께 변경 "
                      f"({', '.join(changed[:MAX_EVIDENCE_NAMES])})")
    weak += [f"공통 모듈 {n} 은 탐색에서 제외됨 - 이 요건으로 바뀌는지 확인" for n in (exp.excluded_common if exp else [])]
    for kind, text in unresolved_signals(exp):
        if kind == "new_candidate":
            (strong if structural else weak).append(f"그래프에 없는 항목이 있어 신규 개발 여부 확인 필요: {text}")
        else:
            (strong if structural else weak).append(f"적재 범위 밖 연계 확인 필요: {text}")
    return list(dict.fromkeys(strong))[:MAX_DETAIL_REASONS], list(dict.fromkeys(weak))[:MAX_DETAIL_REASONS]


def unresolved_signals(exp: ExplorerOutput | None) -> list[tuple[str, str]]:
    """상세 분석 권장 신호가 되는 미해결 (종류, 문장): 신규 후보(new_candidate) · 그래프 밖(out_of_graph).

    Explorer 의 unresolved_items 종류로 가른다 (문구가 바뀌어도 신호가 사라지지 않게). 종류가 없는 이전 결과
    (unresolved 문장만 있는 저장 상태)는 문구("신규 후보"·"그래프 밖")로 가른다.
    """
    if exp is None:
        return []
    if exp.unresolved_items:
        return [(u.kind, u.text) for u in exp.unresolved_items if u.kind in ("new_candidate", "out_of_graph")]
    return [("new_candidate" if "신규 후보" in u else "out_of_graph", u) for u in exp.unresolved
            if "신규 후보" in u or "그래프 밖" in u]


def changed_areas(state: AgentState, paths: list[ImpactPath]) -> list[Area]:
    """[변경] 노드가 걸친 영역 (AREA_ORDER 순, Simulator 와 같은 rules.node_areas 기준)."""
    nodes = {n.node_id: n for n in state.explorer.nodes} if state.explorer else {}
    iface = interface_node_ids(nodes, state.retriever.evidence if state.retriever else [])
    found: set[Area] = set()
    for p in paths:
        for h, ar in zip(p.hops, node_areas([h.node for h in p.hops], nodes, iface)):
            if h.impact.startswith(CHANGE_TAG):
                found |= ar
    return [a for a in AREA_ORDER if a in found]


def with_flow_note(state: AgentState, summary: str) -> str:
    """복잡 요건 요약 끝에 복잡도·난이도 관계를 덧붙인다 (설계서 3-2). 값(복잡도·난이도)은 바꾸지 않는다."""
    notes = []
    if reanalysis_request(state) is not None:
        notes.append("사용자 상세 분석 요청에 따른 재분석 결과입니다.")
    level, _ = requirement_difficulty(state.simulation.area_impacts)
    changed = any(a.impact_type in (ImpactType.ADD, ImpactType.MODIFY) for a in state.simulation.area_impacts)
    if changed and level is Difficulty.MINOR:
        notes.append("복잡 요건으로 분석했으나 확인 결과 변경 범위가 작습니다 (요건 전체 난이도 Minor).")
    return " ".join([summary.strip()] + notes) if notes else summary


def entry_points(state: AgentState, node_id: str) -> list[str]:
    """Explorer 경로에서 그 노드를 지나는 진입점(경로 첫 노드가 Entry)."""
    exp = state.explorer
    nodes = {n.node_id: n for n in exp.nodes} if exp else {}
    return list(dict.fromkeys(p.nodes[0] for p in (exp.paths if exp else [])
                              if node_id in p.nodes[1:] and node_role(nodes.get(p.nodes[0])) is NodeRole.ENTRY))


def area_rows(areas: list[AreaImpact]) -> list[AreaVerdictRow]:
    rows = []
    for a in areas:
        if a.impact_type is ImpactType.NONE and not a.paths:
            continue
        summary = "; ".join(path_summary(p) for p in a.paths[:MAX_PATH_SUMMARY])
        if len(a.paths) > MAX_PATH_SUMMARY:
            summary += f" 외 {len(a.paths) - MAX_PATH_SUMMARY}개"
        refs = list(dict.fromkeys(h.evidence_ref for p in a.paths for h in p.hops if h.evidence_ref))
        rows.append(AreaVerdictRow(
            area=a.area, impact_path_summary=summary or f"{a.impact_type.value} (그래프 경로 없음)", feasibility=a.feasibility,
            difficulty=a.difficulty, alternative=a.alternatives[0].description if a.alternatives else None,
            confidence=a.confidence, evidence_names=refs[:MAX_EVIDENCE_NAMES],
        ))
    return rows


def rule_summary(areas: list[AreaImpact]) -> str:
    changed = [a for a in areas if a.impact_type in (ImpactType.ADD, ImpactType.MODIFY)]
    if not changed:
        return "변경이 필요한 영역을 찾지 못했습니다. 분석 한계와 체크리스트를 확인해 주세요."
    feas = {a.feasibility for a in changed}
    overall = (Feasibility.HARD if Feasibility.HARD in feas else Feasibility.REVIEW if Feasibility.REVIEW in feas
               else Feasibility.FEASIBLE)
    level, basis = requirement_difficulty(areas)
    parts = ", ".join(f"{a.area.value}({a.impact_type.value}·{a.feasibility.value}·{a.difficulty.value})" for a in changed)
    return f"변경 영역: {parts}. 요건 전체 난이도 {level.value} ({basis}), 구현 가능 여부 {overall.value}."


def merge_schedule_factors(areas: list[AreaImpact]) -> list[str]:
    factors = list(dict.fromkeys(f for a in areas for f in a.schedule_factors))
    if len(factors) <= MAX_SCHEDULE_FACTORS:
        return factors
    return factors[:MAX_SCHEDULE_FACTORS] + [f"외 {len(factors) - MAX_SCHEDULE_FACTORS}건 (영역별 판정 참고)"]


def complex_limits(state: AgentState) -> list[str]:
    out = list(state.simulation.unverified_segments)
    v = state.verifier
    if v is not None:
        out += [c.note for c in v.checks if c.note.startswith((REPEATED, WARNING))]
        if v.result is VerifyResult.NEEDS_SUPPLEMENT:
            out.append(f"재계획 한도({config.MAX_REPLAN}회) 안에서 보완하지 못함: {v.reason}")
    return list(dict.fromkeys(out))


def required_checklist(state: AgentState) -> list[tuple[ChecklistOwner, str, Area | None]]:
    """LLM 과 관계없이 항상 넣는 확인 항목: 요건 미확정 사항(업무), 검토 제외 경로·[변경] 공유 노드·탐색 제외 공통 모듈(개발)."""
    items = [(ChecklistOwner.BUSINESS, f"{o.item} ({o.reason})", None)
             for o in (state.planner.structured_request.open_items if state.planner else [])]
    for d in state.simulation.dropped_paths if state.simulation else []:
        question = f"검토 제외 경로 {d.path.path_id} ({path_summary(d.path)}) 가 실제로 무관한지 확인 - 제외 사유: {d.reason}"
        items.append((ChecklistOwner.DEVELOPER, question, None))
    exp = state.explorer
    nodes = {n.node_id: n for n in exp.nodes} if exp else {}
    shared = shared_nodes(exp.paths if exp else [], nodes)
    changed = dict.fromkeys(h.node for p in (unique_paths(state) if state.simulation else []) for h in p.hops
                            if h.node in shared and h.impact.startswith(CHANGE_TAG))
    for n in changed:  # 공유 노드 변경 → 함께 영향받는 진입점 (테스트 범위)
        entries = entry_points(state, n)
        where = f"진입점 {', '.join(entries)}" if len(entries) >= 2 else shared[n]
        items.append((ChecklistOwner.DEVELOPER, f"공유 노드 {n} 변경 - 함께 영향받는 {where} 의 테스트 범위 확인", None))
    for n in exp.excluded_common if exp else []:  # 탐색에서 제외된 공통 모듈
        items.append((ChecklistOwner.DEVELOPER, f"공통 모듈 {n} 은 탐색에서 제외됨 - 이 요건으로 바뀌는지, 다른 화면·처리 영향 확인", None))
    return items


def rule_checklist(areas: list[AreaImpact]) -> list[tuple[ChecklistOwner, str, Area | None]]:
    """LLM 이 없을 때: 근거 부족 영역(개발), 요청안·대안 선택(업무)."""
    items = []
    for a in areas:
        if a.feasibility is Feasibility.REVIEW and a.impact_type in (ImpactType.ADD, ImpactType.MODIFY):
            items.append((ChecklistOwner.DEVELOPER, f"{a.area.value} 영역 구현 가능 여부 확인 (원문 근거 부족)", a.area))
        if a.alternatives:
            alts = " / ".join(x.description for x in a.alternatives)
            items.append((ChecklistOwner.BUSINESS, f"{a.area.value} 영역: 요청안과 대안 중 선택 ({alts})", a.area))
    return items


def number_checklist(items: list[tuple[ChecklistOwner, str, Area | None]]) -> list[ChecklistItem]:
    """중복 질문 제거 후 업무 B-1.. / 개발 D-1.. 번호."""
    out, seen, count = [], set(), {ChecklistOwner.BUSINESS: 0, ChecklistOwner.DEVELOPER: 0}
    for owner, question, area in items:
        q = question.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        count[owner] += 1
        prefix = "B" if owner is ChecklistOwner.BUSINESS else "D"
        out.append(ChecklistItem(item_id=f"{prefix}-{count[owner]}", owner=owner, question=q, related_area=area))
    return out


def mail_draft(state: AgentState, draft: DraftMail | None, conclusion: list[str], rows: list[AreaVerdictRow],
               business: list[str]) -> MailDraft | None:
    """회신 메일 초안 (요청 시). 결론 → 영향 영역 → 확인 요청 순서. 수신자는 비워 둔다."""
    if "mail_draft" not in state.user_input.output_options:
        return None
    if draft is not None:
        return MailDraft(subject=draft.subject, body=draft.body)
    target = state.planner.structured_request.target if state.planner else state.user_input.request_text.strip()[:40]
    lines = ["안녕하세요.", "", "요청하신 건의 영향도 분석 결과를 공유드립니다.", "", "[결론]"] + [f"- {x}" for x in conclusion]
    if rows:
        lines += ["", "[영향 영역]"] + [f"- {r.area.value}: {r.feasibility.value} / {r.difficulty.value}" for r in rows]
    if business:
        lines += ["", "[확인 요청]"] + [f"- {q}" for q in business]
    lines += ["", "감사합니다."]
    return MailDraft(subject=f"[영향도 분석] {target}", body="\n".join(lines))


# --- LLM 입력 ------------------------------------------------------------------------------------


def _request_lines(state: AgentState) -> list[str]:
    lines = ["[요청]", state.user_input.request_text.strip()]
    if state.planner:
        lines += ["", "[요건 항목]"] + [f"- 항목 {it.item_id} ({it.change_type.value}) {it.text}" for it in state.planner.items]
    lines += ["", f"[출력 요청] {', '.join(state.user_input.output_options) or '없음'}"]
    return lines


def _evidence_lines(state: AgentState) -> list[str]:
    evidence = state.retriever.evidence if state.retriever else []
    return ["", "[근거]"] + [f"- {e.node_id} {evidence_ref(e)} ({e.source_kind}): {e.summary}"
                             for e in evidence[:MAX_CONTEXT_EVIDENCE]]


def simple_message(state: AgentState, paths: list[ImpactPath]) -> str:
    lines = _request_lines(state) + ["", "[영향 경로] path_id: 노드 → … (근거 없는 노드는 *)"]
    lines += [f"- {p.path_id}: " + " → ".join(h.node + ("" if h.confidence is Confidence.CONFIRMED else "*") for h in p.hops)
              for p in paths] or ["- 없음"]
    nodes = list(dict.fromkeys(h.node for p in paths for h in p.hops))
    lines += ["", f"[경로 노드] {len(nodes)}개 (중복 없음, changes 는 이 중에서 고른다)", ", ".join(nodes) or "-"]
    lines += _evidence_lines(state)
    limits = unverified_segments(state, paths)
    if limits:
        lines += ["", "[분석 한계]"] + [f"- {x}" for x in limits]
    return "\n".join(lines)


def complex_message(state: AgentState, base_items: list[tuple[ChecklistOwner, str, Area | None]]) -> str:
    sim = state.simulation
    lines = _request_lines(state) + ["", "[영역별 판정] 영역: 영향 유형 / 가능 여부 / 난이도 (근거) / 대안"]
    for a in sim.area_impacts:
        alts = " / ".join(x.description for x in a.alternatives) or "-"
        lines.append(f"- {a.area.value}: {a.impact_type.value} / {a.feasibility.value} / {a.difficulty.value} "
                     f"({a.difficulty_basis}) / 대안: {alts}")
    level, basis = requirement_difficulty(sim.area_impacts)
    lines += ["", f"[요건 전체 난이도] {level.value} ({basis}) - 요약에 이 값을 그대로 쓴다"]
    if sim.requested_vs_alternative:
        lines += ["", "[요청안 / 대안 비교]", sim.requested_vs_alternative]
    lines += ["", "[경로 상세] path_id: 노드(영향) → …"]
    lines += [f"- {p.path_id}: " + " → ".join(f"{h.node}({h.impact})" for h in p.hops) for p in unique_paths(state)]
    lines += _evidence_lines(state)
    lines += ["", "[분석 한계]"] + [f"- {x}" for x in complex_limits(state)]
    lines += ["", "[이미 포함된 확인 항목]"] + [f"- ({o.value}) {q}" for o, q, _ in base_items] if base_items else []
    return "\n".join(lines)


def followup_message(state: AgentState, fu: FollowupInput) -> str:
    lines = _request_lines(state) + ["", f"[후속 입력] ({fu.type.value}) 대상 항목: {', '.join(fu.target_item_ids) or '-'}",
                                     fu.content]
    report = state.composer.complex_report if state.composer else None
    if report:
        lines += ["", "[체크리스트]"] + [f"- {c.item_id} ({c.owner.value}) {c.question}" for c in report.checklist]
        lines += ["", "[요약 결론]", report.summary_conclusion]
    elif state.composer and state.composer.simple_answer:
        lines += ["", "[결론]"] + state.composer.simple_answer.conclusion_lines
    return "\n".join(lines)
