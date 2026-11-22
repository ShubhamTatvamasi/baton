"""Command line interface for the supervisor."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .config import ConfigError, Settings, load_settings
from .judge import JudgeError
from .opencode import OpenCode, OpenCodeError
from .plan import Plan, StepStatus
from .runner import (
    Decision,
    Workspace,
    active_session_at,
    advance,
    dispatch,
    resolve_session,
    wait_for_session,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

STATUS_STYLE = {
    StepStatus.pending: "dim",
    StepStatus.in_progress: "yellow",
    StepStatus.done: "green",
    StepStatus.blocked: "red",
    StepStatus.skipped: "dim",
}


_ROOT: dict[str, Path] = {"directory": Path.cwd()}


def _root() -> Path:
    return _ROOT["directory"]


def _workspace(directory: Path) -> Workspace:
    workspace = Workspace(directory)
    if not workspace.exists():
        console.print(f"[red]No plan at {workspace.plan_path}[/red]")
        console.print("Run [bold]baton init --goal '...' --from PLAN.md[/bold] first.")
        raise typer.Exit(1)
    return workspace


def _client(directory: Path) -> tuple[OpenCode, Settings]:
    settings = load_settings(directory)
    return OpenCode(settings), settings


@app.callback()
def main(
    directory: Path = typer.Option(
        Path("."), "--dir", "-C", help="Repository the plan applies to."
    ),
) -> None:
    _ROOT["directory"] = directory.resolve()


@app.command()
def doctor() -> None:
    """Check that the OpenCode service is reachable and authenticated."""
    try:
        settings = load_settings(_root())
        with OpenCode(settings) as client:
            info = client.info()
            active = client.active_sessions()
    except (ConfigError, OpenCodeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"url: {settings.url}\nversion: {info.get('version')}\n"
            f"active sessions: {len(active)}\njudge model: {settings.model or '(server default)'}",
            title="OpenCode service",
            border_style="green",
        )
    )


@app.command()
def init(
    goal: str = typer.Option(..., "--goal", "-g", help="One-sentence project goal."),
    from_file: Path | None = typer.Option(
        None, "--from", "-f", help="Markdown file with '- [ ]' steps to import."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing plan."),
) -> None:
    """Create .baton/plan.json, optionally importing steps from Markdown."""
    workspace = Workspace(_root())
    if workspace.exists() and not force:
        console.print(f"[red]{workspace.plan_path} already exists[/red] (use --force)")
        raise typer.Exit(1)

    if from_file:
        plan = Plan.from_markdown(
            from_file.read_text(), goal=goal, repo=str(workspace.root)
        )
    else:
        plan = Plan(goal=goal, repo=str(workspace.root))

    workspace.save_plan(plan)
    console.print(f"[green]Created[/green] {workspace.plan_path} with {len(plan.steps)} steps.")
    if not plan.steps:
        console.print("Add steps to the JSON, or re-run with [bold]--from PLAN.md[/bold].")


@app.command()
def status() -> None:
    """Show plan progress and the current session."""
    workspace = _workspace(_root())
    plan = workspace.load_plan()
    state = workspace.load_state()

    table = Table(title=plan.goal, show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Step", overflow="fold")
    table.add_column("Status", width=12)
    table.add_column("Tries", width=6, justify="right")
    for step in plan.steps:
        style = STATUS_STYLE[step.status]
        table.add_row(step.id, step.title, f"[{style}]{step.status.value}[/{style}]", str(step.attempts))
    console.print(table)

    counts = ", ".join(f"{name}: {count}" for name, count in sorted(plan.counts().items()))
    console.print(f"[dim]{counts}[/dim]")
    if state.last_session_id:
        console.print(f"last session: [bold]{state.last_session_id}[/bold] (step {state.last_step_id})")


def _show(decision: Decision, sent_to: str | None) -> None:
    if decision.verdict:
        verdict = decision.verdict
        console.print(
            Panel(
                f"[bold]{verdict.verdict}[/bold] (confidence {verdict.confidence:.2f})\n"
                f"{verdict.notes}\n"
                + (f"\nremaining: {', '.join(verdict.remaining)}" if verdict.remaining else ""),
                title=f"Review of step {decision.step.id if decision.step else '?'}",
                border_style="cyan",
            )
        )
    if decision.instruction:
        console.print(
            Panel(
                Markdown(decision.instruction),
                title=f"Next instruction -> step {decision.step.id}" if decision.step else "Next",
                border_style="magenta",
            )
        )
    if sent_to:
        console.print(f"[green]Sent to session[/green] [bold]{sent_to}[/bold]")
    elif decision.instruction and decision.action != "complete":
        console.print("[dim]Dry run. Re-run with --send to deliver this instruction.[/dim]")
    if decision.action == "complete":
        console.print(Panel("Plan complete. Nothing left to instruct.", border_style="green"))
    elif decision.action == "blocked":
        console.print(
            Panel("Blocked. Resolve the blocker, then edit .baton/plan.json to resume.",
                  border_style="red")
        )


@app.command()
def next(
    session: str | None = typer.Option(None, "--session", "-s", help="Session to review."),
    send: bool = typer.Option(False, "--send", help="Deliver the instruction to OpenCode."),
    model: str | None = typer.Option(None, "--model", help="Judge model: provider/model."),
    agent: str | None = typer.Option(None, "--agent", help="Agent for the new session."),
    as_json: bool = typer.Option(False, "--json", help="Print the decision as JSON."),
) -> None:
    """Review the last session and produce the next instruction."""
    workspace = _workspace(_root())
    try:
        client, settings = _client(_root())
        with client:
            decision = advance(workspace, client, settings, session_id=session, model=model, commit=send)

            sent_to = None
            if send and decision.instruction:
                sent_to = dispatch(
                    workspace,
                    client,
                    decision,
                    session_id=session,
                    agent=agent,
                    model=settings.model,
                )
    except (ConfigError, OpenCodeError, JudgeError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if as_json:
        payload = {
            "action": decision.action,
            "step": decision.step.id if decision.step else None,
            "instruction": decision.instruction,
            "session": sent_to,
            "verdict": decision.verdict.model_dump() if decision.verdict else None,
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    _show(decision, sent_to)


@app.command()
def watch(
    session: str | None = typer.Option(None, "--session", "-s", help="Session to wait for."),
    send: bool = typer.Option(False, "--send", help="Deliver the instruction when it finishes."),
    model: str | None = typer.Option(None, "--model", help="Judge model: provider/model."),
    agent: str | None = typer.Option(None, "--agent", help="Agent for the new session."),
    interval: float = typer.Option(2.0, "--interval", help="Polling interval in seconds."),
) -> None:
    """Wait for the current session to finish, then review it."""
    workspace = _workspace(_root())
    try:
        client, settings = _client(_root())
        with client:
            state = workspace.load_state()
            target = resolve_session(client, state, workspace.root, session)
            if not target:
                target = active_session_at(client, workspace.root)
            if target and target in client.active_sessions():
                console.print(f"Waiting for [bold]{target}[/bold] to finish...")
                wait_for_session(client, target, poll_seconds=interval)

            decision = advance(workspace, client, settings, session_id=target, model=model, commit=send)
            sent_to = None
            if send and decision.instruction:
                sent_to = dispatch(
                    workspace, client, decision, session_id=target, agent=agent,
                    model=settings.model,
                )
    except (ConfigError, OpenCodeError, JudgeError, RuntimeError, TimeoutError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    _show(decision, sent_to)


@app.command()
def run(
    max_steps: int = typer.Option(10, "--max-steps", help="Maximum instructions to send."),
    send: bool = typer.Option(False, "--send", help="Actually drive sessions."),
    model: str | None = typer.Option(None, "--model", help="Judge model: provider/model."),
    agent: str | None = typer.Option(None, "--agent", help="Agent for new sessions."),
) -> None:
    """Drive the plan end to end. Dry run unless --send is given."""
    workspace = _workspace(_root())
    if not send:
        console.print("[yellow]Dry run.[/yellow] Add --send to let the supervisor drive sessions.")

    try:
        client, settings = _client(_root())
        with client:
            last_session: str | None = None
            for _ in range(max_steps):
                decision = advance(
                    workspace, client, settings, session_id=last_session, model=model, commit=send
                )
                _show(decision, None)

                if decision.action in ("complete", "blocked") or not decision.instruction:
                    break
                if not send:
                    break

                last_session = dispatch(
                    workspace, client, decision, session_id=last_session, agent=agent,
                    model=settings.model,
                )
                console.print(f"[green]Sent[/green] -> {last_session}; waiting...")
                if last_session:
                    client.wait(last_session)
    except (ConfigError, OpenCodeError, JudgeError, RuntimeError, TimeoutError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
