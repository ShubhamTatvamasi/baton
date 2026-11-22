"""Gather the facts a reviewer needs: session state, transcript, repo state."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .opencode import OpenCode

MAX_TRANSCRIPT = 8_000


def _run(repo: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def transcript(messages: list[dict], limit: int = MAX_TRANSCRIPT) -> str:
    """Render a compact chronological transcript: user text, assistant text, tools."""
    lines: list[str] = []
    for message in reversed(messages):
        kind = message.get("type")
        if kind == "user":
            lines.append(f"USER: {message.get('text', '').strip()}")
            continue
        if kind != "assistant":
            continue

        text_parts = [
            part.get("text", "")
            for part in message.get("content", [])
            if part.get("type") == "text"
        ]
        tools = [
            part.get("name")
            for part in message.get("content", [])
            if part.get("type") == "tool" and part.get("name")
        ]
        if text_parts:
            lines.append("ASSISTANT: " + "\n".join(text_parts).strip())
        if tools:
            lines.append("TOOLS: " + ", ".join(tools))

    joined = "\n\n".join(line for line in lines if line.strip())
    if len(joined) <= limit:
        return joined
    return "...[truncated]...\n" + joined[-limit:]


@dataclass
class Evidence:
    session_id: str
    session: dict
    git_status: str
    git_log: str
    diff_stat: str
    changed_files: list[str] = field(default_factory=list)
    transcript: str = ""

    def render(self) -> str:
        tokens = self.session.get("tokens", {})
        sections = [
            "## Session",
            f"id: {self.session_id}",
            f"outcome: {self.session.get('outcome', 'unknown')}",
            f"cost_usd: {self.session.get('cost', 0)}",
            f"tokens_in: {tokens.get('input', 0)} tokens_out: {tokens.get('output', 0)}",
            "",
            "## Working tree",
            self.git_status or "(clean)",
            "",
            "## Diff stat",
            self.diff_stat or "(no changes)",
            "",
            "## Recent commits",
            self.git_log or "(no commits)",
            "",
            "## Transcript (tail)",
            self.transcript or "(empty)",
        ]
        return "\n".join(sections)


def collect(repo: str, client: OpenCode, session_id: str, message_limit: int = 30) -> Evidence:
    root = str(Path(repo).resolve())
    status = _run(root, "status", "--porcelain")
    changed = [
        line[3:].strip()
        for line in status.splitlines()
        if line.strip() and not line.startswith("??")
    ]
    return Evidence(
        session_id=session_id,
        session=client.get_session(session_id),
        git_status=status,
        git_log=_run(root, "log", "--oneline", "-10"),
        diff_stat=_run(root, "diff", "--stat"),
        changed_files=changed,
        transcript=transcript(client.messages(session_id, limit=message_limit)),
    )
