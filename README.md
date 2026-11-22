# baton

A plan-driven session supervisor for [OpenCode](https://opencode.ai).

You keep a plan on disk. OpenCode works one step at a time in its own session.
When a session goes idle, `baton` reads the evidence — transcript, git status,
diff, commits — asks a model one narrow question ("is this step actually done?"),
and then writes the next self-contained instruction. It can stop there and let
you press send, or drive the whole plan itself.

```
PLAN ──▶ instruction ──▶ OpenCode session ──▶ evidence ──▶ verdict
  ▲                                                          │
  └──────────────── next instruction ◀──────────────────────┘
```

## Why this shape

The outer loop is deterministic code; the model only judges and writes the next
brief. That is deliberate. Most agent failures come from unbounded autonomy and
context drift, not from weak models. Here:

- **One step at a time.** No agent decides to redesign your architecture mid-task.
- **Evidence over claims.** A step is done when the diff, tests, and commit say so,
  not when the assistant says "done".
- **A fresh session per step.** Each step starts with a self-contained brief, so
  context never has to survive dozens of tool calls.
- **Cheap judgment.** One structured model call per checkpoint, through your
  existing OpenCode provider.
- **Read-only by default.** `baton next` prints the instruction. `--send` is the
  only flag that creates sessions or spends tokens on work.

## Requirements

- OpenCode V2 running locally (`opencode service start`)
- Python 3.11+

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The `baton` entry point is installed into `.venv/bin/baton`.

## Quickstart

1. Write the plan as Markdown in your repo:

   ```markdown
   # Foundation
   - [ ] Create the database schema
     - migrations run cleanly
   - [ ] Add the items API
     - GET /items returns 200
     - covered by a test
   ```

2. Import it:

   ```sh
   .venv/bin/baton init --goal "Ship the items service" --from PLAN.md
   ```

   This writes `.baton/plan.json` — the canonical, machine-editable plan. Keep
   editing the JSON from here; the Markdown was only the seed.

3. Check the wiring:

   ```sh
   .venv/bin/baton doctor
   .venv/bin/baton status
   ```

4. Get the first instruction (no session created, no writes):

   ```sh
   .venv/bin/baton next
   ```

5. Send it and work:

   ```sh
   .venv/bin/baton next --send
   ```

6. When you finish working in OpenCode, review and continue:

   ```sh
   .venv/bin/baton next
   ```

   or wait for the session to finish first:

   ```sh
   .venv/bin/baton watch
   ```

7. Drive the whole plan:

   ```sh
   .venv/bin/baton run --send --max-steps 10
   ```

## Commands

| Command | What it does |
|---|---|
| `baton init` | Create or import `.baton/plan.json` |
| `baton status` | Plan progress, attempts, last session |
| `baton next` | Review the last session and print the next instruction (dry run) |
| `baton next --send` | Same, then create a session and send it |
| `baton watch` | Wait for the current session to go idle, then review |
| `baton run --send` | Loop: instruct, wait, review, advance |
| `baton doctor` | Check the OpenCode service and authentication |

Common flags: `--session ses_...` to review a specific session, `--model
provider/model` to choose the judge model, `--agent` to choose the OpenCode agent.

## The plan format

```json
{
  "version": 1,
  "goal": "Ship the items service",
  "repo": "/absolute/path",
  "steps": [
    {
      "id": "01",
      "title": "Foundation: Create the database schema",
      "goal": "A migration creates the items table",
      "acceptance": ["migrations run cleanly", "indexes exist"],
      "status": "pending",
      "evidence": [],
      "notes": "",
      "session_ids": [],
      "attempts": 0
    }
  ]
}
```

Statuses: `pending`, `in_progress`, `done`, `blocked`, `skipped`.

Acceptance criteria are the contract. If they are vague, the judge will be
vague. Write them so a stranger could check them.

## How it talks to OpenCode

`baton` uses the local OpenCode HTTP API with the service credentials from
`~/.local/state/opencode/service.json`. Nothing is scraped from the TUI.

- `POST /api/session` — create a session at the repo location
- `POST /api/session/{id}/prompt` — deliver the instruction
- `GET /api/session/active` — poll until the session is idle
- `GET /api/session/{id}/message` — read the transcript
- `GET /api/session/{id}` — outcome, cost, tokens
- `POST /api/experimental/generate/text` — the judge call, billed to your provider

Override discovery with `OPENCODE_URL` and `OPENCODE_PASSWORD`. Pin the judge
model in `.baton/config.json`:

```json
{ "judge_model": "gpustack/deepseek-v4.1-flash-1017" }
```

## Design notes

- **Polling, not events, for completion.** The event stream is live-only and
  reconnects can miss the idle event. A 2–3 second poll of
  `/api/session/active` is cheap and cannot miss the transition.
- **The judge grades outcomes.** It is told to treat "it works" as weak evidence
  and a test run, diff, or commit as strong evidence.
- **Invalid judge output is retried once** with the validation error fed back,
  then surfaced to the operator with the raw text.
- **Every decision is logged** to `.baton/runs/` with the verdict, session, cost,
  and tokens.
- **Plan writes are atomic** (temp file + rename).
- **Dry run never mutates the plan.** Reviewing is free of side effects; only
  `--send` commits status changes.

## Not built yet

- Parallel steps or worktrees
- A UI
- Notifications on block/failure
- Test-command execution as evidence (OpenCode runs the tests, not `baton`)
- Cost budgets across a run
