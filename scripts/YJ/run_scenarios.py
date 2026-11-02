"""시나리오 E2E 실행 기록 + 정답셋 평가. UI 와 같은 전체 흐름 (Planner → … → Composer → KPI Agent).

흐름 (docs/planner_detail_analysis_to-be.md 의 사용자 확인을 평가용으로 가정):
    1차 판정 complex                    → 그대로 끝까지 (정답이 simple 이어도 2-3 Simulator · 2-4 Verifier · Composer)
    1차 판정 simple, 정답 complex       → "상세 분석 요청"을 눌렀다고 가정: reanalysis 후속 입력
                                         (내용 = Composer 권장 사유 + "참고: " 참고 신호, UI 와 같은 형식. 둘 다 없으면 "상세 분석 요청")
                                         → Planner 2차 판정 complex 고정 → 탐색부터 다시 → 끝까지
    1차 판정 simple, 정답 simple        → 단순 응답 그대로 (권장이 붙으면 "불필요한 권장")
    Verifier 재계획 라운드는 Planner 가 complex 를 유지한다 (에이전트 규칙 그대로).
정답 complexity 는 "사용자가 상세 분석을 요청할지"를 대신하는 데만 쓰고, 에이전트 입력에는 넣지 않는다 (정답셋은 KPI Agent 만 읽음).

    PYTHONPATH=src python3 scripts/YJ/run_scenarios.py --all --run-id v6/v6-2 --baseline v6/v6-1   (.env 의 Neo4j·LLM 사용, 16건)
    PYTHONPATH=src python3 scripts/YJ/run_scenarios.py SC-003 L-4 --run-id try          (일부만)
    PYTHONPATH=src python3 scripts/YJ/run_scenarios.py --report v6/v6-2 --baseline v6/v6-1   (실행 없이 report.html 만, 기준 실행과 비교)
    run_id 는 <버전>/<버전>-1(as-is) · <버전>-2(to-be). 기준 실행은 같은 E2E 흐름으로 돌린 run 만 준다
    (v5 이전은 2-3 Simulator 까지라 비교 대상이 아님. v6/v6-1 이 첫 기준선)
    --verbose 를 붙이면 에이전트 실행 로그가 터미널에도 찍힌다 (보통은 zsh scripts/YJ/run_all.sh 로 실행)

출력 (outputs/runs/YJ/<run_id>/, v5 처럼 영역별 폴더: tos = SC-*, swing = 나머지)
    <영역>/<case>.md          단계별 응답·현황 (Planner → Explorer → Retriever → Simulator·Verifier → Composer, ReAct, 평가, KPI)
    <영역>/<case>.log         실행 로그 (UI 실행 로그와 같은 형식: 받은 입력 → 동작 → 근거 → 결과)
    <영역>/<case>.state.json  최종 AgentState (원문 포함, 커밋하지 않음)
    <영역>/summary.md · summary.json  영역별 케이스 지표
    report.html               두 영역을 합친 한 장 기록 (scripts/YJ/report_html.py)
    --rebuild          실행 없이 저장된 state.json 으로 md·summary 만 다시 만든다
"""

import argparse
import json
import logging
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
from impact_agent.agents.kpi import load_answer_key  # noqa: E402
from impact_agent.api.agent_log import kpi_brief, kpi_rows, run_table, run_total  # noqa: E402
from impact_agent.api.runner import Runner, SessionRecord  # noqa: E402
from impact_agent.rules import build_impact_paths  # noqa: E402
from impact_agent.schemas import Complexity, FollowupInput, FollowupType, SmPart, UserInput  # noqa: E402
from impact_agent.state import AgentState  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_html import write_report  # noqa: E402

# 실행 로그는 <case>.log 로. 터미널은 진행만 (--verbose 면 실행 로그도 터미널에)
VERBOSE = "--verbose" in sys.argv
logging.getLogger("impact_agent.console").setLevel(logging.INFO if VERBOSE else logging.WARNING)
GT_DIR = ROOT / "eval" / "ground_truth"
ALL_CASES = ["SC-001", "SC-002", "SC-003", "SC-004", "SC-005", "SC-006",
             "H-1", "H-2", "M-1", "M-2", "M-3", "M-4", "L-1", "L-2", "L-3", "L-4"]
