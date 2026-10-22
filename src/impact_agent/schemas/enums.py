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


class KpiMetricName(str, Enum):
    # quality (정답 경로 목록 대비)
    RECALL = "recall"
    MISS_RATE = "miss_rate"
    PRECISION = "precision"
    # performance (실행 로그)
    LATENCY_MS = "latency_ms"
    TOOL_CALLS = "tool_calls"
    EXPLORE_LOOPS = "explore_loops"
    REPLANS = "replans"
    TOTAL_TOKENS = "total_tokens"
