"""코드/화면 의존 그래프 조회 tool (Explorer 사용)."""

from ..schemas import (
    ExpandGraphInput,
    ExpandGraphOutput,
    GetNodeDetailInput,
    GraphNode,
    SearchGraphNodesInput,
    SearchGraphNodesItem,
)


def search_graph_nodes(params: SearchGraphNodesInput) -> list[SearchGraphNodesItem]:
    """키워드로 시작 후보 노드를 검색한다."""
    raise NotImplementedError


def expand_graph(params: ExpandGraphInput) -> ExpandGraphOutput:
    """node_ids 로부터 1~2 hop 이웃 노드/엣지를 확장한다."""
    raise NotImplementedError


def get_node_detail(params: GetNodeDetailInput) -> GraphNode:
    """단일 노드의 상세 정보를 조회한다."""
    raise NotImplementedError
