"""보고서 저장 tool (Composer 사용)."""

from datetime import datetime
from pathlib import Path

from ..schemas import SaveReportInput, SaveReportOutput

REPORT_DIR = Path("outputs/reports")


def save_report(params: SaveReportInput) -> SaveReportOutput:
    if params.format == "pdf":
        # TODO: markdown → pdf 변환
        raise NotImplementedError
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(params.content, encoding="utf-8")
    return SaveReportOutput(file_path=str(path))
