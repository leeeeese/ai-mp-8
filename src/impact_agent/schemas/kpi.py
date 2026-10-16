"""KPI Agent 입출력 (품질·성능 지표)."""

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
    """Supervisor 가 에이전트 실행마다 남기는 측정 기록 (KPI Agent 입력)."""

    agent: str = Field(description="planner / explorer / retriever / simulator / verifier / composer")
    latency_ms: float
    tool_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class KpiOutput(BaseModel):
    """KPI Agent 출력. 단순: Retriever 이후, 복잡: Verifier 이후 → Composer."""

    complexity: Complexity
    metrics: list[KpiMetric]
    agent_runs: list[AgentRunRecord]
    all_passed: bool = Field(description="threshold 가 있는 지표를 모두 충족했는지")
    warnings: list[str] = Field(default_factory=list, description="미달 지표 설명 (Composer 가 분석 한계에 반영)")
