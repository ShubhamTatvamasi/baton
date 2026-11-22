"""The plan: the canonical, machine-readable definition of the work."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"
    skipped = "skipped"


class Step(BaseModel):
    id: str
    title: str
    goal: str = ""
    acceptance: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.pending
    evidence: list[str] = Field(default_factory=list)
    notes: str = ""
    session_ids: list[str] = Field(default_factory=list)
    attempts: int = 0


class Plan(BaseModel):
    version: int = 1
    goal: str
    repo: str
    created: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    steps: list[Step] = Field(default_factory=list)

    # -- lookup -----------------------------------------------------------

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(f"unknown step {step_id}")

    def next_step(self) -> Step | None:
        for step in self.steps:
            if step.status in (StepStatus.pending, StepStatus.in_progress):
                return step
        return None

    @property
    def finished(self) -> bool:
        return all(step.status in (StepStatus.done, StepStatus.skipped) for step in self.steps)

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for step in self.steps:
            result[step.status.value] = result.get(step.status.value, 0) + 1
        return result

    # -- transitions ------------------------------------------------------

    def start(self, step_id: str) -> Step:
        step = self.step(step_id)
        step.status = StepStatus.in_progress
        step.attempts += 1
        return step

    def finish(self, step_id: str, *, evidence: list[str] | None = None, notes: str = "") -> Step:
        step = self.step(step_id)
        step.status = StepStatus.done
        step.evidence = evidence or step.evidence
        if notes:
            step.notes = notes
        return step

    def block(self, step_id: str, reason: str) -> Step:
        step = self.step(step_id)
        step.status = StepStatus.blocked
        step.notes = reason
        return step

    def attach_session(self, step_id: str, session_id: str) -> None:
        step = self.step(step_id)
        if session_id not in step.session_ids:
            step.session_ids.append(session_id)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Plan:
        return cls.model_validate_json(path.read_text())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2) + "\n")
        tmp.replace(path)

    # -- import -----------------------------------------------------------

    @classmethod
    def from_markdown(cls, text: str, *, goal: str, repo: str) -> Plan:
        """Import ``- [ ]`` checkboxes; indented bullets become acceptance criteria."""
        steps: list[Step] = []
        heading = ""
        current: Step | None = None
        box = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                continue

            match = box.match(line)
            if match:
                checked, title = match.groups()
                number = f"{len(steps) + 1:02d}"
                current = Step(
                    id=number,
                    title=f"{heading}: {title}" if heading else title,
                    status=StepStatus.done if checked.lower() == "x" else StepStatus.pending,
                )
                steps.append(current)
                continue

            if current and stripped.startswith(("-", "*")) and line[:1].isspace():
                current.acceptance.append(stripped[1:].strip())

        if not steps:
            raise ValueError("no '- [ ]' steps found in markdown")

        return cls(goal=goal, repo=repo, steps=steps)
