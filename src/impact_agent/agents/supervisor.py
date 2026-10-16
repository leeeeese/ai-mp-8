"""Supervisor: 전체 흐름 제어.

UserInput → Planner → Explorer ⇄ Retriever (≤ MAX_EXPLORE_LOOP)
         → [complex] Simulator → Verifier ─ needs_supplement → Planner (≤ MAX_REPLAN)
         → Composer → ComposerOutput
"""

from .. import config
from ..schemas import AgentName, ComposerOutput, Complexity, ReplanFeedback, UserInput, VerifyResult
from ..state import AgentState
from .composer import ComposerAgent
from .explorer import ExplorerAgent
from .planner import PlannerAgent
from .retriever import RetrieverAgent
from .simulator import SimulatorAgent
from .verifier import VerifierAgent


class Supervisor:
    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.explorer = ExplorerAgent()
        self.retriever = RetrieverAgent()
        self.simulator = SimulatorAgent()
        self.verifier = VerifierAgent()
        self.composer = ComposerAgent()

    def run(self, user_input: UserInput) -> ComposerOutput:
        state = AgentState(user_input=user_input)

        while True:
            state.planner = self.planner.run(state)
            steps = {step.agent for step in state.planner.plan}

            if AgentName.EXPLORER in steps:
                self._explore(state)

            if state.planner.complexity is Complexity.SIMPLE:
                break

            if AgentName.SIMULATOR in steps:
                state.simulation = self.simulator.run(state)
            if AgentName.VERIFIER in steps:
                state.verifier = self.verifier.run(state)
                if self._should_replan(state):
                    continue
            break

        state.composer = self.composer.run(state)
        return state.composer

    def _explore(self, state: AgentState) -> None:
        state.explore_loop_count = 0
        while True:
            state.explorer = self.explorer.run(state)
            state.retriever = self.retriever.run(state)
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
