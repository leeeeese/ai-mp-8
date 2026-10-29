"""Composer 출력 (simple 답변 / complex 보고서)."""

from typing import Literal

from pydantic import BaseModel, Field

from .enums import Area, ChecklistOwner, ChecklistStatus, Complexity, Confidence, Difficulty, Feasibility
from .simulator import DroppedPath, ImpactPath


# --- complex ---------------------------------------------------------------
class AreaVerdictRow(BaseModel):
    """영역별 판정표 한 행."""

    area: Area
    impact_path_summary: str
    feasibility: Feasibility
    difficulty: Difficulty
    alternative: str | None = None
    confidence: Confidence
    evidence_names: list[str] = Field(description="노드·테이블명·소스 위치")


class ChecklistItem(BaseModel):
    item_id: str
    owner: ChecklistOwner
    question: str
    answer: str | None = None
    status: ChecklistStatus = ChecklistStatus.UNCONFIRMED
    related_area: Area | None = None


class ComplexReport(BaseModel):
    summary_conclusion: str
    area_verdicts: list[AreaVerdictRow]
    schedule_factors: list[str]
    path_details: list[ImpactPath]
    analysis_limits: list[str]
    dropped_paths: list[DroppedPath] = Field(default_factory=list, description="검토 제외 경로 (Simulator 가 요건과 무관하다고 본 경로)")
    checklist: list[ChecklistItem]
    stage: Literal["initial", "updated", "final_for_dev"] = "initial"


# --- simple ----------------------------------------------------------------
class SimpleEvidenceRow(BaseModel):
    claim: str
    evidence: str
    location: str | None = None
    confidence: Confidence


class GuidanceQuery(BaseModel):
    purpose: str
    sql: str = Field(description="읽기 전용(SELECT)만 허용")


class QueryResultSummary(BaseModel):
    code_meanings: list[str] = Field(default_factory=list)
    main_causes: list[str] = Field(default_factory=list)
    inquiry_items: list[str] = Field(default_factory=list, description="문의 필요 항목")


class SimpleAnswer(BaseModel):
    conclusion_lines: list[str] = Field(min_length=3, max_length=5)
    impact_paths: list[ImpactPath] = Field(default_factory=list, description="영향 경로 (A→…→끝)")
    evidence_table: list[SimpleEvidenceRow]
    check_queries: list[GuidanceQuery] = Field(default_factory=list)
    inquiry_contacts: list[str] = Field(default_factory=list)
    analysis_limits: list[str] = Field(default_factory=list)
    detail_analysis_recommended: bool = Field(default=False, description="변경 요소가 보이면 True")
    detail_analysis_reasons: list[str] = Field(
        default_factory=list,
        description="상세 분석 권장 사유 (노드 ID 를 그대로 포함. 사용자가 재분석을 요청하면 이 문장이 reanalysis 후속 입력이 된다)",
    )
    query_result_summary: QueryResultSummary | None = None


# --- output ----------------------------------------------------------------
class MailDraft(BaseModel):
    """회신 메일 초안 (요청 시)."""

    to: list[str] = Field(default_factory=list)
    subject: str
    body: str


class ReanalysisRequest(BaseModel):
    """Composer → Supervisor. 답변 반영으로 영향 경로 자체가 바뀌어야 할 때."""

    reason: str
    changed_assumptions: list[str]


class ComposerOutput(BaseModel):
    complexity: Complexity
    complex_report: ComplexReport | None = None
    simple_answer: SimpleAnswer | None = None
    reanalysis_request: ReanalysisRequest | None = None
    mail_draft: MailDraft | None = None
    report_file_path: str | None = Field(default=None, description="PDF/MD 저장 경로 (요청 시, save_report 결과)")
    markdown: str = Field(description="템플릿 렌더링 결과 (사용자 표시용)")
