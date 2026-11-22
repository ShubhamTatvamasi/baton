import pytest
from pydantic import ValidationError

from baton.judge import parse_verdict


def test_parses_fenced_json():
    raw = """```json
{"verdict": "partial", "confidence": 0.7, "evidence": ["added route"],
 "remaining": ["no test"], "next_instruction": "add a test", "notes": "close"}
```"""
    verdict = parse_verdict(raw)
    assert verdict.verdict == "partial"
    assert verdict.next_instruction == "add a test"


def test_parses_json_with_leading_prose():
    raw = 'Here you go:\n{"verdict": "done", "confidence": 1, "notes": "ok"}'
    verdict = parse_verdict(raw)
    assert verdict.verdict == "done"


def test_non_done_requires_instruction():
    with pytest.raises(ValidationError):
        parse_verdict('{"verdict": "partial", "confidence": 0.5}')


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_verdict("looks good to me")
