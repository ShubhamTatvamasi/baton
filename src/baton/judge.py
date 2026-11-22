"""The judge: one structured model call per checkpoint."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from .brief import render_review_request
from .config import Settings
from .opencode import OpenCode
from .plan import Plan, Step

MAX_EVIDENCE_CHARS = 16_000


class Verdict(BaseModel):
    verdict: Literal["done", "partial", "blocked", "off_track"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    next_instruction: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def instruction_required(self) -> Verdict:
        if self.verdict != "done" and not self.next_instruction.strip():
            raise ValueError("next_instruction is required unless verdict is done")
        return self


class JudgeError(RuntimeError):
    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def parse_verdict(raw: str) -> Verdict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in response")
        text = text[start : end + 1]
    return Verdict.model_validate(json.loads(text))


def judge(
    client: OpenCode,
    settings: Settings,
    plan: Plan,
    step: Step,
    evidence: str,
    *,
    model: str | dict | None = None,
    model_ref: dict | None = None,
) -> Verdict:
    # Prefer a deliberate choice, then the model the reviewed session used.
    resolved = model or settings.model or model_ref
    clipped = evidence[-MAX_EVIDENCE_CHARS:]
    prompt = render_review_request(plan, step, clipped, step.attempts)

    raw = client.generate_text(prompt, resolved)
    try:
        return parse_verdict(raw)
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        retry_prompt = (
            f"{prompt}\n\nYour previous reply could not be parsed: {exc}.\n"
            "Return ONLY the JSON object described above."
        )
        raw = client.generate_text(retry_prompt, resolved)
        try:
            return parse_verdict(raw)
        except (ValueError, ValidationError, json.JSONDecodeError) as second:
            raise JudgeError(f"judge returned invalid output twice: {second}", raw) from second
