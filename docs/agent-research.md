# Building an AI Agent — Research Notes

_Compiled November 2026. Sources are linked inline; framework details move fast, so re-check versions before committing._

> **Applied here:** this repo implements the research as `baton`, a plan-driven session supervisor for OpenCode. See [`README.md`](../README.md) for the design and usage.

---

## 1. TL;DR — the ten decisions that actually matter

Most agent projects fail on architecture and operations, not on model choice. In rough order of impact:

1. **Build the simplest thing that can work.** A single LLM call with retrieval beats an agent for most "agent" requests. A fixed workflow beats an autonomous agent whenever the steps are known.
2. **Treat the agent as a loop, not a prompt.** `model + tools + context + control loop + state`. Everything else is detail.
3. **Context engineering is the real moat.** Curate the smallest set of high-signal tokens each turn; compact, offload to files, isolate with subagents.
4. **Design the agent–computer interface (ACI) as carefully as a human UI.** Tool names, descriptions, argument shapes, and error messages decide reliability.
5. **Evals and tracing come before features.** If you can't see the trajectory and score the outcome, you're guessing.
6. **Bound the loop.** Max iterations, token budget, wall-clock timeout, and a defined escalation path.
7. **Assume prompt injection is unsolved.** Least privilege, sandboxing, egress allowlists, and human approval for irreversible actions.
8. **Design for crashes and pauses.** Checkpoint after every step; approvals may take days; processes die.
9. **Start read-only, widen autonomy gradually.** Ship a copilot, then a supervised agent, then an autonomous one.
10. **Measure cost, latency, and task success per step**, not just per request. Route cheap models to cheap steps.

The recurring finding across Anthropic, Google, AWS, and LangChain's 2026 survey: **quality and reliability are the top blockers, cost is not.** Complexity is easy to add and hard to remove.

---

## 2. What an agent actually is

An agent is a program where an LLM decides the control flow. Concretely:

```
loop:
  context  = assemble(system, memory, history, tool results, retrieved docs)
  response = model(context, tools)
  if response is final_answer: return
  result   = execute(tool_call)          # sandboxed, permissioned
  history += (tool_call, result)
  if budget_exhausted: escalate_or_summarize
```

Three layers are worth separating, because they have different failure modes and different vendors:

| Layer | What it is | Where bugs live |
|---|---|---|
| **Model** | The reasoning engine | Wrong tool choice, hallucinated args, instruction drift |
| **Harness** | Loop, context manager, tool executor, permissions, compaction | Context bloat, bad truncation, missing retries, unsafe tools |
| **Product** | UI, auth, tenancy, queues, storage, billing | Everything operational |

A surprising amount of "the model is dumb" is actually a harness bug: truncated tool output, a tool description the model can't disambiguate, history that was summarized too aggressively, or an error message that gives the model nothing to recover from.

### Workflows vs agents

Anthropic's distinction is still the most useful framing ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)):

- **Workflow** — LLM calls orchestrated through predefined code paths. Predictable, cheap, testable.
- **Agent** — the LLM dynamically directs its own process and tool use. Flexible, expensive, non-deterministic.

Five workflow patterns cover a lot of ground before you need an agent:

1. **Prompt chaining** — decompose into fixed sequential steps with gates.
2. **Routing** — classify input, dispatch to a specialized prompt/model.
3. **Parallelization** — sectioning (independent subtasks) or voting (multiple attempts).
4. **Orchestrator–workers** — a planner dynamically delegates to workers.
5. **Evaluator–optimizer** — generator + critic loop until a bar is met.

Google's design-pattern guide adds the multi-agent variants worth knowing: sequential, parallel, loop, and coordinator ([Google Cloud](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system)).

**Rule of thumb:** if you can draw the flowchart, build the workflow. Use an agent only where the number and nature of steps genuinely can't be predicted — coding, research, open-ended support, computer use.

---

## 3. The complexity ladder

Climb one rung at a time, and only when the rung below demonstrably fails:

