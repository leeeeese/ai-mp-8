#!/bin/zsh
# 정답셋 16건을 UI 와 같은 전체 흐름(Planner → … → Composer → KPI)으로 순차 실행하고 report.html 을 만든다.
# Neo4j 만 떠 있으면 된다 (백엔드·프론트는 필요 없음). 끊겨도 같은 명령이면 끝난 케이스는 건너뛴다.
# 버전 폴더 아래 -1 = 수정 전(as-is), -2 = 수정 후(to-be)로 둔다. 결과 <run>/tos · <run>/swing · <run>/report.html, 진행은 터미널 + <run>/run.log
#   zsh scripts/YJ/run_all.sh v6/v6-1                    (as-is, 2026-11-02 완료 = E2E 첫 기준선)
#   zsh scripts/YJ/run_all.sh v6/v6-2 v6/v6-1            (to-be: 수정 후 실행, report.html 에 v6-1 대비 증감)
#   VERBOSE=1 zsh scripts/YJ/run_all.sh v6/v6-2 v6/v6-1  (터미널에 에이전트 진행: ▶ 시작 · ■ 끝 · ↓ 라우팅. 상세는 <케이스>.log)
#   FRESH=1 …                                            (끝난 케이스도 처음부터)
#   GUARDRAIL=1 …                                        (가드레일 10종 × 3회도 함께)
#   PY=<python 경로> …                                   (Python 직접 지정. 없으면 패키지가 있는 Python 을 찾음)
VER=${1:?실행 폴더 (예: v6/v6-2)}
BASE=${2:-}
cd "$(dirname "$0")/../.."

# 패키지(neo4j·openai·langgraph)가 있는 Python 찾기 (conda base 에는 없음)
for cand in ${PY:-} python3 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
  [[ -z $cand ]] && continue
  if $cand -c "import neo4j, openai, langgraph, dotenv" 2>/dev/null; then PY=$cand; break; fi
  PY=
done
if [[ -z $PY ]]; then echo "neo4j·openai·langgraph 가 설치된 Python 이 없습니다. pip install -r requirements.txt 후 PY=<경로> 로 지정하세요."; exit 1; fi
if ! docker ps --filter name=neo4j --format '{{.Status}}' | grep -q '^Up'; then echo "Neo4j 가 꺼져 있습니다: docker compose up -d"; exit 1; fi

OUT=outputs/runs/YJ/$VER
mkdir -p $OUT
LOG=$OUT/run.log
export PYTHONPATH=src PYTHONWARNINGS=ignore
echo "=== 시작 $(date '+%F %T') · Python $PY" | tee -a $LOG
$PY scripts/YJ/run_scenarios.py --all --run-id $VER ${BASE:+--baseline $BASE} ${VERBOSE:+--verbose} ${FRESH:+--fresh} 2>&1 \
  | grep --line-buffered -v "HTTP Request" | tee -a $LOG
# 실행 중 케이스마다 쓰는 report 와 별개로, 끝난 뒤 저장된 summary 전체로 report 를 다시 만든다 (기준 실행 비교 포함)
$PY scripts/YJ/run_scenarios.py --report $VER ${BASE:+--baseline $BASE} 2>&1 | tee -a $LOG
if [[ -n $GUARDRAIL ]]; then
  for r in 1 2 3; do
    if [[ -f $OUT/guardrail/r$r/guardrail.json ]]; then echo "[guardrail r$r] 이미 완료 — 건너뜀" | tee -a $LOG; continue; fi
    $PY scripts/YJ/run_guardrail.py --run-id $VER/guardrail/r$r 2>&1 | tee -a $LOG
  done
fi
echo "=== 종료 $(date '+%F %T') · 기록 $OUT/report.html" | tee -a $LOG
