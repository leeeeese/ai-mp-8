"""Verifier: 영향 경로 누락·도메인 연계·중복·대안 근거 점검."""

from ..schemas import VerifierOutput
from ..state import AgentState
from .base import BaseAgent


class VerifierAgent(BaseAgent[VerifierOutput]):
    name = "verifier"
    output_model = VerifierOutput

    def run(self, state: AgentState) -> VerifierOutput:
        # TODO: LLM 호출 + structured output(VerifierOutput) 구현
        raise NotImplementedError
