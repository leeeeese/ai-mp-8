"""인증 · 권한 · 관리 API (/api/admin/*, /api/auth/me).

- 사용자 구분: 프론트가 고른 mock 사용자 이름을 X-User 헤더로 보낸다 (URL 인코딩, SSE 는 헤더를 못 보내서 ?user=).
  없거나 등록되지 않은 이름 → 401.
- 권한: frontend/src/store/auth.ts 와 같은 표. 화면의 숨김은 편의 기능이고 여기가 최종 검증이다. 위반 → 403 + 감사 로그.
- SM 파트: 기본 권한은 자기 SM 파트만, sessions.all 은 다른 파트 세션을 볼 수 있지만 분석·답변은 자기 파트만.
- 저장: outputs/admin/{users,audit_logs}.json. 재적재 요청은 적재 현황과 같은 저장소. 승인은 상태만 바꾸고, 실행은 관리자 적재 파이프라인.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .runner import JsonStore

Role = Literal["user", "admin"]
Sm = Literal["prod", "dbm"]

BASE_PERMISSIONS = ("analyze", "report", "graph.view", "reload.request")
SUPER_PERMISSIONS = ("reload.approve", "settings", "users.manage", "audit.view", "sessions.all")
PERMISSION_LABEL = {
    "analyze": "요건 분석 실행", "report": "결과물 조회·체크리스트 답변", "graph.view": "적재 현황 조회", "reload.request": "재적재 요청",
    "reload.approve": "재적재 승인·실행", "settings": "루프 한도·프롬프트 설정", "users.manage": "사용자 추가·권한 변경",
    "audit.view": "감사 로그 조회", "sessions.all": "SM 파트 무관 전체 세션 열람",
}
SM_LABEL = {"prod": "상품", "dbm": "DBM"}
SEED_USERS = [  # frontend/src/mocks/db.ts 와 같은 초기 사용자
    {"name": "이세은", "role": "user", "smParts": ["prod"]},
    {"name": "권예지", "role": "user", "smParts": ["dbm"]},
    {"name": "김관리", "role": "admin", "smParts": ["prod", "dbm"]},
]


class NewUser(BaseModel):
    name: str = Field(pattern=r"\S")
    role: Role
    smParts: list[Sm] = Field(min_length=1)


class UserPatch(BaseModel):
    role: Role | None = None
    smParts: list[Sm] | None = Field(default=None, min_length=1)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def permissions(user: dict) -> list[str]:
    return list(BASE_PERMISSIONS) + (list(SUPER_PERMISSIONS) if user["role"] == "admin" else [])


def can(user: dict, permission: str) -> bool:
    return permission in permissions(user)


class Auth:
    def __init__(self, data_dir: Path) -> None:
        self.users = JsonStore(data_dir / "users.json", seed=SEED_USERS)
        self.audit = JsonStore(data_dir / "audit_logs.json")

    def record(self, actor: str, action: str, target: str) -> None:
        self.audit.add({"id": "l" + uuid.uuid4().hex[:8], "at": _now(), "actor": actor, "action": action, "target": target})

    def _identify(self, name: str | None) -> dict:
        user = next((u for u in self.users.records if u["name"] == name), None)
        if user is None:
            raise HTTPException(401, "사용자를 선택해 주세요")
        return user

    def current_user(self, x_user: str | None = Header(default=None)) -> dict:
        return self._identify(unquote(x_user) if x_user else None)

    def stream_user(self, user: str | None = Query(default=None)) -> dict:
        """EventSource 는 헤더를 못 보내서 이름을 쿼리로 받는다."""
        return self._identify(user)

    def deny(self, user: dict, target: str, detail: str) -> HTTPException:
        self.record(user["name"], "권한 거부", target)
        return HTTPException(403, detail)

    def require(self, permission: str) -> Callable[..., dict]:
        """라우트 의존성: 로그인 + 권한. 통과하면 사용자를 돌려준다."""

        def dependency(user: dict = Depends(self.current_user)) -> dict:
            if not can(user, permission):
                raise self.deny(user, PERMISSION_LABEL[permission], f"권한이 없습니다: {PERMISSION_LABEL[permission]}")
            return user

        return dependency

    def check_sm(self, user: dict, sm: str, view: bool = False) -> None:
        """자기 SM 파트만 통과. view 면 sessions.all 도 통과 (조회만)."""
        if sm in user["smParts"] or (view and can(user, "sessions.all")):
            return
        raise self.deny(user, f"SM 파트 {SM_LABEL[sm]}", "이 SM 파트에 접근 권한이 없습니다")


def me_view(user: dict) -> dict:
    return {**user, "permissions": permissions(user)}


def auth_router(auth: Auth) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    @router.get("/me")
    def me(user: dict = Depends(auth.current_user)) -> dict:
        return me_view(user)

    return router


def admin_router(auth: Auth, reload_requests: JsonStore) -> APIRouter:
    router = APIRouter(prefix="/api/admin")

    @router.get("/users")
    def list_users(_: dict = Depends(auth.require("users.manage"))) -> list[dict]:
        return auth.users.records

    @router.post("/users")
    def add_user(body: NewUser, actor: dict = Depends(auth.require("users.manage"))) -> dict:
        user = body.model_dump()

        def add(records: list[dict]) -> None:
            if any(u["name"] == body.name for u in records):
                raise HTTPException(409, "이미 있는 사용자입니다")
            records.insert(0, user)

        auth.users.update(add)
        auth.record(actor["name"], "사용자 추가", body.name)
        return user

    @router.patch("/users/{name}")
    def patch_user(name: str, body: UserPatch, actor: dict = Depends(auth.require("users.manage"))) -> dict:
        patch = body.model_dump(exclude_unset=True, exclude_none=True)

        def change(records: list[dict]) -> dict:
            user = next((u for u in records if u["name"] == name), None)
            if user is None:
                raise HTTPException(404, "사용자를 찾을 수 없습니다")
            after = {**user, **patch}
            others = any(u["role"] == "admin" for u in records if u is not user)
            if not others and after["role"] != "admin":
                raise HTTPException(409, "관리자가 한 명은 있어야 합니다")
            user.update(patch)
            return user

        user = auth.users.update(change)
        auth.record(actor["name"], "권한 변경", user["name"])
        return user

    @router.get("/reload-requests")
    def list_reload_requests(_: dict = Depends(auth.require("reload.approve"))) -> list[dict]:
        return reload_requests.records

    @router.post("/reload-requests/{request_id}/{decision}")
    def decide_reload(request_id: str, decision: Literal["approve", "reject"],
                      actor: dict = Depends(auth.require("reload.approve"))) -> dict:
        def decide(records: list[dict]) -> dict:
            r = next((x for x in records if x["id"] == request_id), None)
            if r is None:
                raise HTTPException(404, "재적재 요청을 찾을 수 없습니다")
            if r["status"] != "pending":
                raise HTTPException(409, "이미 처리된 요청입니다")
            r.update(status="approved" if decision == "approve" else "rejected", decidedBy=actor["name"], decidedAt=_now())
            return r

        r = reload_requests.update(decide)
        auth.record(actor["name"], "재적재 승인" if decision == "approve" else "재적재 반려", f'{r["id"]} ({r["scope"]})')
        return r

    @router.get("/audit-logs")
    def audit_logs(_: dict = Depends(auth.require("audit.view"))) -> list[dict]:
        return auth.audit.records

    return router
