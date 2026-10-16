"""에이전트가 호출하는 tool. 이름 → (함수, 입력 모델) 레지스트리."""

from ..schemas import (
    ExpandGraphInput,
    GetNodeDetailInput,
    SaveReportInput,
    SearchGraphNodesInput,
    SearchSourceInput,
)
from .graph import expand_graph, get_node_detail, search_graph_nodes
from .report import save_report
from .source import search_source

TOOL_REGISTRY = {
    "search_graph_nodes": (search_graph_nodes, SearchGraphNodesInput),
    "expand_graph": (expand_graph, ExpandGraphInput),
    "get_node_detail": (get_node_detail, GetNodeDetailInput),
    "search_source": (search_source, SearchSourceInput),
    "save_report": (save_report, SaveReportInput),
}

__all__ = ["TOOL_REGISTRY", "expand_graph", "get_node_detail", "save_report", "search_graph_nodes", "search_source"]
