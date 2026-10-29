"""세션 실행 · 진행 이벤트 · 저장소.

- Supervisor 를 고치지 않는다. Supervisor 는 에이전트를 getattr(self, name).run(state) 로 부르므로, 각 에이전트를
  ProgressAgent 로 감싸 실행 시작·완료 때 AgentEvent(frontend AgentEvent 모양)를 남긴다. last_usage 등은 그대로 넘긴다.
- Supervisor 하나를 공유한다 (세션 기억 = checkpointer). 분석은 한 번에 하나씩 (PoC, 순서 보장).
- KPI 기록은 JSON 파일에 쌓는다 (세션·분석 결과는 메모리, 서버를 다시 띄우면 사라짐).
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import Complexity
from ..state import AgentState

Emit = Callable[[dict], None]
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
        self.runner.ran.add(self.name)
        self.runner.emit({"node": self.name, "status": "running"})
        start = time.perf_counter()
        out = self.inner.run(state)
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
        self.runner.emit(event)
        return out


class Runner:
    """Supervisor 실행기. 에이전트를 감싸고, 실행 중인 세션의 이벤트 수신처(sink)로 이벤트를 보낸다."""

    def __init__(self, supervisor: Any) -> None:
        self.supervisor = supervisor
        for name in SHOWN_AGENTS:
            setattr(supervisor, name, ProgressAgent(name, getattr(supervisor, name), self))
        self._lock = threading.Lock()
        self._sink: Emit | None = None
        self.ran: set[str] = set()

    def emit(self, event: dict) -> None:
        if self._sink is not None:
            self._sink(event)

    def run(self, sink: Emit, fn: Callable[[], AgentState]) -> AgentState:
        with self._lock:  # Supervisor 공유 → 한 번에 하나씩
            self._sink, self.ran = sink, set()
            try:
                return fn()
            finally:
                self._sink = None


@dataclass
class SessionRecord:
    meta: dict
    messages: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    finished: bool = False
    state: AgentState | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

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


class KpiStore:
    """세션별 KPI 기록 (최근 순). JSON 파일에 저장해 서버를 다시 띄워도 남는다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.records: list[dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, record: dict) -> None:
        with self._lock:
            self.records.insert(0, record)
            self._save()

    def clear(self) -> int:
        with self._lock:
            deleted = len(self.records)
            self.records = []
            self._save()
            return deleted
