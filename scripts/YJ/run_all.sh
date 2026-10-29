#!/bin/zsh
# 시나리오 16건 + 가드레일 3회를 순차로 실행한다. 중간에 끊겨도 같은 명령으로 다시 돌리면 끝난 케이스·회차는 건너뛴다.
#   nohup zsh scripts/YJ/run_all.sh v5 > /dev/null 2>&1 &      (진행 로그: outputs/runs/YJ/<버전>/run.log)
VER=${1:?버전 (예: v5)}
cd "$(dirname "$0")/../.."
OUT=outputs/runs/YJ/$VER
mkdir -p $OUT
LOG=$OUT/run.log
export PYTHONPATH=src PYTHONWARNINGS=ignore
echo "=== 시작 $(date '+%F %T')" >> $LOG
python3 scripts/YJ/run_scenarios.py SC-001 SC-002 SC-003 SC-004 SC-005 SC-006 --run-id $VER/tos >> $LOG 2>&1
python3 scripts/YJ/run_scenarios.py H-1 H-2 M-1 M-2 M-3 M-4 L-1 L-2 L-3 L-4 --run-id $VER/swing >> $LOG 2>&1
for r in 1 2 3; do
  if [[ -f $OUT/guardrail/r$r/guardrail.json ]]; then echo "[guardrail r$r] 이미 완료 — 건너뜀" >> $LOG; continue; fi
  python3 scripts/YJ/run_guardrail.py --run-id $VER/guardrail/r$r >> $LOG 2>&1
done
echo "=== 종료 $(date '+%F %T')" >> $LOG
