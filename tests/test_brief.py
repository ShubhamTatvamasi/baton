from baton.brief import render_step_brief
from baton.evidence import transcript
from baton.plan import Plan


def test_step_brief_contains_goal_criteria_and_rules():
    plan = Plan(
        goal="Build the API",
        repo="/tmp/r",
        steps=[
            {
                "id": "01",
                "title": "Add health check",
                "goal": "GET /health",
                "acceptance": ["returns 200", "covered by a test"],
            }
        ],
    )
    step = plan.steps[0]

    text = render_step_brief(plan, step)
    assert "Build the API" in text
    assert "Add health check" in text
    assert "returns 200" in text
    assert "Work only on the current step" in text


def test_transcript_is_chronological_and_tool_aware():
    messages = [
        {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "done"},
                {"type": "tool", "name": "bash"},
            ],
        },
        {"type": "user", "text": "do it"},
    ]
    rendered = transcript(messages)
    assert rendered.index("USER") < rendered.index("ASSISTANT")
    assert "TOOLS: bash" in rendered


def test_transcript_truncates_from_the_front():
    messages = [{"type": "user", "text": "x" * 500}]
    rendered = transcript(messages, limit=100)
    assert rendered.startswith("...[truncated]...")
    assert len(rendered) < 200
