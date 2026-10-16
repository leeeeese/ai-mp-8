"""Retriever: 노드별 소스/문서 근거 수집, 필요 시 재탐색 요청 (search_source)."""

from ..schemas import RetrieverOutput
from ..state import AgentState
from .base import BaseAgent


class RetrieverAgent(BaseAgent[RetrieverOutput]):
    name = "retriever"
    output_model = RetrieverOutput

    def run(self, state: AgentState) -> RetrieverOutput:
        # TODO: LLM 호출 + structured output(RetrieverOutput) 구현
        raise NotImplementedError
