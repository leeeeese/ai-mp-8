"""에이전트가 호출하는 tool. 이름 → (함수, 입력 모델) 레지스트리.

그래프·소스 tool 은 저장소를 두 번째 인자로 받는다 (search_graph_nodes(params, store)).
"""

from ..schemas import (
    GetNodeDetailInput,
    ReadSourceInput,
    SaveReportInput,
    SearchGraphNodesInput,
    TraverseGraphInput,
)
from .graph import (
    CsvGraphStore,
    GraphStore,
    Neo4jGraphStore,
    get_graph_store,
    get_node_detail,
    search_graph_nodes,
    traverse_graph,
)
from .report import save_report
from .source import SourceRoots, get_source_roots, read_source

TOOL_REGISTRY = {
    "search_graph_nodes": (search_graph_nodes, SearchGraphNodesInput),
    "traverse_graph": (traverse_graph, TraverseGraphInput),
    "get_node_detail": (get_node_detail, GetNodeDetailInput),
    "read_source": (read_source, ReadSourceInput),
    "save_report": (save_report, SaveReportInput),
}

__all__ = [
    "TOOL_REGISTRY",
    "CsvGraphStore",
    "GraphStore",
    "Neo4jGraphStore",
    "SourceRoots",
    "get_graph_store",
    "get_node_detail",
    "get_source_roots",
    "read_source",
    "save_report",
    "search_graph_nodes",
    "traverse_graph",
]
