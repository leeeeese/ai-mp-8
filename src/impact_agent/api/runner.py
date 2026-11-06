"""세션 실행 · 진행 이벤트 · 저장소.

- Supervisor 를 고치지 않는다. Supervisor 는 에이전트를 getattr(self, name).run(state) 로 부르므로, 각 에이전트를
  ProgressAgent 로 감싸 실행 시작·완료 때 AgentEvent(frontend AgentEvent 모양)를 남긴다. last_usage 등은 그대로 넘긴다.
- Supervisor 하나를 공유한다 (세션 기억 = checkpointer). 분석은 한 번에 하나씩 (PoC, 순서 보장).
- 상세 실행 로그: 에이전트 완료 요약(agent_log.py)과 진행 중 로그(trace.py: 라우팅·tool·LLM·ReAct)를 세션 로그로 모은다.
  터미널 · 세션 로그 파일(outputs/logs/<세션 8자>.log) · GET /api/sessions/{id}/logs 에 같은 내용.
- KPI 기록·관리 데이터는 JSON 파일에 쌓는다 (세션·분석 결과는 메모리, 서버를 다시 띄우면 사라짐).
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..schemas import Complexity
from ..state import AgentState
from ..trace import logger as trace_logger
from .agent_log import TITLE, input_rows, output_rows, render, render_brief

Emit = Callable[[dict], None]
Log = Callable[..., None]  # SessionRecord.log
console = logging.getLogger("impact_agent.console")  # 터미널 진행 출력 (uvicorn 머리표 없이, 에이전트 시작·끝·라우팅만)
console.propagate = False
if not console.handlers:
    console.addHandler(logging.StreamHandler())
    console.setLevel(logging.INFO)
PARTNER_ACTION = {
    "simulator": "영향 경로 골격 → 영역 분류 → LLM 영역별 판정(영향·가능성·난이도·대안) → 규칙 보정",
    "verifier": "규칙 점검(영역 누락·근거·교차 연계·중복·재분석 반영) + LLM 대안 근거 판단",
    "composer": "규칙 골격 + LLM 문장 작성 (단순 응답 또는 레포트·체크리스트), 상세 분석 권장 신호 계산",
}
SHOWN_AGENTS = ("planner", "explorer", "retriever", "simulator", "verifier", "composer")  # 진행 패널 단계 (KPI 제외)


def _detail(name: str, out: Any) -> str:
    if name == "planner":
        return f"{'단순' if out.complexity is Complexity.SIMPLE else '복잡'} · 항목 {len(out.items)}개"
    if name == "explorer":
        return f"시작 노드 {len(out.start_nodes)} · 경로 {len(out.paths)}"
    if name == "retriever":
        return f"근거 {len(out.evidence)}" + (" · 재탐색 요청" if out.reexplore_request else "")
    if name == "simulator":
        return f"영역 {sum(a.impact_type.value in ('add', 'modify') for a in out.area_impacts)}개 변경"
    if name == "verifier":
        return "통과" if out.result.value == "pass" else "보완 필요 → 재계획"
    if name == "composer":
        return "레포트 · 체크리스트" if out.complex_report else "단순 응답"
    return ""


def _title(name: str, state: AgentState) -> str:
    title = TITLE[name]
    if name in ("explorer", "retriever"):
        title += f" · 왕복 {state.explore_loop_count + 1}/{config.MAX_EXPLORE_LOOP}"
    elif state.replan_count:
        title += f" · 재계획 {state.replan_count}차"
    return f"{title} · {datetime.now():%H:%M:%S}"


def _footer(event: dict, agent: Any) -> str:
    usage = getattr(agent, "last_usage", None)
    tokens = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0)
    parts = [f"완료 {event['elapsedMs'] / 1000:.1f}s", event["detail"], f"토큰 {tokens:,}"]
    if event["node"] in ("explorer", "retriever"):
        parts.insert(2, f"tool 누적 {event['counters']['toolCalls']}/{config.MAX_TOOL_CALLS}")
    return " │ ".join(parts)


class ProgressAgent:
    """에이전트 래퍼: run 전후로 진행 이벤트를 남기고 나머지 속성은 원래 에이전트로 넘긴다."""

    def __init__(self, name: str, inner: Any, runner: "Runner") -> None:
        self.name, self.inner, self.runner = name, inner, runner

    def __getattr__(self, item: str) -> Any:
        return getattr(self.inner, item)

    def run(self, state: AgentState) -> Any:
        if self.name == "composer":  # 단순 flow 면 이번 실행에 Simulator·Verifier 가 없었음
            for skipped in ("simulator", "verifier"):
                if skipped not in self.runner.ran:
                    self.runner.emit({"node": skipped, "status": "skipped", "detail": "단순 요건"})
                    self.runner.log("생략 (단순 요건)", skipped, kind="note", label=TITLE[skipped])
        self.runner.ran.add(self.name)
        self.runner.current = self.name
        self.runner.emit({"node": self.name, "status": "running"})
        self.runner.log(_title(self.name, state), self.name, kind="open")
        for section, label, text in input_rows(self.name, state):
            self.runner.log(text, self.name, section=section, label=label)
        if self.name in PARTNER_ACTION:  # 진행 중 로그를 남기지 않는 에이전트: LLM 호출 행이 들어갈 동작 섹션을 연다
            self.runner.log(PARTNER_ACTION[self.name], self.name, section="동작", label="처리")
        start = time.perf_counter()
        out = self.inner.run(state)
        for section, label, text in output_rows(self.name, out):
            self.runner.log(text, self.name, section=section, label=label)
        event = {
            "node": self.name, "status": "done", "elapsedMs": round((time.perf_counter() - start) * 1000),
            "detail": _detail(self.name, out),
            "counters": {"toolCalls": state.tool_call_count + (getattr(out, "tool_call_count", 0) or 0),
                         "exploreLoops": state.explore_loop_count + (self.name == "retriever"),
                         "replans": state.replan_count},
        }
        if self.name == "retriever":
            event["evidence"] = [{"nodeId": e.node_id, "file": e.file_path, **({"location": e.location} if e.location else {})}
                                 for e in out.evidence if e.node_id][:20]
        self.runner.log(_footer(event, self.inner), self.name, kind="close")
        self.runner.emit(event)
        return out


class _TraceHandler(logging.Handler):
    """impact_agent.trace 로그를 실행 중인 세션 로그로 넘긴다."""

    def __init__(self, runner: "Runner") -> None:
        super().__init__()
        self.runner = runner

    def emit(self, record: logging.LogRecord) -> None:
        self.runner.log(record.getMessage(), record.agent, section=record.section, label=record.label, kind=record.kind)


class Runner:
    """Supervisor 실행기. 에이전트를 감싸고, 실행 중인 세션의 이벤트 수신처(sink)·로그 수신처로 보낸다."""

    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor
        for name in SHOWN_AGENTS:
            setattr(supervisor, name, ProgressAgent(name, getattr(supervisor, name), self))
        self._lock = threading.Lock()
        self._sink: Emit | None = None
        self._log: Log | None = None
        self.current = "supervisor"  # 로그에 찍을 실행 중인 에이전트
        self.ran: set[str] = set()

    def emit(self, event: dict) -> None:
        if self._sink is not None:
            self._sink(event)

    def log(self, text: str, agent: str | None = None, **fields: Any) -> None:
        if self._log is not None:
            self._log(agent or self.current, text, **fields)

    def run(self, sink: Emit, fn: Callable[[], AgentState], log_to: Log | None = None) -> AgentState:
        handler = _TraceHandler(self)
        with self._lock:  # Supervisor 공유 → 한 번에 하나씩
            self._sink, self._log, self.ran, self.current = sink, log_to, set(), "supervisor"
            trace_logger.addHandler(handler)
            trace_logger.setLevel(logging.INFO)
            try:
                return fn()
            finally:
                trace_logger.removeHandler(handler)
                self._sink = self._log = None


@dataclass
class SessionRecord:
    meta: dict
    messages: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)  # 상세 실행 로그 (라운드를 넘어 누적)
    log_path: Path | None = None
    _section: str | None = None  # 마지막으로 찍은 섹션 (섹션 머리 줄 판단)
    _title: str = ""  # 지금 열려 있는 블록 제목 (터미널 블록 끝 줄에 붙임)
    finished: bool = False
    state: AgentState | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

    def log(self, agent: str, text: str, kind: str = "row", section: str | None = None, label: str = "") -> None:
        """로그 한 건을 저장하고, 터미널엔 진행만(agent_log.render_brief), 세션 로그 파일엔 전체 블록(agent_log.render)을 찍는다."""
        entry = {"at": datetime.now().strftime("%H:%M:%S"), "agent": agent, "kind": kind, "section": section, "label": label, "text": text}
        self.logs.append(entry)
        if kind == "open":
            self._title = text
        for line in render_brief(entry, self._title):
            console.info(line)
        lines, self._section = render(entry, self._section)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("".join(f"{line}\n" for line in lines))

    def push(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def start_round(self) -> None:
        with self.cond:
            self.events, self.finished = [], False
            self.meta["status"] = "running"

    def finish(self) -> None:
        with self.cond:
            self.finished = True
            self.meta["status"] = "done"
            self.cond.notify_all()


class JsonStore:
    """JSON 목록 저장소 (KPI 기록·재적재 요청·사용자·감사 로그, 최근 순). 파일에 저장해 서버를 다시 띄워도 남는다."""

    def __init__(self, path: Path, seed: list[dict] | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.records: list[dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(seed or [])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, record: dict) -> None:
        with self._lock:
            self.records.insert(0, record)
            self._save()

    def update(self, fn: Callable[[list[dict]], Any]) -> Any:
        """목록을 잠그고 fn 으로 고쳐 저장한다. fn 이 예외를 내면 저장하지 않는다."""
        with self._lock:
            result = fn(self.records)
            self._save()
            return result

    def clear(self) -> int:
        with self._lock:
            deleted = len(self.records)
            self.records = []
            self._save()
            return deleted
