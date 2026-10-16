"""소스/문서 검색 tool (Retriever 사용)."""

from ..schemas import SearchSourceInput, SearchSourceItem


def search_source(params: SearchSourceInput) -> list[SearchSourceItem]:
    """도메인(swing/tos) 소스·문서에서 근거를 검색한다."""
    raise NotImplementedError
