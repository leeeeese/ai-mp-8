"""UI 백엔드 API 테스트 (가짜 Planner·Explorer·Retriever + 실제 Simulator·Verifier·Composer·KPI, LLM·Neo4j 없음).

응답 모양은 frontend/src/api/types.ts · 목업 handlers.ts 와 같아야 한다.
"""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from impact_agent.agents import ComposerAgent, SimulatorAgent, Supervisor, VerifierAgent  # noqa: E402
from impact_agent.api import create_app  # noqa: E402
from impact_agent.schemas import AgentName, Complexity, PlanStep  # noqa: E402
from test_simulator import Fixed, make_state  # noqa: E402

COMPLEX_TEXT = "캠페인 비광고 여부 전송"


def fake_supervisor(complexity=Complexity.COMPLEX):
    base = make_state()
    steps = [AgentName.EXPLORER, AgentName.RETRIEVER] + (
        [AgentName.SIMULATOR, AgentName.VERIFIER] if complexity is Complexity.COMPLEX else [])
    planner = base.planner.model_copy(update={"complexity": complexity, "complexity_reason": "테스트 판정",
                                              "plan": [PlanStep(agent=a, purpose="p") for a in steps]})
    sup = Supervisor()
    sup.planner, sup.explorer, sup.retriever = Fixed(planner), Fixed(base.explorer), Fixed(base.retriever)
    sup.simulator, sup.verifier, sup.composer = SimulatorAgent(use_llm=False), VerifierAgent(use_llm=False), ComposerAgent(use_llm=False)
    return sup


@pytest.fixture
def gt_dir(tmp_path):
    d = tmp_path / "gt"
    d.mkdir()
    src = json.loads(open("eval/ground_truth/SC-001.json", encoding="utf-8").read())
    src["request_text"] = COMPLEX_TEXT                     # 이 문장으로 요청하면 정답을 붙인다
    (d / "SC-001.json").write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    return d


def client(tmp_path, gt_dir, complexity=Complexity.COMPLEX):
    app = create_app(lambda: fake_supervisor(complexity), kpi_path=tmp_path / "kpi.json", gt_dir=gt_dir, run_in_thread=False)
    return TestClient(app)


def create(c, text=COMPLEX_TEXT, sm="dbm", input_type="requirement"):
    r = c.post("/api/sessions", json={"smPart": sm, "inputType": input_type, "source": "mail", "text": text})
    assert r.status_code == 200
    return r.json()["sessionId"]


def test_complex_session_answer_report_stream_and_kpi(tmp_path, gt_dir):
    c = client(tmp_path, gt_dir)
    sid = create(c)
    s = c.get(f"/api/sessions/{sid}").json()
    assert s["status"] == "done" and s["complexity"] == "complex" and s["smPart"] == "dbm"
    assert [m["role"] for m in s["messages"]] == ["user", "agent"]
    a = s["messages"][1]["answer"]
    assert a["complexity"] == "complex" and a["plannerVerdict"] == "복잡 — 테스트 판정"
    assert {"area", "impact", "path", "requested", "alternative", "confidence"} <= set(a["areas"][0])
    assert any(p.startswith("Simulator:") for p in a["process"])

    report = c.get(f"/api/sessions/{sid}/report").json()
    assert report["version"] == 1 and report["difficulty"] in ("Major", "Medium", "Minor") and report["checklist"]

    # 진행 이벤트: 감싼 에이전트마다 running → done, KPI 는 진행 패널에 보내지 않음, composer done 으로 끝
    lines = [json.loads(x[6:]) for x in c.get(f"/api/sessions/{sid}/stream").text.split("\n\n") if x.startswith("data: ")]
    nodes = [(e["node"], e["status"]) for e in lines]
    assert ("simulator", "done") in nodes and ("composer", "done") in nodes and not any(n == "kpi" for n, _ in nodes)
    assert nodes[0] == ("supervisor", "running") and nodes[-1] == ("supervisor", "done")

    # 정답셋 문장 → KPI 품질·판정 지표까지
    (rec,) = c.get("/api/kpi/records").json()
    assert rec["session_id"] == sid and rec["req_id"] == "SC-001" and rec["evaluable"]
    assert "node_recall" in rec["metrics"] and "complexity_match" in rec["metrics"] and rec["counts"] is not None
    assert json.loads((tmp_path / "kpi.json").read_text(encoding="utf-8"))[0]["session_id"] == sid   # 파일에 저장


def test_simple_session_detail_fields_and_skipped_events(tmp_path, gt_dir):
    c = client(tmp_path, gt_dir, Complexity.SIMPLE)
    sid = create(c, text="일반 운영 문의", input_type="ops_inquiry")
    a = c.get(f"/api/sessions/{sid}").json()["messages"][1]["answer"]
    assert a["complexity"] == "simple" and "evidence" in a and "checkQueries" in a
    assert {"detail_analysis_recommended", "detail_analysis_reasons"} <= set(a)          # 백엔드와 같은 이름
    nodes = [(json.loads(x[6:])["node"], json.loads(x[6:])["status"])
             for x in c.get(f"/api/sessions/{sid}/stream").text.split("\n\n") if x.startswith("data: ")]
    assert ("simulator", "skipped") in nodes and ("verifier", "skipped") in nodes
    assert nodes.index(("simulator", "skipped")) < nodes.index(("composer", "done"))     # UI 가 composer done 에서 닫음
    assert c.get(f"/api/sessions/{sid}/report").status_code == 404
    (rec,) = c.get("/api/kpi/records").json()
    assert rec["req_id"] is None and not rec["evaluable"] and "latency_ms" in rec["metrics"]   # 일반 요청: 성능만


def test_followup_message_and_checklist_answer(tmp_path, gt_dir):
    c = client(tmp_path, gt_dir)
    sid = create(c)
    c.post(f"/api/sessions/{sid}/messages", json={"kind": "reanalysis", "text": "SVC:CmpSvc.save 확인"})
    s = c.get(f"/api/sessions/{sid}").json()          # 테스트는 동기 실행이라 답변까지 붙어 있음
    assert [m["role"] for m in s["messages"]] == ["user", "agent", "user", "agent"]
    assert s["messages"][2]["kind"] == "reanalysis" and s["status"] == "done"
    records = c.get("/api/kpi/records").json()
    assert len(records) == 2 and records[0]["reanalyzed"]                                # 최근 순, 재분석 표시

    item = c.get(f"/api/sessions/{sid}/report").json()["checklist"][0]
    r = c.patch(f"/api/sessions/{sid}/checklist/{item['id']}", json={"answer": "예, 적용", "status": "confirmed"}).json()
    assert r["status"] == "confirmed" and r["answer"] == "예, 적용"
    r = c.patch(f"/api/sessions/{sid}/checklist/{item['id']}", json={"status": "unconfirmed"}).json()
    assert r["status"] == "unconfirmed"
    assert c.patch(f"/api/sessions/{sid}/checklist/NOPE", json={"status": "confirmed"}).status_code == 404


def test_list_star_reset_and_not_implemented(tmp_path, gt_dir):
    c = client(tmp_path, gt_dir)
    sid = create(c)
    assert c.get("/api/sessions").json()[0]["id"] == sid
    assert c.post(f"/api/sessions/{sid}/star").json()["starred"] is True
    assert c.delete("/api/kpi/records").json() == {"deleted": 1}
    assert c.get("/api/kpi/records").json() == []
    assert c.get("/api/sessions/nope").status_code == 404
    assert c.get("/api/graph/status?sm=dbm").status_code == 501
