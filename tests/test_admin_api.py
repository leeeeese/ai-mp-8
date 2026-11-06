"""인증 · 권한 · 관리 API 테스트 (가짜 에이전트, LLM·Neo4j 없음). 응답 모양은 frontend/src/api/types.ts (User · ReloadRequest · AuditLog) 와 같아야 한다."""

import json
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from impact_agent.api import create_app  # noqa: E402
from test_api import ADMIN, COMPLEX_TEXT, as_header, fake_supervisor, gt_dir  # noqa: E402, F401

PROD, DBM = "이세은", "권예지"


def body(sm="dbm", text=COMPLEX_TEXT):
    return {"smPart": sm, "inputType": "requirement", "source": "mail", "text": text}


@pytest.fixture
def app(tmp_path, gt_dir):  # noqa: F811
    return create_app(fake_supervisor, kpi_path=tmp_path / "kpi.json", gt_dir=gt_dir, run_in_thread=False,
                      admin_dir=tmp_path / "admin", log_dir=tmp_path / "logs", reload_path=tmp_path / "reload.json")


@pytest.fixture
def as_user(app):
    def client(name=ADMIN):
        return TestClient(app, headers=as_header(name) if name else {})
    return client


def actions(admin):
    return [a["action"] for a in admin.get("/api/admin/audit-logs").json()]


# --- 401 / 403 -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/sessions", "/api/kpi/records", "/api/graph/status", "/api/admin/users", "/api/auth/me"])
def test_no_or_unknown_user_is_401(as_user, path):
    assert as_user(None).get(path).status_code == 401
    assert as_user("없는사람").get(path).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/users"), ("post", "/api/admin/users"), ("patch", f"/api/admin/users/{DBM}"),
    ("get", "/api/admin/reload-requests"), ("post", "/api/admin/reload-requests/r1/approve"),
    ("get", "/api/admin/audit-logs"), ("delete", "/api/kpi/records"),
])
def test_user_role_cannot_call_super_apis(as_user, method, path):
    kwargs = {"json": {"name": "새사람", "role": "admin", "smParts": ["dbm"]}} if method in ("post", "patch") else {}
    assert getattr(as_user(DBM), method)(path, **kwargs).status_code == 403


def test_user_is_limited_to_own_sm_part(as_user):
    admin, prod = as_user(), as_user(PROD)
    sid = admin.post("/api/sessions", json=body("dbm")).json()["sessionId"]

    assert prod.post("/api/sessions", json=body("dbm")).status_code == 403          # 다른 파트 분석
    assert prod.get("/api/sessions").json() == []                                    # 목록에서 빠짐
    for path in ("", "/report", "/logs"):
        assert prod.get(f"/api/sessions/{sid}{path}").status_code == 403
    assert as_user(None).get(f"/api/sessions/{sid}/stream", params={"user": PROD}).status_code == 403
    assert prod.post(f"/api/sessions/{sid}/star").status_code == 403
    assert prod.post(f"/api/sessions/{sid}/messages", json={"kind": "chat", "text": "x"}).status_code == 403
    assert prod.get("/api/kpi/records").json() == []
    assert prod.get("/api/graph/status?sm=dbm").status_code == 403
    assert prod.post("/api/graph/reload-requests", json={"sm": "dbm", "scope": "s", "reason": "r"}).status_code == 403
    assert as_user(DBM).get(f"/api/sessions/{sid}").status_code == 200               # 그 파트 사용자는 조회 가능
    assert "권한 거부" in actions(admin)


def test_admin_views_other_part_but_analyzes_only_own(as_user):
    admin = as_user()
    sid = admin.post("/api/sessions", json=body("dbm")).json()["sessionId"]
    admin.patch(f"/api/admin/users/{ADMIN}", json={"smParts": ["prod"]})
    assert admin.get(f"/api/sessions/{sid}").status_code == 200                      # sessions.all: 조회
    assert admin.get("/api/graph/status?sm=dbm").status_code != 403
    assert admin.post("/api/sessions", json=body("dbm")).status_code == 403
    assert admin.post(f"/api/sessions/{sid}/messages", json={"kind": "chat", "text": "x"}).status_code == 403


def test_stream_takes_user_from_query_only_on_stream(as_user):
    admin = as_user()
    sid = admin.post("/api/sessions", json=body()).json()["sessionId"]
    anon = as_user(None)
    assert anon.get(f"/api/sessions/{sid}/stream", params={"user": ADMIN}).status_code == 200    # EventSource
    assert anon.get(f"/api/sessions/{sid}/stream").status_code == 401
    assert anon.get(f"/api/sessions/{sid}", params={"user": ADMIN}).status_code == 401


