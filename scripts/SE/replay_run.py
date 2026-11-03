"""상류 고정 재실행: 저장된 run 의 Planner·Explorer·Retriever 결과를 그대로 두고 Simulator → Verifier → Composer → KPI 만 현재 코드로 다시 실행한다.

run_scenarios.py 의 16건 실행은 상류(Planner·Explorer)가 실행마다 달라서 SE 변경 효과와 상류 변동이 섞인다.
같은 상류 입력으로 SE 구간만 다시 돌려 이전 run 과 바로 비교한다 (replay_case.py 는 케이스 하나를 새 스냅샷으로 여러 번 재생).

    PYTHONPATH=src python3 scripts/SE/replay_run.py --from 20261103-v2 --run-id 20261103-v3-fixed            (전체)
    PYTHONPATH=src python3 scripts/SE/replay_run.py --from 20261103-v2 --run-id 20261103-v3-fixed H-1 H-2   (일부)
    --no-llm   ANALYSIS_LLM=0 (Simulator·Verifier·Composer 규칙만, 동작 확인용)

고정 범위
    - 이전 run 최종 상태의 planner · explorer · retriever · 재계획 기록(replan_count · replan_feedback) · 상류 실행 로그
    - 재계획은 실행하지 않는다: Verifier 가 보완 필요를 내도 Planner 로 돌아가지 않는다 (마지막 라운드만 다시 판정)
    - 지연 = 이전 run 의 상류 실행 시간 + 이번 SE 구간 시간. 타임라인도 상류는 이전 run 것, SE 구간은 이번 것
출력 (outputs/runs/SE/<run_id>/): run_scenarios.py 와 같은 형식 (<case>.md · <case>.state.json · summary.json · summary.md)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_scenarios import GT_DIR, ROOT, KpiTimeline, detail_flow, fmt, fmt_event, record, summary_md  # noqa: E402

from impact_agent.agents import Supervisor  # noqa: E402
from impact_agent.agents.kpi import load_answer_key  # noqa: E402
from impact_agent.api.runner import Runner  # noqa: E402
from impact_agent.schemas import Complexity  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

UPSTREAM = ("planner", "explorer", "retriever")
RUNS = ROOT / "outputs" / "runs" / "SE"


def upstream_state(path: Path, case: str) -> AgentState:
    """이전 run 의 최종 상태에서 SE 구간 결과·실행 로그를 지운 상류 상태."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("simulation", "verifier", "composer", "kpi", "answer_key"):
        data.pop(key, None)
    state = AgentState.model_validate(data)
    state.agent_runs = [r for r in state.agent_runs if r.agent in UPSTREAM]
    state.answer_key = load_answer_key(GT_DIR / f"{case}.json")
    return state


