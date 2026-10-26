"""Planner → Explorer 연계 기준표.

- CHANGE_TYPE_RULES: 요건 유형 → 목표 역할·방향·hop 상한 (Planner 는 유형만 고르고 나머지는 이 표로 정한다)
- SM_PART_DOMAINS: SM 선택 → 탐색 영역
- ROLE_ORDER: 진입 → DB 순서의 역할 단계 (Explorer 멈춤 판정)
- MENTION_LABELS: 언급 종류 → 시작 노드로 찾을 영역별 라벨
"""

from . import config
from .schemas import ChangeType, Direction, Domain, MentionKind, NodeRole, SmPart

F, R, FR = Direction.FORWARD, Direction.REVERSE, Direction.FORWARD_THEN_REVERSE

CHANGE_TYPE_RULES: dict[ChangeType, tuple[NodeRole, Direction]] = {
    ChangeType.SCREEN_CHANGE: (NodeRole.ENTRY_HANDLER, F),  # 화면 입력·표시 변경
    ChangeType.LOGIC_CHANGE: (NodeRole.LOGIC, F),  # 검증·처리 로직 변경
    ChangeType.QUERY_FIELD_ADD: (NodeRole.DATA_ACCESS, F),  # 조회 결과 항목 추가
    ChangeType.DATA_ADD: (NodeRole.TABLE, FR),  # 저장 데이터 추가
    ChangeType.COLUMN_SPEC_CHANGE: (NodeRole.ENTRY, R),  # 컬럼 길이·타입 변경 (컬럼에서 출발)
    ChangeType.BATCH_CHANGE: (NodeRole.DATA_ACCESS, F),  # 배치 처리 변경
    ChangeType.INTERFACE_FIELD_ADD: (NodeRole.ENTRY, R),  # 연계 항목 추가 (컬럼에서 배치까지, 그래프 밖 영역 표시)
    ChangeType.OPS_CAUSE: (NodeRole.TABLE, F),  # 운영 이슈 원인 확인 (진입부터 데이터까지)
    ChangeType.STRUCTURE_QUERY: (NodeRole.COLUMN, F),  # 구조·데이터 위치 질의 (테이블·컬럼 확인)
}


def hop_cap(direction: Direction) -> int:
    return config.MAX_HOPS_BIDIRECTIONAL if direction is FR else config.MAX_HOPS_FORWARD


SM_PART_DOMAINS: dict[SmPart, list[Domain]] = {
    SmPart.MARKETING: [Domain.TOS],
    SmPart.PRODUCT: [Domain.SWING],
}

ROLE_ORDER = [NodeRole.ENTRY, NodeRole.ENTRY_HANDLER, NodeRole.LOGIC, NodeRole.DATA_ACCESS, NodeRole.TABLE, NodeRole.COLUMN]
ROLE_RANK = {role: i for i, role in enumerate(ROLE_ORDER)}

MENTION_LABELS: dict[MentionKind, list[str] | None] = {
    MentionKind.SCREEN: ["Screen"],
    MentionKind.BATCH: ["Batch"],
    MentionKind.FUNCTION: ["Controller", "Service", "SQL", "TP", "Module", "DBIO"],
    MentionKind.TABLE: ["Table"],
    MentionKind.COLUMN: ["Column"],
    MentionKind.INTERFACE: ["Batch", "TP", "Module"],
    MentionKind.TERM: None,
}
