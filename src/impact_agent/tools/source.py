"""소스 직접 조회 tool (Retriever 사용). 벡터 검색 없이 그래프 노드가 가리키는 원문을 읽는다."""

from ..schemas import ReadSourceInput, ReadSourceItem


def read_source(params: ReadSourceInput) -> list[ReadSourceItem]:
    """노드의 source_file / source_line(또는 노드 code 속성)으로 소스 원문을 직접 조회한다."""
    raise NotImplementedError
