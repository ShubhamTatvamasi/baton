"""Deterministic prompt templates.

The supervisor owns structure; the model only fills the judgment-shaped gaps.
Keeping these templates as code makes them diffable, testable, and versionable.
"""

from __future__ import annotations

from .plan import Plan, Step

CONSTRAINTS = """\
Rules for this session:
- Work only on the current step. Do not start the next step.
- Inspect the existing code before editing. Follow the repo's conventions.
- Make the smallest change that satisfies the acceptance criteria.
- Run the relevant tests or checks yourself before declaring the step done.
- Commit your work with a descriptive message when the step is complete.
- End with a short report: what changed, what you verified, and anything still open.
"""


def _acceptance(step: Step) -> str:
    if not step.acceptance:
        return "- (no explicit criteria; use the step goal and the repo's tests)"
    return "\n".join(f"- {item}" for item in step.acceptance)


def render_step_brief(plan: Plan, step: Step, *, prior_context: str = "") -> str:
    """The instruction that starts work on a plan step."""
    parts = [
        f"# Project goal\n{plan.goal}",
        f"# Current step ({step.id}/{len(plan.steps)}): {step.title}",
        f"## Objective\n{step.goal or step.title}",
        f"## Acceptance criteria\n{_acceptance(step)}",
    ]
    if prior_context:
        parts.append(f"## Context from the previous session\n{prior_context}")
    parts.append(CONSTRAINTS)
    return "\n\n".join(parts)


def render_correction(plan: Plan, step: Step, *, done: list[str], remaining: list[str]) -> str:
    """A continuation instruction when a step is only partially complete."""
    done_text = "\n".join(f"- {item}" for item in done) or "- (nothing confirmed yet)"
    remaining_text = "\n".join(f"- {item}" for item in remaining) or "- (finish and verify the objective)"
    return "\n\n".join(
        [
            f"# Continuing step {step.id}: {step.title}",
            f"## Goal\n{step.goal or step.title}",
            f"## Already done\n{done_text}",
            f"## Still required\n{remaining_text}",
            f"## Acceptance criteria\n{_acceptance(step)}",
            CONSTRAINTS,
        ]
    )


def render_review_request(plan: Plan, step: Step, evidence: str, attempt: int) -> str:
    """Ask the judge for a structured verdict. Output must be JSON only."""
    return f"""\
You are the reviewer for an autonomous coding workflow. Decide whether the
current plan step is complete, based only on the evidence below.

# Plan goal
{plan.goal}

# Current step {step.id}: {step.title}
{step.goal or step.title}

## Acceptance criteria
{_acceptance(step)}

# Attempt
{attempt}

# Evidence
{evidence}

# Decision rules
- "done": every acceptance criterion is met and the evidence shows verification.
  Committed work is strong evidence; a claim in the transcript alone is weak.
- "partial": real progress, but something required is missing or unverified.
- "blocked": cannot proceed without a human decision or missing access.
- "off_track": the work does not address the step, or changed unrelated things.

Grade the outcome, not the exact sequence of actions. Be skeptical of
"it works" without a test run, diff, or commit. Do not invent evidence.

# Output
Return ONLY a JSON object, no markdown fence:
{{
  "verdict": "done" | "partial" | "blocked" | "off_track",
  "confidence": 0.0-1.0,
  "evidence": ["specific fact from the evidence"],
  "remaining": ["specific thing still missing"],
  "next_instruction": "a self-contained instruction for the next OpenCode session, or empty when done",
  "notes": "one or two sentences for the human operator"
}}
"""