DIFFICULTY = {"Major": "상", "Medium": "중", "Minor": "하"}
KPI_NAMES = ["node_recall", "node_precision", "critical_miss_rate", "complexity_match", "difficulty_match", "feasibility_match",
             "area_impact_match", "change_tag_accuracy", "verifier_unresolved"]


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
        **change_tags(state, e, relevant),
        "paths_found": len(exp.paths),
        "path_rows": rows,
    }


def detail_flow(first: Complexity, expected: str | None, recommended: bool, notes: list[str]) -> str:
    """simple 판정일 때 상세 분석 권장 결과 (Composer 가 실제로 낸 권장·참고 신호 기준)."""
    if first is not Complexity.SIMPLE:
        return "complex 흐름"
    if expected == "complex":
        return "권장 → 상세 분석 가능" if recommended else "참고 신호만 (권장 없음)" if notes else "신호 없음 (놓침)"
    return "불필요한 권장" if recommended else "참고 신호만 (정상)" if notes else "단순 응답 (정상)"


def diagnose(case: str, state: AgentState, ev: dict, flow: dict, gt: dict) -> list[dict]:
    """케이스 징후 (분류별). 원인 추정이 아니라 측정값으로만 판단한다. 분석 결론은 <run>/findings.json 으로 report 5장에."""
    flags = []
    exp, plan = state.explorer, state.planner
    built = len(build_impact_paths(exp.paths, exp.edges, state.retriever.evidence if state.retriever else [])) if exp else 0
    if flow["first"] != gt.get("complexity"):
        flags.append({"code": "복잡도 판정", "text": f"1차 복잡도 {flow['first']} (정답 {gt.get('complexity')})"})
    if flow["detail_flow"] == "불필요한 권장":
        flags.append({"code": "권장", "text": "불필요한 권장: " + " / ".join(flow["reasons"])[:200]})
    if flow["detail_flow"] == "신호 없음 (놓침)":
        flags.append({"code": "권장", "text": "정답 complex 인데 권장·참고 신호 없음"})
    if flow["first"] == "simple" and gt.get("complexity") == "simple" and len(plan.items) >= 2:
        flags.append({"code": "항목 분리", "text": f"단순 요건인데 항목 {len(plan.items)}개로 분리"})
    oog = [u.text for u in (exp.unresolved_items if exp else []) if u.kind == "out_of_graph"]
    if oog and gt.get("complexity") == "simple":
        flags.append({"code": "그래프 밖", "text": "단순 요건의 그래프 밖: " + "; ".join(oog)[:200]})
    if state.planner.complexity is Complexity.SIMPLE and exp and len(exp.paths) > 20:
        flags.append({"code": "경로 과다", "text": f"단순 흐름 경로 {len(exp.paths)}개"})
    if built > sim.MAX_CONTEXT_PATHS:
        lost = "" if ev["handoff_path_recall"] is None or ev["path_recall"] is None or ev["handoff_path_recall"] >= ev["path_recall"] \
            else f" · 정답 경로 재현율 {ev['path_recall']} → Simulator 전달 {ev['handoff_path_recall']}"
        flags.append({"code": "전달 손실", "text": f"영향 경로 {built}개 (Simulator 는 앞 {sim.MAX_CONTEXT_PATHS}개만){lost}"})
    common = [m for m in ev["must_missing"] if "CMM" in m.upper() or (exp and any(n.node_id == m and n.properties.get("is_common") for n in exp.nodes))]
    if common or any("CMM_CD" in m for m in ev["must_missing"]):
        flags.append({"code": "공통 데이터 미탐", "text": "공통 데이터 미탐: " + ", ".join(dict.fromkeys(common + [m for m in ev["must_missing"] if "CMM_CD" in m]))})
    if ev["distractors_found"]:
        flags.append({"code": "탐색", "text": "방해 노드 도달: " + ", ".join(ev["distractors_found"])})
    if ev["must_missing"] and not common:
        flags.append({"code": "탐색", "text": "must_find 누락: " + ", ".join(ev["must_missing"])})
    return flags


