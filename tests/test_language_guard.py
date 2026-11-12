"""LLM 응답 언어 검사: 일본어·중국어(한자·가나)가 섞이면 1회 재요청, 그래도 섞이면 LLMError (에이전트가 규칙 결과로 대체)."""

import pytest
from pydantic import BaseModel

from impact_agent.llm import LANGUAGE_NOTE, LLMError, OpenAIClient, Usage, foreign_text

JA = "対象商品の有線必須条件に相当する wdCnt < 1 分岐を変更します"
ZH = "需要修改该条件"
KO = "[변경] 유선 필수 조건(wdCnt < 1) 분기를 무선만으로도 가입되게 바꿉니다 (CombScrbSvc.pgm L26-56)"


class Hop(BaseModel):
    node: str
    impact: str


class Draft(BaseModel):
    summary: str
    hops: list[Hop]


def draft(text: str) -> Draft:
    return Draft(summary="요약", hops=[Hop(node="MOD:CombScrbSvc.validateCombMbr", impact=text)])


class Scripted(OpenAIClient):
    """네트워크 없이 정해 둔 응답을 차례로 돌려주는 클라이언트."""

    def __init__(self, replies: list[Draft], language_guard: bool = True) -> None:
        self.model, self.seed, self.language_guard = "fake", None, language_guard
        self.replies, self.users = list(replies), []

    def _parse(self, system, user, schema):
        self.users.append(user)
        return self.replies.pop(0), Usage(10, 5)


def test_foreign_text_detects_japanese_chinese_only():
    assert foreign_text(draft(JA)) and foreign_text(ZH) and foreign_text({"x": ["ﾃｽﾄ"]})
    assert foreign_text(draft(KO)) == []
    assert foreign_text("SQL:tbch1010.make_snd_tgt#2 · TCP_MSG_SCRT.ADVT_YN — 확인 필요 (L1-9) → ①") == []


def test_korean_reply_passes_with_language_note():
    llm = Scripted([draft(KO)])
    out, usage = llm.parse("sys", "user", Draft)
    assert out.hops[0].impact == KO and len(llm.users) == 1 and llm.users[0].endswith(LANGUAGE_NOTE)
    assert usage.input_tokens == 10


def test_foreign_reply_is_retried_once():
    llm = Scripted([draft(JA), draft(KO)])
    out, usage = llm.parse("sys", "user", Draft)
    assert out.hops[0].impact == KO and len(llm.users) == 2 and "[재작성 지시]" in llm.users[1]
    assert usage.input_tokens == 20 and usage.output_tokens == 10


def test_foreign_after_retry_raises_for_rule_fallback():
    llm = Scripted([draft(JA), draft(ZH)])
    with pytest.raises(LLMError, match="일본어·중국어"):
        llm.parse("sys", "user", Draft)


def test_guard_off_for_planner_client():
    llm = Scripted([draft(JA)], language_guard=False)
    out, _ = llm.parse("sys", "user", Draft)
    assert out.hops[0].impact == JA and llm.users == ["user"]


def test_agent_falls_back_to_rules_when_language_keeps_drifting():
    """Composer 에 계속 일본어로 답하는 LLM 을 넣으면 규칙 문구로 결과물을 만든다."""
    from impact_agent.agents import ComposerAgent
    from impact_agent.agents.composer import ComplexDraft, DraftChecklistItem
    from test_composer import complex_state

    bad = ComplexDraft(summary_conclusion=JA, checklist=[DraftChecklistItem(owner="developer", question=JA, related_area=None)],
                       mail=None)

    class Drifting(Scripted):
        def _parse(self, system, user, schema):
            self.users.append(user)
            return bad, Usage(1, 1)

    out = ComposerAgent(llm=Drifting([])).run(complex_state())
    assert foreign_text(out) == [] and any("LLM 작성 실패" in x for x in out.complex_report.analysis_limits)
