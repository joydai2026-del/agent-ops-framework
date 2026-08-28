"""Runbooks as versioned playbooks any model can execute.

A runbook is a markdown file in the project repo with a small machine readable
header. The header is the contract the code depends on (cycle id, lanes, lease,
required closing statuses); the prose below it is the instruction set a language
model follows. One file serves both readers.

Why a file and not a prompt: a prompt lives inside one vendor's agent and dies
with it. A runbook is reviewed in a pull request, diffed when it changes, and
executed by whichever model happens to be awake. Changing the desk's behaviour
is a commit, not a re-prompting session.

The header parser is deliberately tiny (a handful of scalar and list keys) so
the format stays writable by hand and readable without a dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class RunbookError(ValueError):
    pass


@dataclass(frozen=True)
class Runbook:
    path: Path
    cycle: str
    title: str
    lanes: list[str]
    lease_seconds: int
    body: str
    # Operating policy the code enforces, so a rule the prose states is not
    # left as an honour system. A documented rule nothing implements is worse
    # than no rule: it reads as a guarantee and behaves as a suggestion.
    skip_if_ran_within_minutes: int = 0
    skip_if_cycles_ran: tuple[str, ...] = ()
    log_when: str = "always"          # "always" or "work-or-blocked"

    def instructions(self) -> str:
        return self.body


def _parse_header(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RunbookError(f"{path}: runbook must start with a '---' header block")
    header: dict[str, object] = {}
    key = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return header, "\n".join(lines[index + 1:]).strip() + "\n"
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- "):
            if key is None:
                raise RunbookError(f"{path}: list item before any key")
            header.setdefault(key, [])
            if not isinstance(header[key], list):
                raise RunbookError(f"{path}: key {key!r} mixes a scalar and a list")
            header[key].append(line.lstrip()[2:].strip())
            continue
        if ":" not in line:
            raise RunbookError(f"{path}: cannot parse header line {line!r}")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        header[key] = value if value else []
    raise RunbookError(f"{path}: header block was never closed with '---'")


def load(path: str | os.PathLike[str]) -> Runbook:
    file_path = Path(path)
    header, body = _parse_header(file_path.read_text(encoding="utf-8"), file_path)

    for required in ("cycle", "title", "lanes"):
        if required not in header:
            raise RunbookError(f"{file_path}: header is missing {required!r}")
    lanes = header["lanes"]
    if not isinstance(lanes, list) or not lanes:
        raise RunbookError(f"{file_path}: 'lanes' must be a non empty list")
    if len(set(lanes)) != len(lanes):
        raise RunbookError(f"{file_path}: duplicate lane names in {lanes}")

    def integer(key: str, default: int) -> int:
        try:
            return int(str(header.get(key, default)))
        except ValueError as exc:
            raise RunbookError(f"{file_path}: {key} must be an integer") from exc

    log_when = str(header.get("log_when", "always"))
    if log_when not in ("always", "work-or-blocked"):
        raise RunbookError(
            f"{file_path}: log_when must be 'always' or 'work-or-blocked', "
            f"not {log_when!r}"
        )

    skip_cycles = header.get("skip_if_cycles_ran", [])
    if isinstance(skip_cycles, str):
        skip_cycles = [part.strip() for part in skip_cycles.split(",") if part.strip()]

    return Runbook(path=file_path, cycle=str(header["cycle"]),
                   title=str(header["title"]), lanes=[str(lane) for lane in lanes],
                   lease_seconds=integer("lease_seconds", 1800), body=body,
                   skip_if_ran_within_minutes=integer("skip_if_ran_within_minutes", 0),
                   skip_if_cycles_ran=tuple(str(c) for c in skip_cycles),
                   log_when=log_when)


def load_all(directory: str | os.PathLike[str]) -> dict[str, Runbook]:
    books: dict[str, Runbook] = {}
    for candidate in sorted(Path(directory).glob("*.md")):
        book = load(candidate)
        if book.cycle in books:
            raise RunbookError(
                f"two runbooks claim cycle {book.cycle!r}: "
                f"{books[book.cycle].path} and {book.path}"
            )
        books[book.cycle] = book
    return books
