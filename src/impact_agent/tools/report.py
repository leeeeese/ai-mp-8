"""보고서 저장 tool (Composer 사용). MD 는 그대로, PDF 는 markdown → HTML → weasyprint (report 옵션 묶음 필요)."""

from datetime import datetime
from pathlib import Path

from ..schemas import SaveReportInput, SaveReportOutput

REPORT_DIR = Path("outputs/reports")
PDF_CSS = """
body { font-family: "Noto Sans CJK KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; font-size: 10.5pt; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #bbb; padding: 4px 6px; vertical-align: top; }
th { background: #f0f0f0; }
code, pre { font-family: "D2Coding", monospace; font-size: 9pt; }
"""


def save_report(params: SaveReportInput) -> SaveReportOutput:
    """보고서를 outputs/reports 에 저장한다. PDF 변환 패키지(markdown, weasyprint)가 없으면 ImportError."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORT_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}"
    if params.format == "pdf":
        import markdown
        from weasyprint import HTML

        body = markdown.markdown(params.content, extensions=["tables", "fenced_code"])
        path = stem.with_suffix(".pdf")
        HTML(string=f"<html><head><meta charset='utf-8'><style>{PDF_CSS}</style></head><body>{body}</body></html>").write_pdf(path)
    else:
        path = stem.with_suffix(".md")
        path.write_text(params.content, encoding="utf-8")
    return SaveReportOutput(file_path=str(path))
