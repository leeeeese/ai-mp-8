"""시나리오 실행 기록 + 정답셋 평가 (Supervisor·Planner·Explorer·Retriever).

케이스마다 실제 흐름(규칙 탐색 + ReAct 보완)으로 실행하고 정답셋과 비교한다.

흐름 (docs/planner_detail_analysis_to-be.md 의 사용자 확인을 평가용으로 가정):
    1차 판정 complex                    → 그대로 2-3 Simulator 까지 (정답이 simple 이어도)
    1차 판정 simple, 정답 complex       → 사용자가 상세 분석을 요청했다고 가정: reanalysis 후속 입력(내용 = Composer 권장 사유)
                                         → Planner 2차 판정 complex → 탐색부터 다시 → 2-3 Simulator 까지
    1차 판정 simple, 정답 simple        → Retriever 까지 (단순 흐름 그대로)
정답 complexity 는 "사용자가 상세 분석을 요청할지"를 대신하는 데만 쓰고, 에이전트 입력에는 넣지 않는다.

    PYTHONPATH=src python3 scripts/YJ/run_scenarios.py SC-001 SC-003 SC-006 H-1 M-2 L-3   (.env 의 Neo4j 사용)

출력 (outputs/runs/YJ/<run_id>/)
    <case>.md          단계별 응답·현황 (Planner → Explorer → Retriever → (complex) Simulator, ReAct 단계, 실행 로그, 평가)
    <case>.state.json  최종 AgentState
    summary.md / summary.json  케이스별 지표
    --rebuild          실행 없이 저장된 state.json 으로 md·summary 만 다시 만든다
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
from impact_agent.agents import simulator as sim  # noqa: E402
from impact_agent.agents.composer import detail_reasons  # noqa: E402
from impact_agent.rules import build_impact_paths  # noqa: E402
from impact_agent.schemas import Complexity, FollowupInput, FollowupType, SmPart, UserInput  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

GT_DIR = ROOT / "eval" / "ground_truth"
DIFFICULTY = {"Major": "상", "Medium": "중", "Minor": "하"}


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
        **handoff(state, paths, path_nodes),
        **detail_flow(state, gt),
        "path_rows": rows,
    }


def detail_flow(state: AgentState, gt: dict) -> dict:
    """simple 판정이면 Composer 의 상세 분석 권장(규칙 신호, composer.detail_reasons)이 붙는지.

    정답 complex 를 simple 로 봤어도 권장이 붙으면 사용자 확인 → reanalysis 로 상세 분석에 갈 수 있다 (recovered).
    Composer LLM 의 추가 권장 판단은 Composer 를 실행해야 하므로 여기서는 규칙 신호만 본다.
    """
    if state.planner.complexity.value != "simple":
        return {"detail_recommended": None, "detail_reasons": [], "detail_flow": "complex 흐름"}
    exp, ret = state.explorer, state.retriever
    paths = [p for p, _ in build_impact_paths(exp.paths, exp.edges, ret.evidence if ret else [])] if exp else []
    reasons = detail_reasons(state, paths)
    expected = gt.get("complexity")
    flow = ("권장 → 상세 분석 가능" if reasons else "권장 없음 (놓침)") if expected == "complex" else \
        ("불필요한 권장" if reasons else "단순 응답 (정상)")
    return {"detail_recommended": bool(reasons), "detail_reasons": reasons, "detail_flow": flow}


def handoff(state: AgentState, gt_paths: list[dict], gt_nodes: set[str]) -> dict:
    """Simulator 입력(sim.user_message 와 같은 상한)에 실제로 들어가는 양: 앞 MAX_CONTEXT_PATHS 개 경로, 경로 노드 근거의 앞 줄."""
    exp, ret = state.explorer, state.retriever
    evidence = ret.evidence if ret else []
    shown = [p for p, _ in build_impact_paths(exp.paths, exp.edges, evidence)][:sim.MAX_CONTEXT_PATHS]
    view = {h.node for p in shown for h in p.hops}
    seen, used = set(), 0
    for e in evidence:
        if e.node_id not in view:
            continue
        block = len("\n".join((e.content or "").splitlines()[:sim.CONTEXT_LINES_PER_EVIDENCE])) + len(e.summary) + 80
        if used + block > sim.MAX_CONTEXT_CHARS:
            break
        used += block
        seen.add(e.node_id)
    return {
        "handoff_path_recall": round(sum(all(n in view for n in p["nodes"]) for p in gt_paths) / len(gt_paths), 3) if gt_paths else None,
        "handoff_evidence": round(len(gt_nodes & seen) / len(gt_nodes), 3) if gt_nodes else None,
        "key_evidence": sum(e.summary.startswith("[핵심]") for e in evidence),
    }


def perf(state: AgentState, seconds: float) -> dict:
    runs = state.agent_runs
    return {
        "latency_s": round(seconds, 1),
        "agent_latency_s": {a: round(sum(r.latency_ms for r in runs if r.agent == a) / 1000, 1)
                            for a in dict.fromkeys(r.agent for r in runs)},
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "agent_input_tokens": {a: sum(r.input_tokens or 0 for r in runs if r.agent == a) for a in dict.fromkeys(r.agent for r in runs)},
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "tool_calls": state.tool_call_count,
        "explore_loops": state.explore_loop_count,
        "react_steps": {"explorer": len(state.explorer.react_trace) if state.explorer else 0,
                        "retriever": len(state.retriever.react_trace) if state.retriever else 0},
        "llm_calls": sum(1 for r in runs if r.agent in ("planner", "simulator"))  # ReAct 단계 중 thought "-" 는 LLM 없이 시스템이 끝낸 단계
        + sum(s.thought != "-" for s in (state.explorer.react_trace if state.explorer else []))
        + sum(s.thought != "-" for s in (state.retriever.react_trace if state.retriever else [])),
    }


# --- 실행 ---------------------------------------------------------------------------


def run_case(case: str) -> tuple[AgentState, float, dict]:
    gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
    user_input = UserInput(request_text=gt["request_text"], sm_part=SmPart(gt["sm_part"]))
    t0 = time.perf_counter()
    main = Supervisor()
    state = main.analyze(user_input, until="retriever")
    first = state.planner.complexity
    flow = {"first": first.value, "expected": gt.get("complexity"), "reanalysis": None, "reasons": []}
    if first is Complexity.SIMPLE and gt.get("complexity") == "complex":
        exp, ret = state.explorer, state.retriever
        paths = [p for p, _ in build_impact_paths(exp.paths, exp.edges, ret.evidence if ret else [])] if exp else []
        flow["reasons"] = detail_reasons(state, paths)  # Composer 가 단순 응답에 붙일 상세 분석 권장 사유 (규칙 신호)
        content = "\n".join(flow["reasons"]) or "상세 분석 요청"
        flow["reanalysis"] = content
        state = main.followup(main.last_session_id, FollowupInput(type=FollowupType.REANALYSIS, content=content),
                              until="retriever")
    if state.planner.complexity is Complexity.COMPLEX:
        state.simulation = main._call("simulator", main.simulator, state)  # 2-3 Simulator 까지
    flow["final"] = state.planner.complexity.value
    flow["path"] = (f"{first.value} → 2-3" if flow["reanalysis"] is None and first is Complexity.COMPLEX
                    else "simple → 재분석 → complex → 2-3" if flow["reanalysis"] is not None else "simple → 단순 응답")
    react_s = time.perf_counter() - t0

    return state, react_s, flow


# --- 기록 ---------------------------------------------------------------------------


def md_case(case: str, gt: dict, state: AgentState, ev: dict, pf: dict, flow: dict | None = None) -> str:
    p, x, r = state.planner, state.explorer, state.retriever
    sr = p.structured_request
    L = [f"# {case} {gt['title']} ({DIFFICULTY.get(gt['difficulty'], gt['difficulty'])} / {gt['sm_part']})", "",
         "## 입력", "", f"> {gt['request_text']}", ""]
    if flow:
        L += ["## 흐름", "", f"- 1차 판정 **{flow['first']}** (정답 {flow['expected']}) → {flow['path']}"]
        if flow["reanalysis"] is not None:
            L += [f"- 상세 분석 권장 사유 {len(flow['reasons'])}개 (Composer 규칙 신호) → 사용자 확인 가정 → reanalysis 후속 입력"]
            L += [f"  - {x_}" for x_ in flow["reasons"]] or ["  - (사유 없음: 권장이 붙지 않는 경우. 평가용으로 재분석 진행)"]
        L.append("")
    L += [
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

    if state.simulation is not None:
        sim_out = state.simulation
        L += ["", "## 2-3 Simulator (파트너, 결과 요약)", "", "| 영역 | 영향 | 가능성 | 난이도 | 경로 수 |", "|---|---|---|---|---|"]
        L += [f"| {a.area.value} | {a.impact_type.value} | {a.feasibility.value} | {a.difficulty.value} | {len(a.paths)} |"
              for a in sim_out.area_impacts]
        if sim_out.unverified_segments:
            L += ["", f"분석 한계 {len(sim_out.unverified_segments)}건: {sim_out.unverified_segments[:5]}"]
    L += ["", "## 실행 로그 (Supervisor)", "", "| 순서 | 에이전트 | 지연(s) | tool | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|---|---|"]
    L += [f"| {i} | {a.agent} | {a.latency_ms / 1000:.1f} | {a.tool_calls} | {a.input_tokens or '-'} | {a.output_tokens or '-'} |"
          for i, a in enumerate(state.agent_runs, 1)]

    L += ["", "## 정답셋 대비 평가", "", "| 지표 | 값 |", "|---|---|"]
    for k, label in [("complexity_match", "복잡도 일치"), ("start_hit", "시작 노드 적중"), ("path_recall", "경로 재현율"),
                     ("path_exact", "경로 exact"), ("critical_missed", "Critical 미탐"), ("must_find", "must_find"),
                     ("evidence_coverage", "정답 경로 근거율"), ("node_precision", "노드 정밀도"), ("distractors_found", "방해 노드"),
                     ("handoff_path_recall", "Simulator 전달 경로 재현율"), ("handoff_evidence", "Simulator 전달 근거율"),
                     ("key_evidence", "[핵심] 근거 수"), ("detail_flow", "simple 판정 시 상세 분석 권장")]:
        L.append(f"| {label} | {ev[k]} |")
    for k, label in [("latency_s", "지연(s)"), ("tool_calls", "tool 호출"), ("llm_calls", "LLM 호출"),
                     ("input_tokens", "입력 토큰"), ("output_tokens", "출력 토큰")]:
        L.append(f"| {label} | {pf[k]} |")
    L += ["", "| 정답 경로 | Critical | 결과 |", "|---|---|---|"]
    for a in ev["path_rows"]:
        L.append(f"| {a['path_id']} | {'C' if a['critical'] else ''} | {a['result']} ({a['covered']}/{a['len']}) |")
    if ev["must_missing"]:
        L += ["", f"must_find 누락: {ev['must_missing']}"]
    if ev["detail_reasons"]:
        L += ["", "**상세 분석 권장 사유** (Composer 규칙 신호)", ""] + [f"- {r}" for r in ev["detail_reasons"]]
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


def record(case: str, gt: dict, state: AgentState, seconds: float, flow: dict | None, out_dir: Path) -> dict:
    """케이스 평가 → <case>.md 저장 + summary 항목."""
    ev, pf = evaluate(state, gt), perf(state, seconds)
    (out_dir / f"{case}.md").write_text(md_case(case, gt, state, ev, pf, flow), encoding="utf-8")
    return {"case": case, "title": gt["title"], "domain": "tos" if case.startswith("SC") else "swing",
            "difficulty": DIFFICULTY.get(gt["difficulty"], gt["difficulty"]), "flow": flow,
            "result": {**{k: v for k, v in ev.items() if k != "path_rows"}, **pf}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M"))
    ap.add_argument("--fresh", action="store_true", help="이미 끝난 케이스도 다시 실행 (기본은 이어서 실행)")
    ap.add_argument("--rebuild", action="store_true", help="실행 없이 저장된 state.json 으로 md·summary 만 다시 만듦")
    args = ap.parse_args()
    out_dir = ROOT / "outputs" / "runs" / "YJ" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 이어서 실행: 케이스가 끝날 때마다 summary 를 저장하므로, 중간에 끊겨도 다시 돌리면 끝난 케이스는 건너뛴다
    path = out_dir / "summary.json"
    summary = [] if args.fresh or not path.exists() else json.loads(path.read_text(encoding="utf-8"))
    if args.rebuild:  # 저장된 state.json 으로 다시 만들기 (지연은 저장된 값, 흐름은 summary 의 flow)
        old = {s["case"]: s for s in summary if "error" not in s}
        summary = [record(c, json.loads((GT_DIR / f"{c}.json").read_text(encoding="utf-8")),
                          AgentState.model_validate_json((out_dir / f"{c}.state.json").read_text(encoding="utf-8")),
                          (old[c].get("result") or old[c]["react"])["latency_s"], old[c].get("flow"), out_dir)
                   for c in args.cases if c in old]
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")
        print(f"다시 만듦: {out_dir} ({len(summary)}건)")
        return
    done = {s["case"] for s in summary if "error" not in s}
    summary = [s for s in summary if s["case"] in done]

    def save() -> None:
        order = {c: i for i, c in enumerate(args.cases)}
        summary.sort(key=lambda s: order.get(s["case"], len(order)))
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(summary_md(summary), encoding="utf-8")

    for case in args.cases:
        if case in done:
            print(f"[{case}] 이미 완료 — 건너뜀", flush=True)
            continue
        gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
        print(f"[{case}] 실행 중...", flush=True)
        try:
            state, react_s, flow = run_case(case)
        except Exception as e:  # 한 케이스 실패가 전체를 막지 않게 기록만 (다음 실행 때 다시 시도)
            print(f"[{case}] 실패: {type(e).__name__}: {e}", flush=True)
            summary.append({"case": case, "error": f"{type(e).__name__}: {e}"})
            save()
            continue
        (out_dir / f"{case}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        summary.append(record(case, gt, state, react_s, flow, out_dir))
        save()
        r = summary[-1]["result"]
        print(f"[{case}] 완료 {react_s:.0f}s  흐름 {flow['path']}  경로 {r['path_recall']}  must {r['must_find']}", flush=True)
    save()
    print(f"기록: {out_dir}")


def summary_md(summary: list[dict]) -> str:
    L = ["# 시나리오 실행 요약", "",
         "| 케이스 | 영역 | 난이도 | 흐름 | 복잡도(실제/정답) | 시작 적중 | 경로 재현율 | Critical 미탐 | must_find | 근거율 | 노드 정밀도 | 전달 경로·근거율 | 상세 분석 권장 | 지연(s) | tool | LLM 호출 | 토큰(입/출) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        if "error" in s:
            L.append(f"| {s['case']} | 실패: {s['error']} |" + " |" * 15)
            continue
        a = s.get("result") or s["react"]
        L.append(f"| {s['case']} | {s['domain']} | {s['difficulty']} | {(s.get('flow') or {}).get('path', '-')} | "
                 f"{a['complexity_actual']}/{a['complexity_expected']} | {a['start_hit']} | {a['path_recall']} | "
                 f"{a['critical_missed']}/{a['critical_total']} | {a['must_find']} | {a['evidence_coverage']} | "
                 f"{a['node_precision']} | {a.get('handoff_path_recall')}·{a.get('handoff_evidence')} | {a.get('detail_flow', '-')} | "
                 f"{a['latency_s']} | {a['tool_calls']} | {a['llm_calls']} | {a['input_tokens']}/{a['output_tokens']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
