"""Retriever: Explorer 가 찾은 경로의 노드 소스를 직접 조회해 근거 수집, 필요 시 확장 요청 (read_source)."""

from ..schemas import RetrieverOutput
from ..state import AgentState
from .base import BaseAgent


class RetrieverAgent(BaseAgent[RetrieverOutput]):
    name = "retriever"
    output_model = RetrieverOutput

    def run(self, state: AgentState) -> RetrieverOutput:
        # TODO: LLM 호출 + structured output(RetrieverOutput) 구현
        raise NotImplementedError
