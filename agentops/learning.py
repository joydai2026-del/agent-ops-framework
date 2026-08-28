"""The learn first loop.

The desk drafts, the human sends. That split is the safety property, and it is
also the training signal: whatever the human actually sent is the ground truth
for how this desk answers. So every cycle reads the human's own outbound mail
BEFORE it drafts anything new, and writes what it learned into a shared pool
the next agent reads.

Three cases, all of them worth learning from:

1. A draft existed and the human edited it before sending. The DIFF is the
   correction, and it is the highest value signal in the system.
2. No draft existed; the human just replied. The whole reply is the model.
3. The human originated a message the desk never saw. Still recorded, because
   the goal is a durable model of how this desk is run, not only draft repair.

Two rules fall out of reading sent mail, and both are enforced here:

* SENT REPLY GATE: if the newest message on a thread is outbound, the thread is
  answered. Drafting on it produces a duplicate that also tends to contradict
  the position the human already took. Check the whole thread including sent
  mail, never the inbound message alone.
* LEARN BEFORE DRAFT: the learning pass runs first in the cycle, so the drafts
  written this run already carry this run's lessons.

The pool is a JSONL file in the shared project folder, not an agent's private
memory. A lesson that lives inside one agent is a lesson the next agent has to
be taught again.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path

INBOUND = "inbound"
OUTBOUND = "outbound"

# Lesson kinds, in descending order of signal.
EDIT_OF_DRAFT = "edit_of_draft"
DIRECT_REPLY = "direct_reply"
ORIGINATED = "originated"

CLOSED_NO_DRAFT = "closed: newest message is the operator's own reply"
COURTESY_CLOSE = "closed: newest inbound message is a courtesy close"
DRAFT_WARRANTED = "open: newest message is inbound and awaiting an answer"

_COURTESY = re.compile(
    r"^\W*(thanks|thank you|thx|got it|confirmed|sounds good|perfect|great,? thanks)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Message:
    id: str
    thread: str
    direction: str
    sent_at: str  # ISO 8601, sorts lexicographically
    subject: str
    body: str

    @staticmethod
    def from_dict(raw: dict) -> "Message":
        missing = {"id", "thread", "direction", "sent_at", "subject", "body"} - set(raw)
        if missing:
            raise ValueError(f"message is missing fields: {sorted(missing)}")
        if raw["direction"] not in (INBOUND, OUTBOUND):
            raise ValueError(f"unknown direction {raw['direction']!r}")
        return Message(**{k: raw[k] for k in
                          ("id", "thread", "direction", "sent_at", "subject", "body")})


@dataclass
class Lesson:
    kind: str
    thread: str
    source_message: str
    subject: str
    summary: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        payload = f"{self.kind}|{self.thread}|{self.source_message}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]


def newest(messages: list[Message]) -> Message | None:
    if not messages:
        return None
    return sorted(messages, key=lambda m: (m.sent_at, m.id))[-1]


def thread_state(messages: list[Message]) -> tuple[bool, str]:
    """Should this thread be drafted on? Returns (should_draft, reason).

    The whole thread is required, including outbound mail. A run that looks
    only at the inbound message is blind to the operator's own reply.
    """
    latest = newest(messages)
    if latest is None:
        return False, "closed: empty thread"
    if latest.direction == OUTBOUND:
        return False, CLOSED_NO_DRAFT
    if _COURTESY.match(latest.body.strip()):
        return False, COURTESY_CLOSE
    return True, DRAFT_WARRANTED


def diff_draft_against_sent(draft_body: str, sent_body: str) -> tuple[list[str], list[str]]:
    """Sentence level diff. Returns (added_by_operator, removed_from_draft)."""
    before, after = _sentences(draft_body), _sentences(sent_body)
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(before[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(after[j1:j2])
    return added, removed


def _signals(added: list[str], removed: list[str]) -> list[str]:
    """Named heuristics over the edit. Deliberately few and deliberately honest.

    These are hints for the human reading the pool, never silent rewrites of
    policy. Anything the heuristics miss still survives as the raw added and
    removed sentences, which is why those are stored verbatim.
    """
    found: list[str] = []
    joined_add = " ".join(added).lower()
    joined_del = " ".join(removed).lower()

    if re.search(r"\b(reach out to|redirect|forward(?:ing)? (?:you|this) to|speak with|contact)\b",
                 joined_add):
        found.append("routes the request to someone else")
    if re.search(r"\b(i can|we can|i will|we will|happy to|i'll)\b", joined_del) and \
       not re.search(r"\b(i can|we can|i will|we will|happy to|i'll)\b", joined_add):
        found.append("removed a commitment the draft had made")
    if re.search(r"\b(cannot|can't|unable|not able|we do not|we don't)\b", joined_add):
        found.append("declines something the draft accepted")
    if len(" ".join(added)) + 40 < len(" ".join(removed)):
        found.append("materially shorter than the draft")
    if re.search(r"\b(by|before|on)\s+\w+day\b|\b\d{1,2}(am|pm|:\d{2})", joined_add):
        found.append("adds a concrete date, time or deadline")
    return found


def learn_from_outbox(
    outbox: list[Message],
    drafts: dict[str, str],
    since: str | None = None,
    inbound_threads: set[str] | None = None,
) -> list[Lesson]:
    """Build lessons from every outbound message since the last run.

    `drafts` maps thread id to the body this desk had drafted, if any.
    `since` is the previous run's high water mark (ISO timestamp, exclusive).
    `inbound_threads` are the threads the desk has ever seen inbound mail on;
    an outbound message on a thread outside that set was originated by the
    operator and the desk never saw it coming.
    """
    known_threads = inbound_threads or set()
    lessons: list[Lesson] = []
    for message in sorted(outbox, key=lambda m: (m.sent_at, m.id)):
        if message.direction != OUTBOUND:
            continue
        if since and message.sent_at <= since:
            continue

        draft = drafts.get(message.thread)
        if draft:
            added, removed = diff_draft_against_sent(draft, message.body)
            if not added and not removed:
                lessons.append(Lesson(
                    kind=EDIT_OF_DRAFT, thread=message.thread,
                    source_message=message.id, subject=message.subject,
                    summary="draft was sent unchanged; the pattern it used is confirmed",
                ))
                continue
            lessons.append(Lesson(
                kind=EDIT_OF_DRAFT, thread=message.thread,
                source_message=message.id, subject=message.subject,
                summary="operator edited the draft before sending; the edits are the correction",
                added=added, removed=removed, signals=_signals(added, removed),
            ))
            continue

        kind = DIRECT_REPLY if message.thread in known_threads else ORIGINATED
        summary = ("operator replied before any draft existed; the whole reply is the model"
                   if kind == DIRECT_REPLY else
                   "operator originated this message; recorded to model how the desk is run")
        lessons.append(Lesson(
            kind=kind, thread=message.thread, source_message=message.id,
            subject=message.subject, summary=summary,
            added=_sentences(message.body),
            signals=_signals(_sentences(message.body), []),
        ))
    return lessons


class LearningPool:
    """Append only JSONL pool, shared across agents and models.

    Deduplicated by lesson fingerprint so a re-run of the same cycle, or a
    second agent that took over a stale claim, cannot double count.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries

    def fingerprints(self) -> set[str]:
        return {entry["fingerprint"] for entry in self.load()}

    def add(self, lessons: list[Lesson]) -> list[Lesson]:
        """Append new lessons. Returns only the ones actually written."""
        seen = self.fingerprints()
        fresh = []
        for lesson in lessons:
            fp = lesson.fingerprint()
            if fp in seen:
                continue
            seen.add(fp)
            fresh.append(lesson)
        if not fresh:
            return []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for lesson in fresh:
                record = {"fingerprint": lesson.fingerprint(), **asdict(lesson)}
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        return fresh

    def high_water_mark(self, outbox: list[Message]) -> str | None:
        """Latest sent timestamp already represented in the pool."""
        known = {entry["source_message"] for entry in self.load()}
        stamps = [m.sent_at for m in outbox if m.id in known]
        return max(stamps) if stamps else None

    def guidance(self, limit: int = 20) -> list[str]:
        """What a drafting step should read before it writes anything."""
        lines: list[str] = []
        for entry in self.load()[-limit:]:
            bullet = f"[{entry['kind']}] {entry['subject']}: {entry['summary']}"
            if entry.get("signals"):
                bullet += " (" + "; ".join(entry["signals"]) + ")"
            lines.append(bullet)
        return lines
