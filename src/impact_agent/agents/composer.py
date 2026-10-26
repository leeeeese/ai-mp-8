"""Composer: 분석 결과를 사용자 결과물로 만든다. 설계: docs/SE_agent_design.md 3장

- 단순 (Retriever → Composer): SimpleAnswer = 요약 결론 · 영향 경로 · 근거 (+ 확인 쿼리, 분석 한계)
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
from ..rules import build_impact_paths, evidence_ref
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
    Feasibility,
    FollowupInput,
    FollowupType,
    GuidanceQuery,
    ImpactPath,
    ImpactType,
    MailDraft,
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
from .base import BaseAgent
from .simulator import DraftHop, apply_hop_impacts, unverified_segments
from .verifier import REPEATED, WARNING, unique_paths

# Supervisor 가 Composer 로 바로 보내는 후속 입력 (supervisor.COMPOSER_FOLLOWUPS 와 같은 값)
ANSWER_FOLLOWUPS = {FollowupType.BUSINESS_ANSWER, FollowupType.DEVELOPER_OPINION, FollowupType.QUERY_RESULT}
DETAIL_CHANGE_TYPES = {ChangeType.DATA_ADD, ChangeType.COLUMN_SPEC_CHANGE, ChangeType.QUERY_FIELD_ADD,
                       ChangeType.INTERFACE_FIELD_ADD}
WRITE_SQL = re.compile(r"\b(insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|exec|execute|call)\b", re.IGNORECASE)
DIFFICULTY_RANK = [Difficulty.MINOR, Difficulty.MEDIUM, Difficulty.MAJOR]
MAX_PATH_SUMMARY = 3  # 판정표 한 칸에 보여 줄 경로 수
MAX_EVIDENCE_NAMES = 5
MAX_CONTEXT_EVIDENCE = 40


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


class SimpleDraft(BaseModel):
    conclusion_lines: list[str] = Field(description="3~5줄. 결론 먼저, 노드명·테이블명을 붙여 쓴다")
    hops: list[DraftHop] = Field(description="경로 노드별 영향 한 문장")
    claims: list[DraftClaim]
    check_queries: list[DraftQuery] = Field(description="운영 문의일 때 확인용 SQL. 아니면 []")
    inquiry_contacts: list[str] = Field(description="문의가 필요한 담당·부서. 없으면 []")
    detail_analysis_recommended: bool = Field(description="변경 요소가 보이면 true")
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
            return None
        try:
            draft, self.last_usage = self.llm.parse(self.system_prompt, user, schema)
        except LLM_FAILURES as exc:  # LLM 실패는 규칙 문구로 대체
            self.notes.append(f"LLM 작성 실패로 규칙 문구 사용: {exc}")
            return None
        return draft

    # --- 단순 ------------------------------------------------------------------------------------

    def _simple(self, state: AgentState) -> ComposerOutput:
        exp, ret = state.explorer, state.retriever
        evidence = ret.evidence if ret else []
        paths = [p for p, _ in build_impact_paths(exp.paths if exp else [], exp.edges if exp else [], evidence)]
        draft: SimpleDraft | None = self._draft(SimpleDraft, simple_message(state, paths))
        if draft is not None:
            apply_hop_impacts(draft, paths)
        claims = {c.node_id: c.claim for c in draft.claims} if draft else {}
        answer = SimpleAnswer(
            conclusion_lines=fit_lines(draft.conclusion_lines if draft else [], rule_conclusion(state, paths, evidence)),
            impact_paths=paths,
            evidence_table=evidence_rows(evidence, claims),
            check_queries=safe_queries(draft.check_queries) if draft else [],
            inquiry_contacts=draft.inquiry_contacts if draft else [],
            analysis_limits=unverified_segments(state, paths) + self.notes,
            detail_analysis_recommended=(draft.detail_analysis_recommended if draft else False) or rule_detail(state),
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
        report = ComplexReport(
            summary_conclusion=(draft.summary_conclusion.strip() if draft else "") or rule_summary(sim.area_impacts),
            area_verdicts=area_rows(sim.area_impacts),
            schedule_factors=list(dict.fromkeys(f for a in sim.area_impacts for f in a.schedule_factors)),
            path_details=unique_paths(state),
            analysis_limits=complex_limits(state) + self.notes,
            dropped_paths=sim.dropped_paths,
            checklist=number_checklist(base_items + (
                [(d.owner, d.question, d.related_area) for d in draft.checklist] if draft else rule_checklist(sim.area_impacts))),
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


def safe_queries(queries: list[DraftQuery]) -> list[GuidanceQuery]:
    """읽기 전용 SELECT 한 문장만 통과 (쓰기·DDL·여러 문장은 버림)."""
    out = []
    for q in queries:
        sql = q.sql.strip().rstrip(";").strip()
        if re.match(r"(select|with)\b", sql, re.IGNORECASE) and ";" not in sql and not WRITE_SQL.search(sql):
            out.append(GuidanceQuery(purpose=q.purpose, sql=sql))
    return out


def rule_detail(state: AgentState) -> bool:
    """구조 변경 유형(데이터·컬럼·조회 항목·연계 항목 추가)이 있으면 상세 분석 권장."""
    return bool(state.planner) and any(it.change_type in DETAIL_CHANGE_TYPES for it in state.planner.items)


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
    level = max((a.difficulty for a in changed), key=DIFFICULTY_RANK.index)
    parts = ", ".join(f"{a.area.value}({a.impact_type.value}·{a.feasibility.value}·{a.difficulty.value})" for a in changed)
    return f"변경 영역: {parts}. 요건 전체 난이도 {level.value}, 구현 가능 여부 {overall.value}."


def complex_limits(state: AgentState) -> list[str]:
    out = list(state.simulation.unverified_segments)
    v = state.verifier
    if v is not None:
        out += [c.note for c in v.checks if c.note.startswith((REPEATED, WARNING))]
        if v.result is VerifyResult.NEEDS_SUPPLEMENT:
            out.append(f"재계획 한도({config.MAX_REPLAN}회) 안에서 보완하지 못함: {v.reason}")
    return list(dict.fromkeys(out))


def required_checklist(state: AgentState) -> list[tuple[ChecklistOwner, str, Area | None]]:
    """LLM 과 관계없이 항상 넣는 확인 항목: 요건 미확정 사항(업무), 검토 제외 경로(개발)."""
    items = [(ChecklistOwner.BUSINESS, f"{o.item} ({o.reason})", None)
             for o in (state.planner.structured_request.open_items if state.planner else [])]
    for d in state.simulation.dropped_paths if state.simulation else []:
        question = f"검토 제외 경로 {d.path.path_id} ({path_summary(d.path)}) 가 실제로 무관한지 확인 - 제외 사유: {d.reason}"
        items.append((ChecklistOwner.DEVELOPER, question, None))
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