```
1. Single prompt + structured output
2. Prompt + retrieval (RAG)
3. Fixed workflow (chain / route / parallel / evaluator)
4. Single agent with a small, sharp toolset
5. Single agent + memory + subagents (context isolation)
6. Multi-agent system (coordinator / specialists)
```

Every rung adds latency, cost, and a new failure surface. Anthropic's guidance — "start simple, scale intelligently" — is echoed by every serious practitioner source. Multi-agent is justified when:

- subtasks need genuinely different tools or prompts,
- parallel exploration pays for itself (research, codebase search),
- or context isolation is the point (subagents return 1–2k-token summaries instead of 50k-token transcripts).

It is **not** justified by "it sounds more advanced." There is no independent evidence that one multi-agent topology beats another on current models; coordination overhead often eats the gains.

---

## 4. Context engineering (the highest-leverage skill)

The core insight: context is a **finite attention budget**. Recall degrades as context grows ("context rot"), across all models. More tokens ≠ more capability. See [Anthropic: Effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents).

### Principles

- **Smallest set of high-signal tokens.** Every turn, ask what the model actually needs to take the next correct action.
- **Just-in-time retrieval.** Keep lightweight identifiers (paths, IDs, URLs) and let the agent pull detail via tools, instead of pre-loading everything.
- **Structure the system prompt like a spec**, not a personality essay: role, task, constraints, tool guidance, output contract, escalation rules.
- **Few-shot examples beat adjectives.** Show one good tool call and one good refusal.

### The four levers for long-horizon work

| Lever | What it does | When to use |
|---|---|---|
| **Compaction** | Summarize history near the window limit, keep recent turns + key artifacts | Long back-and-forth conversations |
| **Tool-result clearing** | Drop raw outputs once consumed, keep references | Tool-heavy loops (search, DB, files) |
| **Structured note-taking** | Agent writes `NOTES.md` / progress file / to-do list outside context | Multi-session, milestone-driven work |
| **Subagents** | Fresh context per subtask, return a distilled summary | Research, broad search, parallel exploration |

Anthropic's long-running-agent work adds a fifth: **an initializer run** that scaffolds the environment (feature list, progress file, `init.sh`, initial commit), then every later session reads state, picks one item, works incrementally, tests end-to-end, and leaves a clean commit + progress note ([Effective harnesses](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)). That pattern generalizes well beyond coding.

### Memory

Three tiers, don't conflate them:

1. **Working memory** — the current context window. Managed by compaction.
2. **Session/episodic memory** — conversation history, checkpoints, transcripts. Usually your DB.
3. **Long-term semantic memory** — facts/preferences across sessions. Vector store, knowledge graph, or plain files; Mem0, Zep, Letta, and LangMem are the usual candidates.

Start with files + a database. Add a memory product when you have evidence that cross-session recall is the bottleneck. Memory systems add a second retrieval failure mode and a privacy surface; most early agents don't need one.

### Anti-patterns

- Stuffing the whole repo/doc set into the prompt "just in case."
- Summarizing with an untuned prompt and losing the one detail that mattered.
- Letting subagents return raw tool output.
- Memory that grows without dedupe, contradiction handling, or a delete path.

---

## 5. Tools and the agent–computer interface

Tool design is prompt engineering for actions. Anthropic reports spending **more time optimizing tools than prompts** on their SWE-bench agent.

### What good tools look like

- **One obvious way to do a thing.** Overlapping tools cause dithering.
- **Names and args that are self-explanatory.** `search_orders(customer_email, since)` beats `query(q)`.
- **Absolute paths / opaque IDs over relative or inferred state.** Poka-yoke the interface so mistakes are hard.
- **Descriptions with an example, edge cases, and boundaries** vs neighboring tools.
- **Structured errors the model can act on**: `{"error":"not_found","hint":"try search_customers first"}` — not a stack trace.
- **Idempotency keys and handles** for anything that creates or mutates state.
- **Bounded outputs.** Paginate, cap rows, return summaries + handles for detail.
- **Side effects declared.** Read vs write vs destructive, so the harness can gate them.

### MCP

