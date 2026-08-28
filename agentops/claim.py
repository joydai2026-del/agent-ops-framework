"""Cycle claiming: exactly one agent runs a scheduled cycle.

The roster is a list of agents, any of which may be alive, rate limited, or
down at any given moment. A scheduled poke wakes all of them at once. Without
a claim they all run the same cycle and the desk emits duplicate work.

The claim is a file. Two properties make it a lock rather than a note:

* It is PUBLISHED ATOMICALLY AND ALREADY COMPLETE. The claim is written to a
  uniquely named temporary file, flushed, and only then linked into place with
  `os.link`, which fails if the name exists. A contender therefore never
  observes a half written claim. The obvious implementation (create the file,
  then write it) has a real window where a contender reads zero bytes, concludes
  the lock is corrupt, and becomes a second winner.
* It carries a LEASE. An agent that dies mid cycle cannot wedge the desk
  forever: once the lease expires another agent may take over, and the takeover
  is recorded so the work log shows a cycle was rescued rather than silently
  rerun.

Takeover is the one genuinely contended write, so it goes through a `flock`
guard and is verified by reading back what actually landed. An agent whose
replace lost the race stands down instead of assuming it won.

What this does NOT provide, stated plainly because a lock that oversells itself
is worse than no lock: it does not fence a slow worker. An agent that overruns
its lease is not interrupted, so its late writes are not rejected. Everything a
cycle writes is therefore idempotent by construction (see `worklog.append` and
`LearningPool.add`), which makes an overrun boring rather than corrupting. If
you need true fencing, the claim token below is the value to thread into every
downstream write.

There is no daemon, no service and no database. A dead machine leaves a stale
file, and a stale file is recoverable by inspection.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_LEASE_SECONDS = 1800

# How long an unreadable lock file is tolerated before it is treated as debris.
# A claim is published complete, so an unparseable one is either genuinely
# corrupt or being written by an ancient, broken client. Waiting this out costs
# one cycle; deleting it immediately costs correctness.
CORRUPT_GRACE_SECONDS = 60


class ClaimError(RuntimeError):
    """Raised when a claim operation is refused."""


@dataclass(frozen=True)
class Claim:
    cycle: str
    holder: str
    pid: int
    started: float
    lease_seconds: int
    token: str
    took_over_from: str | None = None

    @property
    def expires_at(self) -> float:
        return self.started + self.lease_seconds

    def is_expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at


def _lock_path(state_dir: Path, cycle: str) -> Path:
    safe = cycle.replace("/", "_")
    return Path(state_dir) / f"{safe}.claim.json"


def _guard_path(path: Path) -> Path:
    return path.with_suffix(".guard")


@contextlib.contextmanager
def _guard(path: Path):
    """Serialize the read-modify-write half of takeover and release.

    A separate file, so the guard is never the thing being replaced.
    """
    guard = _guard_path(path)
    guard.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(guard), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


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
            token=str(raw["token"]),
            took_over_from=raw.get("took_over_from"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _spool(path: Path, claim: Claim) -> Path:
    """Write the claim to a uniquely named file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(asdict(claim), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return tmp


def _publish_new(path: Path, claim: Claim) -> bool:
    """Publish a claim atomically and complete. False means somebody beat us."""
    tmp = _spool(path, claim)
    try:
        os.link(tmp, path)   # atomic, and fails if the name already exists
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _replace(path: Path, claim: Claim) -> None:
    tmp = _spool(path, claim)
    os.replace(tmp, path)


def _new_claim(cycle: str, agent: str, stamp: float, lease: int,
               took_over_from: str | None = None) -> Claim:
    return Claim(cycle=cycle, holder=agent, pid=os.getpid(), started=stamp,
                 lease_seconds=lease, token=uuid.uuid4().hex,
                 took_over_from=took_over_from)


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

    if _publish_new(path, _new_claim(cycle, agent, stamp, lease_seconds)):
        return _read(path)

    # Somebody holds it. Everything from here is contended, so take the guard.
    with _guard(path):
        existing = _read(path)

        if existing is None:
            # Unreadable. Since claims are published complete, this is debris
            # rather than a race, but only clear it once it has sat unreadable
            # long enough that no live writer can be mid publish.
            try:
                age = stamp - path.stat().st_mtime
            except FileNotFoundError:
                age = CORRUPT_GRACE_SECONDS + 1
            if age < CORRUPT_GRACE_SECONDS:
                return None
            fresh = _new_claim(cycle, agent, stamp, lease_seconds)
            _replace(path, fresh)
            landed = _read(path)
            return landed if landed and landed.token == fresh.token else None

        if existing.holder == agent and not existing.is_expired(stamp):
            # Re-entrant: the same agent poked twice keeps its own live claim.
            return existing

        if not existing.is_expired(stamp):
            return None

        # The lease is up, including the case where this same agent's own
        # previous claim expired: it does not get a free pass on its own name.
        fresh = _new_claim(cycle, agent, stamp, lease_seconds,
                           took_over_from=existing.holder)
        _replace(path, fresh)
        landed = _read(path)
        if landed is None or landed.token != fresh.token:
            return None
        return landed


def current_claim(state_dir: str | os.PathLike[str], cycle: str) -> Claim | None:
    return _read(_lock_path(Path(state_dir), cycle))


def release_cycle(state_dir: str | os.PathLike[str], cycle: str, agent: str,
                  token: str | None = None) -> bool:
    """Release a claim.

    Only the holder may release it, and when a token is supplied it must be the
    exact claim's token. That is what stops a slow agent, already replaced by a
    takeover, from deleting its replacement's live claim on the way out.
    """
    path = _lock_path(Path(state_dir), cycle)
    with _guard(path):
        existing = _read(path)
        if existing is None:
            return False
        if existing.holder != agent:
            raise ClaimError(
                f"{agent} cannot release a cycle held by {existing.holder}"
            )
        if token is not None and existing.token != token:
            raise ClaimError(
                f"{agent} holds a stale claim on {cycle}; it was taken over. "
                "Refusing to release the current holder's claim."
            )
        path.unlink(missing_ok=True)
        return True
