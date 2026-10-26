"""CLI

    python -m impact_agent "<요청 문장>" --sm-part marketing
    python -m impact_agent --case SC-001 --until retriever            # 정답셋 요청 문장으로 Retriever 까지 실행
    python -m impact_agent --case SC-001 --until retriever --plan plan.json   # LLM 대신 준비한 PlannerOutput 사용
    python -m impact_agent ... --confirm "비마케팅 여부=COL:TCP_MSG_SCRT.ADVT_YN" --out state.json
    python -m impact_agent --case L-1 --answer-key eval/ground_truth/L-1.json        # + KPI 평가 (PoC)
"""

import argparse
import json
import sys
from pathlib import Path

from .agents import Supervisor
from .agents.kpi import load_answer_key
from .schemas import ConfirmedNode, PlannerOutput, SmPart, UserInput

ROOT = Path(__file__).resolve().parents[2]


class FixedPlanner:
    """--plan 으로 받은 PlannerOutput 을 그대로 돌려준다 (LLM 없이 Explorer·Retriever 확인용)."""

    def __init__(self, path: Path) -> None:
        self.output = PlannerOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def run(self, state):
        return self.output.model_copy(deep=True)


def _react_lines(trace) -> list[str]:
    lines = []
    for s in trace:
        lines.append(f"  react {s.step}. {s.action} {s.args or ''}")
        lines.append(f"     thought: {s.thought}")
        lines.append(f"     obs: {s.observation.splitlines()[0][:150] if s.observation else ''}")
    return lines


def summary(state) -> str:
    lines = []
    if state.planner:
        lines.append(f"[Planner] {state.planner.complexity.value} / 영역 {[d.value for d in state.planner.domains]}")
        for it in state.planner.items:
            mentions = ", ".join(f"{m.kind.value}:{m.phrase}" for m in it.mentions)
            lines.append(f"  항목 {it.item_id} {it.change_type.value} → {it.target_role.value} {it.direction.value} "
                         f"(hop {it.hop_cap}) | {mentions}")
    if state.explorer:
        e = state.explorer
        lines.append(f"[Explorer] 시작 노드 {len(e.start_nodes)}, 노드 {len(e.nodes)}, 경로 {len(e.paths)}, "
                     f"tool {e.tool_call_count}, 루프 {state.explore_loop_count}")
        for s in e.start_nodes:
            lines.append(f"  start [{s.item_id}] {s.node_id} ← {s.reason}")
        for p in e.paths:
            lines.append(f"  path [{p.item_id}] {p.stop_reason.value:<15} {' > '.join(p.nodes)}")
        for u in e.unresolved:
            lines.append(f"  unresolved {u}")
        if e.excluded_common:
            lines.append(f"  excluded_common {e.excluded_common}")
        lines += _react_lines(e.react_trace)
    if state.retriever:
        r = state.retriever
        lines.append(f"[Retriever] 근거 {len(r.evidence)}, 근거 없음 {len(r.nodes_without_evidence)}, 재탐색 요청 {len(r.reexplore_request)}")
        for q in r.reexplore_request:
            lines.append(f"  reexplore [{q.item_id}] {q.mode.value} {q.node_ids} - {q.reason}")
        lines += _react_lines(r.react_trace)
    lines.append(f"[실행] tool 호출 {state.tool_call_count} / " + ", ".join(f"{a.agent} {a.latency_ms:.0f}ms" for a in state.agent_runs))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="impact_agent")
    parser.add_argument("request_text", nargs="?")
    parser.add_argument("--sm-part", choices=[p.value for p in SmPart])
    parser.add_argument("--case", help="eval/ground_truth/<case>.json 의 request_text·sm_part 사용")
    parser.add_argument("--until", choices=["planner", "explorer", "retriever", "simulator", "verifier"])
    parser.add_argument("--plan", type=Path, help="LLM 대신 사용할 PlannerOutput JSON")
    parser.add_argument("--confirm", action="append", default=[], help='확정 매핑 "표현=노드ID" (여러 번 가능)')
    parser.add_argument("--out", type=Path, help="최종 상태(AgentState) JSON 저장 경로")
    parser.add_argument("--answer-key", type=Path, help="PoC 평가용 정답 파일 (eval/ground_truth/<요건ID>.json)")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    request, sm_part = args.request_text, args.sm_part
    if args.case:
        gt = json.loads((ROOT / "eval" / "ground_truth" / f"{args.case}.json").read_text(encoding="utf-8"))
        request, sm_part = request or gt["request_text"], sm_part or gt["sm_part"]
    if not request or not sm_part:
        parser.error("요청 문장과 --sm-part (또는 --case) 가 필요합니다")
    confirmed = [ConfirmedNode(phrase=c.split("=", 1)[0], node_id=c.split("=", 1)[1]) for c in args.confirm if "=" in c]

    answer_key = load_answer_key(args.answer_key) if args.answer_key else None

    sup = Supervisor()
    if args.plan:
        sup.planner = FixedPlanner(args.plan)
    user_input = UserInput(request_text=request, sm_part=SmPart(sm_part), confirmed_nodes=confirmed)
    state = sup.analyze(user_input, until=args.until, answer_key=answer_key)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    if args.until:
        print(summary(state))
    else:
        print(state.composer.markdown)

    kpi = state.kpi
    if kpi is not None:
        print("\n## KPI")
        for m in kpi.metrics:
            print(f"- {m.name.value}: {m.value:g} {m.unit or ''}".rstrip())


if __name__ == "__main__":
    sys.exit(main())
