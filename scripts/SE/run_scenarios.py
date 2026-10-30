"""시나리오 실행 기록 + 정답셋 평가 (Simulator·Verifier·Composer + KPI Agent).

케이스마다 UI 와 같은 전체 흐름(Planner → … → Composer → KPI)으로 실행하고, KPI Agent 결과(state.kpi)를 그대로 기록한다.
KPI 는 새로 계산하지 않는다 (KPI 설계서 그대로).

흐름 (docs/planner_detail_analysis_to-be.md 의 사용자 확인을 평가용으로 가정, scripts/YJ/run_scenarios.py 와 같은 가정):
    1차 판정 complex                → 그대로 끝까지 (정답이 simple 이어도)
    1차 판정 simple, 정답 complex   → 사용자가 상세 분석을 요청했다고 가정: reanalysis 후속 입력(내용 = Composer 권장 사유)
                                     → 2차 전체 흐름 (권장이 없었으면 "권장 없음 (놓침)" 으로 기록하고 평가용으로 진행)
    1차 판정 simple, 정답 simple    → 단순 응답 그대로 (권장이 붙으면 "불필요한 권장")
정답 complexity 는 "사용자가 상세 분석을 요청할지"를 대신하는 데만 쓰고, 에이전트 입력에는 넣지 않는다.

    PYTHONPATH=src python3 scripts/SE/run_scenarios.py SC-001 SC-002 SC-003 SC-004 SC-005 SC-006 --run-id v1/tos   (.env 의 Neo4j·LLM 사용)

출력 (outputs/runs/SE/<run_id>/)
    <case>.md          흐름 · Simulator · Verifier · Composer · KPI(1차/2차) · 실행 로그
    <case>.state.json  최종 AgentState (원문 포함, 커밋하지 않음)
    summary.md / summary.json  케이스별 지표
    --rebuild          실행 없이 저장된 state.json·summary 로 md·summary 만 다시 만든다
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from impact_agent.agents import Supervisor  # noqa: E402
from impact_agent.agents.kpi import load_answer_key  # noqa: E402
from impact_agent.agents.simulator import requirement_difficulty  # noqa: E402
from impact_agent.schemas import Complexity, FollowupInput, FollowupType, SmPart, UserInput  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

GT_DIR = ROOT / "eval" / "ground_truth"
DIFFICULTY = {"Major": "상", "Medium": "중", "Minor": "하"}
# summary 에 옮길 KPI 지표 (KpiMetricName 값). 없는 지표는 '-' (해당 없음)
SUMMARY_METRICS = [
    ("node_recall", "탐지율"), ("critical_miss_rate", "Critical 미탐률"), ("node_precision", "정밀도"),
    ("complexity_match", "복잡도 일치"), ("difficulty_match", "난이도 일치"), ("feasibility_match", "가능 여부 일치"),
    ("area_impact_match", "영역 일치"), ("change_tag_accuracy", "변경/참조 표시"), ("wrong_path_adopted", "오답 경로 채택"),
    ("verifier_unresolved", "Verifier 미해결"),
]
MAX_LIST = 30  # md 목록 상한


# --- 실행 ---------------------------------------------------------------------------


def run_case(case: str) -> tuple[AgentState, float, dict]:
    gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
    user_input = UserInput(request_text=gt["request_text"], sm_part=SmPart(gt["sm_part"]))
    t0 = time.perf_counter()
    sup = Supervisor()
    state = sup.analyze(user_input, answer_key=load_answer_key(GT_DIR / f"{case}.json"))
    first = state.planner.complexity
    simple = state.composer.simple_answer if state.composer else None
    reasons = list(simple.detail_analysis_reasons) if simple else []
    recommended = bool(simple and simple.detail_analysis_recommended)
    flow = {"first": first.value, "expected": gt.get("complexity"), "recommended": recommended if simple else None,
            "reasons": reasons, "reanalysis": None, "first_kpi": state.kpi.model_dump(mode="json") if state.kpi else None}
    if first is Complexity.SIMPLE and gt.get("complexity") == "complex":
        content = "\n".join(reasons) or "상세 분석 요청"
        flow["reanalysis"] = content
        state = sup.followup(sup.last_session_id, FollowupInput(type=FollowupType.REANALYSIS, content=content))
    flow["final"] = state.planner.complexity.value
    flow["detail_flow"] = detail_flow(first, gt.get("complexity"), recommended)
    flow["path"] = ("simple → 재분석 → " + flow["final"] if flow["reanalysis"] is not None
                    else f"{first.value} → 끝까지")
    return state, time.perf_counter() - t0, flow


def detail_flow(first: Complexity, expected: str | None, recommended: bool) -> str:
    if first is not Complexity.SIMPLE:
        return "complex 흐름"
    if expected == "complex":
        return "권장 → 상세 분석 가능" if recommended else "권장 없음 (놓침)"
    return "불필요한 권장" if recommended else "단순 응답 (정상)"


# --- 평가 (KPI Agent 결과를 옮겨 적기만 함) -------------------------------------------


def metric_values(kpi: dict | None) -> dict[str, float]:
    return {m["name"]: m["value"] for m in (kpi or {}).get("metrics", [])}


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def perf(state: AgentState, seconds: float) -> dict:
    runs = state.agent_runs
    agents = list(dict.fromkeys(r.agent for r in runs))
    return {
        "latency_s": round(seconds, 1),
        "agent_latency_s": {a: round(sum(r.latency_ms for r in runs if r.agent == a) / 1000, 1) for a in agents},
        "agent_tokens": {a: sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in runs if r.agent == a) for a in agents},
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "tool_calls": state.tool_call_count,
        "explore_loops": state.explore_loop_count,
        "replans": state.replan_count,
    }


def summarize(state: AgentState) -> dict:
    """SE 담당 구간 요약 (summary 용)."""
    out: dict = {"complexity": state.planner.complexity.value if state.planner else None}
    sim = state.simulation
    if sim is not None:
        level, _ = requirement_difficulty(sim.area_impacts)
        out["difficulty"] = level.value
        out["areas"] = {a.area.value: a.impact_type.value for a in sim.area_impacts}
        out["dropped_paths"] = len(sim.dropped_paths)
    if state.verifier is not None:
        out["verifier"] = state.verifier.result.value
    c = state.composer
    if c is not None and c.complex_report is not None:
        out["checklist"] = len(c.complex_report.checklist)
    return out


# --- 기록 ---------------------------------------------------------------------------


def md_kpi(kpi: dict | None, title: str) -> list[str]:
    if kpi is None:
        return [f"### {title}", "", "- KPI 결과 없음", ""]
    L = [f"### {title}", ""]
    if not kpi.get("evaluable", True):
        L += [f"- 평가 불가: {kpi.get('skip_reason')}", ""]
    L += ["| 지표 | 분류 | 값 | 기준 | 충족 |", "|---|---|---|---|---|"]
    L += [f"| {m['name']} | {m['category']} | {fmt(m['value'])} | {fmt(m.get('threshold'))} | {fmt(m.get('passed'))} |"
          for m in kpi.get("metrics", [])]
    for key, label in [("missed_nodes", "미탐 노드"), ("false_positive_nodes", "오탐 노드"), ("distractors_found", "방해 노드"),
                       ("judgement_notes", "판정 품질 메모"), ("warnings", "경고")]:
        items = kpi.get(key) or []
        if items:
            L += ["", f"**{label}** ({len(items)})", ""] + [f"- {x}" for x in items[:MAX_LIST]]
            if len(items) > MAX_LIST:
                L.append(f"- … 외 {len(items) - MAX_LIST}개")
    return L + [""]


def md_case(case: str, gt: dict, state: AgentState, pf: dict, flow: dict) -> str:
    p = state.planner
    L = [f"# {case} {gt['title']} ({DIFFICULTY.get(gt['difficulty'], gt['difficulty'])} / {gt['sm_part']})", "",
         "## 입력", "", f"> {gt['request_text']}", "",
         "## 흐름", "",
         f"- 1차 판정 **{flow['first']}** (정답 {flow['expected']}) → {flow['path']}",
         f"- simple 판정 시 상세 분석 권장: {flow['detail_flow']}"]
    if flow["reasons"]:
        L += [f"  - {r}" for r in flow["reasons"]]
    if flow["reanalysis"] is not None:
        L.append("- 사용자 확인 가정 → reanalysis 후속 입력 → 2차 전체 흐름")
    if p is not None:
        L += [f"- 최종 Planner: **{p.complexity.value}** — {p.complexity_reason}",
              "- 요건 항목: " + " / ".join(f"{it.item_id}. ({it.change_type.value}) {it.text}" for it in p.items)]

    sim = state.simulation
    if sim is not None:
        level, basis = requirement_difficulty(sim.area_impacts)
        exp_areas = gt["expected"].get("areas", {})
        L += ["", "## 2-3 Simulator", "",
              f"요건 전체 난이도 **{level.value}** (정답 {gt['difficulty']}) — {basis}", "",
              "| 영역 | 영향 (정답) | 가능성 | 난이도 | 난이도 근거 | 경로 수 | 확신도 |", "|---|---|---|---|---|---|---|"]
        L += [f"| {a.area.value} | {a.impact_type.value} ({exp_areas.get(a.area.value, '-')}) | {a.feasibility.value} | "
              f"{a.difficulty.value} | {a.difficulty_basis} | {len(a.paths)} | {a.confidence.value} |" for a in sim.area_impacts]
        alts = [(a.area.value, x) for a in sim.area_impacts for x in a.alternatives]
        if alts:
            L += ["", "**대안**", ""] + [f"- [{ar}] {x.description} ({x.feasibility.value}·{x.difficulty.value}) — {x.pros_cons}"
                                       for ar, x in alts]
        if sim.requested_vs_alternative:
            L += ["", f"**요청안 / 대안**: {sim.requested_vs_alternative}"]
        hops = list(dict.fromkeys((h.node, h.impact) for a in sim.area_impacts for q in a.paths for h in q.hops))
        L += ["", f"**노드별 영향** ({len(hops)})", ""] + [f"- `{n}` {i}" for n, i in hops[:MAX_LIST]]
        if len(hops) > MAX_LIST:
            L.append(f"- … 외 {len(hops) - MAX_LIST}개")
        if sim.dropped_paths:
            L += ["", f"**검토 제외 경로** ({len(sim.dropped_paths)})", ""]
            L += [f"- {d.path.path_id} {' → '.join(h.node for h in d.path.hops)} — {d.reason}" for d in sim.dropped_paths]
        if sim.unverified_segments:
            L += ["", f"**분석 한계** ({len(sim.unverified_segments)})", ""] + [f"- {u}" for u in sim.unverified_segments[:MAX_LIST]]

    v = state.verifier
    if v is not None:
        L += ["", "## 2-4 Verifier", "", f"결과 **{v.result.value}** · 재계획 {state.replan_count}회", "",
              "| 점검 | 영역 | 결과 | 내용 |", "|---|---|---|---|"]
        L += [f"| {c.check} | {c.area.value if c.area else '-'} | {c.result.value} | {c.note} |" for c in v.checks]
        if v.reason:
            L += ["", f"- 보완 사유: {v.reason}", f"- 추가 범위: {v.additional_scope}"]
        if state.replan_feedback:
            L += ["", "**재계획 이력**", ""] + [f"- {fb.round}차: {fb.reason}" for fb in state.replan_feedback]

    c = state.composer
    if c is not None:
        L += ["", "## 3 Composer", ""]
        if c.complex_report is not None:
            r = c.complex_report
            L += [f"**요약**: {r.summary_conclusion}", "", f"**체크리스트** ({len(r.checklist)})", ""]
            L += [f"- {x.item_id} ({x.owner.value}{', ' + x.related_area.value if x.related_area else ''}) {x.question}"
                  for x in r.checklist]
            opens = gt["expected"].get("open_items", [])
            if opens:
                L += ["", f"**정답 open_items** ({len(opens)}, 사람이 대조)", ""]
                L += [f"- ({o.get('owner', '-')}) {o.get('question', o)}" for o in opens]
            if r.analysis_limits:
                L += ["", f"**분석 한계** ({len(r.analysis_limits)})", ""] + [f"- {x}" for x in r.analysis_limits[:MAX_LIST]]
        elif c.simple_answer is not None:
            s = c.simple_answer
            L += ["**결론**", ""] + [f"- {x}" for x in s.conclusion_lines]
            L += ["", f"상세 분석 권장 **{s.detail_analysis_recommended}**"] + [f"- {x}" for x in s.detail_analysis_reasons]
            if s.check_queries:
                L += ["", "**확인 쿼리**", ""] + [f"- {q.purpose}: `{q.sql}`" for q in s.check_queries]
            if s.analysis_limits:
                L += ["", f"**분석 한계** ({len(s.analysis_limits)})", ""] + [f"- {x}" for x in s.analysis_limits[:MAX_LIST]]

    L += ["", "## KPI (KPI Agent 결과)", ""]
    if flow["reanalysis"] is not None:
        L += md_kpi(flow.get("first_kpi"), "1차 (단순)")
        L += md_kpi(state.kpi.model_dump(mode="json") if state.kpi else None, "2차 (재분석)")
    else:
        L += md_kpi(state.kpi.model_dump(mode="json") if state.kpi else None, "결과")

    L += ["## 실행 로그 (Supervisor)", "", "| 순서 | 에이전트 | 지연(s) | tool | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|---|---|"]
    L += [f"| {i} | {a.agent} | {a.latency_ms / 1000:.1f} | {a.tool_calls} | {a.input_tokens or '-'} | {a.output_tokens or '-'} |"
          for i, a in enumerate(state.agent_runs, 1)]
    L += ["", f"합계: 지연 {pf['latency_s']}s · tool {pf['tool_calls']} · 왕복 {pf['explore_loops']} · 재계획 {pf['replans']} · "
              f"토큰 {pf['input_tokens']}/{pf['output_tokens']}"]
    return "\n".join(L) + "\n"


def record(case: str, gt: dict, state: AgentState, seconds: float, flow: dict, out_dir: Path) -> dict:
    """케이스 기록 → <case>.md 저장 + summary 항목."""
    pf = perf(state, seconds)
    (out_dir / f"{case}.md").write_text(md_case(case, gt, state, pf, flow), encoding="utf-8")
    kpi = state.kpi.model_dump(mode="json") if state.kpi else None
    return {"case": case, "title": gt["title"], "domain": "tos" if case.startswith("SC") else "swing",
            "difficulty": DIFFICULTY.get(gt["difficulty"], gt["difficulty"]), "flow": flow,
            "result": {**summarize(state), **pf, "kpi": metric_values(kpi),
                       "first_kpi": metric_values(flow.get("first_kpi")) if flow["reanalysis"] is not None else None,
                       "missed_nodes": (kpi or {}).get("missed_nodes", [])}}


def summary_md(summary: list[dict]) -> str:
    head = ["케이스", "영역", "난이도", "흐름", "복잡도(1차/최종/정답)", "상세 분석 권장", "난이도(실제)", "검증"]
    head += [label for _, label in SUMMARY_METRICS] + ["재계획", "지연(s)", "토큰(입/출)"]
    L = ["# 시나리오 실행 요약 (SE)", "",
         "KPI 값은 KPI Agent 결과 그대로 (재분석 케이스는 2차 결과). `-` = 해당 없음.", "",
         "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in summary:
        if "error" in s:
            L.append(f"| {s['case']} | 실패: {s['error']} |" + " |" * (len(head) - 2))
            continue
        a, f = s["result"], s["flow"]
        k = a.get("kpi", {})
        row = [s["case"], s["domain"], s["difficulty"], f.get("path", "-"),
               f"{f['first']}/{f.get('final', '-')}/{f['expected']}", f.get("detail_flow", "-"), a.get("difficulty", "-"),
               a.get("verifier", "-")]
        row += [fmt(k.get(name)) for name, _ in SUMMARY_METRICS]
        row += [str(a["replans"]), str(a["latency_s"]), f"{a['input_tokens']}/{a['output_tokens']}"]
        L.append("| " + " | ".join(row) + " |")
    missed = [(s["case"], s["result"]["missed_nodes"]) for s in summary if "error" not in s and s["result"].get("missed_nodes")]
    if missed:
        L += ["", "## 미탐 노드", ""] + [f"- {c}: {', '.join(m)}" for c, m in missed]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M"))
    ap.add_argument("--fresh", action="store_true", help="이미 끝난 케이스도 다시 실행 (기본은 이어서 실행)")
    ap.add_argument("--rebuild", action="store_true", help="실행 없이 저장된 state.json 으로 md·summary 만 다시 만듦")
    args = ap.parse_args()
    out_dir = ROOT / "outputs" / "runs" / "SE" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 이어서 실행: 케이스가 끝날 때마다 summary 를 저장하므로, 중간에 끊겨도 다시 돌리면 끝난 케이스는 건너뛴다
    path = out_dir / "summary.json"
    summary = [] if args.fresh or not path.exists() else json.loads(path.read_text(encoding="utf-8"))

    def save() -> None:
        order = {c: i for i, c in enumerate(args.cases)}
        summary.sort(key=lambda s: order.get(s["case"], len(order)))
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")

    if args.rebuild:  # 저장된 state.json 으로 다시 만들기 (지연·흐름은 summary 의 값)
        old = {s["case"]: s for s in summary if "error" not in s}
        summary = [record(c, json.loads((GT_DIR / f"{c}.json").read_text(encoding="utf-8")),
                          AgentState.model_validate_json((out_dir / f"{c}.state.json").read_text(encoding="utf-8")),
                          old[c]["result"]["latency_s"], old[c]["flow"], out_dir)
                   for c in args.cases if c in old]
        save()
        print(f"다시 만듦: {out_dir} ({len(summary)}건)")
        return
    done = {s["case"] for s in summary if "error" not in s}
    summary = [s for s in summary if s["case"] in done]

    for case in args.cases:
        if case in done:
            print(f"[{case}] 이미 완료 — 건너뜀", flush=True)
            continue
        gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
        print(f"[{case}] 실행 중...", flush=True)
        try:
            state, seconds, flow = run_case(case)
        except Exception as e:  # 한 케이스 실패가 전체를 막지 않게 기록만 (다음 실행 때 다시 시도)
            print(f"[{case}] 실패: {type(e).__name__}: {e}", flush=True)
            summary.append({"case": case, "error": f"{type(e).__name__}: {e}"})
            save()
            continue
        (out_dir / f"{case}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        summary.append(record(case, gt, state, seconds, flow, out_dir))
        save()
        k = summary[-1]["result"]["kpi"]
        print(f"[{case}] 완료 {seconds:.0f}s  흐름 {flow['path']}  권장 {flow['detail_flow']}  "
              f"탐지율 {fmt(k.get('node_recall'))}  정밀도 {fmt(k.get('node_precision'))}", flush=True)
    save()
    print(f"기록: {out_dir}")


if __name__ == "__main__":
    main()
