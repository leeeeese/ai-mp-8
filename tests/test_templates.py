from impact_agent.schemas import Confidence, SimpleAnswer, SimpleEvidenceRow
from impact_agent.templates import render_simple


def test_render_simple():
    answer = SimpleAnswer(
        conclusion_lines=["a", "b", "c"],
        evidence_table=[SimpleEvidenceRow(claim="c", evidence="e", confidence=Confidence.CONFIRMED)],
    )
    md = render_simple(answer)
    assert "## 결론" in md and "| c | e | - | confirmed |" in md
