"""The reporting contract: silence is not allowed.

Every scheduled cycle ends in exactly one of four words. "Nothing to do" is a
result and has to be said out loud, because an agent that stays quiet when it
found no work is indistinguishable from an agent that never woke up.

The second half of the contract is lane coverage. A cycle declares its lanes up
front; the report must carry one line per lane. Without this, a lane that had
no work quietly disappears from the report, and three weeks later nobody can
say when it stopped running.
"""

from __future__ import annotations

from dataclasses import dataclass, field

COMPLETE = "COMPLETE"
ZERO_WORK = "ZERO WORK"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"

STATUSES = (COMPLETE, ZERO_WORK, PARTIAL, BLOCKED)


class ReportError(ValueError):
    """Raised when a report violates the reporting contract."""


@dataclass
class CycleReport:
    cycle: str
    agent: str
    lanes: list[str]
    lines: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def record(self, lane: str, line: str) -> None:
        if lane not in self.lanes:
            raise ReportError(f"{lane!r} is not a declared lane of {self.cycle}")
        if not line.strip():
            raise ReportError(f"lane {lane!r} reported an empty line")
        self.lines[lane] = line.strip()

    def missing_lanes(self) -> list[str]:
        return [lane for lane in self.lanes if lane not in self.lines]

    def status(self) -> str:
        """Derive the status word from the lane lines. Never guessed by hand."""
        missing = self.missing_lanes()
        if any(self.lines.get(lane, "").upper().startswith("BLOCKED")
               for lane in self.lanes):
            return BLOCKED
        if missing:
            return PARTIAL
        if all(self.lines[lane].upper().startswith("ZERO") for lane in self.lanes):
            return ZERO_WORK
        return COMPLETE

    def render(self) -> str:
        if not self.lanes:
            raise ReportError("a cycle with no declared lanes cannot be reported")
        status = self.status()
        head = f"{status} - {self.cycle} - {self.agent}"
        body = []
        for lane in self.lanes:
            body.append(f"  {lane}: {self.lines.get(lane, 'NOT RUN (no line reported)')}")
        if self.notes:
            body.append(f"  notes: {self.notes}")
        return "\n".join([head, *body])
