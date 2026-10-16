"""Composer: simple 답변 / complex 보고서 작성 및 markdown 렌더링."""

from ..schemas import ComposerOutput
from ..state import AgentState
from .base import BaseAgent


class ComposerAgent(BaseAgent[ComposerOutput]):
    name = "composer"
    output_model = ComposerOutput

    def run(self, state: AgentState) -> ComposerOutput:
        # TODO: LLM 호출 + structured output(ComposerOutput) 구현
        raise NotImplementedError
