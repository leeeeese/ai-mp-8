"""Explorer: 시작 노드 선정 후 그래프 1~2 hop 확장 (search_graph_nodes, expand_graph, get_node_detail)."""

from ..schemas import ExplorerOutput
from ..state import AgentState
from .base import BaseAgent


class ExplorerAgent(BaseAgent[ExplorerOutput]):
    name = "explorer"
    output_model = ExplorerOutput

    def run(self, state: AgentState) -> ExplorerOutput:
        # TODO: LLM 호출 + structured output(ExplorerOutput) 구현
        raise NotImplementedError