The Model Context Protocol is now the default integration layer: one client, many servers ([spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Practical guidance:

- Prefer a **small number of well-designed MCP servers** over exposing 100 tools to one agent.
- Treat every MCP server as **untrusted third-party code**: pin versions, review, sandbox, strip secrets from the environment, restrict egress ([MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)).
- Tool description quality is a known weak spot in the ecosystem — audit descriptions before trusting a server with a capable model.
- MCP is an integration convenience, not an architecture. Your internal tools can just be functions.

---

## 6. Frameworks: what to pick in late 2026

You do **not** need a framework to build an agent — the loop is ~100 lines. But frameworks earn their keep for durability, streaming, approvals, and multi-agent plumbing. The main options, as of Oct–Nov 2026:

| Framework | Language | Sweet spot | Watch out |
|---|---|---|---|
| **Pydantic AI** | Python | Typed tools/outputs, model-agnostic, 9 durable-execution engines, OTel | Python only; library, not a platform |
| **LangGraph / LangChain** | Py, TS | Explicit graphs, checkpointing, interrupts, huge integration surface | More concepts; best with LangSmith |
| **OpenAI Agents SDK** | Py, TS | Thin, OpenAI-native, handoffs, guardrails, sessions | Non-OpenAI models via beta adapters; traces go to OpenAI by default |
| **Claude Agent SDK** | Py, TS | Claude Code's harness: files, shell, permissions, compaction, subagents | Claude only; subprocess + local state; proprietary binary |
| **Google ADK** | Py, Java, Go? | Gemini/Vertex, A2A, workflow agents | Ecosystem skew |
| **Microsoft Agent Framework** | Py, .NET | Azure/Foundry, durable workflows, enterprise governance | Azure gravity |
| **Mastra** | TypeScript | TS-first agents/workflows, good DX | Younger ecosystem |
| **Vercel AI SDK** | TypeScript | Streaming UI, front-end integration | Agent runtime is thinner; pair with a workflow engine |
| **AWS Strands** | Py, TS | Bedrock-native, model-agnostic | AWS gravity |
| **CrewAI** | Python | Fast multi-agent prototypes, role metaphors | Abstraction can obscure control; production hardening is on you |

Sources: [Pydantic's 2026 comparison](https://pydantic.dev/articles/best-python-ai-agent-frameworks), [framework comparison table](https://9io.ai/blog/ai-agent-frameworks-compared), [Runtype](https://www.runtype.com/explore/agent-framework-comparison), [Dbggr](https://dbggr.dev/articles/agent-frameworks-compared/).

### Decision guide

- **Python team, type safety, any model → Pydantic AI.** Best default for a greenfield backend agent. Validation errors get fed back to the model; durable execution via Temporal/DBOS/Prefect if needed.
- **Complex control flow, long approvals, audit trails → LangGraph.** The graph is the product; checkpoints and `interrupt()` are first-class.
- **All-in on OpenAI, minimal abstraction → OpenAI Agents SDK.** Small API, easy to read, good handoffs/guardrails.
- **Coding/ops agent on Claude, files + shell → Claude Agent SDK.** You get a finished harness; don't rebuild file editing and permissions.
- **TypeScript product with a chat UI → Mastra or Vercel AI SDK + a workflow engine.**
- **Enterprise Azure/.NET → Microsoft Agent Framework.**

**Honest take:** start with **one model provider and a thin loop**, keep the model behind an interface, and add a framework when you hit a concrete need (durability, interrupts, multi-agent). Framework churn is real; your prompts, tools, evals, and data outlive it.

### When a framework is worth it

Adopt one when any of these is true:
- runs must survive a process restart,
- approvals can take hours/days,
- you need full tracing out of the box,
- or you're coordinating 3+ agents with shared state.

Otherwise, a typed function-calling loop plus a queue is simpler and easier to debug.

---

## 7. State, durability, and human-in-the-loop

"Resumable" means three different things — know which one you bought:

1. **Saved conversation** — history survives; an interrupted step restarts from the last message. (OpenAI sessions, Claude transcripts.)
2. **Checkpointed state** — state saved after each node; resume from the checkpoint. (LangGraph, ADK, MS Agent Framework.)
3. **Durable execution engine** — every step recorded and replayed; survives crashes mid-step. (Temporal, DBOS, Restate, Inngest, Durable Task.)

Failure modes to design around:
- LangGraph restarts an interrupted node from its **first line** — side effects before `interrupt()` repeat.
- ADK warns that tools may run more than once on resume — make writes idempotent.
- Mastra's default in-memory store loses suspended runs on exit.
- Claude Agent SDK holds the process open during `canUseTool`; long approvals need a `defer` hook so the process can exit.

**Human approval** should be a first-class primitive, not a prompt instruction:

- Gate **irreversible / high-blast-radius** actions: money movement, deletes, external comms, prod deploys, permission changes.
- Show the reviewer the **proposed action + arguments + reasoning + confidence**.
- Store the paused run outside the process; approvals may arrive days later.
- Log every decision (approve/edit/reject) with who and why.
- Default to **ask** on ambiguity, not guess.

---

## 8. Evaluation and observability

Observability is table stakes (89% adoption in LangChain's 2026 survey); evals lag (52% offline, 37% online). That gap is where quality problems hide.

### Instrument first

Emit an OpenTelemetry trace per run with spans for: model call, tool call (args, result, duration, error), retrieval, compaction, approval wait. Log tokens, cost, model, prompt version, and a run ID that ties to user feedback. Vendor-neutral OTel keeps you portable; LangSmith, Langfuse, Braintrust, Arize, and Logfire all ingest it.

### Evaluate at three levels

| Level | Question | Example |
|---|---|---|
| **Run (step)** | Did it call the right tool with the right args? | "Used `search_orders`, not `list_all_orders`" |
| **Trace (turn)** | Did it complete the task via a sane path? | Final answer + trajectory + side effects |
| **Thread (conversation)** | Did it resolve the user's problem over time? | Multi-turn intent, memory, recovery |

Per Anthropic's eval guidance ([Demystifying evals](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)):

- **Grade the outcome and the environment state**, not the exact tool sequence. Agents find valid paths you didn't imagine.
- Use **partial credit** for multi-component tasks.
- Run **multiple trials** — nondeterminism is the point.
- **Binary pass/fail** for LLM judges; numeric scales drift.
- Keep a **code-based grader** wherever an objective check exists.

### Offline vs online

- **Offline**: curated dataset, reference outcomes, runs in CI on every prompt/tool/model change. This is your regression suite.
- **Online**: sample production traces (start ~10%), score safety/coherence/groundedness, attach user feedback. Bad traces become new offline cases.

The loop that actually improves agents: **trace → find failure → add eval case → fix → re-run suite.**

### Metrics that matter

Task success rate, partial-credit score, tool-call error rate, steps per success (path convergence), p50/p95 latency, tokens and $ per success, escalation rate, approval rejection rate, and deflection/resolution rate for support agents.

---

## 9. Security and guardrails

As of 2026 there is **no reliable defense against prompt injection**. Plan accordingly: assume the model can be convinced to misuse any capability you give it.

### The control stack

1. **Least privilege per tool and per user.** The agent's credentials should be narrower than the requesting user's, scoped per session. Never let model output decide authorization.
2. **Sandbox execution.** Containers/microVMs for code and shell; no host filesystem, no inherited secrets, non-root, default-deny egress with an allowlist.
3. **Trust boundaries.** Treat web pages, emails, documents, MCP servers, and tool outputs as untrusted data. Never let retrieved content redefine the system prompt or tool permissions.
4. **Action gating.** Read-only by default; destructive tools require approval or are disabled entirely in v1.
5. **Graduated autonomy.** Read-only → suggest → act-with-approval → act-with-audit. Widen per user/team/use case, behind flags.
6. **Runtime guardrails.** Input/output filtering, PII detection, topic deny-lists, output schema validation. Prefer deterministic checks; use a second model for semantic checks.
7. **Kill switch and rate limits** per user, per tool, per agent.
8. **Audit trail.** Every tool call, approval, and config change, attributable and retained.

Useful references: [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html), [NSA/CISA MCP security guidance](https://media.defense.gov/2026/Jun/02/2003943289/-1/-1/0/CSI_MCP_SECURITY.PDF), [NVIDIA's secure agent deployment notes](https://developer.nvidia.com/blog/four-ways-to-deploy-more-secure-ai-agents).

### Also: data handling

- Don't put secrets in prompts or tool schemas.
- Redact before logging; traces often contain PII and full tool payloads.
- Know your provider's retention and training posture; disable telemetry you don't want (OpenAI tracing is on by default; Claude Code telemetry can be disabled).
- Multi-tenant isolation at the storage and tool-credential layer, not just the prompt.

---

## 10. Reliability, cost, and latency

### Bound everything

- Max iterations (3–5 for most tasks), max tokens, max wall-clock, max tool calls.
- On exhaustion: summarize progress, return partial results, and escalate — don't spin.
- Timeouts and retries with backoff on every external call; distinguish transient from permanent errors.
- Circuit breakers for flaky dependencies; fallback model or degraded mode.

### Model routing

Match model to step, not to product:

- small/fast model: intent classification, routing, extraction, simple tool calls
- frontier model: planning, hard reasoning, final synthesis
- specialized/cheap model: judging, summarizing, compaction

Teams routinely cut cost this way without losing quality. Measure on your own traffic; don't trust generic benchmarks.

### Other levers

- **Prompt caching** for stable system prompts and tool schemas.
- **Stream** partial output; perceived latency matters more than total.
- **Parallelize** independent tool calls.
- **Cache** idempotent lookups.
- **Compress** tool output at the boundary (rows → summary + handle).

---

## 11. Deployment and operations

Minimum viable production shape:

```
API/UI → queue → agent worker (stateless) → tools (sandboxed) → DB/object store
                     ↓
              trace exporter → observability backend
```

- **Stateless workers + externalized state.** Scale horizontally; survive restarts.
- **Queue for long runs.** HTTP requests aren't the right unit for a 10-minute agent.
- **Version everything**: prompt, tool schema, model, config. A trace should tell you exactly what ran.
- **Canary and roll back** prompts like code. Prompt changes are behavior changes.
- **Per-step budgets and alerts** on cost, latency, error rate.
- **Shadow mode** for new versions: run on real traffic, compare, don't act.
- **Data retention and deletion** paths for transcripts and memory.

---

## 12. Anti-patterns (observed repeatedly)

- **Monolithic agent** with 40 tools and a 5,000-word prompt. Split it.
- **Unbounded loops.** A confused agent will happily burn your budget.
- **Trust-later architecture.** Guardrails, tracing, and evals bolted on after launch cost more and break more.
- **Framework-first.** Choosing the stack before the task; then bending the task to the framework.
- **Vibe-check evals.** "It looked good in the demo" is not a release gate.
- **Over-summarization.** Compaction that drops the one constraint that mattered.
- **Exact-trajectory tests.** Brittle; they punish valid creativity.
- **Memory maximalism.** Storing everything, retrieving noise, never deleting.
- **Treating retrieved text as instructions.** The classic injection vector.
- **Autonomy without observability.** No trace, no audit, no rollback.

---

## 13. Recommended stack for a greenfield project (Nov 2026)

This is a default, not a doctrine. Adjust to your team and surface.

**Backend / agent core (Python):**
- Pydantic AI for the loop + typed tools/outputs, or a hand-rolled loop if the agent is simple.
- FastAPI for the API; Postgres for state, checkpoints, and memory.
- Temporal or DBOS only when runs must survive crashes or wait days for approvals.
- OpenTelemetry → Langfuse (self-hostable) or LangSmith/Logfire.
- MCP client for third-party integrations; internal tools as typed functions.
- Sandbox: container/microVM with no host mounts, non-root, egress allowlist.

**Frontend / product (TypeScript):**
- Next.js + Vercel AI SDK for streaming chat and tool-call rendering.
- If the agent *is* the product surface, consider Mastra for TS-native orchestration.

**Models:**
- One frontier model for planning/synthesis, one cheap fast model for routing/extraction/judging.
- Keep a provider abstraction; don't hardcode a vendor in business logic.
- Evaluate at least two providers on your own eval set before locking in.

**Non-negotiables from day one:**
- trace every run
- a 20–50 case eval set with programmatic outcome checks
- max-iteration + budget caps
- approval gate for writes
- read-only v1

---

## 14. A pragmatic build sequence

**Week 0 — define the job**
Pick one narrow, valuable task with a checkable outcome. Write 10 real examples and define "done" operationally. Decide the autonomy level for v1 (read-only is fine).

**Week 1 — walking skeleton**
Single loop, 2–4 tools, streaming UI or CLI, tracing on. Hard-code the happy path. No memory, no multi-agent, no framework unless it's free.

**Week 2 — evals**
Turn the 10 examples into 30–50 cases with outcome assertions. Build the runner. Establish a baseline score. Now every change is measurable.

**Week 3 — reliability**
Error handling, retries, timeouts, iteration caps, structured tool errors, context compaction. Re-run evals. Fix the top failure clusters from traces.

**Week 4 — trust & state**
Checkpointing, approval flow for writes, per-user auth and tool scoping, audit log, cost/latency dashboards.

**Week 5 — widen scope**
Only now: more tools, subagents for context isolation, second model for routing, memory if the data says you need it.

**Week 6 — production hardening**
Load test, canary, rollback, retention policy, on-call runbook, red-team the tool surface.

Rule: **no new capability without an eval case and a trace to prove it works.**

---

## 15. Open questions to settle before writing code

1. **What is the agent's job**, in one sentence, with a measurable success criterion?
2. **Who is the user and where do they interact** — chat UI, CLI, API, Slack, IDE, background job?
3. **What can it touch?** Read-only v1 vs writes; which systems; whose credentials?
4. **What's the autonomy level** — suggest, act-with-approval, or act-then-report?
5. **Latency budget** — interactive (<5s) vs background (minutes)?
6. **Stack constraints** — Python, TypeScript, existing infra, cloud, on-prem/air-gapped?
7. **Data sensitivity** — PII, regulated data, tenant isolation, retention requirements?
8. **Build vs buy** — is this a product differentiator or an internal utility?

---

## 16. Source list

**Foundational**
- Anthropic — [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic — [Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)
- Anthropic — [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)
- Anthropic — [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)
- Google Cloud — [Choose a design pattern for your agentic AI system](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system)
- Microsoft — [Architecting agent solutions](https://learn.microsoft.com/en-us/agents/architecture/)
- AWS — [Inside an enterprise AI agent: a governed, observable loop architecture](https://aws.amazon.com/blogs/architecture/inside-an-enterprise-ai-agent-a-governed-observable-loop-architecture/)

**Frameworks & ecosystem**
- Pydantic — [Best Python AI agent frameworks in 2026](https://pydantic.dev/articles/best-python-ai-agent-frameworks)
- [AI agent frameworks compared on state, approvals, tracing and lock-in](https://9io.ai/blog/ai-agent-frameworks-compared)
- [AI agent SDKs compared: OpenAI, Claude, LangGraph, Pydantic AI](https://dbggr.dev/articles/agent-frameworks-compared/)
- LangChain — [State of Agent Engineering](https://www.langchain.com/state-of-agent-engineering)
- MCP — [Tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [Security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)

**Evaluation & operations**
- LangChain — [Evaluating AI agents at the run, trace, and thread level](https://www.langchain.com/resources/agent-evals)
- Arize — [Agent evaluation](https://arize.com/ai-agents/agent-evaluation/)
- Google — [Evaluating AI agents: moving past the vibe check](https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/)

**Security**
- OWASP — [AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
- NSA/CISA — [MCP security design considerations (PDF)](https://media.defense.gov/2026/Jun/02/2003943289/-1/-1/0/CSI_MCP_SECURITY.PDF)
