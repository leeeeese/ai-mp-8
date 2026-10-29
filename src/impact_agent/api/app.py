"""FastAPI 앱. 실행: uvicorn impact_agent.api.app:app --reload --port 8000 (프론트는 Vite 프록시로 /api 를 여기로 보낸다)

경로·응답은 목업(frontend/src/mocks/handlers.ts)과 같다. 구현한 것: 세션(목록·생성·조회·후속 입력·즐겨찾기·결과물·
체크리스트 답변·진행 SSE), KPI 기록(조회·초기화). 적재 현황(/api/graph/*)·관리(/api/admin/*)는 아직 501.
- 분석은 백그라운드 스레드에서 Supervisor 로 돌고, 진행은 /stream (SSE) 으로 받는다.
- 요청 문장이 정답셋(eval/ground_truth)의 request_text 와 같으면 정답을 붙여 KPI 품질·판정 지표까지 계산한다.
"""

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agents.kpi import load_answer_key
from ..schemas import AnswerKey, ChecklistOwner, ChecklistStatus, FollowupInput, FollowupType, SmPart, UserInput
from ..state import AgentState
from .runner import KpiStore, Runner, SessionRecord
from .views import answer_view, kpi_record, report_view

ROOT = Path(__file__).resolve().parents[3]
GT_DIR = ROOT / "eval" / "ground_truth"
DEFAULT_KPI_PATH = ROOT / "outputs" / "kpi" / "records.json"
SM_TO_BACKEND = {"prod": SmPart.PRODUCT, "dbm": SmPart.MARKETING}
# UI 입력 유형 → 후속 입력 유형. chat(추가 질문)은 대응 유형이 없어 변경 분석(Planner 부터)으로 둔다 (가안)
KIND_TO_FOLLOWUP = {
    "chat": FollowupType.CHANGE_ANALYSIS, "change": FollowupType.CHANGE_ANALYSIS, "answer": FollowupType.BUSINESS_ANSWER,
    "query_result": FollowupType.QUERY_RESULT, "reanalysis": FollowupType.REANALYSIS,
}
TITLE_LEN = 28
KEEPALIVE_SEC = 15


class CreateSessionRequest(BaseModel):
    smPart: Literal["prod", "dbm"]
    inputType: Literal["requirement", "ops_inquiry"]
    source: Literal["mail", "itsd", "etc"]
    text: str
    files: list[str] = []


class MessageRequest(BaseModel):
    kind: Literal["chat", "query_result", "answer", "change", "reanalysis"]
    text: str


