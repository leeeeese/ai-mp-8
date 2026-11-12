"""FastAPI 앱. 실행: uvicorn impact_agent.api.app:app --reload --port 8000 (프론트는 Vite 프록시로 /api 를 여기로 보낸다)

경로·응답은 목업(frontend/src/mocks/handlers.ts)과 같다. 구현한 것: 세션(목록·생성·조회·후속 입력·즐겨찾기·결과물·
체크리스트 답변·진행 SSE·상세 로그·분석 후 산출물(회신 메일 초안·결과물 저장·개발 전달용 정리본)), KPI 기록(조회·초기화). 적재 현황(/api/graph/*, api/graph.py). 관리(권한)(/api/admin/*, api/auth.py).
- 모든 경로는 사용자 필요 (X-User, api/auth.py). 권한·SM 파트 위반은 403.
- 분석은 백그라운드 스레드에서 Supervisor 로 돌고, 진행은 /stream (SSE) 으로 받는다.
- 요청 문장이 정답셋(eval/ground_truth)의 request_text 와 같으면 정답을 붙여 KPI 품질·판정 지표까지 계산한다.
"""

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..agents.kpi import load_answer_key
from ..agents.supervisor import COMPOSER_FOLLOWUPS
from ..schemas import AnswerKey, ChecklistOwner, ChecklistStatus, FollowupInput, FollowupType, SmPart, UserInput
from ..state import AgentState
from ..templates import render_complex
from ..trace import clip as _clip
from .agent_log import INTAKE_TITLE, Row, followup_rows, intake_rows, kpi_brief, kpi_rows, run_table, run_total
from .auth import Auth, admin_router, auth_router, can
from .graph import GraphSource, default_source, graph_router
from .runner import JsonStore, Runner, SessionRecord
from .views import answer_view, kpi_record, report_view

ROOT = Path(__file__).resolve().parents[3]
GT_DIR = ROOT / "eval" / "ground_truth"
DEFAULT_KPI_PATH = ROOT / "outputs" / "kpi" / "records.json"
DEFAULT_RELOAD_PATH = ROOT / "outputs" / "graph" / "reload_requests.json"
DEFAULT_ADMIN_DIR = ROOT / "outputs" / "admin"
DEFAULT_LOG_DIR = ROOT / "outputs" / "logs"
SM_TO_BACKEND = {"prod": SmPart.PRODUCT, "dbm": SmPart.MARKETING}
# UI 입력 유형 → 후속 입력 유형. chat(추가 질문)은 대응 유형이 없어 변경 분석(Planner 부터)으로 둔다 (가안)
KIND_TO_FOLLOWUP = {
    "chat": FollowupType.CHANGE_ANALYSIS, "change": FollowupType.CHANGE_ANALYSIS, "answer": FollowupType.BUSINESS_ANSWER,
    "query_result": FollowupType.QUERY_RESULT, "reanalysis": FollowupType.REANALYSIS,
}
TITLE_LEN = 28
KEEPALIVE_SEC = 15
OutputFormat = Literal["pdf", "md"]
MEDIA_TYPE = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8"}


