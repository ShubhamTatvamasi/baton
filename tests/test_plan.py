from baton.plan import Plan, StepStatus


def test_markdown_import_reads_checkboxes_and_acceptance():
    text = """
# Foundation
- [x] Create the database schema
  - migrations run cleanly
  - indexes exist
- [ ] Add the API layer
  - GET /items returns 200

# Polish
- [ ] Write docs
"""
    plan = Plan.from_markdown(text, goal="ship it", repo="/tmp/repo")

    assert [step.title for step in plan.steps] == [
        "Foundation: Create the database schema",
        "Foundation: Add the API layer",
        "Polish: Write docs",
    ]
    assert plan.steps[0].status == StepStatus.done
    assert plan.steps[1].acceptance == ["GET /items returns 200"]
    assert plan.steps[1].id == "02"


def test_next_step_skips_done_and_skipped():
    plan = Plan(
        goal="g",
        repo="/tmp/r",
        steps=[
            {"id": "1", "title": "a", "status": "done"},
            {"id": "2", "title": "b", "status": "skipped"},
            {"id": "3", "title": "c"},
        ],
    )
    assert plan.next_step().id == "3"
    assert not plan.finished

    plan.finish("3")
    assert plan.finished


def test_transitions_record_attempts_and_evidence():
    plan = Plan(goal="g", repo="/tmp/r", steps=[{"id": "1", "title": "a"}])

    step = plan.start("1")
    assert step.status == StepStatus.in_progress
    assert step.attempts == 1

    plan.attach_session("1", "ses_1")
    plan.attach_session("1", "ses_1")
    assert step.session_ids == ["ses_1"]

    plan.finish("1", evidence=["tests pass"], notes="ok")
    assert step.status == StepStatus.done
    assert step.evidence == ["tests pass"]


def test_roundtrip(tmp_path):
    plan = Plan(goal="g", repo="/tmp/r", steps=[{"id": "1", "title": "a"}])
    path = tmp_path / "plan.json"
    plan.save(path)

    loaded = Plan.load(path)
    assert loaded.goal == "g"
    assert loaded.steps[0].title == "a"
