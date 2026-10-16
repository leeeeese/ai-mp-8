"""Simulator: 영역(screen/db/interface/batch)별 영향 경로·가능성·난이도 산정."""

from ..schemas import SimulationOutput
from ..state import AgentState
from .base import BaseAgent


class SimulatorAgent(BaseAgent[SimulationOutput]):
    name = "simulator"
    output_model = SimulationOutput

    def run(self, state: AgentState) -> SimulationOutput:
        # TODO: LLM 호출 + structured output(SimulationOutput) 구현
        raise NotImplementedError
