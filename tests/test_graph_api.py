"""적재 현황 API 테스트 (작은 CSV 그래프 + 임시 소스 루트, Neo4j 없음).

응답 모양은 frontend/src/api/types.ts (GraphStatus · Neighborhood · ReloadRequest) 와 같아야 한다.
"""

import csv
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from impact_agent.api import create_app  # noqa: E402
from impact_agent.api.graph import CsvGraphSource, fingerprint, source_manifest  # noqa: E402
from test_api import fake_supervisor  # noqa: E402

NODES = [  # node_id, label, role, name, source_file, properties
    ("SCR:A", "Screen", "Entry", "캠페인 화면", "web/a.js", {"desc": "캠페인 등록", "url": "/a"}),
    ("TP:B", "TP", "EntryHandler", "등록 TP", "tp/b.java", {"desc": "등록"}),
    ("DBIO:C", "DBIO", "DataAccess", "캠페인 저장", "dbio/c.xml", {}),
    ("TBL:T", "Table", "Table", "캠페인", "", {"comment": "캠페인 마스터"}),
    ("BAT:J", "Batch", "Entry", "발송 Job", "batch/j.xml",
     {"desc": "발송", "steps": ["STEP01 적재 (PROC j1)", "STEP02 결과 (PROC j2)"], "program": "j1"}),
    ("MOD:X", "Module", "Logic", "외톨이", "mod/x.java", {"desc": "연결 없음"}),
]
EDGES = [("SCR:A", "TP:B", "OPENS", {}), ("TP:B", "DBIO:C", "INVOKES", {}),
         ("DBIO:C", "TBL:T", "WRITES", {"columns": "CMPN_ID,CMPN_NM"}), ("BAT:J", "TBL:T", "READS", {})]