def change_tags(state: AgentState, e: dict, relevant: set[str]) -> dict:
    """결과 경로의 [변경]/[참조] 표시로 본 정밀도. KPI 정밀도 분모는 경로 위 전체 노드라, 에이전트가 고친다고 한 노드만 따로 본다.

    결과 경로 = 복잡은 Simulator 영향 경로, 단순은 Composer 단순 응답 경로 (KPI final_nodes 와 같은 단계).
    """
    paths = ([p for a in state.simulation.area_impacts for p in a.paths] if state.simulation else
             state.composer.simple_answer.impact_paths if state.composer and state.composer.simple_answer else [])
    tag: dict[str, str] = {}
    for p in paths:
        for h in p.hops:
            t = "change" if h.impact.startswith(sim.CHANGE_TAG) else "reference" if h.impact.startswith(sim.REFERENCE_TAG) else "none"
            if tag.get(h.node) != "change":
                tag[h.node] = t
    changed = [n for n, t in tag.items() if t == "change"]
    gt_change = {i["node_id"] for i in e.get("impacted", []) if i.get("impact_type") in ("modify", "add")}
    fp = state.kpi.false_positive_nodes if state.kpi else []
    return {
        "output_nodes": len(tag), "change_nodes": len(changed), "untagged_nodes": sum(t == "none" for t in tag.values()),
        "change_precision": round(sum(n in relevant for n in changed) / len(changed), 3) if changed else None,
        "change_recall": round(len(set(changed) & gt_change) / len(gt_change), 3) if gt_change else None,
        "fp_reference_ratio": round(sum(tag.get(n) in ("reference", "none") for n in fp) / len(fp), 3) if fp else None,
    }


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
        "tool_calls": sum(r.tool_calls for r in runs),
        "explore_loops": state.explore_loop_count,
        "replans": state.replan_count,
        "rounds": sum(r.agent == "planner" for r in runs),
        "react_steps": {"explorer": len(state.explorer.react_trace) if state.explorer else 0,
                        "retriever": len(state.retriever.react_trace) if state.retriever else 0},
        "llm_calls": sum(1 for r in runs if r.agent in ("planner", "simulator", "verifier", "composer"))  # ReAct 단계 중 thought "-" 는 LLM 없이 시스템이 끝낸 단계
        + sum(s.thought != "-" for s in (state.explorer.react_trace if state.explorer else []))
        + sum(s.thought != "-" for s in (state.retriever.react_trace if state.retriever else [])),
    }


# --- 실행 ---------------------------------------------------------------------------


def run_case(case: str, out_dir: Path) -> tuple[AgentState, AgentState | None, float, dict]:
    """한 케이스를 UI 와 같은 흐름으로 실행. (최종 state, 재분석 전 1차 state, 초, 흐름) 을 돌려준다."""
    gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
    user_input = UserInput(request_text=gt["request_text"], sm_part=SmPart(gt["sm_part"]))
    (out_dir / f"{case}.log").unlink(missing_ok=True)
    rec = SessionRecord(meta={"id": case}, log_path=out_dir / f"{case}.log")  # 실행 로그 (UI 실행 로그와 같은 형식)
    sup = Supervisor()
    runner = Runner(sup)
    t0 = time.perf_counter()

    def run(round_no: int, label: str, text: str, fn) -> AgentState:
        rec.log("supervisor", f"{round_no}차 실행 · {case} · {label}", kind="run")
        rec.log("supervisor", text, kind="note", label="입력")
        before = len(sup.last_state.agent_runs) if round_no > 1 and sup.last_state else 0
        quiet = VERBOSE  # --verbose 면 실행 로그(▶ 시작 · ■ 끝 · ↓ 라우팅)가 터미널에 나오므로 진행 줄은 생략
        state = runner.run(lambda e: print(f"    {e['node']:<10} {e['status']}" + (f" {e['elapsedMs'] / 1000:.1f}s" if e.get("elapsedMs") else ""),
                                           flush=True) if e["status"] == "done" and not quiet else None, fn, rec.log)
        rec.log("supervisor", "실행 요약", kind="open")
        for line in run_table(state.agent_runs[before:]):
            rec.log("supervisor", line, kind="plain")
        rec.log("supervisor", run_total(state.agent_runs[before:]), kind="close")
        rec.log("kpi", f"KPI 평가 · 정답셋 {case}", kind="open")
        for label_, value in kpi_rows(state):
            rec.log("kpi", value, label=label_)
        rec.log("kpi", kpi_brief(state), kind="close")
        return state

    key = load_answer_key(GT_DIR / f"{case}.json")
    state = run(1, "새 분석", gt["request_text"], lambda: sup.analyze(user_input, session_id=case, answer_key=key))
    first = state.planner.complexity
    simple = state.composer.simple_answer if state.composer else None
    reasons = list(simple.detail_analysis_reasons) if simple else []
    notes = list(simple.detail_analysis_notes) if simple else []
    recommended = bool(simple and simple.detail_analysis_recommended)
    flow = {"first": first.value, "expected": gt.get("complexity"), "recommended": recommended, "reasons": reasons, "notes": notes,
            "reanalysis": None, "first_kpi": _kpi_values(state)}
    first_state = None
    if first is Complexity.SIMPLE and gt.get("complexity") == "complex":
        first_state = state.model_copy(deep=True)
        content = "\n".join(reasons + [f"참고: {x}" for x in notes]) or "상세 분석 요청"  # UI 의 "상세 분석 요청" 과 같은 형식
        flow["reanalysis"] = content
        print("    --- 상세 분석 요청 가정 → reanalysis → 2차", flush=True)
        state = run(2, "후속 입력 reanalysis (상세 분석 요청 가정)", content,
                    lambda: sup.followup(case, FollowupInput(type=FollowupType.REANALYSIS, content=content)))
    flow["final"] = state.planner.complexity.value
    flow["detail_flow"] = detail_flow(first, gt.get("complexity"), recommended, notes)
    flow["path"] = (f"simple → 재분석 → {flow['final']} → 끝까지" if flow["reanalysis"] is not None
                    else "complex → 끝까지" if first is Complexity.COMPLEX else "simple → 단순 응답")
    return state, first_state, time.perf_counter() - t0, flow


