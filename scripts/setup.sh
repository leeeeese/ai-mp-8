#!/usr/bin/env bash
# macOS / Linux 개발 환경 설정: 가상환경 생성 → 패키지 일괄 설치 → .env 생성 → 테스트
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 이상이 필요합니다"'

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

[ -f .env ] || cp .env.example .env

.venv/bin/python -m pytest -q
echo
echo "완료. 가상환경 활성화: source .venv/bin/activate"
echo ".env 에 OPENAI_API_KEY, NEO4J_PASSWORD 등을 채워 주세요."
