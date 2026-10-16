"""Planner: 요청 구조화, simple/complex 판정, 실행 계획 수립."""

from ..schemas import PlannerOutput
from ..state import AgentState
from .base import BaseAgent


class PlannerAgent(BaseAgent[PlannerOutput]):
    name = "planner"
    output_model = PlannerOutput

    def run(self, state: AgentState) -> PlannerOutput:
        # TODO: LLM 호출 + structured output(PlannerOutput) 구현
        raise NotImplementedError