def _kpi_values(state: AgentState) -> dict:
    return {m.name.value: m.value for m in state.kpi.metrics} if state.kpi else {}


# --- 기록 ---------------------------------------------------------------------------


def md_case(case: str, gt: dict, state: AgentState, ev: dict, pf: dict, flow: dict | None = None) -> str:
    p, x, r = state.planner, state.explorer, state.retriever
    sr = p.structured_request
    L = [f"# {case} {gt['title']} ({DIFFICULTY.get(gt['difficulty'], gt['difficulty'])} / {gt['sm_part']})", "",
         "## 입력", "", f"> {gt['request_text']}", ""]
    if flow:
        L += ["## 흐름", "", f"- 1차 판정 **{flow['first']}** (정답 {flow['expected']}) → {flow['path']}",
              f"- simple 판정 시 상세 분석 권장: {flow.get('detail_flow', '-')}"]
        L += [f"  - 권장 사유: {x_}" for x_ in flow.get("reasons", [])] + [f"  - 참고 신호: {x_}" for x_ in flow.get("notes", [])]
        if flow["reanalysis"] is not None:
            L += ["- 상세 분석 요청 가정 → reanalysis 후속 입력 (아래 내용) → 2차", "", "```", flow["reanalysis"], "```"]
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
    if state.verifier is not None:
        v = state.verifier
        L += ["", "## 2-4 Verifier (파트너, 결과 요약)", "", f"- 결과 {v.result.value} · 재계획 {state.replan_count}회" + (f" · 사유 {v.reason}" if v.reason else "")]
        L += [f"- {c.check} {c.area.value if c.area else '-'}: {c.result.value} — {c.note}" for c in v.checks if c.result.value != "pass"]
    c_ = state.composer
    if c_ is not None:
        L += ["", "## 3 Composer (파트너, 결과 요약)", ""]
        if c_.simple_answer:
            L += [f"- 결론: {x}" for x in c_.simple_answer.conclusion_lines]
            L += [f"- 상세 분석 권장 {c_.simple_answer.detail_analysis_recommended} · 사유 {c_.simple_answer.detail_analysis_reasons} · 참고 {c_.simple_answer.detail_analysis_notes}"]
        elif c_.complex_report:
            L += [f"- 결론: {c_.complex_report.summary_conclusion}", f"- 체크리스트 {len(c_.complex_report.checklist)}건 · 분석 한계 {len(c_.complex_report.analysis_limits)}건"]
    L += ["", "## 실행 로그 (Supervisor)", "", "| 순서 | 에이전트 | 지연(s) | tool | 입력 토큰 | 출력 토큰 |", "|---|---|---|---|---|---|"]
    L += [f"| {i} | {a.agent} | {a.latency_ms / 1000:.1f} | {a.tool_calls} | {a.input_tokens or '-'} | {a.output_tokens or '-'} |"
          for i, a in enumerate(state.agent_runs, 1)]

    L += ["", "## 정답셋 대비 평가", "", "| 지표 | 값 |", "|---|---|"]
    for k, label in [("complexity_match", "복잡도 일치"), ("start_hit", "시작 노드 적중"), ("path_recall", "경로 재현율"),
                     ("path_exact", "경로 exact"), ("critical_missed", "Critical 미탐"), ("must_find", "must_find"),
                     ("evidence_coverage", "정답 경로 근거율"), ("node_precision", "노드 정밀도"), ("distractors_found", "방해 노드"),
                     ("handoff_path_recall", "Simulator 전달 경로 재현율"), ("handoff_evidence", "Simulator 전달 근거율"),
                     ("key_evidence", "[핵심] 근거 수"), ("paths_found", "Explorer 경로 수")]:
        L.append(f"| {label} | {ev[k]} |")
    for k, label in [("latency_s", "지연(s)"), ("tool_calls", "tool 호출"), ("llm_calls", "LLM 호출"),
                     ("input_tokens", "입력 토큰"), ("output_tokens", "출력 토큰")]:
        L.append(f"| {label} | {pf[k]} |")
    L += ["", "| 정답 경로 | Critical | 결과 |", "|---|---|---|"]
    for a in ev["path_rows"]:
        L.append(f"| {a['path_id']} | {'C' if a['critical'] else ''} | {a['result']} ({a['covered']}/{a['len']}) |")
    if ev["must_missing"]:
        L += ["", f"must_find 누락: {ev['must_missing']}"]
    if state.kpi is not None:
        k = state.kpi
        L += ["", "## KPI Agent (최종 라운드)", "", "| 지표 | 값 |", "|---|---|"] + [f"| {m.name.value} | {m.value:.3g} |" for m in k.metrics]
        L += [f"- 판정 메모: {x}" for x in k.judgement_notes] + ([f"- 미탐: {k.missed_nodes}"] if k.missed_nodes else [])
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


