"""agent-ops-framework: the moving parts of an agent run operations desk.

Model agnostic and agent agnostic by construction. Nothing here imports a
vendor SDK; the pieces are a lock file, four status words, a CSV, a markdown
runbook and a JSONL pool. Any model that can read a file and run a command can
operate the desk, and replacing the model is a config change.
"""

__version__ = "0.1.0"

from . import claim, learning, mailbox, report, runbook, worklog  # noqa: F401

__all__ = ["claim", "learning", "mailbox", "report", "runbook", "worklog"]
