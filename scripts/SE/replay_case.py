"""상류 고정 재생: Planner·Explorer·Retriever 결과를 스냅샷으로 고정하고 Simulator → Verifier → Composer → KPI 만 반복 실행한다.

SE 담당 구간 고도화용 단일 시나리오 테스트 (상류 변동을 빼고 SE 변경 효과만 비교).

    # 1) 스냅샷 (1회, .env 의 Neo4j·LLM 사용). 1차 simple + 정답 complex 면 reanalysis 후 Retriever 까지
    PYTHONPATH=src python3 scripts/SE/replay_case.py snapshot SC-003
    #    조건 B: 정답 경로(path_id)를 그래프·소스에서 읽어 A 스냅샷에 추가 (입력 부족 vs 판정 오류 분리)
    PYTHONPATH=src python3 scripts/SE/replay_case.py inject SC-003 --paths P2

    # 2) 재생 (버전마다). 재계획은 실행하지 않고 Verifier 판정만 남긴다
    PYTHONPATH=src python3 scripts/SE/replay_case.py replay SC-003 --cond A --runs 3 --version v0
    PYTHONPATH=src python3 scripts/SE/replay_case.py replay SC-003 --cond B --runs 3 --version v0 --no-llm

출력 (outputs/runs/SE/replay/<case>/)
    snapshots/<case>.<A|B>.json        고정한 상류 상태 (원문 포함, 커밋하지 않음)  / snapshots/<case>.B.inject.md 주입 내역
    <version>/<cond>[-rule]-r<n>.md    회차별 기록 (run_scenarios.py 와 같은 형식)
    <version>/<cond>[-rule]-r<n>.state.json
    <version>/checkpoints.md           체크포인트 × 회차 (자동 항목은 값, 수동 항목은 판정 칸)
    <version>/summary.json
    versions.md                        버전·조건별 자동 체크포인트 평균
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from run_scenarios import fmt, md_case, metric_values, perf  # noqa: E402

from impact_agent.agents import Supervisor  # noqa: E402
from impact_agent.agents.kpi import load_answer_key  # noqa: E402
from impact_agent.agents.retriever import _summary  # noqa: E402
from impact_agent.agents.simulator import NODE_ID_IN_TEXT, requirement_difficulty  # noqa: E402
from impact_agent.schemas import (  # noqa: E402
    Complexity,
    Direction,
    Evidence,
    ExplorePath,
    FollowupInput,
    FollowupType,
    ReadSourceInput,
    SmPart,
    StopReason,
    UserInput,
)
from impact_agent.state import AgentState  # noqa: E402
from impact_agent.tools.graph import get_graph_store  # noqa: E402
from impact_agent.tools.source import read_source  # noqa: E402

GT_DIR = ROOT / "eval" / "ground_truth"
OUT_ROOT = ROOT / "outputs" / "runs" / "SE" / "replay"
CHANGE_TAG = "[변경]"
# 자동 체크포인트 (KPI Agent 지표 그대로, 설계 변경 없음)
AUTO_METRICS = [
    ("area_impact_match", "S1 영역 영향 일치"), ("difficulty_match", "S2 난이도 일치"), ("feasibility_match", "S3 가능 여부 일치"),
    ("change_tag_accuracy", "S4·S5 변경/참조 표시"), ("wrong_path_adopted", "오답 경로 채택"),
    ("node_recall", "탐지율"), ("critical_miss_rate", "Critical 미탐률"), ("node_precision", "노드 정밀도(참고)"),
    ("change_node_precision", "[변경] 정밀도"),
]


def gt_of(case: str) -> dict:
    return json.loads((GT_DIR / f"{case}.json").read_text(encoding="utf-8"))


def case_dir(case: str) -> Path:
    return OUT_ROOT / case


def snapshot_path(case: str, cond: str) -> Path:
    return case_dir(case) / "snapshots" / f"{case}.{cond}.json"


# --- 1) 스냅샷 -------------------------------------------------------------------


def snapshot(case: str) -> None:
    gt = gt_of(case)
    sup = Supervisor()
    user_input = UserInput(request_text=gt["request_text"], sm_part=SmPart(gt["sm_part"]))
    state = sup.analyze(user_input, until="retriever", answer_key=load_answer_key(GT_DIR / f"{case}.json"))
    first = state.planner.complexity
    if first is Complexity.SIMPLE and gt.get("complexity") == "complex":  # run_scenarios.py 와 같은 가정 (사용자 상세 분석 요청)
        # 1차 단순 응답을 실제 흐름처럼 Composer 로 만든다 (권장 신호가 Composer 의 [변경] 표시를 쓰므로)
        answer = sup.composer.run(state.model_copy(deep=True)).simple_answer
        reasons, notes = answer.detail_analysis_reasons, answer.detail_analysis_notes  # 약한 신호도 함께 (run_scenarios.py·UI 와 같음)
        content = "\n".join(reasons + [f"참고: {x}" for x in notes]) or "상세 분석 요청"
        state = sup.followup(sup.last_session_id, FollowupInput(type=FollowupType.REANALYSIS, content=content),
                             until="retriever")
    path = snapshot_path(case, "A")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    print(f"스냅샷 A: {path}  (1차 {first.value} → {state.planner.complexity.value}, 경로 {len(state.explorer.paths)}, "
          f"근거 {len(state.retriever.evidence)})")


def inject(case: str, path_ids: list[str], item_id: int | None) -> None:
    """정답 경로를 A 스냅샷에 추가해 B 를 만든다. 그래프에 실제 있는 노드·엣지·원문만 넣고, 없으면 그 경로는 건너뛴다."""
    gt = gt_of(case)
    state = AgentState.model_validate_json(snapshot_path(case, "A").read_text(encoding="utf-8"))
    store = get_graph_store()
    exp, ret = state.explorer, state.retriever
    item = item_id or (state.planner.items[0].item_id if state.planner.items else 1)
    have_nodes = {n.node_id for n in exp.nodes}
    have_edges = {(e.from_id, e.to_id, e.type) for e in exp.edges}
    have_ev = {e.node_id for e in ret.evidence}
    log = [f"# {case} 조건 B 주입 내역", "", f"- 대상 항목: {item}", ""]
    for pid in path_ids:
        gp = next((p for p in gt["expected"]["paths"] if p["path_id"] == pid), None)
        if gp is None:
            log.append(f"- {pid}: 정답셋에 없음 — 건너뜀")
            continue
        nodes = [store.node(n) for n in gp["nodes"]]
        if any(n is None for n in nodes):
            log.append(f"- {pid}: 그래프에 없는 노드 {[i for i, n in zip(gp['nodes'], nodes) if n is None]} — 건너뜀")
            continue
        edges = []
        for a, b in zip(gp["nodes"], gp["nodes"][1:]):
            e = next((x for x in store.out_edges(a) if x.to_id == b), None)
            if e is None:
                break
            edges.append(e)
        if len(edges) != len(gp["nodes"]) - 1:
            log.append(f"- {pid}: 그래프에 이어지는 엣지가 없음 — 건너뜀")
            continue
        exp.nodes += [n for n in nodes if n.node_id not in have_nodes]
        exp.edges += [e for e in edges if (e.from_id, e.to_id, e.type) not in have_edges]
        exp.paths.append(ExplorePath(item_id=item, nodes=gp["nodes"], direction=Direction.FORWARD,
                                     reached_role=nodes[-1].role, stop_reason=StopReason.TARGET_REACHED))
        exp.excluded_common = [n for n in exp.excluded_common if n not in gp["nodes"]]
        added = []
        need = [n for n in gp["nodes"] if n not in have_ev]
        for src in read_source(ReadSourceInput(node_ids=need), store) if need else []:
            if not src.found or not src.content.strip():
                continue
            node = store.node(src.node_id)
            ret.evidence.append(Evidence(
                node_id=src.node_id, file_path=src.file_path, symbol=node.name if node else None,
                location=f"L{src.start_line}-{src.end_line}" if src.start_line else None,
                summary=_summary(node, src), source_kind=src.source_kind,
                domain=node.domain if node else None, content=src.content))
            added.append(src.node_id)
        ret.nodes_without_evidence = [n for n in ret.nodes_without_evidence if n not in added]
        ret.nodes_without_evidence += [n for n in need if n not in added and n not in ret.nodes_without_evidence]
        have_nodes |= set(gp["nodes"])
        have_ev |= set(added)
        log.append(f"- {pid}: `{' → '.join(gp['nodes'])}` 추가 (엣지 {[e.type for e in edges]}, 원문 추가 {added or '-'})")
    out = snapshot_path(case, "B")
    out.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    (out.parent / f"{case}.B.inject.md").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log[3:]) + f"\n스냅샷 B: {out}")


# --- 2) 재생 ---------------------------------------------------------------------


def replay_once(base: AgentState, sup: Supervisor) -> tuple[AgentState, float]:
    """스냅샷 복사본에서 SE 구간만 실행. 재계획(Planner 재실행)은 하지 않는다."""
    state = base.model_copy(deep=True)
    state.simulation = state.verifier = state.composer = state.kpi = None
    t0 = time.perf_counter()
    if state.planner.complexity is Complexity.COMPLEX:
        state.simulation = sup._call("simulator", sup.simulator, state)
        state.verifier = sup._call("verifier", sup.verifier, state)
    state.composer = sup._call("composer", sup.composer, state)
    state.kpi = sup.kpi.run(state)
    return state, time.perf_counter() - t0


def tagged_change(state: AgentState) -> set[str]:
    sim = state.simulation
    return {h.node for a in sim.area_impacts for p in a.paths for h in p.hops if h.impact.startswith(CHANGE_TAG)} if sim else set()


def auto_checks(state: AgentState, gt: dict, se_seconds: float) -> dict:
    """체크포인트 자동 항목: KPI 지표 그대로 + 정답셋 함정·제외 경로·날조 노드 대조 (KPI 계산은 바꾸지 않음)."""
    k = metric_values(state.kpi.model_dump(mode="json") if state.kpi else None)
    out = {label: k.get(name) for name, label in AUTO_METRICS}
    e = gt["expected"]
    must = {i["node_id"] for i in e.get("impacted", []) if i.get("must_find")}
    changed = tagged_change(state)
    traps = [w["node_id"] for w in gt.get("wrong_impacts", [])] + [d["node_id"] for d in gt.get("distractors", [])]
    sim, v, c = state.simulation, state.verifier, state.composer
    exp, ret = state.explorer, state.retriever
    known = ({n.node_id for n in exp.nodes} | {x.node_id for x in exp.start_nodes} | set(exp.excluded_common)) if exp else set()
    known |= ({e.node_id for e in ret.evidence if e.node_id} | set(ret.nodes_without_evidence)) if ret else set()
    text = ""
    if c and c.complex_report:
        r = c.complex_report
        text = " ".join([r.summary_conclusion] + [x.question for x in r.checklist])
    elif c and c.simple_answer:
        text = " ".join(c.simple_answer.conclusion_lines + c.simple_answer.detail_analysis_reasons)
    out.update({
        "S2 실제 난이도": requirement_difficulty(sim.area_impacts)[0].value if sim else "-",
        "S1 실제 영역": " ".join(f"{a.area.value}:{a.impact_type.value}" for a in sim.area_impacts) if sim else "-",
        "S6 함정 노드를 [변경]": [n for n in traps if n in changed],
        "S8 제외 경로의 must_find": sorted({h.node for d in (sim.dropped_paths if sim else []) for h in d.path.hops} & must),
        "S8 제외 경로 수": len(sim.dropped_paths) if sim else 0,
        "V 결과": v.result.value if v else "-",
        "V 보완 사유": v.reason if v else "-",
        "V 추가 범위": v.additional_scope if v else [],
        "C 체크리스트 수": len(c.complex_report.checklist) if c and c.complex_report else "-",
        "C 상세 분석 권장": c.simple_answer.detail_analysis_recommended if c and c.simple_answer else "-",
        "C3 입력에 없는 노드 ID": sorted({m.rstrip(".") for m in NODE_ID_IN_TEXT.findall(text)} - known),
        "SE 지연(s)": round(se_seconds, 1),
        "SE 토큰": sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in state.agent_runs
                     if r.agent in ("simulator", "verifier", "composer")),
    })
    return out


def checkpoints_md(case: str, gt: dict, version: str, rows: list[dict]) -> str:
    cols = [f"{r['cond']}-r{r['run']}" for r in rows]
    L = [f"# {case} 체크포인트 ({version})", "", f"정답: 난이도 {gt['difficulty']} · 영역 {gt['expected'].get('areas')} · "
         f"가능 여부 {gt['expected'].get('feasibility')}", "", "## 자동", "",
         "| 체크포인트 | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for key in rows[0]["auto"] if rows else []:
        L.append(f"| {key} | " + " | ".join(fmt(r["auto"][key]) if not isinstance(r["auto"][key], list)
                                             else (", ".join(r["auto"][key]) or "없음") for r in rows) + " |")
    e = gt["expected"]
    manual = [("S6 함정 (md 노드별 영향 확인)", ", ".join([w["node_id"] for w in gt.get("wrong_impacts", [])]
                                                   + [d["node_id"] for d in gt.get("distractors", [])]) or "-"),
              ("S7 해결 방향", (gt.get("conclusion") or {}).get("correct", "-")),
              ("C1 요약 정합 (가능 여부·난이도·핵심)", "Simulator 판정과 같고 S7 핵심 포함"),
              ("C4 분석 한계", "; ".join(gt.get("analysis_limits", [])) or "-")]
    manual += [(f"C2 open_item ({o.get('owner', '-')})", o.get("question", str(o))) for o in e.get("open_items", [])]
    L += ["", "## 수동 (O / △ / X)", "", "| 체크포인트 | 정답 | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
    L += [f"| {k} | {v} |" + " |" * len(cols) for k, v in manual]
    L += ["", "## 원인 분류 (실패 체크포인트마다)", "",
          "| 체크포인트 | 원인 (입력 부족·상류 / 입력 구성 / 프롬프트 / 규칙 보정 / Composer 정리) | 근거 | 다음 조치 |",
          "|---|---|---|---|", "| | | | |"]
    return "\n".join(L) + "\n"


def versions_md(case: str) -> str:
    L = [f"# {case} 버전 비교 (자동 체크포인트 평균)", ""]
    heads = [label for _, label in AUTO_METRICS] + ["SE 지연(s)", "SE 토큰"]
    L += ["| 버전 | 조건 | 회차 | " + " | ".join(heads) + " |", "|---|---|---|" + "---|" * len(heads)]
    for p in sorted(case_dir(case).glob("*/summary.json")):
        rows = json.loads(p.read_text(encoding="utf-8"))
        for cond in dict.fromkeys(r["cond"] for r in rows):
            rs = [r for r in rows if r["cond"] == cond]
            vals = []
            for h in heads:
                xs = [r["auto"].get(h) for r in rs if isinstance(r["auto"].get(h), (int, float))]
                vals.append(fmt(sum(xs) / len(xs)) if xs else "-")
            L.append(f"| {p.parent.name} | {cond} | {len(rs)} | " + " | ".join(vals) + " |")
    return "\n".join(L) + "\n"


def replay(case: str, cond: str, runs: int, version: str, no_llm: bool) -> None:
    if no_llm:
        os.environ["ANALYSIS_LLM"] = "0"
    gt = gt_of(case)
    base = AgentState.model_validate_json(snapshot_path(case, cond).read_text(encoding="utf-8"))
    base.answer_key = load_answer_key(GT_DIR / f"{case}.json")
    label = cond + ("-rule" if no_llm else "")
    out_dir = case_dir(case) / version
    out_dir.mkdir(parents=True, exist_ok=True)
    sum_path = out_dir / "summary.json"
    rows = [r for r in (json.loads(sum_path.read_text(encoding="utf-8")) if sum_path.exists() else []) if r["cond"] != label]
    sup = Supervisor()
    for n in range(1, runs + 1):
        state, seconds = replay_once(base, sup)
        name = f"{label}-r{n}"
        flow = {"first": base.planner.complexity.value, "expected": gt.get("complexity"), "final": state.planner.complexity.value,
                "path": f"재생 (상류 고정 {cond}{', 규칙만' if no_llm else ''}, 재계획 미실행)", "detail_flow": "-",
                "reasons": [], "reanalysis": None, "first_kpi": None}
        (out_dir / f"{name}.state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / f"{name}.md").write_text(md_case(case, gt, state, perf(state, seconds), flow), encoding="utf-8")
        rows.append({"cond": label, "run": n, "auto": auto_checks(state, gt, seconds)})
        a = rows[-1]["auto"]
        print(f"[{case} {version} {name}] {seconds:.0f}s  영역 {fmt(a['S1 영역 영향 일치'])}  난이도 {a['S2 실제 난이도']}  "
              f"Verifier {a['V 결과']}  [변경] 정밀도 {fmt(a['[변경] 정밀도'])}", flush=True)
    rows.sort(key=lambda r: (r["cond"], r["run"]))
    sum_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "checkpoints.md").write_text(checkpoints_md(case, gt, version, rows), encoding="utf-8")
    (case_dir(case) / "versions.md").write_text(versions_md(case), encoding="utf-8")
    print(f"기록: {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="상류(Retriever 까지) 스냅샷 A")
    s.add_argument("case")
    i = sub.add_parser("inject", help="A 에 정답 경로를 추가해 B 생성")
    i.add_argument("case")
    i.add_argument("--paths", nargs="+", required=True, help="정답셋 expected.paths 의 path_id (예: P2)")
    i.add_argument("--item", type=int, default=None, help="추가 경로의 요건 항목 id (기본: 첫 항목)")
    r = sub.add_parser("replay", help="스냅샷에서 Simulator → Verifier → Composer → KPI 반복 실행")
    r.add_argument("case")
    r.add_argument("--cond", choices=["A", "B"], default="A")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--version", required=True, help="고도화 버전 (예: v0)")
    r.add_argument("--no-llm", action="store_true", help="ANALYSIS_LLM=0 (규칙만)")
    args = ap.parse_args()
    if args.cmd == "snapshot":
        snapshot(args.case)
    elif args.cmd == "inject":
        inject(args.case, args.paths, args.item)
    else:
        replay(args.case, args.cond, args.runs, args.version, args.no_llm)


if __name__ == "__main__":
    main()
