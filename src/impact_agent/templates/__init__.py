"""Composer 출력 markdown 렌더링."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..schemas import ComplexReport, SimpleAnswer

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_simple(answer: SimpleAnswer) -> str:
    return _env.get_template("simple_answer.md.j2").render(answer=answer)


def render_complex(report: ComplexReport) -> str:
    return _env.get_template("complex_report.md.j2").render(report=report)


def render_handover(report: ComplexReport, request_text: str, target: str | None,
                    changes: list[tuple[str, str, str | None]]) -> str:
    """개발 전달용 정리본 (체크리스트 전부 확정 후)."""
    return _env.get_template("handover.md.j2").render(report=report, request_text=request_text, target=target, changes=changes)
