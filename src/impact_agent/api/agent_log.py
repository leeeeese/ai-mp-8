"""실행 로그의 구조와 표시. 로그 한 건 = {at, agent, kind, section, label, text}.

    kind  run(실행 머리) · note(블록 밖 행) · open(블록 시작) · row(행) · plain(표 행) · close(블록 끝) · route(라우팅)
에이전트 블록은 받은 입력(← 앞 단계) → 동작 → 근거 → 결과(→ 다음 단계) 섹션으로 나눈다.
받은 입력은 실행 직전 state 에서(input_rows), 결과·근거는 에이전트 출력에서(output_rows) ProgressAgent 가 뽑고,
동작(tool·ReAct·LLM)은 에이전트가 진행 중에 trace 로 남긴다. 파트너 에이전트 코드는 건드리지 않는다.
터미널·파일·화면 모두 같은 내용 (줄이거나 숨기지 않음).
"""

import unicodedata
from typing import Any

from .. import config
from ..rules import SM_PART_DOMAINS, build_impact_paths
from ..schemas import AgentRunRecord, Complexity, SmPart, VerifyResult
from ..state import AgentState
from ..trace import clip

WIDTH = 110  # 블록 머리·꼬리 선 길이 (한글은 2칸)
LABEL_WIDTH = 10
SEP = " > "
INTAKE_TITLE = "0 요청 접수"
INPUT_TYPE = {"requirement": "요건서", "ops_inquiry": "운영 문의"}
SOURCE = {"mail": "메일", "itsd": "ITSD", "etc": "기타"}
TITLE = {"planner": "1 Planner", "explorer": "2-1 Explorer", "retriever": "2-2 Retriever", "simulator": "2-3 Simulator",
         "verifier": "2-4 Verifier", "composer": "3 Composer"}
IN, EVIDENCE, OUT = "받은 입력", "근거", "결과"
# Simulator 가 LLM 입력에 넣는 범위 (agents/simulator.py user_message 와 같은 값)
SIM_PATHS, SIM_LINES, SIM_CHARS = 40, 12, 16000
Row = tuple[str, str, str]  # section, label, text


# --- 표시 ------------------------------------------------------------------------------------


def _cells(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, cells: int) -> str:
    return text + " " * max(cells - _cells(text), 0)


def _fill(head: str, ch: str) -> str:
    return head + ch * max(WIDTH - _cells(head), 3)


def render(e: dict, last_section: str | None) -> tuple[list[str], str | None]:
    """로그 한 건 → 출력 줄. 섹션이 바뀌면 머리 줄을 넣는다. (줄, 현재 섹션) 을 돌려준다."""
    kind, text = e["kind"], e["text"]
    if kind == "run":
        return ["", _fill(f"══ {text} ", "═")], None
    if kind == "open":
        return [_fill(f"┌─ {text} ", "─")], None
    if kind == "close":
        return [f"└─ {text}"], None
    if kind == "route":
        return [f"   ↓ {text}"], None
    if kind == "note":
        return [f"   {_pad(e['label'], LABEL_WIDTH)} {text}"], last_section
    lines = []
    section = e["section"] or last_section
    if section and section != last_section:
        lines.append(f"│ ▸ {section}")
    lines.append(f"│     {text}" if kind == "plain" else f"│   {_pad(e['label'], LABEL_WIDTH)} {text}")
    return lines, section


# --- 받은 입력 (실행 직전 state) ------------------------------------------------------------------


def _source(name: str, state: AgentState) -> str:
    if name == "planner":
        return "Verifier 재계획" if state.replan_feedback and state.planner else "요청 접수"
    if name == "explorer":
        return "Retriever 재탐색 요청" if state.retriever and state.retriever.reexplore_request else "Planner"
    return {"retriever": "Explorer", "simulator": "Explorer · Retriever", "verifier": "Simulator · Retriever",
            "composer": "Verifier" if state.verifier else "Retriever"}[name]


