# Windows (PowerShell) 개발 환경 설정: 가상환경 생성 → 패키지 일괄 설치 → .env 생성 → 테스트
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 이상이 필요합니다'"

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path .env)) { Copy-Item .env.example .env }

.\.venv\Scripts\python.exe -m pytest -q
Write-Host ""
Write-Host "완료. 가상환경 활성화: .\.venv\Scripts\Activate.ps1"
Write-Host ".env 에 OPENAI_API_KEY, NEO4J_PASSWORD 등을 채워 주세요."
