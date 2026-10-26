"""시나리오 실행 기록 + 정답셋 평가 (Supervisor·Planner·Explorer·Retriever).

케이스마다 Retriever 까지 실행하고 (Simulator 이후는 미구현), 같은 Planner 결과로 규칙만(REACT=0) 다시 돌려 ReAct 전후를 비교한다.

    PYTHONPATH=src python3 scripts/YJ/run_scenarios.py SC-001 SC-003 SC-006 H-1 M-2 L-3   (.env 의 Neo4j 사용)

출력 (outputs/runs/YJ/<run_id>/)
    <case>.md          단계별 응답·현황 (Planner → Explorer → Retriever, ReAct 단계, 실행 로그, 평가)
    <case>.state.json  최종 AgentState (ReAct 실행)
    summary.md / summary.json  케이스별 지표 (ReAct / 규칙만)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from impact_agent.agents import ExplorerAgent, RetrieverAgent, Supervisor  # noqa: E402
from impact_agent.schemas import SmPart, UserInput  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

GT_DIR = ROOT / "eval" / "ground_truth"
DIFFICULTY = {"Major": "상", "Medium": "중", "Minor": "하"}


class FixedPlanner:
    def __init__(self, output):
        self.output = output

    def run(self, state):
        return self.output.model_copy(deep=True)


# --- 평가 ---------------------------------------------------------------------------


def contains_in_order(path: list[str], sub: list[str]) -> int:
    i = 0
    for n in path:
        if i < len(sub) and n == sub[i]:
            i += 1
    return i


def evaluate(state: AgentState, gt: dict) -> dict:
    exp, ret, e = state.explorer, state.retriever, gt["expected"]
    found = {n.node_id for n in exp.nodes} | {s.node_id for s in exp.start_nodes}
    starts = {s.node_id for s in exp.start_nodes}
    evid = {x.node_id for x in ret.evidence} if ret else set()
    paths = [p for p in e["paths"] if p.get("status", "confirmed") == "confirmed"]
    rows = []
    for p in paths:
        best = max((contains_in_order(ep.nodes, p["nodes"]) for ep in exp.paths), default=0)
        covered = sum(n in found for n in p["nodes"])
        rows.append({"path_id": p["path_id"], "critical": p.get("critical", False), "len": len(p["nodes"]),
                     "in_order": best, "covered": covered,
                     "result": "exact" if best == len(p["nodes"]) else "covered" if covered == len(p["nodes"])
                     else "partial" if covered else "missed"})
    must = [i["node_id"] for i in e.get("impacted", []) if i.get("must_find")]
    path_nodes = {n for p in paths for n in p["nodes"]}
    relevant = path_nodes | {i["node_id"] for i in e.get("impacted", [])} | set(e.get("reference_only", []))
    distract = [d["node_id"] for d in gt.get("distractors", []) if d["node_id"] in found]
    full = [r for r in rows if r["result"] in ("exact", "covered")]
    critical = [r for r in rows if r["critical"]]
    return {
        "complexity_expected": gt.get("complexity"),
        "complexity_actual": state.planner.complexity.value,
        "complexity_match": gt.get("complexity") == state.planner.complexity.value,
        "start_hit": any(s in starts for s in e.get("start_nodes", [])),
        "path_recall": round(len(full) / len(rows), 3) if rows else None,
        "path_exact": sum(r["result"] == "exact" for r in rows),
        "paths_total": len(rows),
        "critical_missed": sum(r["result"] not in ("exact", "covered") for r in critical),
        "critical_total": len(critical),
        "must_find": f"{sum(m in found for m in must)}/{len(must)}",
        "must_find_rate": round(sum(m in found for m in must) / len(must), 3) if must else None,
        "must_missing": [m for m in must if m not in found],
        "evidence_coverage": round(len(path_nodes & evid) / len(path_nodes), 3) if path_nodes else None,
        "node_precision": round(len(found & relevant) / len(found), 3) if found else None,
        "distractors_found": distract,
        "path_rows": rows,
    }


def perf(state: AgentState, seconds: float) -> dict:
    runs = state.agent_runs
    return {
        "latency_s": round(seconds, 1),
        "agent_latency_s": {a: round(sum(r.latency_ms for r in runs if r.agent == a) / 1000, 1)
                            for a in dict.fromkeys(r.agent for r in runs)},
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "tool_calls": state.tool_call_count,
        "explore_loops": state.explore_loop_count,
        "react_steps": {"explorer": len(state.explorer.react_trace) if state.explorer else 0,
                        "retriever": len(state.retriever.react_trace) if state.retriever else 0},
        "llm_calls": sum(1 for r in runs if r.agent == "planner") + (len(state.explorer.react_trace) if state.explorer else 0)
        + (len(state.retriever.react_trace) if state.retriever else 0),
    }


# --- 실행 ---------------------------------------------------------------------------


def run_case(case: str) -> tuple[AgentState, float, AgentState, float]:
    gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
    user_input = UserInput(request_text=gt["request_text"], sm_part=SmPart(gt["sm_part"]))
    t0 = time.perf_counter()
    state = Supervisor().analyze(user_input, until="retriever")
    react_s = time.perf_counter() - t0

    # 규칙만 (같은 Planner 결과, ReAct 끔)
    os.environ["REACT"] = "0"
    try:
        sup = Supervisor()
        sup.planner = FixedPlanner(state.planner)
        sup.explorer, sup.retriever = ExplorerAgent(), RetrieverAgent()
        t0 = time.perf_counter()
        base = sup.analyze(user_input, until="retriever")
        base_s = time.perf_counter() - t0
    finally:
        os.environ.pop("REACT", None)
    return state, react_s, base, base_s


# --- 기록 ---------------------------------------------------------------------------


def md_case(case: str, gt: dict, state: AgentState, ev: dict, pf: dict, base_ev: dict, base_pf: dict) -> str:
    p, x, r = state.planner, state.explorer, state.retriever
    sr = p.structured_request
    L = [f"# {case} {gt['title']} ({DIFFICULTY.get(gt['difficulty'], gt['difficulty'])} / {gt['sm_part']})", "",
         "## 입력", "", f"> {gt['request_text']}", "",
         "## 1. Planner (LLM, Few-shot)", "",
         f"- 복잡도: **{p.complexity.value}** (정답 {gt.get('complexity')}) — {p.complexity_reason}",
         f"- 구조화: 대상 `{sr.target}` / 행위 `{sr.action}` / 조건 `{sr.condition}` / 정책 `{sr.policy}` / 범위 `{sr.scope}` / 유형 `{sr.request_type.value}`",
         f"- 업무 용어: {sr.business_terms or '-'}",
         f"- 업무 확정 필요: {[f'{o.item} ({o.reason})' for o in sr.open_items] or '-'}",
         f"- 영역 {[d.value for d in p.domains]} / 공통 제외 {p.exclude.is_common} / 계획 {[s.agent.value for s in p.plan]}", "",
         "| 항목 | change_type | 목표·방향·hop | 언급 표현 | 그래프 밖 |", "|---|---|---|---|---|"]
    for it in p.items:
        m = "<br>".join(f"{mm.kind.value}: {mm.phrase}" + (f" ({', '.join(mm.synonyms)})" if mm.synonyms else "")
                        + (" [신규]" if mm.maybe_new else "") for mm in it.mentions)
        L.append(f"| {it.item_id}. {it.text} | {it.change_type.value} | {it.target_role.value} · {it.direction.value} · {it.hop_cap} | {m} | {', '.join(it.out_of_graph) or '-'} |")

    L += ["", "## 2-1. Explorer (규칙 → ReAct 보완)", "",
          f"시작 노드 {len(x.start_nodes)} · 노드 {len(x.nodes)} · 경로 {len(x.paths)} · tool {x.tool_call_count} · 재탐색 루프 {state.explore_loop_count}", "",
          "**시작 노드**", ""]
    L += [f"- [{s.item_id}] `{s.node_id}` — {s.reason}" for s in x.start_nodes]
    L += ["", "**경로** (진입 → DB, 최대 30개)", ""]
    L += [f"- [{q.item_id}] {q.stop_reason.value}: {' > '.join(q.nodes)}" for q in x.paths[:30]]
    if len(x.paths) > 30:
        L.append(f"- … 외 {len(x.paths) - 30}개")
    L += ["", "**미해결**", ""] + ([f"- {u}" for u in x.unresolved] or ["- 없음"])
    L += ["", "**ReAct 단계**", ""] + react_md(x.react_trace)

    L += ["", "## 2-2. Retriever (원문 → ReAct 보완)", "",
          f"근거 {len(r.evidence)} · 근거 없음 {len(r.nodes_without_evidence)} · 재탐색 요청 {len(r.reexplore_request)} · tool {r.tool_call_count}", ""]
    L += ["| 노드 | 파일·위치 | 종류 | 요약 |", "|---|---|---|---|"]
    L += [f"| `{e.node_id}` | {e.file_path} {e.location or ''} | {e.source_kind} | {e.summary[:80]} |" for e in r.evidence[:40]]
    if len(r.evidence) > 40:
        L.append(f"| … 외 {len(r.evidence) - 40}건 | | | |")
    if r.nodes_without_evidence:
        L += ["", f"근거 없음: {r.nodes_without_evidence}"]
    if r.reexplore_request:
        L += ["", "**재탐색 요청 (마지막 라운드)**", ""] + [f"- [{q.item_id}] {q.mode.value} {q.node_ids} — {q.reason}" for q in r.reexplore_request]
    L += ["", "**ReAct 단계**", ""] + react_md(r.react_trace)

    L += ["", "## 실행 로그 (Supervisor)", "", "| 순서 | 에이전트 | 지연(s) | tool | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|---|---|"]
    L += [f"| {i} | {a.agent} | {a.latency_ms / 1000:.1f} | {a.tool_calls} | {a.input_tokens or '-'} | {a.output_tokens or '-'} |"
          for i, a in enumerate(state.agent_runs, 1)]

    L += ["", "## 정답셋 대비 평가", "", "| 지표 | ReAct | 규칙만 |", "|---|---|---|"]
    for k, label in [("complexity_match", "복잡도 일치"), ("start_hit", "시작 노드 적중"), ("path_recall", "경로 재현율"),
                     ("path_exact", "경로 exact"), ("critical_missed", "Critical 미탐"), ("must_find", "must_find"),
                     ("evidence_coverage", "정답 경로 근거율"), ("node_precision", "노드 정밀도"), ("distractors_found", "방해 노드")]:
        L.append(f"| {label} | {ev[k]} | {base_ev[k]} |")
    for k, label in [("latency_s", "지연(s)"), ("tool_calls", "tool 호출"), ("llm_calls", "LLM 호출"),
                     ("input_tokens", "입력 토큰"), ("output_tokens", "출력 토큰")]:
        L.append(f"| {label} | {pf[k]} | {base_pf[k]} |")
    L += ["", "| 정답 경로 | Critical | ReAct | 규칙만 |", "|---|---|---|---|"]
    for a, b in zip(ev["path_rows"], base_ev["path_rows"]):
        L.append(f"| {a['path_id']} | {'C' if a['critical'] else ''} | {a['result']} ({a['covered']}/{a['len']}) | {b['result']} ({b['covered']}/{b['len']}) |")
    if ev["must_missing"]:
        L += ["", f"must_find 누락: {ev['must_missing']}"]
    return "\n".join(L) + "\n"


def react_md(trace) -> list[str]:
    if not trace:
        return ["- (실행 안 함)"]
    out = []
    for s in trace:
        obs = s.observation.replace("\n", " / ")
        out += [f"{s.step}. **{s.action}** `{json.dumps(s.args, ensure_ascii=False)}`",
                f"   - Thought: {s.thought}", f"   - Observation: {obs[:300]}{'…' if len(obs) > 300 else ''}"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M"))
    args = ap.parse_args()
    out_dir = ROOT / "outputs" / "runs" / "YJ" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for case in args.cases:
        gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
        print(f"[{case}] 실행 중...", flush=True)
        try:
            state, react_s, base, base_s = run_case(case)
        except Exception as e:  # 한 케이스 실패가 전체를 막지 않게 기록만
            print(f"[{case}] 실패: {type(e).__name__}: {e}", flush=True)
            summary.append({"case": case, "error": f"{type(e).__name__}: {e}"})
            continue
        ev, pf = evaluate(state, gt), perf(state, react_s)
        base_ev, base_pf = evaluate(base, gt), perf(base, base_s)
        (out_dir / f"{case}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / f"{case}.md").write_text(md_case(case, gt, state, ev, pf, base_ev, base_pf), encoding="utf-8")
        summary.append({"case": case, "title": gt["title"], "domain": "tos" if case.startswith("SC") else "swing",
                        "difficulty": DIFFICULTY.get(gt["difficulty"], gt["difficulty"]),
                        "react": {**{k: v for k, v in ev.items() if k != "path_rows"}, **pf},
                        "rule_only": {**{k: v for k, v in base_ev.items() if k != "path_rows"}, **base_pf}})
        print(f"[{case}] 완료 {react_s:.0f}s  경로 {ev['path_recall']} (규칙만 {base_ev['path_recall']})  "
              f"must {ev['must_find']} (규칙만 {base_ev['must_find']})", flush=True)

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")
    print(f"기록: {out_dir}")


def summary_md(summary: list[dict]) -> str:
    L = ["# 시나리오 실행 요약", "", "값은 `ReAct / 규칙만` (같은 Planner 결과).", "",
         "| 케이스 | 영역 | 난이도 | 복잡도(실제/정답) | 시작 적중 | 경로 재현율 | Critical 미탐 | must_find | 근거율 | 노드 정밀도 | 지연(s) | tool | LLM 호출 | 토큰(입/출) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        if "error" in s:
            L.append(f"| {s['case']} | 실패: {s['error']} |" + " |" * 12)
            continue
        a, b = s["react"], s["rule_only"]
        L.append(f"| {s['case']} | {s['domain']} | {s['difficulty']} | {a['complexity_actual']}/{a['complexity_expected']} | "
                 f"{a['start_hit']} / {b['start_hit']} | {a['path_recall']} / {b['path_recall']} | "
                 f"{a['critical_missed']}/{a['critical_total']} / {b['critical_missed']}/{b['critical_total']} | "
                 f"{a['must_find']} / {b['must_find']} | {a['evidence_coverage']} / {b['evidence_coverage']} | "
                 f"{a['node_precision']} / {b['node_precision']} | {a['latency_s']} / {b['latency_s']} | "
                 f"{a['tool_calls']} / {b['tool_calls']} | {a['llm_calls']} / {b['llm_calls']} | "
                 f"{a['input_tokens']}/{a['output_tokens']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