def replay_case(state: AgentState, source_flow: dict) -> tuple[AgentState, float, dict]:
    """SE 구간만 실행 (재계획 없음). 타임라인은 이전 run 의 상류 이벤트 뒤에 이번 이벤트를 이어 붙인다."""
    sup = Supervisor()
    runner = Runner(sup)
    sup.kpi = KpiTimeline(sup.kpi, runner)
    upstream = [e for e in source_flow.get("timeline", []) if e.get("node") in UPSTREAM]
    offset = max((e["t"] for e in upstream), default=0.0)
    last_round = max((e.get("round", 1) for e in upstream), default=1)
    timeline = list(upstream)
    t0 = time.perf_counter()

    def push(event: dict) -> None:
        entry = {"t": round(offset + time.perf_counter() - t0, 1), "round": last_round, **event}
        timeline.append(entry)
        print(f"  {fmt_event(entry)}", flush=True)

    def run_se() -> AgentState:
        if state.planner.complexity is Complexity.COMPLEX:
            state.simulation = sup._call("simulator", sup.simulator, state)
            state.verifier = sup._call("verifier", sup.verifier, state)
        state.composer = sup._call("composer", sup.composer, state)
        state.kpi = sup.kpi.run(state)
        return state

    runner.run(push, run_se)
    se_seconds = time.perf_counter() - t0
    simple = state.composer.simple_answer if state.composer else None
    recommended = bool(simple and simple.detail_analysis_recommended)
    first = Complexity(source_flow["first"])
    flow = {**source_flow, "timeline": timeline, "first_kpi": None,
            "recommended": recommended if simple else None,
            "reasons": list(simple.detail_analysis_reasons) if simple else [],
            "notes": list(simple.detail_analysis_notes) if simple else [],
            "detail_flow": detail_flow(first, source_flow.get("expected"), recommended),
            "replay": {"from": source_flow.get("replay", {}).get("from"), "se_seconds": round(se_seconds, 1)}}
    if flow["reanalysis"] is not None:  # 1차 단순 응답은 다시 만들지 않으므로 권장 결과는 이전 run 값
        flow["detail_flow"], flow["recommended"] = source_flow["detail_flow"], source_flow.get("recommended")
    seconds = sum(r.latency_ms for r in state.agent_runs if r.agent in UPSTREAM) / 1000 + se_seconds
    return state, seconds, flow


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="생략하면 이전 run 의 전체 케이스")
    ap.add_argument("--from", dest="source", required=True, help="상류를 가져올 run id (예: 20261103-v2)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--no-llm", action="store_true", help="ANALYSIS_LLM=0 (규칙만)")
    ap.add_argument("--fresh", action="store_true", help="이미 끝난 케이스도 다시 실행 (기본은 이어서 실행)")
    args = ap.parse_args()
    if args.no_llm:
        os.environ["ANALYSIS_LLM"] = "0"
    src_dir, out_dir = RUNS / args.source, RUNS / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    source = {s["case"]: s for s in json.loads((src_dir / "summary.json").read_text(encoding="utf-8")) if "error" not in s}
    cases = args.cases or list(source)

    path = out_dir / "summary.json"
    summary = [] if args.fresh or not path.exists() else json.loads(path.read_text(encoding="utf-8"))
    done = {s["case"] for s in summary if "error" not in s}
    summary = [s for s in summary if s["case"] in done]

    def save() -> None:
        order = {c: i for i, c in enumerate(cases)}
        summary.sort(key=lambda s: order.get(s["case"], len(order)))
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")

    for case in cases:
        if case in done:
            print(f"[{case}] 이미 완료 — 건너뜀", flush=True)
            continue
        if case not in source:
            print(f"[{case}] {args.source} 에 결과 없음 — 건너뜀", flush=True)
            continue
        gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
        print(f"[{case}] 재실행 중 (상류 고정: {args.source})...", flush=True)
        try:
            flow_in = {**source[case]["flow"], "replay": {"from": args.source}}
            state, seconds, flow = replay_case(upstream_state(src_dir / f"{case}.state.json", case), flow_in)
        except Exception as e:  # 한 케이스 실패가 전체를 막지 않게 기록만 (다음 실행 때 다시 시도)
            print(f"[{case}] 실패: {type(e).__name__}: {e}", flush=True)
            summary.append({"case": case, "error": f"{type(e).__name__}: {e}"})
            save()
            continue
        flow["path"] = f"{source[case]['flow']['path']} (상류 고정 {args.source}, 재계획 미실행)"
        (out_dir / f"{case}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        summary.append(record(case, gt, state, seconds, flow, out_dir))
        save()
        k = summary[-1]["result"]["kpi"]
        print(f"[{case}] 완료 SE {flow['replay']['se_seconds']:.0f}s  권장 {flow['detail_flow']}  "
              f"탐지율 {fmt(k.get('node_recall'))}  정밀도 {fmt(k.get('node_precision'))}", flush=True)
    save()
    print(f"기록: {out_dir}")


if __name__ == "__main__":
    main()
