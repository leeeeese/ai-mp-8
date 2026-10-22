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


class NodeLabel(str, Enum):
    """영향도 그래프 노드 유형. tos: Controller/Service/SQL, swing: TP/Module, 공통: Screen/Batch/DBIO/Table/Column."""

    SCREEN = "Screen"
    CONTROLLER = "Controller"
    TP = "TP"
    BATCH = "Batch"
    SERVICE = "Service"
    MODULE = "Module"
    DBIO = "DBIO"
    SQL = "SQL"
    TABLE = "Table"
    COLUMN = "Column"


class EdgeType(str, Enum):
    CALLS = "CALLS"  # Screen→Controller/TP, TP→TP
    INVOKES = "INVOKES"  # 로직→로직, 로직→DBIO
    EXECUTES = "EXECUTES"  # Service/Batch→SQL (tos)
    READS = "READS"  # DBIO/SQL→Table
    WRITES = "WRITES"  # DBIO/SQL→Table
    REFERENCES = "REFERENCES"  # Table→Table
    HAS_COLUMN = "HAS_COLUMN"  # Table→Column


class TraverseDirection(str, Enum):
    """그래프 탐색 방향."""

    DOWNSTREAM = "downstream"  # 호출되는 쪽 (Screen → TP → Module → DBIO → Table → Column)
    UPSTREAM = "upstream"  # 호출하는 쪽


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
