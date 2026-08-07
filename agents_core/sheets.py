"""Google Sheets sync for the finance ledger.

Optional: requires google-api-python-client + google-auth and a service-account JSON.
Config via env:
    AGENT_GOOGLE_SERVICE_ACCOUNT_FILE   path to the service-account JSON
    AGENT_SHEET_ID                      id from the sheet URL (.../spreadsheets/d/<ID>/edit...)
    AGENT_SHEET_RANGE                   default "A1:E1000"

If not configured, the tools return a clear setup error instead of failing silently.
"""
from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR, get_settings
from .tools import ToolError


def _credentials():
    from google.oauth2 import service_account  # type: ignore

    cfg = get_settings()
    if not cfg.google_service_account_file or not cfg.sheet_id:
        raise ToolError(
            "Google Sheets is not configured. Set AGENT_GOOGLE_SERVICE_ACCOUNT_FILE and "
            "AGENT_SHEET_ID in Agents/.env (see README)."
        )
    sa_path = Path(cfg.google_service_account_file).expanduser()
    if not sa_path.is_file():
        raise ToolError(f"service account file not found: {sa_path}")
    return service_account.Credentials.from_service_account_file(
        str(sa_path), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )


def _service():
    from googleapiclient.discovery import build  # type: ignore

    return build("sheets", "v4", credentials=_credentials(), cache_discovery=False)


def _rows_from_csv() -> list[list[str]]:
    csv_path = DATA_DIR / "finance_ledger.csv"
    if not csv_path.exists():
        return [["date", "type", "category", "description", "amount"]]
    rows = []
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        rows.append(line.split(","))
    return rows


def push_to_sheets() -> str:
    """Write the local CSV ledger to the configured sheet."""
    try:
        service = _service()
    except ImportError as exc:
        raise ToolError(
            "google-api-python-client not installed. Run: pip install google-api-python-client google-auth"
        ) from exc
    cfg = get_settings()
    values = _rows_from_csv()
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=cfg.sheet_id,
            range=cfg.sheet_range,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )
    return f"pushed {len(values)} rows to sheet {cfg.sheet_id} ({result.get('updatedCells', 0)} cells)"


def pull_from_sheets() -> str:
    """Overwrite the local CSV ledger with the sheet contents."""
    try:
        service = _service()
    except ImportError as exc:
        raise ToolError(
            "google-api-python-client not installed. Run: pip install google-api-python-client google-auth"
        ) from exc
    cfg = get_settings()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=cfg.sheet_id, range=cfg.sheet_range)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return "sheet is empty — nothing to pull"
    csv_path = DATA_DIR / "finance_ledger.csv"
    csv_path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    return f"pulled {len(rows)} rows from sheet into finance_ledger.csv"


def export_summary_to_sheets(summary_text: str) -> str:
    """Write a summary text to a cell (tab 'Summary', A1) for dashboards."""
    service = _service()
    cfg = get_settings()
    body = {"values": [[line] for line in summary_text.splitlines()[:50]]}
    service.spreadsheets().values().update(
        spreadsheetId=cfg.sheet_id,
        range="Summary!A1",
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()
    return "summary written to Summary!A1"