def input_rows(name: str, state: AgentState) -> list[Row]:
    sec = f"{IN} ← {_source(name, state)}"
    rows: list[Row] = []
    if name == "planner":
        rows.append((sec, "SM", f"{state.user_input.sm_part.value} → 탐색 영역 {', '.join(d.value for d in SM_PART_DOMAINS[state.user_input.sm_part])}"))
        rows.append((sec, "요청 문장", clip(state.user_input.request_text, 400)))
        rows += [(sec, "후속 입력", f"({f.type.value}) {clip(f.content)}") for f in state.followups]
        rows += [(sec, f"재계획 {fb.round}", f"{clip(fb.reason)} / 추가 범위 {fb.additional_scope}") for fb in state.replan_feedback]
    elif name == "explorer":
        plan = state.planner
        requests = state.retriever.reexplore_request if state.retriever and state.explorer else []
        if requests:
            rows += [(sec, f"요청 {r.item_id}", f"{r.mode.value} {r.node_ids} → {r.target_role.value if r.target_role else '항목 목표'} — {clip(r.reason, 150)}") for r in requests]
            rows.append((sec, "이전 결과", f"경로 {len(state.explorer.paths)} · 노드 {len(state.explorer.nodes)} 에 이어 붙임"))
        else:
            rows.append((sec, "범위", f"영역 {', '.join(d.value for d in plan.domains)} · 공통 제외 {plan.exclude.is_common} · tool 남은 {state.tools_left()}회"))
            for it in plan.items:
                rows.append((sec, f"항목 {it.item_id}", f"{it.change_type.value} → 목표 {it.target_role.value} · {it.direction.value} · hop {it.hop_cap} | {clip(it.text, 150)}"))
                rows += [(sec, f"  {m.kind.value}", f"'{m.phrase}'" + (f" 동의어 {m.synonyms}" if m.synonyms else "") + (" [신규 가능]" if m.maybe_new else ""))
                         for m in it.mentions]
    elif name == "retriever":
        exp = state.explorer
        nodes = list(dict.fromkeys([s.node_id for s in exp.start_nodes] + [n for p in exp.paths for n in p.nodes]))
        prev = {e.node_id for e in state.retriever.evidence} if state.retriever else set()
        rows.append((sec, "경로", f"{len(exp.paths)}개 · 시작 노드 {len(exp.start_nodes)} · 경로 노드 {len(nodes)}"))
        rows.append((sec, "읽을 노드", f"{len([n for n in nodes if n not in prev])}개 (이전 라운드 근거 {len(prev)}개 노드는 이어 받음)"))
        rows += [(sec, "미해결", clip(u)) for u in exp.unresolved]
    elif name == "simulator":
        exp, ev = state.explorer, state.retriever.evidence if state.retriever else []
        built = build_impact_paths(exp.paths if exp else [], exp.edges if exp else [], ev)
        on_path = {h.node for p, _ in built[:SIM_PATHS] for h in p.hops}
        rows.append((sec, "경로", f"Explorer 경로 {len(exp.paths) if exp else 0} → 영향 경로 {len(built)}개 (LLM 에는 앞 {SIM_PATHS}개)"))
        rows.append((sec, "근거", f"Retriever 근거 {len(ev)} 중 경로 노드 근거 {sum(e.node_id in on_path for e in ev)}건 (근거당 앞 {SIM_LINES}줄 · 전체 {SIM_CHARS:,}자까지)"))
        rows += [(sec, "재분석 요청", clip(f.content, 300)) for f in state.followups[-1:] if f.type.value == "reanalysis"]
    elif name == "verifier":
        sim = state.simulation
        rows.append((sec, "판정", " · ".join(f"{a.area.value} {a.impact_type.value}/{a.feasibility.value}/{a.difficulty.value}" for a in sim.area_impacts)))
        rows.append((sec, "경로·근거", f"경로 {sum(len(a.paths) for a in sim.area_impacts)} · 근거 {len(state.retriever.evidence) if state.retriever else 0} · "
                     f"재계획 {state.replan_count}/{config.MAX_REPLAN}"))
    elif name == "composer":
        last = state.followups[-1] if state.followups else None
        if state.planner and state.planner.complexity is Complexity.COMPLEX and state.simulation:
            rows.append((sec, "복잡", f"영역 판정 {len(state.simulation.area_impacts)} · Verifier {state.verifier.result.value if state.verifier else '-'}"))
        else:
            rows.append((sec, "단순", f"Explorer 경로 {len(state.explorer.paths) if state.explorer else 0} · 근거 {len(state.retriever.evidence) if state.retriever else 0} (LLM 에는 근거 앞 40건)"))
        if last:
            rows.append((sec, "후속 입력", f"({last.type.value}) {clip(last.content)}"))
    return rows


