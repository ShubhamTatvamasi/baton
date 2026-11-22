"""The control loop: decide, instruct, dispatch, wait, repeat."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .brief import render_correction, render_step_brief
from .config import Settings
from .evidence import Evidence, collect
from .judge import Verdict, judge
from .opencode import OpenCode
from .plan import Plan, Step


class State(BaseModel):
    last_session_id: str | None = None
    last_step_id: str | None = None
    updated: str = ""


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.dir = self.root / ".baton"
        self.plan_path = self.dir / "plan.json"
        self.state_path = self.dir / "state.json"
        self.runs = self.dir / "runs"

    def exists(self) -> bool:
        return self.plan_path.exists()

    def load_plan(self) -> Plan:
        return Plan.load(self.plan_path)

    def save_plan(self, plan: Plan) -> None:
        plan.save(self.plan_path)

    def load_state(self) -> State:
        if not self.state_path.exists():
            return State()
        return State.model_validate_json(self.state_path.read_text())

    def save_state(self, state: State) -> None:
        state.updated = datetime.now(UTC).isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n")

    def log_run(self, step: Step, verdict: Verdict, evidence: Evidence) -> Path:
        self.runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.runs / f"{stamp}-{step.id}.json"
        path.write_text(
            json.dumps(
                {
                    "step": step.id,
                    "verdict": verdict.model_dump(),
                    "session": evidence.session_id,
                    "outcome": evidence.session.get("outcome"),
                    "cost": evidence.session.get("cost"),
                    "tokens": evidence.session.get("tokens"),
                },
                indent=2,
            )
            + "\n"
        )
        return path


@dataclass
class Decision:
    action: Literal["brief", "continue", "advance", "blocked", "complete"]
    instruction: str | None
    step: Step | None
    verdict: Verdict | None = None
    evidence: Evidence | None = None


def resolve_session(client: OpenCode, state: State, root: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if state.last_session_id:
        return state.last_session_id
    latest = client.latest_session_at(str(root))
    return latest["id"] if latest else None


def active_session_at(client: OpenCode, root: Path) -> str | None:
    prefix = str(root)
    for session_id in client.active_sessions():
        info = client.get_session(session_id)
        if info.get("location", {}).get("directory") == prefix:
            return session_id
    return None


def advance(
    workspace: Workspace,
    client: OpenCode,
    settings: Settings,
    *,
    session_id: str | None = None,
    model: str | None = None,
    commit: bool = True,
) -> Decision:
    plan = workspace.load_plan()
    step = plan.next_step()
    if step is None:
        return Decision(action="complete", instruction=None, step=None)

    # First contact with this step: no prior work to judge, just brief it.
    if step.attempts == 0:
        plan.start(step.id)
        if commit:
            workspace.save_plan(plan)
        return Decision(action="brief", instruction=render_step_brief(plan, step), step=step)

    target = resolve_session(client, workspace.load_state(), workspace.root, session_id)
    if not target:
        raise RuntimeError(
            "no session to review. Run `baton next --send` to start the step, "
            "or pass --session ses_..."
        )

    evidence = collect(str(workspace.root), client, target)
    verdict = judge(
        client,
        settings,
        plan,
        step,
        evidence.render(),
        model=model,
        model_ref=evidence.session.get("model"),
    )
    workspace.log_run(step, verdict, evidence)

    if verdict.verdict == "done":
        plan.finish(step.id, evidence=verdict.evidence, notes=verdict.notes)
        nxt = plan.next_step()
        if nxt is None:
            if commit:
                workspace.save_plan(plan)
            return Decision("complete", None, step, verdict, evidence)
        plan.start(nxt.id)
        if commit:
            workspace.save_plan(plan)
        return Decision("advance", render_step_brief(plan, nxt), nxt, verdict, evidence)

    if verdict.verdict == "blocked":
        plan.block(step.id, verdict.notes or "blocked by reviewer")
        if commit:
            workspace.save_plan(plan)
        return Decision("blocked", None, step, verdict, evidence)

    instruction = verdict.next_instruction.strip() or render_correction(
        plan, step, done=verdict.evidence, remaining=verdict.remaining
    )
    if commit:
        workspace.save_plan(plan)
    return Decision("continue", instruction, step, verdict, evidence)


def dispatch(
    workspace: Workspace,
    client: OpenCode,
    decision: Decision,
    *,
    session_id: str | None = None,
    agent: str | None = None,
    model: str | dict | None = None,
) -> str | None:
    """Send the instruction. Returns the session it went to."""
    if not decision.instruction or not decision.step:
        return None

    plan = workspace.load_plan()
    state = workspace.load_state()

    resolved = session_id or (decision.evidence.session_id if decision.evidence else None)
    if decision.action == "continue" and resolved:
        target = resolved
    else:
        created = client.create_session(
            str(workspace.root),
            title=f"step {decision.step.id}: {decision.step.title}"[:120],
            agent=agent,
            model=model,
        )
        target = created["id"]

    client.prompt(target, decision.instruction)
    plan.attach_session(decision.step.id, target)
    workspace.save_plan(plan)

    state.last_session_id = target
    state.last_step_id = decision.step.id
    workspace.save_state(state)
    return target


def wait_for_session(
    client: OpenCode,
    session_id: str,
    *,
    poll_seconds: float = 3.0,
    max_idle_checks: int = 2400,
) -> None:
    """Wait until the session is no longer active.

    Polling is deliberate: it is cheap, survives reconnects, and cannot miss an
    idle event. The SSE stream is still available for UIs that need it.
    """
    checks = 0
    while checks < max_idle_checks:
        if session_id not in client.active_sessions():
            return
        checks += 1
        time.sleep(poll_seconds)
    raise TimeoutError(f"session {session_id} still running after {checks * poll_seconds:.0f}s")
