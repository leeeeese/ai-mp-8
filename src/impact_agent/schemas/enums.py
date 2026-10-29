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
    QUALITY = "quality"  # 노드 탐지 (정답 노드 목록 대비)
    JUDGEMENT = "judgement"  # 판정 품질 (정답 복잡도·난이도·영역·가능 여부 대비, 측정만)
    PERFORMANCE = "performance"


class NodeSeverity(str, Enum):
    """정답 노드 중요도 (미탐 시 영향)."""

    CRITICAL = "Critical"  # 장애·과금 오류 가능
    MAJOR = "Major"  # 오동작·운영 이슈 가능
    MINOR = "Minor"  # 참고·부수적


class NodeVerdict(str, Enum):
    """노드 판정 (KPI 설계서 5.1)."""

    DETECTED = "detected"  # TP: 정답 노드가 final_nodes 에 있음
    MISSED = "missed"  # FN: 정답 노드가 final_nodes 에 없음
    FALSE_POSITIVE = "false_positive"  # FP: 정답·제외 목록에 없는 평가 범위 안 출력 노드
    EXCLUDED = "excluded"  # 평가 제외: 정답 미확정·참고 노드 (분모·분자 모두 제외, 건수만 공개)
    OUT_OF_SCOPE = "out_of_scope"  # 범위 밖: 유형이 eval_target_labels 에 없음


class KpiMetricName(str, Enum):
    # quality (정답 노드 목록 대비, 비율은 0~1)
    NODE_RECALL = "node_recall"  # 노드 탐지율 = TP / (TP + FN)
    NODE_MISS_RATE = "node_miss_rate"  # 노드 미탐률 = FN / (TP + FN)
    CRITICAL_MISS_RATE = "critical_miss_rate"  # Critical 정답 노드 중 FN 비율 (권장 0)
    NODE_PRECISION = "node_precision"  # 노드 정밀도 = TP / (TP + FP)
    EXCLUDED_NODES = "excluded_nodes"  # 평가 제외 노드 수
    OUT_OF_SCOPE_NODES = "out_of_scope_nodes"  # 범위 밖 노드 수
    # judgement (판정 품질, 측정만. 1 = 일치/해당, 0 = 불일치/해당 없음, 비율은 0~1. 비교값이 없으면 생략)
    COMPLEXITY_MATCH = "complexity_match"  # Planner 복잡도(재분석이면 재분석 후 값) = 정답 complexity
    DIFFICULTY_MATCH = "difficulty_match"  # (복잡) 요건 전체 난이도 requirement_difficulty = 정답 difficulty
    FEASIBILITY_MATCH = "feasibility_match"  # (복잡) 변경 영역 종합 가능 여부 = 정답 expected.feasibility
    AREA_IMPACT_MATCH = "area_impact_match"  # (복잡) 4개 영역 impact_type 이 정답 expected.areas 와 같은 비율
    CHANGE_TAG_ACCURACY = "change_tag_accuracy"  # (복잡) 정답 변경·참조 노드 중 Simulator [변경]/[참조] 표시가 맞은 비율
    WRONG_PATH_ADOPTED = "wrong_path_adopted"  # (복잡) 정답셋 wrong_paths 중 고유 노드를 [변경] 으로 판정한 오답 경로 수
    DETAIL_RECOMMENDED = "detail_recommended"  # (단순) 상세 분석을 권장했는지
    COMPLEX_MINOR = "complex_minor"  # (복잡) 변경이 있는데 요건 전체 난이도 Minor (Composer 표시 신호)
    VERIFIER_UNRESOLVED = "verifier_unresolved"  # (복잡) 재계획 한도 안에 Verifier 보완 필요를 못 메움
    # performance (실행 로그)
    LATENCY_MS = "latency_ms"
    TOOL_CALLS = "tool_calls"
    EXPLORE_LOOPS = "explore_loops"
    REPLANS = "replans"
    TOTAL_TOKENS = "total_tokens"
