# Neo4j 영향도 그래프 적재·확인 가이드

## 0. 데이터 받기 (git 에 없음)

`data/`(목업 소스·문서·그래프 seed·Neo4j 저장소)와 `mock_dev/`(목업 작성·검증용)는 `.gitignore` 대상이라 저장소에 없습니다.
필요하면 **관리자(파트너 모두 해당)에게 `data` 폴더 zip 을 요청**해서 저장소 루트에 풀어 주세요 (zip 안이 `data/…` 로 시작하므로 루트에서 풀면 `data/` 가 됩니다).

```
data/
├── source/<영역>/   tos · swing 목업 소스                      ← 적재에 필요 (zip 포함)
├── docs/<영역>/     정의서·인터페이스 문서                     ← 적재에 필요 (zip 포함). swing 은 한글 이름·설명을 여기서 읽음
├── graph/           nodes.csv · edges.csv · seed.cypher        ← zip 포함 (2번에서 다시 만들어지며, 받은 것과 같으면 정상)
├── snapshot/tos/    운영 DB 조회 결과                           ← zip 포함. 그래프 적재에는 쓰지 않지만 분석 근거 (정답셋이 근거 위치로 참조)
└── neo4j/           Neo4j 저장소                                ← zip 제외 (1번에서 새로 생성, 2번으로 적재)
```

```bash
# 저장소 루트에서
unzip impact-agent-data-<날짜>.zip      # → data/source, data/docs, data/snapshot, data/graph
```

## 1. Neo4j 실행

`.env` 에 `NEO4J_PASSWORD` 를 정하고 Docker Desktop 을 켠 뒤 (자세한 내용은 README "Neo4j 실행"):

```bash
docker compose up -d
```

> zip 에 `data/neo4j` 가 들어 있으면 그 저장소를 만든 사람의 비밀번호로 계정이 이미 만들어져 있습니다.
> 내 `.env` 비밀번호로 쓰려면 `docker compose down` → `rm -rf data/neo4j` → `docker compose up -d` 후 2번으로 적재하세요.

## 2. 그래프 생성·적재

```bash
pip install -r requirements.txt          # 최초 1회 (neo4j 드라이버 포함)
python scripts/build_graph.py --strict   # data/source → data/graph/{nodes.csv, edges.csv, seed.cypher}
python scripts/build_graph.py --load     # Neo4j 적재 (기존 :Node 그래프를 지우고 다시 적재)
python scripts/check_ground_truth.py     # 정답셋(eval/ground_truth/*.json)이 그래프와 맞는지 검사
```

적재 결과: swing 노드 366 · tos 노드 322 · 엣지 850, `check_ground_truth.py` 16건 모두 OK. 노드 ID 규칙과 엣지 근거는 [mock_data.md](mock_data.md) 참고.

> swing 파서는 코드 관례로 노드·엣지를 만들고, 한글 이름·설명은 `data/docs/swing/` 정의서에서 읽습니다. 정의서가 없으면 그래프는 만들어지지만 한글 검색이 거의 되지 않습니다.

## 3. 브라우저에서 확인

http://localhost:7474 → Connect URL `bolt://localhost:7687`, 사용자 `neo4j`, 비밀번호 `.env` 의 `NEO4J_PASSWORD`.

**노드 종류별 색 적용 (최초 1회)**: 입력창에 `:style` 실행 → 스타일 창 오른쪽 위 업로드(↑) 버튼으로 [`docs/neo4j/impact_graph.grass`](neo4j/impact_graph.grass) 선택.
되돌리기는 같은 창의 되돌리기(↺) 버튼.

| 색 | 노드 |
|---|---|
| 주황 | Screen |
| 노랑 | Controller (tos) · TP (swing) · Batch |
| 보라 | Service (tos) · Module (swing) |
| 파랑 / 청록 | SQL (tos) / DBIO |
| 초록 / 연두 | Table / Column |

**전체 그래프 보기**: 브라우저는 처음에 노드 300개까지만 그립니다. Column 을 뺀 구조(283개)는 그대로 보이고,
Column 까지 전부(688개) 보려면 왼쪽 사이드바 설정(톱니바퀴) → **Graph Visualization** → `Initial Node Display` 를 1000 으로 바꿉니다.
(이 브라우저 버전(2026.x)은 `:config` 명령을 지원하지 않습니다.)

```cypher
// 전체 (Column 405개 포함, 무거움)
MATCH (n:Node) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m

// Column 을 뺀 전체 구조 (권장)
MATCH (n:Node) WHERE NOT n:Column OPTIONAL MATCH (n)-[r]->(m) WHERE NOT m:Column RETURN n, r, m

// 영역 하나만 ('tos' 또는 'swing')
MATCH (n:Node {domain:'tos'}) WHERE NOT n:Column OPTIONAL MATCH (n)-[r]->(m) WHERE NOT m:Column RETURN n, r, m
```

**자주 쓰는 확인 쿼리**

```cypher
// 영역·노드 종류별 개수
MATCH (n:Node) RETURN n.domain AS 영역, labels(n)[1] AS 종류, count(*) AS 개수 ORDER BY 영역, 종류

// 엣지 종류별 개수
MATCH ()-[r]->() RETURN type(r) AS 엣지, count(*) AS 개수 ORDER BY 엣지

// 노드 하나에서 이어지는 영향 (호출되는 쪽 3hop)
MATCH p=(:Node {node_id:'BAT:TBTW1010'})-[*1..3]->() RETURN p

// 두 노드 사이 경로 (예: SC-001 정답 경로)
MATCH p=(:Node {node_id:'SCR:TOSCP0110M'})-[*1..6]->(:Node {node_id:'COL:TCP_MSG_SCRT.ADVT_YN'}) RETURN p

// 노드 속성 (원문 code, source_file, source_line)
MATCH (n:Node {node_id:'SVC:CmpnService.saveMsgScrt'}) RETURN n
```

- 노드를 클릭하면 오른쪽 패널에 속성이 보입니다. 표시 이름을 ID 로 바꾸려면 아래 범례의 라벨 → caption 을 `node_id` 로 선택.
- 모든 노드에는 공통 라벨 `Node` 와 종류 라벨(`Screen`, `SQL` …)이 함께 붙어 있습니다. 쿼리에서는 `:Node {node_id: ...}` 로 찾으면 됩니다.