# --- 결과·근거 (에이전트 출력) ---------------------------------------------------------------------


def _planner(out: Any) -> list[Row]:
    r, sec = out.structured_request, f"{OUT} → Explorer"
    rows: list[Row] = [
        (sec, "복잡도", f"{'단순' if out.complexity is Complexity.SIMPLE else '복잡'} — {clip(out.complexity_reason, 400)}"),
        (sec, "구조화", f"대상 {r.target} / 행위 {r.action} / 조건 {r.condition or '-'} / 정책 {r.policy or '-'} / 유형 {r.request_type.value}"),
    ]
    for it in out.items:
        rows.append((sec, f"항목 {it.item_id}", f"{it.change_type.value} → 목표 {it.target_role.value} · {it.direction.value} · hop {it.hop_cap} (기준표) | {clip(it.text, 150)}"))
        rows += [(sec, f"  {m.kind.value}", f"'{m.phrase}'" + (f" 동의어 {m.synonyms}" if m.synonyms else "") + (" [신규 가능]" if m.maybe_new else ""))
                 for m in it.mentions]
        if it.out_of_graph:
            rows.append((sec, "  그래프 밖", ", ".join(it.out_of_graph)))
    rows += [(sec, "업무 확정", f"{o.item} — {clip(o.reason, 150)}") for o in r.open_items]
    rows.append((sec, "계획", f"{' → '.join(s.agent.value for s in out.plan)} · 공통 제외 {out.exclude.is_common}"))
    return rows


def _explorer(out: Any) -> list[Row]:
    sec = f"{OUT} → Retriever"
    rows: list[Row] = [(sec, "요약", f"시작 노드 {len(out.start_nodes)} · 노드 {len(out.nodes)} · 경로 {len(out.paths)} (경로 순서 = 뒤 단계 전달 우선순위)")]
    rows += [(sec, f"항목 {r.item_id}", f"목표 {r.target_role.value} {'도달' if r.reached else '미도달'} · hop {r.hops_used}"
              + (f" · 끊긴 노드 {r.frontier}" if r.frontier else "") + (f" · 매칭 실패 {r.unmatched_mentions}" if r.unmatched_mentions else ""))
             for r in out.item_results]
    rows += [(sec, f"시작 [{s.item_id}]", f"{s.node_id} ← {s.reason}") for s in out.start_nodes]
    rows += [(sec, f"경로 {i}", f"[{p.item_id}] {p.stop_reason.value}: {SEP.join(p.nodes)}") for i, p in enumerate(out.paths, start=1)]
    rows += [(sec, "미해결", u) for u in out.unresolved]
    if out.excluded_common:
        rows.append((sec, "공통 제외", ", ".join(out.excluded_common)))
    return rows


def _retriever(out: Any) -> list[Row]:
    rows: list[Row] = [(EVIDENCE, "[핵심]" if e.summary.startswith("[핵심]") else e.source_kind,
                        f"{e.node_id} | {e.file_path} {e.location or ''} | {clip(e.summary.removeprefix('[핵심]').strip(), 160)}")
                       for e in out.evidence]
    sec = f"{OUT} → Simulator/Composer"
    rows.append((sec, "요약", f"근거 {len(out.evidence)} ([핵심] {sum(e.summary.startswith('[핵심]') for e in out.evidence)}) · "
                 f"근거 없음 {len(out.nodes_without_evidence)} · 재탐색 요청 {len(out.reexplore_request)}"))
    rows += [(sec, f"재탐색 {r.item_id}", f"{r.mode.value} {r.node_ids} → {r.target_role.value if r.target_role else '항목 목표'} — {r.reason}")
             for r in out.reexplore_request]
    if out.nodes_without_evidence:
        rows.append((sec, "근거 없음", ", ".join(out.nodes_without_evidence)))
    return rows


