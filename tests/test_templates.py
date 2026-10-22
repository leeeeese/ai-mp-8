from impact_agent.schemas import Confidence, ImpactPath, PathHop, SimpleAnswer, SimpleEvidenceRow
from impact_agent.templates import render_simple


def test_render_simple():
    answer = SimpleAnswer(
        conclusion_lines=["a", "b", "c"],
        evidence_table=[SimpleEvidenceRow(claim="c", evidence="e", confidence=Confidence.CONFIRMED)],
    )
    md = render_simple(answer)
    assert "## 결론" in md and "| c | e | - | confirmed |" in md
    assert "## 영향 경로" not in md


def test_render_simple_with_paths():
    hops = [PathHop(node=n, relation="r", impact="i", confidence=Confidence.CONFIRMED) for n in ("SCR:a", "TP:b")]
    answer = SimpleAnswer(
        conclusion_lines=["a", "b", "c"], impact_paths=[ImpactPath(path_id="P1", hops=hops)], evidence_table=[],
    )
    assert "- P1: SCR:a → TP:b" in render_simple(answer)