# --- /api/auth/me ---------------------------------------------------------------------


def test_me_returns_permissions(as_user):
    me = as_user(DBM).get("/api/auth/me").json()
    assert me["name"] == DBM and me["smParts"] == ["dbm"]
    assert me["permissions"] == ["analyze", "report", "graph.view", "reload.request"]
    assert "users.manage" in as_user().get("/api/auth/me").json()["permissions"]


# --- 사용자 ---------------------------------------------------------------------------


def test_add_and_patch_user_applies_immediately_and_audits(as_user, tmp_path):
    admin = as_user()
    new = {"name": "새사람", "role": "user", "smParts": ["dbm"], "isNew": True}
    u = admin.post("/api/admin/users", json=new).json()
    assert u == {"name": "새사람", "role": "user", "smParts": ["dbm"]}
    assert admin.post("/api/admin/users", json=new).status_code == 409
    assert as_user("새사람").get("/api/graph/status?sm=prod").status_code == 403

    r = admin.patch(f"/api/admin/users/{quote('새사람')}", json={**u, "smParts": ["prod"], "role": "admin"}).json()
    assert r == {"name": "새사람", "role": "admin", "smParts": ["prod"]}
    assert "sessions.all" in as_user("새사람").get("/api/auth/me").json()["permissions"]
    assert {"사용자 추가", "권한 변경"} <= set(actions(admin))
    saved = json.loads((tmp_path / "admin" / "users.json").read_text(encoding="utf-8"))   # 재시작해도 남음
    assert next(x for x in saved if x["name"] == "새사람")["role"] == "admin"


@pytest.mark.parametrize("bad", [
    {"name": " ", "role": "user", "smParts": ["dbm"]},
    {"name": "x", "role": "root", "smParts": ["dbm"]},
    {"name": "x", "role": "user", "smParts": []},
    {"name": "x", "role": "user", "smParts": ["hr"]},
])
def test_invalid_user_is_422(as_user, bad):
    assert as_user().post("/api/admin/users", json=bad).status_code == 422


def test_cannot_remove_last_admin(as_user):
    admin = as_user()
    assert admin.patch(f"/api/admin/users/{ADMIN}", json={"role": "user"}).status_code == 409
    assert admin.patch("/api/admin/users/없는사람", json={"role": "user"}).status_code == 404
    admin.patch(f"/api/admin/users/{DBM}", json={"role": "admin"})                   # 다른 관리자가 있으면 가능
    assert admin.patch(f"/api/admin/users/{ADMIN}", json={"role": "user"}).status_code == 200


# --- 재적재 요청 ------------------------------------------------------------------------


def test_reload_request_approve_reject_flow(as_user):
    admin, user = as_user(), as_user(DBM)
    r = user.post("/api/graph/reload-requests", json={"sm": "dbm", "scope": "online", "reason": "반영"}).json()
    assert r["requestedBy"] == DBM and r["status"] == "pending"
    assert admin.get("/api/admin/reload-requests").json()[0]["id"] == r["id"]

    done = admin.post(f'/api/admin/reload-requests/{r["id"]}/approve').json()
    assert done["status"] == "approved" and done["decidedBy"] == ADMIN
    assert admin.post(f'/api/admin/reload-requests/{r["id"]}/reject').status_code == 409          # 이미 처리
    assert admin.post("/api/admin/reload-requests/NOPE/approve").status_code == 404
    assert admin.post(f'/api/admin/reload-requests/{r["id"]}/foo').status_code == 422

    r2 = user.post("/api/graph/reload-requests", json={"sm": "dbm", "scope": "전체", "reason": "정기"}).json()
    assert admin.post(f'/api/admin/reload-requests/{r2["id"]}/reject').json()["status"] == "rejected"
    assert {"재적재 승인", "재적재 반려"} <= set(actions(admin))


def test_kpi_reset_is_audited(as_user):
    admin = as_user()
    admin.post("/api/sessions", json=body())
    assert admin.delete("/api/kpi/records").json() == {"deleted": 1}
    log = admin.get("/api/admin/audit-logs").json()[0]
    assert log["action"] == "KPI 기록 초기화" and log["target"] == "1건" and log["actor"] == ADMIN
    assert {"id", "at", "actor", "action", "target"} == set(log)
