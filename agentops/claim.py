"""Cycle claiming: exactly one agent runs a scheduled cycle.

The roster is a list of agents, any of which may be alive, rate limited, or
down at any given moment. A scheduled poke wakes all of them at once. Without
a claim they all run the same cycle and the desk emits duplicate work.

The claim is a filesystem lock created with O_CREAT|O_EXCL, which is atomic on
POSIX and on any single filesystem the agents share. First writer wins, every
other agent stands down. A lock carries a deadline so that an agent which dies
mid cycle cannot wedge the desk forever: after the lease expires any other
agent may take over, and the takeover is recorded so the work log shows it.

There is no daemon, no service and no database. A dead machine leaves a stale
file, and a stale file is recoverable by inspection.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_LEASE_SECONDS = 1800


class ClaimError(RuntimeError):
    """Raised when a claim operation is refused."""


@dataclass(frozen=True)
class Claim:
    cycle: str
    holder: str
    pid: int
    started: float
    lease_seconds: int
    took_over_from: str | None = None

    @property
    def expires_at(self) -> float:
        return self.started + self.lease_seconds

    def is_expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at


def _lock_path(state_dir: Path, cycle: str) -> Path:
    safe = cycle.replace("/", "_")
    return Path(state_dir) / f"{safe}.claim.json"


def _read(path: Path) -> Claim | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        return Claim(
            cycle=raw["cycle"],
            holder=raw["holder"],
            pid=int(raw["pid"]),
            started=float(raw["started"]),
            lease_seconds=int(raw["lease_seconds"]),
            took_over_from=raw.get("took_over_from"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_exclusive(path: Path, claim: Claim) -> bool:
    """Create the lock file atomically. False means somebody else got there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(claim), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return True


def claim_cycle(
    state_dir: str | os.PathLike[str],
    cycle: str,
    agent: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: float | None = None,
) -> Claim | None:
    """Try to claim `cycle` for `agent`.

    Returns the winning Claim, or None if another live agent holds it. A claim
    whose lease has expired is taken over, and the new claim records who it
    took over from.
    """
    if not agent:
        raise ClaimError("agent name is required; an anonymous claim is not traceable")
    if lease_seconds <= 0:
        raise ClaimError("lease_seconds must be positive")

    stamp = time.time() if now is None else now
    path = _lock_path(Path(state_dir), cycle)
    fresh = Claim(cycle=cycle, holder=agent, pid=os.getpid(), started=stamp,
                  lease_seconds=lease_seconds)

    if _write_exclusive(path, fresh):
        return fresh

    existing = _read(path)
    if existing is None:
        # Corrupt or truncated lock. Treat as abandoned and reclaim in place.
        path.unlink(missing_ok=True)
        return claim_cycle(state_dir, cycle, agent, lease_seconds, now=stamp)

    if existing.holder == agent:
        # Re-entrant: the same agent poked twice keeps its own claim.
        return existing

    if not existing.is_expired(stamp):
        return None

    takeover = Claim(cycle=cycle, holder=agent, pid=os.getpid(), started=stamp,
                     lease_seconds=lease_seconds, took_over_from=existing.holder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(takeover), indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)

    # Re-read: if two agents raced on takeover, only one replace lands last and
    # the loser must stand down rather than assume it won.
    landed = _read(path)
    if landed is None or landed.holder != agent or landed.started != stamp:
        return None
    return landed


def current_claim(state_dir: str | os.PathLike[str], cycle: str) -> Claim | None:
    return _read(_lock_path(Path(state_dir), cycle))


def release_cycle(state_dir: str | os.PathLike[str], cycle: str, agent: str) -> bool:
    """Release a claim. Only the holder may release it."""
    path = _lock_path(Path(state_dir), cycle)
    existing = _read(path)
    if existing is None:
        return False
    if existing.holder != agent:
        raise ClaimError(
            f"{agent} cannot release a cycle held by {existing.holder}"
        )
    path.unlink(missing_ok=True)
    return True