def record(case: str, gt: dict, state: AgentState, seconds: float, flow: dict, out_dir: Path,
           first_state: AgentState | None = None) -> dict:
    """케이스 평가 → <case>.md 저장 + summary 항목."""
    ev, pf = evaluate(state, gt), perf(state, seconds)
    (out_dir / f"{case}.md").write_text(md_case(case, gt, state, ev, pf, flow), encoding="utf-8")
    first = evaluate(first_state, gt) if first_state is not None else None
    return {"case": case, "title": gt["title"], "domain": domain_of(case),
            "difficulty": DIFFICULTY.get(gt["difficulty"], gt["difficulty"]), "flow": flow,
            "result": {**{k: v for k, v in ev.items() if k != "path_rows"}, **pf},
            "first_round": {k: first[k] for k in ("path_recall", "must_find", "node_precision", "paths_found")} if first else None,
            "kpi": _kpi_values(state), "agent_latency_s": pf["agent_latency_s"],
            "flags": diagnose(case, state, ev, flow, gt)}


def domain_of(case: str) -> str:
    return "tos" if case.startswith("SC") else "swing"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true", help="정답셋 16건 전부")
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M"))
    ap.add_argument("--baseline", help="report.html 에서 비교할 이전 E2E run_id (예: v6/v6-2 실행 때 v6/v6-1)")
    ap.add_argument("--report", metavar="RUN_ID", help="실행 없이 그 run 의 report.html 만 다시 만듦")
    ap.add_argument("--verbose", action="store_true", help="터미널에 에이전트 진행(▶ 시작 · ■ 끝 · ↓ 라우팅)을 출력. 상세는 <case>.log")
    ap.add_argument("--fresh", action="store_true", help="이미 끝난 케이스도 다시 실행 (기본은 이어서 실행)")
    ap.add_argument("--rebuild", action="store_true", help="실행 없이 저장된 state.json 으로 md·summary 만 다시 만듦")
    args = ap.parse_args()
    runs = ROOT / "outputs" / "runs" / "YJ"
    if args.report:
        print(f"기록: {write_report(runs / args.report, runs / args.baseline if args.baseline else None)}")
        return
    args.cases = ALL_CASES if args.all else args.cases
    if not args.cases:
        ap.error("케이스를 주거나 --all")
    run_dir = runs / args.run_id
    dirs = {d: run_dir / d for d in ("tos", "swing")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    base = runs / args.baseline if args.baseline else None

    # 이어서 실행: 케이스가 끝날 때마다 영역별 summary 를 저장하므로, 중간에 끊겨도 다시 돌리면 끝난 케이스는 건너뛴다
    summary = [] if args.fresh else [x for d in dirs.values() if (f := d / "summary.json").exists()
                                     for x in json.loads(f.read_text(encoding="utf-8"))]

    def save() -> None:
        order = {c: i for i, c in enumerate(ALL_CASES)}
        summary.sort(key=lambda x: order.get(x["case"], len(order)))
        for name, d in dirs.items():
            mine = [x for x in summary if domain_of(x["case"]) == name]
            (d / "summary.json").write_text(json.dumps(mine, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "summary.md").write_text(summary_md(mine), encoding="utf-8")
        write_report(run_dir, base)

    if args.rebuild:  # 저장된 state.json 으로 다시 만들기 (지연은 저장된 값, 흐름은 summary 의 flow)
        old = {x["case"]: x for x in summary if "error" not in x}
        rebuilt = []
        for c in args.cases:
            if c not in old:
                continue
            d = dirs[domain_of(c)]
            first = d / f"{c}.first.state.json"
            rebuilt.append(record(c, json.loads((GT_DIR / f"{c}.json").read_text(encoding="utf-8")),
                                  AgentState.model_validate_json((d / f"{c}.state.json").read_text(encoding="utf-8")),
                                  (old[c].get("result") or old[c]["react"])["latency_s"], old[c].get("flow"), d,
                                  AgentState.model_validate_json(first.read_text(encoding="utf-8")) if first.exists() else None))
        summary[:] = [x for x in summary if x["case"] not in {r["case"] for r in rebuilt}] + rebuilt
        save()
        print(f"다시 만듦: {run_dir} ({len(rebuilt)}건)")
        return
    done = {x["case"] for x in summary if "error" not in x}
    summary[:] = [x for x in summary if x["case"] in done]

    for case in args.cases:
        if case in done:
            print(f"[{case}] 이미 완료 — 건너뜀", flush=True)
            continue
        out_dir = dirs[domain_of(case)]
        gt = json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))
        print(f"[{case}] 실행 중... ({domain_of(case)})", flush=True)
        try:
            state, first_state, react_s, flow = run_case(case, out_dir)
        except Exception as e:  # 한 케이스 실패가 전체를 막지 않게 기록만 (다음 실행 때 다시 시도)
            print(f"[{case}] 실패: {type(e).__name__}: {e}", flush=True)
            summary.append({"case": case, "error": f"{type(e).__name__}: {e}"})
            save()
            continue
        (out_dir / f"{case}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        if first_state is not None:
            (out_dir / f"{case}.first.state.json").write_text(first_state.model_dump_json(indent=2), encoding="utf-8")
        summary.append(record(case, gt, state, react_s, flow, out_dir, first_state))
        save()
        r, k = summary[-1]["result"], summary[-1]["kpi"]
        print(f"[{case}] 완료 {react_s:.0f}s  흐름 {flow['path']}  권장 {flow['detail_flow']}  경로 {r['path_recall']}  "
              f"must {r['must_find']}  탐지율 {k.get('node_recall', '-')}", flush=True)
    save()
    print(f"기록: {run_dir}  (report.html, tos/, swing/)")


def summary_md(summary: list[dict]) -> str:
    L = ["# 시나리오 E2E 실행 요약", "",
         "| 케이스 | 영역 | 난이도 | 흐름 | 1차/정답 | 상세 분석 권장 | 경로 재현율 | Critical 미탐 | must_find | 근거율 | Simulator 전달 | "
         "KPI 탐지율 | KPI 정밀도 | 변경/참조 | 재계획 | 지연(s) | tool | 토큰(입/출) |",
         "|" + "---|" * 18]
    for s in summary:
        if "error" in s:
            L.append(f"| {s['case']} | 실패: {s['error']} |" + " |" * 16)
            continue
        a, f, k = s["result"], s.get("flow") or {}, s.get("kpi") or {}
        g = lambda n: f"{k[n]:.3g}" if isinstance(k.get(n), float) else "-"  # noqa: E731
        L.append(f"| {s['case']} | {s['domain']} | {s['difficulty']} | {f.get('path', '-')} | {f.get('first')}/{f.get('expected')} | "
                 f"{f.get('detail_flow', '-')} | {a['path_recall']} | {a['critical_missed']}/{a['critical_total']} | {a['must_find']} | "
                 f"{a['evidence_coverage']} | {a.get('handoff_path_recall')} | {g('node_recall')} | {g('node_precision')} | "
                 f"{g('change_tag_accuracy')} | {a.get('replans', 0)} | {a['latency_s']} | {a['tool_calls']} | {a['input_tokens']}/{a['output_tokens']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
