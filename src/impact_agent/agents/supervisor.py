"""Supervisor: LangGraph 노드·조건부 엣지로 순서·분기·재계획을 제어하고 실행 로그를 남긴다 (LLM 미사용).

    START ─(새 요건·변경 분석·재분석)→ planner ─→ explorer ─→ retriever ─(재탐색 요청, 루프·tool 한도 안)→ explorer
          └(후속 답변·조회 결과)→ composer                    ├(simple)→ composer → kpi → END
                                                              └(complex)→ simulator → verifier ─(보완 필요, 재계획 한도 안)→ planner
                                                                                              └(통과·한도 초과)→ composer

- 재계획 사유(Verifier 의 reason·additional_scope)는 replan_feedback 에 누적해 Planner 가 다음 계획에 반영한다 (Reflexion).
- Planner 가 탐색 항목을 하나도 만들지 않으면(분석 대상 없음) 탐색·시뮬레이션 없이 Composer 로 바로 간다.
- 재탐색은 남은 tool 호출이 MIN_CALLS_FOR_REEXPLORE 이상일 때만 (Explorer 탐색 + Retriever 새 노드 읽기). 한도 계산은 AgentState.tools_left 하나.
- 상태(AgentState)는 checkpointer 에 세션(thread_id)별로 보관해 후속 입력에 재사용한다. 후속 입력은 최근 MEMORY_WINDOW_N 개만 유지.
- (PoC 평가) KPI Agent 는 Composer 이후 AnswerKey(정답 경로 목록) + 실행 로그로 평가한다. 결과는 Composer 에 전달하지 않는다.
"""

import time
import uuid
from typing import TypeVar

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .. import config
from ..rules import MIN_CALLS_FOR_REEXPLORE
from ..schemas import (
    AgentName,
    AgentRunRecord,
    AnswerKey,
    Complexity,
    ComposerOutput,
    FollowupInput,
    FollowupType,
    ReplanFeedback,
    UserInput,
    VerifyResult,
)
from ..state import AgentState
from .composer import ComposerAgent
from .explorer import ExplorerAgent
from .kpi import KpiAgent
from .planner import PlannerAgent
from .retriever import RetrieverAgent
from .simulator import SimulatorAgent
from .verifier import VerifierAgent

OutputT = TypeVar("OutputT", bound=BaseModel)
STAGES = ("planner", "explorer", "retriever", "simulator", "verifier", "composer")
RECURSION_LIMIT = 100  # LangGraph 노드 실행 수 안전장치 (루프 한도는 config.MAX_* 가 먼저 막는다)
# 후속 입력 중 결과물만 갱신하면 되는 유형 (그 외 변경 분석·재분석은 Planner 부터)
COMPOSER_FOLLOWUPS = {FollowupType.BUSINESS_ANSWER, FollowupType.DEVELOPER_OPINION, FollowupType.QUERY_RESULT}


def _updates(state: AgentState) -> dict:
    """노드가 바꾼 상태를 LangGraph 채널에 그대로 돌려준다."""
    return {f: getattr(state, f) for f in AgentState.model_fields}


