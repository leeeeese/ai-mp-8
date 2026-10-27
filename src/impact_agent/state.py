"""Supervisor 가 에이전트 사이에서 주고받는 공유 상태."""

from pydantic import BaseModel, Field

from .schemas import (
    AgentRunRecord,
    AnswerKey,
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
    composer: ComposerOutput | None = None
    kpi: KpiOutput | None = None  # Composer 이후 평가 결과

    # 루프 제어 (config.MAX_* 와 비교)
    explore_loop_count: int = 0
    replan_count: int = 0
    tool_call_count: int = 0
    replan_feedback: list[ReplanFeedback] = Field(default_factory=list)

    # 실행 로그 (Supervisor 가 적재) — KPI Agent 입력
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)

    # PoC 평가용 정답 경로 목록 — KPI Agent 입력 (분석 에이전트는 참조하지 않음)
    answer_key: AnswerKey | None = None