class CreateSessionRequest(BaseModel):
    smPart: Literal["prod", "dbm"]
    inputType: Literal["requirement", "ops_inquiry"]
    source: Literal["mail", "itsd", "etc"]
    text: str
    files: list[str] = []
    outputOptions: list[Literal["mail_draft", "pdf"]] = []  # 분석과 함께 만들 산출물 (분석 후에는 /outputs/* 로 요청)


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
               gt_dir: Path = GT_DIR, run_in_thread: bool = True, graph_source: Callable[[], GraphSource] = default_source,
               reload_path: Path = DEFAULT_RELOAD_PATH, admin_dir: Path = DEFAULT_ADMIN_DIR,
               log_dir: Path = DEFAULT_LOG_DIR) -> FastAPI:
    """앱 생성. 테스트는 supervisor_factory(가짜 에이전트)·kpi_path·admin_dir·log_dir(임시 경로)·run_in_thread=False 를 넘긴다.
    적재 현황은 graph_source(기본 .env 의 Neo4j, 첫 요청 때 연결)·reload_path(재적재 요청 저장 파일)."""
    if supervisor_factory is None:
        from dotenv import load_dotenv

        from ..agents import Supervisor

        load_dotenv(ROOT / ".env")
        supervisor_factory = Supervisor
    runner = Runner(supervisor_factory())
    kpi = JsonStore(kpi_path)
    reload_requests = JsonStore(reload_path)
    auth = Auth(admin_dir)
    gt = ground_truth_keys(gt_dir)
    sessions: dict[str, SessionRecord] = {}
    app = FastAPI(title="Impact Agent API")

    def get(session_id: str, user: dict, view: bool = False) -> SessionRecord:
        """세션 조회 + SM 파트 검사. view 는 읽기 전용 경로 (sessions.all 이면 다른 파트도 통과)."""
        if session_id not in sessions:
            raise HTTPException(404, "세션을 찾을 수 없습니다")
        auth.check_sm(user, sessions[session_id].meta["smPart"], view=view)
        return sessions[session_id]

    def summary(rec: SessionRecord) -> dict:
        return {k: rec.meta[k] for k in ("id", "title", "smPart", "createdAt", "starred", "status")} | (
            {"complexity": rec.meta["complexity"]} if rec.meta.get("complexity") else {})

    def view(rec: SessionRecord) -> dict:
        return summary(rec) | {"inputType": rec.meta["inputType"], "source": rec.meta["source"], "messages": rec.messages}

    def execute(rec: SessionRecord, fn: Callable[[], AgentState], label: str, intake: list[Row]) -> None:
        """한 라운드 실행: 진행 이벤트 → 답변 메시지 → KPI 기록. 실패해도 진행 스트림이 끝나게 composer done 을 보낸다."""
        rec.meta["rounds"] += 1
        rec.log("supervisor", f"{rec.meta['rounds']}차 실행 · {label} · 세션 {rec.meta['id'][:8]}", kind="run")
        rec.log("supervisor", f"{INTAKE_TITLE} · {datetime.now():%H:%M:%S}", kind="open")
        for section, row_label, value in intake:
            rec.log("supervisor", value, section=section, label=row_label)
        rec.log("supervisor", "접수 완료 │ Supervisor 실행", kind="close")
        runs_before = len(rec.state.agent_runs) if rec.state else 0
        rec.push({"node": "supervisor", "status": "running", "detail": "요청 접수, SM 파트 고정",
                  "counters": {"toolCalls": 0, "exploreLoops": 0, "replans": 0}})
        try:
            state = runner.run(rec.push, fn, rec.log)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 실패를 알리고 스트림을 닫는다
            logging.getLogger("uvicorn.error").exception("[%s] 분석 실패", rec.meta["id"][:8])
            rec.log("supervisor", f"{type(exc).__name__}: {_clip(exc)}", kind="note", label="분석 실패")
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
            runs = state.agent_runs[runs_before:]
            rec.log("supervisor", "실행 요약", kind="open")
            for line in run_table(runs):
                rec.log("supervisor", line, kind="plain")
            rec.log("supervisor", run_total(runs), kind="close")
            rec.log("kpi", f"KPI 평가 · 정답셋 {state.kpi.req_id if state.kpi and state.kpi.req_id else '없음'}", kind="open")
            for label_, value in kpi_rows(state):
                rec.log("kpi", value, label=label_)
            rec.log("kpi", kpi_brief(state), kind="close")
        finally:
            rec.push({"node": "supervisor", "status": "done", "detail": "완료"})
            rec.finish()

    def start(rec: SessionRecord, fn: Callable[[], AgentState], label: str, intake: list[Row]) -> None:
        rec.start_round()
        if run_in_thread:
            threading.Thread(target=execute, args=(rec, fn, label, intake), daemon=True).start()
        else:
            execute(rec, fn, label, intake)

    # --- 세션 ------------------------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions(user: dict = Depends(auth.current_user)) -> list[dict]:
        mine = [r for r in sessions.values() if can(user, "sessions.all") or r.meta["smPart"] in user["smParts"]]
        return [summary(r) for r in sorted(mine, key=lambda r: r.meta["createdAt"], reverse=True)]

    @app.post("/api/sessions")
    def create_session(req: CreateSessionRequest, user: dict = Depends(auth.require("analyze"))) -> dict:
        auth.check_sm(user, req.smPart)
        sid = uuid.uuid4().hex
        text = req.text.strip()
        rec = SessionRecord(meta={
            "id": sid, "title": text[:TITLE_LEN] + ("…" if len(text) > TITLE_LEN else ""), "smPart": req.smPart,
            "inputType": req.inputType, "source": req.source, "starred": False, "status": "running", "createdAt": _now(),
            "agentVersion": _agent_version(), "createdBy": user["name"], "rounds": 0,
        }, log_path=log_dir / f"{sid[:8]}.log")
        rec.messages.append({"id": uuid.uuid4().hex, "role": "user", "text": text, "createdAt": _now()})
        sessions[sid] = rec
        key: AnswerKey | None = load_answer_key(gt[_norm(text)]) if _norm(text) in gt else None
        user_input = UserInput(request_text=text, sm_part=SM_TO_BACKEND[req.smPart], output_options=req.outputOptions)
        start(rec, lambda: runner.supervisor.analyze(user_input, session_id=sid, answer_key=key),
              f'새 분석 · SM {req.smPart} · {user["name"]} · 정답셋 {key.req_id if key else "없음"}',
              intake_rows(user, req.smPart, req.inputType, req.source, text, SM_TO_BACKEND[req.smPart], key.req_id if key else None, sid))
        return {"sessionId": sid}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, user: dict = Depends(auth.current_user)) -> dict:
        return view(get(session_id, user, view=True))

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, req: MessageRequest, user: dict = Depends(auth.require("analyze"))) -> dict:
        rec = get(session_id, user)
        if rec.meta["status"] == "running":
            raise HTTPException(409, "분석이 끝난 뒤 입력할 수 있습니다")
        rec.messages.append({"id": uuid.uuid4().hex, "role": "user", "kind": req.kind, "text": req.text, "createdAt": _now()})
        fu = FollowupInput(type=KIND_TO_FOLLOWUP[req.kind], content=req.text)
        intake = followup_rows(user, rec.meta["smPart"], req.kind, fu.type.value, req.text, rec.state, fu.type not in COMPOSER_FOLLOWUPS)
        start(rec, lambda: runner.supervisor.followup(session_id, fu), f'후속 입력 {fu.type.value} · {user["name"]}', intake)
        return view(rec)

    @app.post("/api/sessions/{session_id}/star")
    def toggle_star(session_id: str, user: dict = Depends(auth.current_user)) -> dict:
        rec = get(session_id, user, view=True)
        rec.meta["starred"] = not rec.meta["starred"]
        return summary(rec)

    @app.get("/api/sessions/{session_id}/report")
    def get_report(session_id: str, version: int | None = None,  # noqa: ARG001 - 버전은 v1 하나
                   user: dict = Depends(auth.require("report"))) -> dict:
        rec = get(session_id, user, view=True)
        report = report_view(rec.state) if rec.state else None
        if report is None:
            raise HTTPException(404, "복잡 요건 결과물이 없습니다")
        return report

    @app.patch("/api/sessions/{session_id}/checklist/{item_id}")
    def answer_checklist(session_id: str, item_id: str, req: ChecklistRequest,
                         user: dict = Depends(auth.require("report"))) -> dict:
        rec = get(session_id, user)
        if rec.meta["status"] == "running":
            raise HTTPException(409, "분석이 끝난 뒤 답변할 수 있습니다")
        report = rec.state.composer.complex_report if rec.state and rec.state.composer else None
        item = next((c for c in report.checklist if c.item_id == item_id), None) if report else None
        if item is None:
            raise HTTPException(404, "체크리스트 항목을 찾을 수 없습니다")
        if req.status == "confirmed" and req.answer:  # 답변 → Composer 가 결과물 갱신 (Supervisor 후속 입력)
            kind = FollowupType.BUSINESS_ANSWER if item.owner is ChecklistOwner.BUSINESS else FollowupType.DEVELOPER_OPINION
            fu = FollowupInput(type=kind, content=req.answer, target_item_ids=[item_id])
            rec.log("supervisor", _clip(req.answer), kind="note", label=f"답변 {item_id}")
            rec.state = runner.run(lambda _e: None, lambda: runner.supervisor.followup(session_id, fu), rec.log)
        else:  # 답변 없이 확정 표시 · 미확정으로 되돌리기: 결과물만 고치고 단계를 다시 매긴 뒤 세션 상태에 저장
            item.status, item.answer = ChecklistStatus(req.status), req.answer or item.answer
            done = all(c.status is ChecklistStatus.CONFIRMED for c in report.checklist)
            report.stage = "final_for_dev" if done else "updated"
            rec.state.composer.markdown = render_complex(report)
            runner.supervisor.update_composer(session_id, rec.state.composer)
            rec.log("supervisor", f"{item_id} → {req.status} · 단계 {report.stage}", kind="note", label="체크리스트")
        items = rec.state.composer.complex_report.checklist
        c = next(x for x in items if x.item_id == item_id)
        return {"id": c.item_id, "owner": c.owner.value, "question": c.question, "status": c.status.value,
                **({"answer": c.answer} if c.answer else {})}

    # --- 분석 후 산출물 (결과물 화면 버튼) ---------------------------------------------------

    def finished(session_id: str, user: dict) -> SessionRecord:
        rec = get(session_id, user, view=True)
        if rec.meta["status"] == "running":
            raise HTTPException(409, "분석이 끝난 뒤 요청할 수 있습니다")
        if rec.state is None or rec.state.composer is None:
            raise HTTPException(404, "분석 결과가 없습니다")
        return rec

    def composer_call(rec: SessionRecord, title: str, fn: Callable[[Any], Any]) -> Any:
        """Composer 를 그래프 밖에서 부른다 (Supervisor 공유 잠금 · 상세 로그는 세션 로그에)."""
        def go() -> Any:
            runner.current = "composer"
            rec.log("composer", f"{title} · {datetime.now():%H:%M:%S}", kind="open")
            try:
                return fn(runner.supervisor.composer)
            finally:
                rec.log("composer", f"{title} 완료", kind="close")
        return runner.run(lambda _e: None, go, rec.log)

    def download(rec: SessionRecord, path: str, name: str) -> FileResponse:
        p = Path(path)
        return FileResponse(p, media_type=MEDIA_TYPE.get(p.suffix, "application/octet-stream"),
                            filename=f"{name}_{rec.meta['id'][:8]}{p.suffix}", headers={"X-Output-Format": p.suffix[1:]})

    @app.post("/api/sessions/{session_id}/outputs/mail")
    def output_mail(session_id: str, user: dict = Depends(auth.require("report"))) -> dict:
        rec = finished(session_id, user)
        mail = composer_call(rec, "회신 메일 초안", lambda c: c.compose_mail(rec.state))
        rec.state.composer.mail_draft = mail
        return mail.model_dump()

    @app.get("/api/sessions/{session_id}/outputs/report")
    def output_report(session_id: str, format: OutputFormat = "pdf",  # noqa: A002 - 쿼리 이름
                      user: dict = Depends(auth.require("report"))) -> FileResponse:
        rec = finished(session_id, user)
        path, _ = composer_call(rec, f"결과물 저장 ({format})", lambda c: c.save_output(rec.state.composer.markdown, format))
        rec.state.composer.report_file_path = path
        return download(rec, path, "report")

    @app.get("/api/sessions/{session_id}/outputs/handover")
    def output_handover(session_id: str, format: OutputFormat = "pdf",  # noqa: A002 - 쿼리 이름
                        user: dict = Depends(auth.require("report"))) -> FileResponse:
        rec = finished(session_id, user)
        report = rec.state.composer.complex_report
        if report is None:
            raise HTTPException(404, "복잡 요건 결과물이 없습니다")
        if open_ := [c.item_id for c in report.checklist if c.status is not ChecklistStatus.CONFIRMED]:
            raise HTTPException(409, f"미확정 항목이 남아 있습니다: {', '.join(open_)}")
        path, _ = composer_call(rec, f"개발 전달용 정리본 ({format})",
                                lambda c: c.save_output(c.handover_markdown(rec.state), format))
        return download(rec, path, "handover")

    @app.get("/api/sessions/{session_id}/stream")
    async def stream(session_id: str, user: dict = Depends(auth.stream_user)) -> StreamingResponse:
        rec = get(session_id, user, view=True)

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

    @app.get("/api/sessions/{session_id}/logs")
    def session_logs(session_id: str, user: dict = Depends(auth.current_user)) -> list[dict]:
        return get(session_id, user, view=True).logs

    # --- KPI -------------------------------------------------------------------------

    @app.get("/api/kpi/records")
    def kpi_records(user: dict = Depends(auth.current_user)) -> list[dict]:
        return [r for r in kpi.records if can(user, "sessions.all") or r["sm_part"] in user["smParts"]]

    @app.delete("/api/kpi/records")
    def reset_kpi_records(user: dict = Depends(auth.require("settings"))) -> dict:
        deleted = kpi.clear()
        auth.record(user["name"], "KPI 기록 초기화", f"{deleted}건")
        return {"deleted": deleted}

    # --- 적재 현황 (api/graph.py) · 인증 · 관리 (api/auth.py) ---------------------------------

    app.include_router(graph_router(graph_source, reload_requests, auth))
    app.include_router(auth_router(auth))
    app.include_router(admin_router(auth, reload_requests))
    return app


def _wait(rec: SessionRecord, sent: int) -> None:
    with rec.cond:
        rec.cond.wait_for(lambda: len(rec.events) > sent or rec.finished, timeout=KEEPALIVE_SEC)


def __getattr__(name: str) -> FastAPI:
    """`uvicorn impact_agent.api.app:app` 로 띄울 때만 실제 Supervisor 로 앱을 만든다 (import 만으로는 만들지 않음)."""
    if name == "app":
        return create_app()
    raise AttributeError(name)
