"""공통 Enum 정의 (schemas/schemas.json 의 enum $defs)."""

from enum import Enum


class Difficulty(str, Enum):
    MAJOR = "Major"
    MEDIUM = "Medium"
    MINOR = "Minor"


class Feasibility(str, Enum):
    FEASIBLE = "feasible"
    HARD = "hard"
    REVIEW = "review"


class Area(str, Enum):
    SCREEN = "screen"
    DB = "db"
    INTERFACE = "interface"
    BATCH = "batch"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"


class ImpactType(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    REFERENCE_ONLY = "reference_only"
    NONE = "none"


class ChecklistOwner(str, Enum):
    BUSINESS = "business"
    DEVELOPER = "developer"


class ChecklistStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


class Complexity(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


class Domain(str, Enum):
    SWING = "swing"
    TOS = "tos"


class NodeRole(str, Enum):
    """공통 역할 라벨. 영역별 라벨(tos Controller / swing TP 등)과 무관하게 탐색 목표를 가리킨다."""

    ENTRY = "Entry"  # 진입: Screen, Batch
    ENTRY_HANDLER = "EntryHandler"  # 처리 진입: tos Controller, swing TP
    LOGIC = "Logic"  # 업무 로직: tos Service, swing Module
    DATA_ACCESS = "DataAccess"  # 데이터 접근: tos SQL(XML·Pro*C), DBIO
    TABLE = "Table"
    COLUMN = "Column"


class ChangeType(str, Enum):
    """요건 항목 유형. 목표 역할·방향·hop 상한은 config.CHANGE_TYPE_RULES 로 정한다."""

    SCREEN_CHANGE = "screen_change"  # 화면 입력·표시 변경
    LOGIC_CHANGE = "logic_change"  # 검증·처리 로직 변경
    QUERY_FIELD_ADD = "query_field_add"  # 조회 결과 항목 추가
    DATA_ADD = "data_add"  # 저장 데이터 추가
    COLUMN_SPEC_CHANGE = "column_spec_change"  # 컬럼 길이·타입 변경
    BATCH_CHANGE = "batch_change"  # 배치 처리 변경
    INTERFACE_FIELD_ADD = "interface_field_add"  # 연계 항목 추가
    OPS_CAUSE = "ops_cause"  # 운영 이슈 원인 확인
    STRUCTURE_QUERY = "structure_query"  # 구조·데이터 위치 질의


class Direction(str, Enum):
    FORWARD = "forward"  # 진입 → DB (호출되는 쪽)
    REVERSE = "reverse"  # DB → 진입 (호출하는 쪽)
    FORWARD_THEN_REVERSE = "forward_then_reverse"  # 정방향으로 수정 대상을 찾고, 그 대상에서 다른 진입점으로 역방향 확장


class MentionKind(str, Enum):
    """요건에서 언급한 대상의 종류 (Planner 가 그래프를 모르는 상태에서 고른다)."""

    SCREEN = "Screen"
    BATCH = "Batch"
    FUNCTION = "Function"  # 기능·처리·로직·서비스·SQL
    TABLE = "Table"
    COLUMN = "Column"
    INTERFACE = "Interface"  # 연계·전문·외부 채널
    TERM = "Term"  # 종류를 정하기 어려운 업무 용어


class MatchSource(str, Enum):
    CONFIRMED = "confirmed"  # 사용자가 확정한 매핑
    GLOSSARY = "glossary"  # 용어사전(terms) 일치
    TEXT = "text"  # 노드명·설명·코멘트 검색


class StopReason(str, Enum):
    TARGET_REACHED = "target_reached"  # 목표 역할 도달
    NEAREST_ROLE = "nearest_role"  # 경로에 목표 역할이 없어 가장 가까운 역할에서 멈춤
    HOP_CAP = "hop_cap"  # hop 상한
    DEAD_END = "dead_end"  # 더 이어지는 노드 없음
    EXCLUDED_COMMON = "excluded_common"  # 공통 노드라 확장하지 않음
    NO_START_NODE = "no_start_node"  # 시작 노드를 찾지 못함


class ReexploreMode(str, Enum):
    CONTINUE = "continue"  # hop 상한에 걸린 경로를 이어서 탐색
    REVERSE = "reverse"  # 대상 노드에서 다른 진입점으로 역방향 확장
    NEXT_TARGET = "next_target"  # 다음 목표 역할까지 더 탐색


class SmPart(str, Enum):
    PRODUCT = "product"
    MARKETING = "marketing"


class FollowupType(str, Enum):
    BUSINESS_ANSWER = "business_answer"
    DEVELOPER_OPINION = "developer_opinion"
    QUERY_RESULT = "query_result"
    CHANGE_ANALYSIS = "change_analysis"
    REANALYSIS = "reanalysis"


class AnalysisPoint(str, Enum):
    INITIAL = "initial"
    OPERATING = "operating"


class InputSource(str, Enum):
    ITSD = "itsd"
    MAIL = "mail"
    ETC = "etc"


class InputType(str, Enum):
    REQUIREMENT = "requirement"
    OPS_INQUIRY = "ops_inquiry"


class AgentName(str, Enum):
    EXPLORER = "explorer"
    RETRIEVER = "retriever"
    SIMULATOR = "simulator"
    VERIFIER = "verifier"


class RequestType(str, Enum):
    CHANGE = "change"
    STRUCTURE_QUERY = "structure_query"
    OPS_ISSUE = "ops_issue"


class VerifyResult(str, Enum):
    PASS = "pass"
    NEEDS_SUPPLEMENT = "needs_supplement"


class KpiCategory(str, Enum):
    QUALITY = "quality"
    PERFORMANCE = "performance"


class AnswerPathStatus(str, Enum):
    """정답 경로의 실제 영향 여부 확정 상태."""

    CONFIRMED = "confirmed"
    PENDING = "pending"  # 미확정 → KPI 계산에서 제외, 평가 보류 건수로 별도 공개


class PathDetection(str, Enum):
    """정답 경로별 탐지 판정."""

    EXACT = "exact"  # 끝까지 정확히 탐지
    PARTIAL = "partial"  # 중간 단계까지만 탐지
    INITIAL = "initial"  # 시작 노드만 탐지
    MISSED = "missed"  # 탐지 못 함
    PENDING = "pending"  # 미확정 경로 (평가 보류)


class KpiMetricName(str, Enum):
    # quality (정답 경로 목록 대비)
    PATH_RECALL = "path_recall"  # 정확 탐지 / 확정 경로
    CRITICAL_MISS_RATE = "critical_miss_rate"  # Critical 확정 경로 중 정확 탐지 못 한 비율 (부분·초기 포함)
    PATH_PRECISION = "path_precision"  # 정확 / 제시 경로 (부분·초기 포함 분모, 가안)
    PATH_PRECISION_EXCL_PARTIAL = "path_precision_excl_partial"  # 정확 / (정확 + 오탐)
    PARTIAL_PATHS = "partial_paths"  # 부분 탐지 경로 수
    INITIAL_PATHS = "initial_paths"  # 초기 탐지 경로 수
    PENDING_PATHS = "pending_paths"  # 평가 보류(미확정) 경로 수
    # performance (실행 로그)
    LATENCY_MS = "latency_ms"
    TOOL_CALLS = "tool_calls"
    EXPLORE_LOOPS = "explore_loops"
    REPLANS = "replans"
    TOTAL_TOKENS = "total_tokens"
