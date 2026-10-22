"""KPI Agent 입출력 (PoC 평가: 정답 경로 목록 대비 품질 + 실행 로그 기반 성능)."""

from pydantic import BaseModel, Field

from .enums import Complexity, KpiCategory, KpiMetricName


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


class AnswerKey(BaseModel):
    """정답 경로 목록 (KPI Agent 입력). data/ground_truth/<요건ID>.json 에서 만든다."""

    req_id: str
    expected_nodes: list[str] = Field(description="반드시 찾아야 하는 노드 (재현율·미탐율 분모)")
    optional_nodes: list[str] = Field(default_factory=list, description="찾으면 정답으로 인정하는 노드 (정밀도 계산 시 오탐 아님)")
    distractor_nodes: list[str] = Field(default_factory=list, description="키워드가 겹치지만 영향이 없는 노드 (오탐 분석용)")


class KpiOutput(BaseModel):
    """KPI Agent 출력. Composer 이후 실행하는 평가 결과이며 Composer 에 전달하지 않는다."""

    complexity: Complexity
    req_id: str | None = Field(default=None, description="평가한 요건 ID (AnswerKey 가 없으면 None)")
    metrics: list[KpiMetric]
    agent_runs: list[AgentRunRecord]
    matched_nodes: list[str] = Field(default_factory=list, description="찾은 정답 노드")
    missed_nodes: list[str] = Field(default_factory=list, description="놓친 정답 노드 (미탐)")
    extra_nodes: list[str] = Field(default_factory=list, description="정답이 아닌데 영향으로 낸 노드 (오탐)")
    all_passed: bool = Field(description="threshold 가 있는 지표를 모두 충족했는지")
    warnings: list[str] = Field(default_factory=list, description="미달 지표 설명 (평가 리포트용)")
