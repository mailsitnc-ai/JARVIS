"""Work with Google Sheets: search, read a range, create a spreadsheet, append a row, or set a cell/range.

Uses the Sheets API (needs Google connected with the spreadsheets scope: re-run `jarvis google login`).
"""
import re

from core.actions import ActionBroker

SKILL = {
    "name": "google_sheets",
    "description": "Search, read, create or update your Google Sheets, e.g. 'read my sheet Budget', "
                   "'add a row to sheet Expenses: Coffee, 4.50', 'set A1 in sheet Log to Done'.",
    "triggers": [
        r"\bgoogle\s+sheets?\b",
        r"\bspreadsheets?\b",
        r"\b(?:read|open|search|find|create|make|add\s+a\s+row|append|write|put|set|update)\b[^.\n]*\bsheet\b",
        r"\bsheet\b[^.\n]*\b(?:cell|row|column|range|[A-Z]\d)\b",
    ],
    "version": 1,
    "origin": "builtin",
}
_RANGE = re.compile(r"\b([A-Z]{1,2}\d{1,4}(?::[A-Z]{1,2}\d{1,4})?)\b")


def _actions(context):
    return context.get("actions") or ActionBroker(dry_run=bool(context.get("dry_run")))


def _name(request):
    m = re.search(r"\b(?:sheet|spreadsheet|workbook)\s+(?:called|named|titled\s+)?[\"']?([\w][\w '&\-]{1,60}?)[\"']?"
                  r"(?:\s+(?:to|cell|range|row|column|about|and|with|for|A\d)\b|[:.\n]|$)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:in|to|from)\s+(?:my\s+)?(?:google\s+)?(?:sheet|spreadsheet)\s+[\"']?([\w][\w '&\-]{1,60}?)"
                  r"[\"']?(?:[:.\n]|$)", request, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _values(text):
    return [v.strip().strip("\"'") for v in re.split(r"\s*[,|\t]\s*", text.strip()) if v.strip()]


def run(request, context):
    low = request.lower()
    actions = _actions(context)
    dry = context.get("dry_run")

    # Create.
    if re.search(r"\b(?:create|make|new|start)\b", low) and re.search(r"\b(?:sheet|spreadsheet|workbook)\b", low):
        m = re.search(r"\b(?:titled|called|named)\s+[\"']?(.+?)[\"']?(?:[.\n]|$)", request, re.IGNORECASE)
        title = (m.group(1).strip() if m else None) or _name(request) or "Untitled"
        return f"Would create a Google Sheet '{title}'." if dry else actions.create_sheet(title)

    name = _name(request)

    # Append a row.
    if re.search(r"\b(?:add\s+a\s+row|append|add\s+row)\b", low):
        if not name:
            return "Which sheet should I add a row to? e.g. 'add a row to sheet Expenses: Coffee, 4.50'."
        m = re.search(r"(?::|\brow\b[^:]*:|\bwith\b|\bof\b)\s*(.+)$", request)
        vals = _values(m.group(1)) if m else _values(re.sub(r"^.*\bto\b", "", request))
        if not vals:
            return "What values go in the row? e.g. 'add a row to sheet Expenses: Coffee, 4.50'."
        return f"Would add a row to '{name}'." if dry else actions.append_row(name, vals)

    # Set a cell / range.
    if re.search(r"\b(?:set|write|put|update|change)\b", low) and name:
        rng = _RANGE.search(request)
        m = re.search(r"\b(?:to|=|as|with)\s+(.+?)(?:[.\n]|$)", request)
        if rng and m:
            vals = _values(m.group(1))
            grid = [vals] if vals else [[""]]
            return f"Would write to '{name}' {rng.group(1)}." if dry else actions.write_sheet(name, rng.group(1), grid)

    # Read.
    if re.search(r"\b(?:read|open|show|what'?s\s+in|get|view)\b", low) and name:
        rng = _RANGE.search(request)
        cell_range = rng.group(1) if rng else "A1:Z50"
        return f"Would read '{name}'." if dry else actions.read_sheet(name, cell_range)

    # Search.
    if dry:
        return "Would search your Google Sheets."
    m = re.search(r"\b(?:for|about|matching|named|called)\s+(.+?)(?:[.\n?]|$)", request, re.IGNORECASE)
    query = (m.group(1).strip().strip("\"'") if m else None) or name
    if not query:
        return "What should I search your Google Sheets for?"
    return actions.search_sheets(query)
