"""KPI Agent 입출력 (PoC 평가: 정답 노드 목록 대비 품질 + 실행 로그 기반 성능). 설계: docs/SE_kpi-agent-design.md"""

from pydantic import BaseModel, Field

from .enums import (
    Area,
    Complexity,
    Difficulty,
    Domain,
    Feasibility,
    ImpactType,
    KpiCategory,
    KpiMetricName,
    NodeSeverity,
    NodeVerdict,
    SmPart,
)


class KpiMetric(BaseModel):
    name: KpiMetricName
    category: KpiCategory
    value: float
    unit: str | None = Field(default=None, description="ratio / ms / count / tokens")
    threshold: float | None = Field(default=None, description="기준값 (없으면 측정만)")
    passed: bool | None = Field(default=None, description="threshold 가 있을 때 충족 여부")


class AgentRunRecord(BaseModel):
    """Supervisor 가 에이전트 실행마다 남기는 실행 로그 (KPI Agent 입력)."""

    agent: str = Field(description="planner / explorer / retriever / simulator / verifier / composer")
    latency_ms: float
    tool_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class AnswerNode(BaseModel):
    """정답 노드 하나 (영향받는 노드)."""

    node_id: str = Field(description="그래프 노드 ID (<접두어>:<식별 키>)")
    type: str = Field(description="노드 유형 (Screen / Controller / TP / Batch / Service / Module / DBIO / SQL / Table / Column)")
    stage: int | None = Field(default=None, ge=0, le=5, description="그래프 단계 0~5 (DBIO 는 경로 맥락에 따라 2 또는 3)")
    severity: NodeSeverity = NodeSeverity.MAJOR


class ExcludedNode(BaseModel):
    """평가 제외 노드: 출력돼도 오탐으로 세지 않고, 분모에서도 뺀다."""

    node_id: str
    reason: str = Field(description="must_find=false / reference_only / 정답 경로 중간 노드 / 미확정 경로 등")


class AnswerKey(BaseModel):
    """정답 노드 목록 (KPI Agent 입력). eval/ground_truth/<요건ID>.json 에서 만든다 (load_answer_key)."""

    req_id: str
    system: Domain | None = Field(default=None, description="swing / tos (집계 축)")
    sm_part: SmPart | None = None
    complexity: Complexity | None = Field(default=None, description="정답 복잡도 (집계 축·확장 KPI 용)")
    eval_target_labels: list[str] = Field(default_factory=list, description="평가할 노드 유형. 비우면 전체 유형")
    answer_nodes: list[AnswerNode] = Field(default_factory=list, description="반드시 찾아야 하는 노드 (탐지율 분모)")
    excluded_nodes: list[ExcludedNode] = Field(default_factory=list)
    distractor_nodes: list[str] = Field(default_factory=list, description="키워드가 겹치지만 영향이 없는 노드 (오탐 분석용)")
    # 판정 품질 비교값 (정답셋 expected·difficulty·wrong_paths, 없으면 해당 지표 생략)
    expected_difficulty: Difficulty | None = Field(default=None, description="정답 요건 난이도 (difficulty)")
    expected_feasibility: Feasibility | None = Field(default=None, description="정답 구현 가능 여부 (expected.feasibility)")
    expected_areas: dict[Area, ImpactType] = Field(default_factory=dict, description="정답 영역별 영향 유형 (expected.areas)")
    change_nodes: list[str] = Field(default_factory=list, description="정답 변경 노드 (impacted 중 modify·add) → [변경] 기대")
    reference_nodes: list[str] = Field(default_factory=list,
                                       description="정답 참조 노드 (reference_only, impacted 중 reference_only) → [참조] 기대")
    wrong_paths: list[list[str]] = Field(default_factory=list,
                                         description="오답 경로의 고유 노드 (정답 경로·영향 노드에 없는 노드만, wrong_paths)")


class NodeJudgement(BaseModel):
    """사례별 노드 판정표 한 줄."""

    node_id: str
    type: str
    stage: int | None = None
    severity: NodeSeverity | None = Field(default=None, description="정답 노드만")
    verdict: NodeVerdict
    in_output: bool = Field(description="final_nodes 에 있었는지")


class GroupKpi(BaseModel):
    """집계 축(노드 유형·단계 등) 값 하나의 탐지 성능."""

    key: str
    tp: int = 0
    fn: int = 0
    fp: int = 0
    recall: float | None = Field(default=None, description="TP / (TP + FN), 분모 0 이면 None (해당 없음)")
    precision: float | None = Field(default=None, description="TP / (TP + FP), 분모 0 이면 None (해당 없음)")


class KpiOutput(BaseModel):
    """KPI Agent 출력. Composer 이후 실행하는 평가 결과이며 Composer 에 전달하지 않는다."""

    complexity: Complexity = Field(description="실행 flow (Planner 판정)")
    req_id: str | None = Field(default=None, description="평가한 요건 ID (AnswerKey 가 없으면 None)")
    evaluable: bool = Field(default=True, description="품질 KPI 를 계산했는지 (AnswerKey·final_nodes 가 있어야 함)")
    skip_reason: str | None = Field(default=None, description="evaluable=false 사유")
    metrics: list[KpiMetric]
    agent_runs: list[AgentRunRecord]
    final_nodes: list[str] = Field(default_factory=list, description="평가한 Agent 출력 노드 (중복 제거)")
    judgements: list[NodeJudgement] = Field(default_factory=list, description="노드 판정표")
    by_type: list[GroupKpi] = Field(default_factory=list, description="노드 유형별 탐지 성능")
    by_stage: list[GroupKpi] = Field(default_factory=list, description="단계별 탐지 성능")
    missed_nodes: list[str] = Field(default_factory=list, description="놓친 정답 노드 (미탐)")
    false_positive_nodes: list[str] = Field(default_factory=list, description="정답이 아닌데 출력한 노드 (오탐)")
    distractors_found: list[str] = Field(default_factory=list, description="출력에 섞인 distractor 노드")
    all_passed: bool = Field(description="threshold 가 있는 지표를 모두 충족했는지")
    warnings: list[str] = Field(default_factory=list, description="미달 지표·평가 불가 설명 (평가 리포트용)")
    reanalyzed: bool = Field(default=False, description="사용자 상세 분석 요청(reanalysis)으로 다시 분석한 결과인지")
    judgement_notes: list[str] = Field(default_factory=list, description="판정 품질 불일치 설명 (평가 리포트용)")