class ChecklistRequest(BaseModel):
    answer: str | None = None
    status: Literal["confirmed", "unconfirmed"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(text: str) -> str:
    return " ".join(text.split())


def _agent_version() -> str:
    try:
        return version("impact-agent")
    except PackageNotFoundError:
        return "dev"


def ground_truth_keys(gt_dir: Path = GT_DIR) -> dict[str, Path]:
    """정답셋 요청 문장(공백 정리) → 정답 파일. 같은 문장으로 요청하면 정답을 붙인다."""
    out = {}
    for f in sorted(gt_dir.glob("*.json")):
        text = json.loads(f.read_text(encoding="utf-8")).get("request_text")
        if text:
            out[_norm(text)] = f
    return out


def create_app(supervisor_factory: Callable[[], Any] | None = None, kpi_path: Path = DEFAULT_KPI_PATH,
               gt_dir: Path = GT_DIR, run_in_thread: bool = True) -> FastAPI:
    """앱 생성. 테스트는 supervisor_factory(가짜 에이전트)·kpi_path(임시 파일)·run_in_thread=False 를 넘긴다."""
    if supervisor_factory is None:
        from dotenv import load_dotenv

        from ..agents import Supervisor

        load_dotenv(ROOT / ".env")
        supervisor_factory = Supervisor
    runner = Runner(supervisor_factory())
    kpi = KpiStore(kpi_path)
    gt = ground_truth_keys(gt_dir)
    sessions: dict[str, SessionRecord] = {}
    app = FastAPI(title="Impact Agent API")

    def get(session_id: str) -> SessionRecord:
        if session_id not in sessions:
            raise HTTPException(404, "세션을 찾을 수 없습니다")
        return sessions[session_id]

    def summary(rec: SessionRecord) -> dict:
        return {k: rec.meta[k] for k in ("id", "title", "smPart", "createdAt", "starred", "status")} | (
            {"complexity": rec.meta["complexity"]} if rec.meta.get("complexity") else {})

    def view(rec: SessionRecord) -> dict:
        return summary(rec) | {"inputType": rec.meta["inputType"], "source": rec.meta["source"], "messages": rec.messages}

    def execute(rec: SessionRecord, fn: Callable[[], AgentState]) -> None:
        """한 라운드 실행: 진행 이벤트 → 답변 메시지 → KPI 기록. 실패해도 진행 스트림이 끝나게 composer done 을 보낸다."""
        rec.push({"node": "supervisor", "status": "running", "detail": "요청 접수, SM 파트 고정",
                  "counters": {"toolCalls": 0, "exploreLoops": 0, "replans": 0}})
        try:
            state = runner.run(rec.push, fn)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 실패를 알리고 스트림을 닫는다
            rec.messages.append({"id": uuid.uuid4().hex, "role": "agent", "text": f"분석 중 오류가 발생했습니다: {exc}",
                                 "createdAt": _now()})
            rec.push({"node": "composer", "status": "done", "detail": "오류"})
        else:
            rec.state = state
            if state.composer is not None:
                rec.meta["complexity"] = state.composer.complexity.value
                rec.messages.append({"id": uuid.uuid4().hex, "role": "agent", "answer": answer_view(state), "createdAt": _now()})
            if state.kpi is not None:
                rec.meta["finishedAt"] = _now()
                kpi.add(kpi_record(rec.meta, state))
        finally:
            rec.push({"node": "supervisor", "status": "done", "detail": "완료"})
            rec.finish()

    def start(rec: SessionRecord, fn: Callable[[], AgentState]) -> None:
        rec.start_round()
        if run_in_thread:
            threading.Thread(target=execute, args=(rec, fn), daemon=True).start()
        else:
            execute(rec, fn)

    # --- 세션 ------------------------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return [summary(r) for r in sorted(sessions.values(), key=lambda r: r.meta["createdAt"], reverse=True)]

    @app.post("/api/sessions")
    def create_session(req: CreateSessionRequest) -> dict:
        sid = uuid.uuid4().hex
        text = req.text.strip()
        rec = SessionRecord(meta={
            "id": sid, "title": text[:TITLE_LEN] + ("…" if len(text) > TITLE_LEN else ""), "smPart": req.smPart,
            "inputType": req.inputType, "source": req.source, "starred": False, "status": "running", "createdAt": _now(),
            "agentVersion": _agent_version(),
        })
        rec.messages.append({"id": uuid.uuid4().hex, "role": "user", "text": text, "createdAt": _now()})
        sessions[sid] = rec
        key: AnswerKey | None = load_answer_key(gt[_norm(text)]) if _norm(text) in gt else None
        user_input = UserInput(request_text=text, sm_part=SM_TO_BACKEND[req.smPart])
        start(rec, lambda: runner.supervisor.analyze(user_input, session_id=sid, answer_key=key))
        return {"sessionId": sid}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return view(get(session_id))

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, req: MessageRequest) -> dict:
        rec = get(session_id)
        if rec.meta["status"] == "running":
            raise HTTPException(409, "분석이 끝난 뒤 입력할 수 있습니다")
        rec.messages.append({"id": uuid.uuid4().hex, "role": "user", "kind": req.kind, "text": req.text, "createdAt": _now()})
        fu = FollowupInput(type=KIND_TO_FOLLOWUP[req.kind], content=req.text)
        start(rec, lambda: runner.supervisor.followup(session_id, fu))
        return view(rec)

    @app.post("/api/sessions/{session_id}/star")
    def toggle_star(session_id: str) -> dict:
        rec = get(session_id)
        rec.meta["starred"] = not rec.meta["starred"]
        return summary(rec)

    @app.get("/api/sessions/{session_id}/report")
    def get_report(session_id: str, version: int | None = None) -> dict:  # noqa: ARG001 - 버전은 v1 하나
        rec = get(session_id)
        report = report_view(rec.state) if rec.state else None
        if report is None:
            raise HTTPException(404, "복잡 요건 결과물이 없습니다")
        return report

    @app.patch("/api/sessions/{session_id}/checklist/{item_id}")
    def answer_checklist(session_id: str, item_id: str, req: ChecklistRequest) -> dict:
        rec = get(session_id)
        if rec.meta["status"] == "running":
            raise HTTPException(409, "분석이 끝난 뒤 답변할 수 있습니다")
        report = rec.state.composer.complex_report if rec.state and rec.state.composer else None
        item = next((c for c in report.checklist if c.item_id == item_id), None) if report else None
        if item is None:
            raise HTTPException(404, "체크리스트 항목을 찾을 수 없습니다")
        if req.status == "confirmed" and req.answer:  # 답변 → Composer 가 결과물 갱신 (Supervisor 후속 입력)
            kind = FollowupType.BUSINESS_ANSWER if item.owner is ChecklistOwner.BUSINESS else FollowupType.DEVELOPER_OPINION
            fu = FollowupInput(type=kind, content=req.answer, target_item_ids=[item_id])
            rec.state = runner.run(lambda _e: None, lambda: runner.supervisor.followup(session_id, fu))
        else:  # 미확정으로 되돌리기: 결과물만 고친다
            item.status, item.answer = ChecklistStatus(req.status), req.answer
        items = rec.state.composer.complex_report.checklist
        c = next(x for x in items if x.item_id == item_id)
        return {"id": c.item_id, "owner": c.owner.value, "question": c.question, "status": c.status.value,
                **({"answer": c.answer} if c.answer else {})}

    @app.get("/api/sessions/{session_id}/stream")
    async def stream(session_id: str) -> StreamingResponse:
        rec = get(session_id)

        async def events():
            sent = 0
            while True:
                pending = rec.events[sent:]
                for e in pending:
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                sent += len(pending)
                if rec.finished and sent >= len(rec.events):
                    return
                await asyncio.to_thread(_wait, rec, sent)
                if sent >= len(rec.events) and not rec.finished:
                    yield ": keepalive\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # --- KPI -------------------------------------------------------------------------

    @app.get("/api/kpi/records")
    def kpi_records() -> list[dict]:
        return kpi.records

    @app.delete("/api/kpi/records")
    def reset_kpi_records() -> dict:
        return {"deleted": kpi.clear()}

    # --- 아직 구현하지 않은 API ----------------------------------------------------------

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    def not_implemented(rest: str) -> None:
        raise HTTPException(501, f"/api/{rest} 는 아직 구현하지 않았습니다 (적재 현황·관리 API)")

    return app


def _wait(rec: SessionRecord, sent: int) -> None:
    with rec.cond:
        rec.cond.wait_for(lambda: len(rec.events) > sent or rec.finished, timeout=KEEPALIVE_SEC)


def __getattr__(name: str) -> FastAPI:
    """`uvicorn impact_agent.api.app:app` 로 띄울 때만 실제 Supervisor 로 앱을 만든다 (import 만으로는 만들지 않음)."""
    if name == "app":
        return create_app()
    raise AttributeError(name)