def _simulator(out: Any) -> list[Row]:
    rows: list[Row] = []
    for a in out.area_impacts:
        rows.append((EVIDENCE, a.area.value, f"{a.impact_type.value} — {clip(a.difficulty_basis, 200)}"))
        for p in a.paths:
            hops = " > ".join(f"{h.node}[{clip(h.impact, 60)}]" + (f"←{h.evidence_ref}" if h.evidence_ref else "*") for h in p.hops)
            rows.append((EVIDENCE, f"  {p.path_id}", hops))
    sec = f"{OUT} → Verifier"
    rows += [(sec, a.area.value, f"{a.impact_type.value} · {a.feasibility.value} · {a.difficulty.value} · {a.confidence.value} · 경로 {len(a.paths)}"
              + (f" · 불가 사유 {a.infeasible_reason}" if a.infeasible_reason else "") + (f" · 대안 {len(a.alternatives)}" if a.alternatives else ""))
             for a in out.area_impacts]
    rows += [(sec, "대안", f"{a.area.value}: {clip(x.description, 160)}") for a in out.area_impacts for x in a.alternatives]
    if out.requested_vs_alternative:
        rows.append((sec, "요청 vs 대안", clip(out.requested_vs_alternative)))
    rows += [(sec, "미확인", u) for u in out.unverified_segments]
    rows += [(sec, "제외 경로", f"{d.path.path_id}: {clip(d.reason, 150)}") for d in out.dropped_paths]
    return rows


def _verifier(out: Any) -> list[Row]:
    rows: list[Row] = [(EVIDENCE, f"{c.check}", f"{'통과' if c.result is VerifyResult.PASS else '보완'} {c.area.value if c.area else '-'}: {c.note}")
                       for c in out.checks]
    sec = f"{OUT} → {'Planner (재계획)' if out.result is VerifyResult.NEEDS_SUPPLEMENT else 'Composer'}"
    rows.append((sec, "판정", f"{'통과' if out.result is VerifyResult.PASS else '보완 필요'} · 점검 {len(out.checks)}건 중 보완 "
                 f"{sum(c.result is not VerifyResult.PASS for c in out.checks)}건"))
    if out.reason:
        rows.append((sec, "사유", out.reason))
    if out.additional_scope:
        rows.append((sec, "추가 범위", ", ".join(out.additional_scope)))
    return rows


def _composer(out: Any) -> list[Row]:
    s, c, sec = out.simple_answer, out.complex_report, f"{OUT} → 사용자"
    if s:
        rows: list[Row] = [(EVIDENCE, r.confidence.value, f"{clip(r.claim, 120)} ← {clip(r.evidence, 80)} {r.location or ''}") for r in s.evidence_table]
        rows.append((sec, "유형", f"단순 응답 · 근거 {len(s.evidence_table)} · 조회 쿼리 {len(s.check_queries)} · 분석 한계 {len(s.analysis_limits)}"))
        rows += [(sec, f"결론 {i}", x) for i, x in enumerate(s.conclusion_lines, start=1)]
        rows += [(sec, "분석 한계", x) for x in s.analysis_limits]
        rows.append((sec, "상세 분석", "권장 (강한 신호)" if s.detail_analysis_recommended else "권장 안 함"))
        rows += [(sec, "  사유", x) for x in s.detail_analysis_reasons]
        rows += [(sec, "  참고 신호", x) for x in s.detail_analysis_notes]  # 약한 신호: 권장 안 함, 상세 분석 요청 시 함께 전달
        return rows
    if c:
        rows = [(EVIDENCE, r.area.value, f"{r.feasibility.value} · {r.difficulty.value} · {r.confidence.value} | {clip(r.impact_path_summary, 160)} | "
                 f"근거 {', '.join(r.evidence_names)}") for r in c.area_verdicts]
        rows.append((sec, "유형", f"레포트 · 체크리스트 {len(c.checklist)} · 분석 한계 {len(c.analysis_limits)} · 일정 요인 {len(c.schedule_factors)}"))
        rows.append((sec, "결론", c.summary_conclusion))
        rows += [(sec, f"체크 {x.owner.value}", x.question) for x in c.checklist]
        rows += [(sec, "분석 한계", x) for x in c.analysis_limits]
        return rows
    return [(sec, "유형", "결과물 없음")]


FORMATTERS = {"planner": _planner, "explorer": _explorer, "retriever": _retriever, "simulator": _simulator,
              "verifier": _verifier, "composer": _composer}


def output_rows(name: str, out: Any) -> list[Row]:
    fmt = FORMATTERS.get(name)
    return fmt(out) if fmt else []


# --- 요청 접수 (Planner 앞단) --------------------------------------------------------------------