class Supervisor:
    def __init__(self, checkpointer=None) -> None:
        self.planner = PlannerAgent()
        self.explorer = ExplorerAgent()
        self.retriever = RetrieverAgent()
        self.simulator = SimulatorAgent()
        self.verifier = VerifierAgent()
        self.kpi = KpiAgent()
        self.composer = ComposerAgent()
        self.checkpointer = checkpointer or MemorySaver()
        self.last_session_id: str | None = None
        self.last_state: AgentState | None = None

    # --- 진입 -------------------------------------------------------------------

    def run(self, user_input: UserInput, answer_key: AnswerKey | None = None) -> ComposerOutput:
        """분석을 실행하고 Composer 결과를 반환한다. KPI 평가 결과는 last_state.kpi 에 남는다."""
        return self.analyze(user_input, answer_key=answer_key).composer

    def analyze(
        self,
        user_input: UserInput,
        until: str | None = None,
        session_id: str | None = None,
        answer_key: AnswerKey | None = None,
    ) -> AgentState:
        """새 요건을 분석하고 상태를 돌려준다. until 을 주면 그 단계까지만 실행한다 (Composer·KPI 생략)."""
        if until is not None and until not in STAGES:
            raise ValueError(f"until 은 {STAGES} 중 하나")
        state = AgentState(user_input=user_input, answer_key=answer_key)
        return self._invoke(state, until, session_id or uuid.uuid4().hex)

    def followup(self, session_id: str, followup: FollowupInput, until: str | None = None) -> AgentState:
        """같은 세션에 후속 입력을 넣는다. 답변·조회 결과는 Composer 로, 변경 분석·재분석 요청은 Planner 로 간다."""
        values = self._graph(until).get_state(self._config(session_id)).values
        if not values:
            raise KeyError(f"세션 {session_id} 이 없습니다")
        state = AgentState.model_validate(dict(values))
        state.followups = (state.followups + [followup])[-config.MEMORY_WINDOW_N:]
        return self._invoke(state, until, session_id)

    def _invoke(self, state: AgentState, until: str | None, session_id: str) -> AgentState:
        self.last_session_id = session_id
        result = self._graph(until).invoke(_updates(state), self._config(session_id))
        self.last_state = AgentState.model_validate(dict(result))
        return self.last_state

    @staticmethod
    def _config(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": RECURSION_LIMIT}

    # --- 그래프 -------------------------------------------------------------------

    def _graph(self, until: str | None):
        """노드 이름은 대문자 (AgentState 필드명과 겹치면 안 됨)."""
        g = StateGraph(AgentState)
        g.add_node("PLANNER", self._planner_node)
        g.add_node("EXPLORER", self._agent_node("explorer", "explorer"))
        g.add_node("RETRIEVER", self._retriever_node)
        g.add_node("SIMULATOR", self._agent_node("simulator", "simulation"))
        g.add_node("VERIFIER", self._verifier_node)
        g.add_node("KPI", self._kpi_node)
        g.add_node("COMPOSER", self._agent_node("composer", "composer"))

        g.add_conditional_edges(START, self._route_start, {"PLANNER": "PLANNER", "COMPOSER": "COMPOSER"})
        g.add_conditional_edges("PLANNER", lambda s: self._route_planner(s, until),
                                {"EXPLORER": "EXPLORER", "SIMULATOR": "SIMULATOR", "COMPOSER": "COMPOSER", END: END})
        g.add_edge("EXPLORER", "RETRIEVER")
        g.add_conditional_edges("RETRIEVER", lambda s: self._route_retriever(s, until),
                                {"EXPLORER": "EXPLORER", "SIMULATOR": "SIMULATOR", "COMPOSER": "COMPOSER", END: END})
        g.add_conditional_edges("SIMULATOR", lambda s: END if until == "simulator" else self._next_after_sim(s),
                                {"VERIFIER": "VERIFIER", "COMPOSER": "COMPOSER", END: END})
        g.add_conditional_edges("VERIFIER", lambda s: self._route_verifier(s, until),
                                {"PLANNER": "PLANNER", "COMPOSER": "COMPOSER", END: END})
        g.add_edge("COMPOSER", "KPI")  # PoC 평가: Composer 결과에는 반영하지 않음
        g.add_edge("KPI", END)
        return g.compile(checkpointer=self.checkpointer)

    # --- 노드 ---------------------------------------------------------------------

    def _agent_node(self, name: str, field: str):
        def node(state: AgentState) -> dict:
            setattr(state, field, self._call(name, getattr(self, name), state))
            return _updates(state)
        return node

    def _planner_node(self, state: AgentState) -> dict:
        state.planner = self._call("planner", self.planner, state)
        self._apply_confirmed(state)
        state.explore_loop_count = 0
        state.explorer = state.retriever = None  # 재계획 라운드는 새로 탐색
        return _updates(state)

    def _retriever_node(self, state: AgentState) -> dict:
        state.retriever = self._call("retriever", self.retriever, state)
        state.explore_loop_count += 1
        return _updates(state)

    def _verifier_node(self, state: AgentState) -> dict:
        state.verifier = self._call("verifier", self.verifier, state)
        verdict = state.verifier
        if verdict.result is VerifyResult.NEEDS_SUPPLEMENT and state.replan_count < config.MAX_REPLAN:
            state.replan_count += 1
            state.replan_feedback.append(ReplanFeedback(
                round=state.replan_count, reason=verdict.reason or "", additional_scope=verdict.additional_scope))
        return _updates(state)

    def _kpi_node(self, state: AgentState) -> dict:
        state.kpi = self.kpi.run(state)
        return _updates(state)

    # --- 라우팅 (조건부 엣지) --------------------------------------------------------

    def _route_start(self, state: AgentState) -> str:
        last = state.followups[-1] if state.followups else None
        if last is not None and last.type in COMPOSER_FOLLOWUPS and state.planner is not None:
            return "COMPOSER"
        return "PLANNER"

    def _route_planner(self, state: AgentState, until: str | None) -> str:
        if until == "planner":
            return END
        if not state.planner.items:  # 분석 대상 없음 (예: 업무와 무관한 질문) → 탐색·시뮬레이션 생략
            return END if until in ("explorer", "retriever", "simulator", "verifier") else "COMPOSER"
        if AgentName.EXPLORER in self._steps(state):
            return "EXPLORER"
        return self._after_explore(state, until)

    def _route_retriever(self, state: AgentState, until: str | None) -> str:
        if (state.retriever.reexplore_request and state.explore_loop_count < config.MAX_EXPLORE_LOOP
                and state.tools_left() >= MIN_CALLS_FOR_REEXPLORE):
            return "EXPLORER"
        return self._after_explore(state, until)

    def _after_explore(self, state: AgentState, until: str | None) -> str:
        if until in ("explorer", "retriever"):
            return END
        if state.planner.complexity is Complexity.SIMPLE:
            return "COMPOSER"
        return self._next_after_sim(state) if AgentName.SIMULATOR not in self._steps(state) else "SIMULATOR"

    def _next_after_sim(self, state: AgentState) -> str:
        return "VERIFIER" if AgentName.VERIFIER in self._steps(state) else "COMPOSER"

    def _route_verifier(self, state: AgentState, until: str | None) -> str:
        # 이번 Verifier 실행에서 재계획이 잡혔으면 (replan_count 가 지금까지의 Planner 실행 수만큼 올라감) Planner 로
        planner_runs = sum(r.agent == "planner" for r in state.agent_runs)
        if state.verifier.result is VerifyResult.NEEDS_SUPPLEMENT and state.replan_count >= planner_runs:
            return "PLANNER"
        return END if until == "verifier" else "COMPOSER"

    @staticmethod
    def _steps(state: AgentState) -> set[AgentName]:
        return {step.agent for step in state.planner.plan}

    # --- 공통 -----------------------------------------------------------------------

    def _call(self, name: str, agent, state: AgentState) -> OutputT:
        """에이전트를 실행하고 실행 로그(시간·tool 호출·토큰, KPI Agent 입력)를 남긴다."""
        start = time.perf_counter()
        output = agent.run(state)
        tool_calls = getattr(output, "tool_call_count", 0) or 0
        usage = getattr(agent, "last_usage", None)
        state.agent_runs.append(AgentRunRecord(
            agent=name, latency_ms=(time.perf_counter() - start) * 1000, tool_calls=tool_calls,
            input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None)))
        state.tool_call_count += tool_calls
        return output

    def _apply_confirmed(self, state: AgentState) -> None:
        """사용자가 확정한 매핑(phrase → node_id)을 탐색 명세의 같은 표현에 반영한다."""
        confirmed = {c.phrase.strip(): c.node_id for c in state.user_input.confirmed_nodes}
        if not confirmed or state.planner is None:
            return
        for item in state.planner.items:
            for m in item.mentions:
                if m.phrase.strip() in confirmed:
                    m.confirmed_node = confirmed[m.phrase.strip()]
