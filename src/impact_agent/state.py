"""Supervisor 가 에이전트 사이에서 주고받는 공유 상태."""

from pydantic import BaseModel, Field

from .schemas import (
    AgentRunRecord,
    ComposerOutput,
    ExplorerOutput,
    FollowupInput,
    InputMeta,
    KpiOutput,
    PlannerOutput,
    ReplanFeedback,
    RetrieverOutput,
    SimulationOutput,
    UserInput,
    VerifierOutput,
)


class AgentState(BaseModel):
    # 입력
    user_input: UserInput
    input_meta: InputMeta | None = None
    followups: list[FollowupInput] = Field(default_factory=list)

    # 단계별 산출물
    planner: PlannerOutput | None = None
    explorer: ExplorerOutput | None = None
    retriever: RetrieverOutput | None = None
    simulation: SimulationOutput | None = None
    verifier: VerifierOutput | None = None
    kpi: KpiOutput | None = None
    composer: ComposerOutput | None = None

    # 루프 제어 (config.MAX_* 와 비교)
    explore_loop_count: int = 0
    replan_count: int = 0
    tool_call_count: int = 0
    replan_feedback: list[ReplanFeedback] = Field(default_factory=list)

    # KPI 측정용 실행 기록 (Supervisor 가 적재)
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)
