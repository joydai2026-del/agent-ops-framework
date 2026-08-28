"""The work log: the sign-off any stakeholder can read without an agent.

One row per scheduled cycle occurrence, appended by the agent that ran it, as
the last step of the run. No row means that occurrence did not finish. That
single sentence is the whole value: the human does not have to interrogate a
chat transcript, read logs, or trust an agent's summary of itself. They open one
table.

Format is CSV on purpose. A spreadsheet, a terminal, `git diff` and any language
can all read it, which is what "agent agnostic" has to mean once the humans are
included. The production desk this was extracted from writes the same rows into
a shared spreadsheet; the schema is identical and the appender is swappable.

Two correctness properties:

* **Identity is the occurrence, not the day.** The key is
  (date, cycle, run_key). An hourly cycle runs many times a day, so keying on
  (date, cycle) alone would have every sweep silently overwrite the last one and
  leave a log that looks like the desk ran once. `run_key` is the scheduled slot
  (an hour, a slot name), empty for a once-a-day cycle.
* **The upsert is a transaction.** Read, validate, upsert and write happen under
  an exclusive `flock`, and the replacement is spooled to a uniquely named file
  in the same directory before `os.replace`. Two cycles finishing at the same
  moment otherwise lose a sign-off, which is the one failure this file exists to
  make impossible.
"""

from __future__ import annotations

import contextlib
import csv
import fcntl
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .report import CycleReport, STATUSES

BASE_COLUMNS = ["date", "cycle", "run_key", "agent", "status"]
TAIL_COLUMNS = ["notes", "signed_off"]


def header_for(lanes: list[str]) -> list[str]:
    return [*BASE_COLUMNS, *lanes, *TAIL_COLUMNS]


class WorkLogError(RuntimeError):
    pass


@contextlib.contextmanager
def _guard(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = path.with_suffix(path.suffix + ".guard")
    handle = os.open(str(guard), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _validate_header(header: list[str], path: Path) -> list[str]:
    """Return the lane columns, refusing anything malformed."""
    if len(set(header)) != len(header):
        raise WorkLogError(f"{path}: work log header has duplicate columns: {header}")
    if header[:len(BASE_COLUMNS)] != BASE_COLUMNS or header[-len(TAIL_COLUMNS):] != TAIL_COLUMNS:
        raise WorkLogError(
            f"{path}: work log header is not in the expected shape.\n"
            f"  expected: {BASE_COLUMNS} + <lanes> + {TAIL_COLUMNS}\n"
            f"  found:    {header}"
        )
    return header[len(BASE_COLUMNS):-len(TAIL_COLUMNS)]


def append(
    path: str | os.PathLike[str],
    report: CycleReport,
    when: datetime | None = None,
    schema: list[str] | None = None,
    run_key: str = "",
) -> dict[str, str]:
    """Append (or update) this occurrence's sign-off row. Returns the row.

    One log holds every cycle of the desk, and different cycles run different
    lanes, so the file's lane columns are the desk's full lane set (`schema`)
    rather than this one cycle's. A lane a cycle does not run is written "n/a",
    which reads differently from "NOT RUN", and that distinction is the reason
    the column exists at all.
    """
    stamp = when or datetime.now(timezone.utc)
    status = report.status()
    if status not in STATUSES:
        raise WorkLogError(f"refusing to log unknown status {status!r}")

    log_path = Path(path)
    with _guard(log_path):
        header, rows = _read_rows(log_path)
        if header:
            existing_lanes = _validate_header(header, log_path)
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

        row = {
            "date": stamp.date().isoformat(),
            "cycle": report.cycle,
            "run_key": run_key,
            "agent": report.agent,
            "status": status,
            "notes": report.notes,
            "signed_off": stamp.replace(microsecond=0).isoformat(),
        }
        for lane in lanes:
            row[lane] = "n/a" if lane not in report.lanes else \
                report.lines.get(lane, "NOT RUN")

        key = (row["date"], row["cycle"], row["run_key"])
        for index, existing in enumerate(rows):
            if (existing.get("date"), existing.get("cycle"),
                    existing.get("run_key", "")) == key:
                rows[index] = row
                break
        else:
            rows.append(row)

        _write_rows(log_path, header_for(lanes), rows)
        return row


def read(path: str | os.PathLike[str]) -> list[dict[str, str]]:
    _, rows = _read_rows(Path(path))
    return rows


def missing_cycles(
    path: str | os.PathLike[str], date: str, expected: list[str]
) -> list[str]:
    """Cycles scheduled for `date` with no sign-off row at all.

    A cycle that ran and reported BLOCKED is NOT missing: it is present and
    unhealthy, which is a different question. `unhealthy_cycles` answers that
    one. Together they are what makes the log a monitor rather than a diary,
    and what gives the next cycle its catch-up list.
    """
    done = {row["cycle"] for row in read(path) if row.get("date") == date}
    return [cycle for cycle in expected if cycle not in done]


def unhealthy_cycles(
    path: str | os.PathLike[str], date: str
) -> list[dict[str, str]]:
    """Rows for `date` that finished in a state a human should look at."""
    return [row for row in read(path)
            if row.get("date") == date and row.get("status") in ("PARTIAL", "BLOCKED")]