def _who(user: dict, sm: str) -> list[Row]:
    role = "관리자" if user["role"] == "admin" else "유저"
    return [("권한 확인", "사용자", f'{user["name"]} ({role} · SM {", ".join(user["smParts"])})'),
            ("권한 확인", "결과", f"요건 분석 권한 통과 · SM {sm} 접근 통과")]


def _limits() -> Row:
    return ("공유 상태 구성", "실행 한도", f"tool {config.MAX_TOOL_CALLS}회 · 왕복 {config.MAX_EXPLORE_LOOP}회 · 재계획 {config.MAX_REPLAN}회")


def intake_rows(user: dict, sm: str, input_type: str, source: str, text: str, sm_part: SmPart, answer_key: str | None, session_id: str) -> list[Row]:
    """새 요건: 입력 → 권한 → 공유 상태 (Planner 에 넘어가기 전 과정)."""
    domains = ", ".join(d.value for d in SM_PART_DOMAINS[sm_part])
    return [
        ("입력 접수", "유형", f"{INPUT_TYPE[input_type]} · 출처 {SOURCE[source]} · {len(text):,}자"),
        ("입력 접수", "요청 문장", clip(text, 400)),
        *_who(user, sm),
        ("공유 상태 구성", "세션", f"새 세션 {session_id[:8]} · SM 파트 고정"),
        ("공유 상태 구성", "탐색 영역", f"SM {sm} → {sm_part.value} → {domains}"),
        ("공유 상태 구성", "정답셋", f"{answer_key} 일치 (KPI Agent 만 사용, 분석 에이전트에는 전달 안 함)" if answer_key else "일치 없음 (성능 지표만 기록)"),
        _limits(),
    ]


def followup_rows(user: dict, sm: str, kind: str, followup_type: str, text: str, state: AgentState | None, restart: bool) -> list[Row]:
    """후속 입력: 입력 → 권한 → 이전 세션 상태를 이어 받음."""
    prior = state.planner.complexity.value if state and state.planner else "-"
    runs = len(state.agent_runs) if state else 0
    return [
        ("입력 접수", "유형", f"후속 입력 {kind} → {followup_type} · {len(text):,}자"),
        ("입력 접수", "내용", clip(text, 400)),
        *_who(user, sm),
        ("공유 상태 구성", "이어 받음", f"이전 판정 {prior} · 이전 에이전트 실행 {runs}회 · 후속 입력 최근 {config.MEMORY_WINDOW_N}개 유지"),
        ("공유 상태 구성", "다음 처리", "Planner 부터 다시 분석" if restart else "Composer 가 결과물만 갱신"),
        _limits(),
    ]


# --- 실행 요약 · KPI ---------------------------------------------------------------------------


def run_table(runs: list[AgentRunRecord]) -> list[str]:
    """에이전트별 실행 표. 표 머리는 칸이 맞게 영문."""
    lines = [f"{'#':<3}{'agent':<12}{'time(s)':>8}{'tool':>6}{'input':>10}{'output':>9}"]
    lines += [f"{i:<3}{r.agent:<12}{r.latency_ms / 1000:>8.1f}{r.tool_calls:>6}{r.input_tokens or 0:>10,}{r.output_tokens or 0:>9,}"
              for i, r in enumerate(runs, start=1)]
    return lines


def run_total(runs: list[AgentRunRecord]) -> str:
    tokens = sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in runs)
    return f"합계 {sum(r.latency_ms for r in runs) / 1000:.1f}s │ tool {sum(r.tool_calls for r in runs)} │ 토큰 {tokens:,}"


def kpi_rows(state: AgentState) -> list[tuple[str, str]]:
    k = state.kpi
    if k is None or not k.evaluable:
        return [("정답셋", "없음 (성능 지표만 기록)")]
    values = {m.name.value: m.value for m in k.metrics}
    names = {"node_recall": "노드 탐지율", "node_precision": "노드 정밀도", "complexity_match": "복잡도 일치", "feasibility_match": "가능성 일치",
             "difficulty_match": "난이도 일치", "area_impact_match": "영역 일치"}
    rows = [(label, f"{values[n]:.3f}") for n, label in names.items() if values.get(n) is not None]
    if k.missed_nodes:
        rows.append(("미탐", ", ".join(k.missed_nodes)))
    if k.false_positive_nodes:
        rows.append(("오탐", ", ".join(k.false_positive_nodes)))
    rows += [("판정 메모", n) for n in k.judgement_notes]
    return rows
