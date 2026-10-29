"""UI 백엔드 API (FastAPI). 실행: uvicorn impact_agent.api.app:app --reload --port 8000

계약: frontend/src/api/types.ts, docs/ui_design.md 7장. 목업(frontend/src/mocks/handlers.ts)과 같은 경로·응답 모양.
"""

from .app import create_app

__all__ = ["create_app"]
