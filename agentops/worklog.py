"""The work log: the sign-off any stakeholder can read without an agent.

One row per scheduled cycle, appended by the agent that ran it, as the last
step of the run. No row means the cycle did not finish. That single sentence is
the whole value: the human does not have to interrogate a chat transcript, read
logs, or trust an agent's summary of itself. They open one table.

Format is CSV on purpose. A spreadsheet, a terminal, `git diff` and any language
can all read it, which is what "agent agnostic" has to mean in practice. The
production desk this was extracted from writes the same rows into a shared
spreadsheet; the schema is identical and the appender is swappable.

Appends are idempotent by (date, cycle): a re-poked cycle updates its row in
place rather than logging the same cycle twice.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from .report import CycleReport, STATUSES

BASE_COLUMNS = ["date", "cycle", "agent", "status"]
TAIL_COLUMNS = ["notes", "signed_off"]


def header_for(lanes: list[str]) -> list[str]:
    return [*BASE_COLUMNS, *lanes, *TAIL_COLUMNS]


class WorkLogError(RuntimeError):
    pass


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})
    os.replace(tmp, path)


def append(
    path: str | os.PathLike[str],
    report: CycleReport,
    when: datetime | None = None,
    schema: list[str] | None = None,
) -> dict[str, str]:
    """Append (or update) this cycle's sign-off row. Returns the row written.

    One log holds every cycle of the desk, and different cycles run different
    lanes, so the file's lane columns are the desk's full lane set (`schema`)
    rather than this one cycle's. A lane a cycle does not run is written as
    "n/a", which reads differently from "NOT RUN" and that distinction is the
    reason the column exists at all.
    """
    stamp = when or datetime.now(timezone.utc)
    status = report.status()
    if status not in STATUSES:
        raise WorkLogError(f"refusing to log unknown status {status!r}")

    log_path = Path(path)
    header, rows = _read_rows(log_path)
    if header:
        existing_lanes = [c for c in header
                          if c not in BASE_COLUMNS and c not in TAIL_COLUMNS]
        lanes = schema or existing_lanes
        if schema and existing_lanes != schema:
            raise WorkLogError(
                "work log schema changed; migrate the file deliberately.\n"
                f"  existing: {existing_lanes}\n  wanted:   {schema}"
            )
        lanes = existing_lanes
    else:
        lanes = schema or list(report.lanes)

    unknown = [lane for lane in report.lanes if lane not in lanes]
    if unknown:
        raise WorkLogError(
            f"cycle {report.cycle!r} reports lanes absent from the work log "
            f"schema: {unknown}. Add the column deliberately, do not drop the lane."
        )
    wanted = header_for(lanes)

    row = {
        "date": stamp.date().isoformat(),
        "cycle": report.cycle,
        "agent": report.agent,
        "status": status,
        "notes": report.notes,
        "signed_off": stamp.replace(microsecond=0).isoformat(),
    }
    for lane in lanes:
        if lane not in report.lanes:
            row[lane] = "n/a"
        else:
            row[lane] = report.lines.get(lane, "NOT RUN")

    key = (row["date"], row["cycle"])
    for index, existing in enumerate(rows):
        if (existing.get("date"), existing.get("cycle")) == key:
            rows[index] = row
            break
    else:
        rows.append(row)

    _write_rows(log_path, wanted, rows)
    return row


def read(path: str | os.PathLike[str]) -> list[dict[str, str]]:
    _, rows = _read_rows(Path(path))
    return rows


def missing_cycles(
    path: str | os.PathLike[str], date: str, expected: list[str]
) -> list[str]:
    """Cycles that were scheduled for `date` but never signed off.

    This is what makes the log a monitor and not just a diary: a stakeholder,
    or the next agent's catch-up step, can ask what did not finish.
    """
    done = {row["cycle"] for row in read(path) if row.get("date") == date}
    return [cycle for cycle in expected if cycle not in done]