@pytest.fixture
def env(tmp_path, monkeypatch):
    gdir, src = tmp_path / "graph", tmp_path / "src"
    gdir.mkdir()
    with open(gdir / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "label", "role", "name", "domain", "source_file", "source_line", "properties"])
        for nid, label, role, name, sf, p in NODES:
            w.writerow([nid, label, role, name, "tos", sf, 1, json.dumps(p, ensure_ascii=False)])
    with open(gdir / "edges.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "type", "properties"])
        for a, b, t, p in EDGES:
            w.writerow([a, b, t, json.dumps(p)])
    for rel in ("web/a.js", "tp/b.java", "dbio/c.xml", "batch/j.xml"):  # mod/x.java 는 원문 없음
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(rel, encoding="utf-8")
    monkeypatch.setenv("SOURCE_ROOT_TOS", str(src))
    manifest = source_manifest(src)
    meta = {"domain": "tos", "loaded_at": "2026-10-29T10:00:00+09:00", "files": list(manifest), "hashes": list(manifest.values()),
            "fingerprint": fingerprint(manifest), "nodes": 6, "rels": 4, "warnings": 0}
    old = {**meta, "loaded_at": "2026-10-20T10:00:00+09:00", "fingerprint": "0" * 40, "nodes": 5, "warnings": 2}
    app = create_app(fake_supervisor, kpi_path=tmp_path / "kpi.json", run_in_thread=False,
                     graph_source=lambda: CsvGraphSource(gdir, [old, meta]), reload_path=tmp_path / "reload.json")
    return TestClient(app), src, tmp_path


def test_status_counts_checks_and_history(env):
    c, _, _ = env
    s = c.get("/api/graph/status?sm=dbm").json()
    assert s["sm"] == "dbm" and s["connected"] and (s["nodes"], s["rels"]) == (6, 4)
    assert [lb["label"] for lb in s["labels"]] == ["Screen", "Batch", "TP", "Module", "DBIO", "Table"]
    assert s["sourceMapRate"] == pytest.approx(4 / 6)  # TBL 은 source_file 없음, MOD 는 원문 파일 없음
    checks = {ck["id"]: ck["items"] for ck in s["checks"]}
    assert checks["isolated"] == ["MOD:X"]
    assert checks["rw-columns"] == ["BAT:J → TBL:T"]
    assert checks["source-file"] == ["TBL:T"]
    assert checks["meta"] == ["DBIO:C"]
    assert checks["missing-source"] == ["BAT:J → j2.pc"]
    assert [h["result"] for h in s["history"]] == ["success", "warning"]  # 최근 순
    m = s["masterSync"]
    assert (m["percent"], m["behindCommits"], m["pendingFiles"]) == (100, 0, [])
    assert m["loadedCommit"] == m["masterCommit"] and m["loadedAt"] == "2026-10-29T10:00:00+09:00"


def test_master_sync_detects_changed_added_and_removed_files(env):
    c, src, _ = env
    (src / "web/a.js").write_text("changed", encoding="utf-8")
    (src / "new.js").write_text("new", encoding="utf-8")
    (src / "tp/b.java").unlink()
    m = c.get("/api/graph/status?sm=dbm").json()["masterSync"]
    assert m["behindCommits"] == 1 and m["loadedCommit"] != m["masterCommit"]
    assert m["pendingFiles"] == ["new.js", "tp/b.java (삭제)", "web/a.js"]
    assert m["percent"] == 40  # 5개 중 2개만 같음


def test_search_ranks_id_before_name_and_desc(env):
    c, _, _ = env
    assert [h["id"] for h in c.get("/api/graph/search", params={"sm": "dbm", "q": "tp:b"}).json()] == ["TP:B"]
    assert c.get("/api/graph/search", params={"sm": "dbm", "q": "T"}).json()[0]["id"] == "TBL:T"  # 접두어 뺀 ID 일치
    hits = c.get("/api/graph/search", params={"sm": "dbm", "q": "캠페인"}).json()
    assert [h["id"] for h in hits] == ["DBIO:C", "SCR:A", "TBL:T"]  # 이름 일치 (desc 만 맞는 노드는 뒤)
    assert hits[0] == {"id": "DBIO:C", "name": "캠페인 저장", "label": "DBIO"}
    assert c.get("/api/graph/search", params={"sm": "prod", "q": "캠페인"}).json() == []  # 다른 영역


def test_neighbors_two_hops_and_404(env):
    c, _, _ = env
    n = c.get("/api/graph/neighbors", params={"sm": "dbm", "nodeId": "TP:B", "hops": 2}).json()
    assert n["center"] == "TP:B"
    assert {x["id"]: x["hop"] for x in n["nodes"]} == {"TP:B": 0, "SCR:A": 1, "DBIO:C": 1, "TBL:T": 2}
    assert {(e["from"], e["to"]) for e in n["edges"]} == {("SCR:A", "TP:B"), ("TP:B", "DBIO:C"), ("DBIO:C", "TBL:T")}
    w = next(e for e in n["edges"] if e["type"] == "WRITES")
    assert w["columns"] == "CMPN_ID,CMPN_NM"
    scr = next(x for x in n["nodes"] if x["id"] == "SCR:A")
    assert scr["sourceFile"] == "web/a.js" and scr["url"] == "/a" and "steps" not in scr
    assert c.get("/api/graph/neighbors", params={"sm": "dbm", "nodeId": "없음"}).status_code == 404


def test_reload_request_saved_and_admin_still_501(env):
    c, _, tmp = env
    r = c.post("/api/graph/reload-requests", json={"sm": "dbm", "scope": "online/cp", "reason": "수정분 반영"}).json()
    assert r["status"] == "pending" and r["sm"] == "dbm" and r["id"].startswith("r")
    assert json.loads((tmp / "reload.json").read_text(encoding="utf-8"))[0]["id"] == r["id"]
    assert c.get("/api/admin/users").status_code == 501


def test_disconnected_store_still_renders(tmp_path):
    def broken():
        raise ConnectionError("down")
    c = TestClient(create_app(fake_supervisor, kpi_path=tmp_path / "kpi.json", run_in_thread=False, graph_source=broken,
                              reload_path=tmp_path / "r.json"))
    s = c.get("/api/graph/status?sm=prod").json()
    assert s["connected"] is False and s["nodes"] == 0
    assert c.get("/api/graph/search?sm=prod&q=a").status_code == 503
