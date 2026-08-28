"""A file backed stand in for whatever messaging system the desk actually uses.

The reference implementation reads JSON from disk so the demo runs anywhere with
no credentials and no network. The production desk this pattern came from talks
to a real mail API through the gated wrapper in `bin/`. Everything above this
module (claiming, runbooks, reporting, the work log, the learning pool) is
unaware of which one it is talking to, which is the point: the transport is an
adapter, the operating model is not.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from .learning import INBOUND, OUTBOUND, Message, in_order


class SendBlocked(PermissionError):
    """Raised when something tries to send from inside the desk."""


def _load_dir(directory: Path) -> list[Message]:
    messages: list[Message] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in (raw if isinstance(raw, list) else [raw]):
            messages.append(Message.from_dict(item))
    return messages


class Mailbox:
    def __init__(self, root: str | os.PathLike[str], allow_send: bool = False):
        self.root = Path(root)
        self.allow_send = allow_send

    def inbox(self) -> list[Message]:
        return _load_dir(self.root / "inbox")

    def outbox(self) -> list[Message]:
        return _load_dir(self.root / "outbox")

    def threads(self) -> dict[str, list[Message]]:
        """Full threads, inbound and outbound merged.

        Merging is not optional. A thread assembled from inbound mail alone
        hides the operator's own replies, and every duplicate draft this desk
        has ever produced came from exactly that blindness.
        """
        grouped: dict[str, list[Message]] = defaultdict(list)
        for message in [*self.inbox(), *self.outbox()]:
            grouped[message.thread].append(message)
        return {thread: in_order(messages) for thread, messages in grouped.items()}

    def existing_drafts(self) -> dict[str, str]:
        """Thread id to draft body.

        LIMITATION, stated because it changes what a real adapter must do: this
        demo store keys drafts by thread and overwrites. If a second inbound
        message arrives and the desk redrafts before the human sends the first
        draft, her sent message is diffed against the wrong draft and the lesson
        is false. A real adapter must keep drafts individually addressable
        (draft id, the inbound message answered, created time) and match a sent
        message to its own unconsumed draft. See docs/porting.md.
        """
        drafts: dict[str, str] = {}
        for path in sorted((self.root / "drafts").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in (raw if isinstance(raw, list) else [raw]):
                drafts[item["thread"]] = item["body"]
        return drafts

    def save_draft(self, thread: str, subject: str, body: str,
                   in_reply_to: str | None = None) -> Path:
        target = self.root / "drafts" / f"{thread}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"thread": thread, "subject": subject, "body": body}
        if in_reply_to:
            # Provenance: which inbound message this draft answers. The demo
            # store cannot yet use it to disambiguate, but recording it is what
            # makes a later fix possible without losing history.
            record["in_reply_to"] = in_reply_to
        target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return target

    def send(self, *_args, **_kwargs):
        """Present only so the refusal is explicit and testable.

        This is the software half of a guarantee whose real teeth are in the
        credential wrapper: the desk's identity is not permitted to send, so
        even a confused or compromised agent has nothing to send with.
        """
        raise SendBlocked(
            "this desk drafts only; sending is the operator's action. "
            "The credential wrapper enforces the same rule below the agent."
        )


__all__ = ["Mailbox", "SendBlocked", "Message", "INBOUND", "OUTBOUND"]
