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
