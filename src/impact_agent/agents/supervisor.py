"""Supervisor: 순서·분기·재계획 제어.

UserInput → Planner → Explorer ⇄ Retriever (≤ MAX_EXPLORE_LOOP)
         → [단순] KPI Agent → Composer
         → [복잡] Simulator → Verifier ─ needs_supplement → Planner (≤ MAX_REPLAN)
                                        └ pass → KPI Agent → Composer
"""

import time
from typing import TypeVar

from pydantic import BaseModel

from .. import config
from ..schemas import (
    AgentName,
    AgentRunRecord,
    Complexity,
    ComposerOutput,
    ExplorerOutput,
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


class Supervisor:
    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.explorer = ExplorerAgent()
        self.retriever = RetrieverAgent()
        self.simulator = SimulatorAgent()
        self.verifier = VerifierAgent()
        self.kpi = KpiAgent()
        self.composer = ComposerAgent()

    def run(self, user_input: UserInput) -> ComposerOutput:
        state = AgentState(user_input=user_input)

        while True:
            state.planner = self._call("planner", self.planner, state)
            steps = {step.agent for step in state.planner.plan}

            if AgentName.EXPLORER in steps:
                self._explore(state)

            if state.planner.complexity is Complexity.SIMPLE:
                break

            if AgentName.SIMULATOR in steps:
                state.simulation = self._call("simulator", self.simulator, state)
            if AgentName.VERIFIER in steps:
                state.verifier = self._call("verifier", self.verifier, state)
                if self._should_replan(state):
                    continue
            break

        state.kpi = self.kpi.run(state)
        state.composer = self._call("composer", self.composer, state)
        return state.composer

    def _call(self, name: str, agent, state: AgentState) -> OutputT:
        """에이전트를 실행하고 KPI 측정용 실행 기록을 남긴다."""
        start = time.perf_counter()
        output = agent.run(state)
        tool_calls = output.tool_call_count if isinstance(output, ExplorerOutput) else 0
        state.agent_runs.append(
            AgentRunRecord(agent=name, latency_ms=(time.perf_counter() - start) * 1000, tool_calls=tool_calls)
        )
        state.tool_call_count += tool_calls
        return output

    def _explore(self, state: AgentState) -> None:
        state.explore_loop_count = 0
        while True:
            state.explorer = self._call("explorer", self.explorer, state)
            state.retriever = self._call("retriever", self.retriever, state)
            state.explore_loop_count += 1
            if not state.retriever.reexplore_request:
                return
            if state.explore_loop_count >= config.MAX_EXPLORE_LOOP:
                return

    def _should_replan(self, state: AgentState) -> bool:
        verdict = state.verifier
        if verdict is None or verdict.result is VerifyResult.PASS:
            return False
        if state.replan_count >= config.MAX_REPLAN:
            return False
        state.replan_count += 1
        state.replan_feedback.append(
            ReplanFeedback(
                round=state.replan_count,
                reason=verdict.reason or "",
                additional_scope=verdict.additional_scope,
            )
        )
        return True
