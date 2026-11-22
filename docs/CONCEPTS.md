# NexusAI concepts: What, Why, How

One section per concept. Each gives what it is, the engineering problem it
solves, how NexusAI implements it, what happens on failure, and how it would
scale. The "In an interview" lines are short answers you can say out loud.

---

## 1. Multi-agent architecture

**What.** Four agents with one responsibility each (market, competitors,
customers, strategy) and an orchestrator that coordinates them.

**Why.** One agent doing everything needs one huge prompt, every tool, and
produces output that is hard to validate. Splitting gives small prompts, a
narrow output contract per agent, a narrow tool set per agent, and parallel
work.

**How.** `agents/base.py` holds the pipeline every agent shares: gather →
sanitise → analyse → validate. A subclass supplies three things: its search
plan, its output model, and its role prompt. See `agents/competitor/agent.py`
for a complete agent in about 80 lines.

**Trade-off.** More moving parts and more total latency per request than a
single call. `evaluation/workflow_eval.py` includes a single-agent baseline so
the comparison can be measured instead of assumed.

**In an interview.** "Each agent has one job, one output schema and only the
tools that job needs. That makes each one testable alone and limits what a
compromised agent can do."

---

## 2. Orchestration, planning and delegation

**What.** The orchestrator turns a request into tasks, hands them out, tracks
them and assembles the result.

**Why.** Agents should not know about each other. Ordering, retries and
aggregation belong in one place.

**How.**
- `planner.py` produces a plan with a fixed shape: three independent research tasks and one synthesis task that depends on them. With an LLM it also extracts a short search topic and focus questions; without one it uses a template.
- `graph.py` expresses the plan as a LangGraph state machine. The three research nodes fan out and run concurrently; `synthesize` has an edge from all three, so it waits for them (fan-in). Fields written by parallel nodes have reducers that merge the updates.
- `delegation.py` defines one `Delegator` interface with two implementations: in-process (`LocalDelegator`) and A2A (`A2ADelegator`). The orchestrator code is identical for both.

**On failure.** See section 9.

**Scaling.** The orchestrator is stateless between requests apart from the
database, so several backend instances can run behind a load balancer. Runs
are started as in-process background tasks; at higher volume you would move
them to a job queue so a restart does not lose work.

**In an interview.** "The plan shape is fixed for predictability; the LLM only
fills in the topic and questions. Research runs in parallel, synthesis waits
for all three."

---

## 3. A2A protocol

**What.** An open protocol for agents to talk to each other as independent
services. Its building blocks:

| Term | Meaning | In NexusAI |
| --- | --- | --- |
| Agent Card | JSON document describing an agent: name, skills, endpoint, capabilities | Served at `/.well-known/agent-card.json` by each agent |
| Discovery | Finding out what agents exist and what they can do | `AgentRegistry` fetches every card and indexes skills |
| Message | One turn of communication, made of parts (text, data, file) | The orchestrator sends one data part with the task input |
| Task | A unit of work with an id and a state | Created by the agent for every request |
| Task lifecycle | `submitted → working → completed / failed / rejected / canceled` | Published by `ResearchAgentExecutor`, followed by `A2AClient` |
| Artifact | The output of a task | One JSON artifact named `agent_result` |

**Why.** Without a standard, every pair of agents needs custom glue, and you
cannot swap an agent written by another team or in another framework. A2A
gives a common way to describe, find and call agents, including long-running
work.

**How.**
- Server: `agents/a2a_server.py` uses the official SDK's `DefaultRequestHandler`, `InMemoryTaskStore` and route factories. Our code is the `AgentExecutor`: parse the message, run the agent, publish status updates and the artifact.
- Client: `protocols/a2a/client.py` sends the message with the SDK client, reads the event stream, records each state, and collects the artifact. On timeout it asks the agent to cancel the task.
- Discovery: the orchestrator is configured only with base URLs. It asks "who advertises the skill `competitor_analysis`?" and uses whichever agent answers.

**A2A vs a function call.** A function call shares memory, a process and a
failure. An A2A call crosses a network to a service that may be down, slow,
a different version, or owned by someone else. That is why there is discovery,
a task state machine, timeouts, and validation of the response.

**On failure.** Unreachable agent: absent from the registry, task marked
`unavailable`. Agent raises: task ends `failed`. Bad input: task ends
`rejected` and is not retried.

**Scaling.** Agents are stateless HTTP services: run several replicas of one
behind a load balancer. The in-memory task store would be replaced by the
SDK's database task store so any replica can answer `tasks/get`.

**In an interview.** "A2A is for agent-to-agent communication across process
and team boundaries. Agents publish a card, clients discover skills from it,
send a message, and follow a task through a defined lifecycle to an artifact."

---

## 4. MCP (Model Context Protocol)

**What.** An open protocol for connecting an AI application to tools and data.

| Term | Meaning | In NexusAI |
| --- | --- | --- |
| Host | The application that contains the model/agent | Each agent process |
| Client | The connector inside the host that talks to one server | `ToolClient` using the SDK's `Client` |
| Server | A process exposing capabilities | The three servers in `protocols/mcp/servers/` |
| Tool | An action with typed arguments, invoked on demand | `web_search`, `fetch_page`, `search_companies`, `cagr`, ... |
| Resource | Read-only context identified by a URI | `business://dataset/info` |
| Prompt | A reusable prompt template | `competitor_comparison` |
| Transport | How messages travel | Streamable HTTP between processes; in-process in tests |

**Tools vs resources vs prompts.** A tool *does* something and is chosen at run
time. A resource *is* something to read, loaded by the application. A prompt is
a template a user or application picks. The business data server exposes all
three so you can see the difference.

**Why.** Without it, every agent embeds its own web-search code and every new
tool means changing every agent. With it, a tool is written once, described by
a schema, and any MCP client can discover and call it.

**How.** `ToolClient.call` does, in order: authorise → confirm the server
advertises the tool → check the cache → call with a timeout and bounded
retries → record the call. Servers validate arguments from type annotations
(`Field(ge=1, le=10)`), and `fetch_page` refuses URLs that resolve to
non-public addresses (SSRF protection), re-checking every redirect.

**A2A vs MCP.** MCP connects an agent to a *tool*: you send arguments and get a
result. A2A connects an agent to another *agent*: you send a goal, and it
decides how to achieve it, possibly over a long time.

**On failure.** Transport error or timeout: retried. Tool error (bad arguments,
404): not retried, because the same call would fail again. Either way the
agent loses that piece of evidence and continues.

**Scaling.** Servers are stateless (`stateless_http=True`), so they scale
horizontally. The cache moves to Redis so all agents share it.

**In an interview.** "MCP standardises how an agent discovers and calls tools.
A2A standardises how agents delegate to each other. We use MCP for web search,
data and calculation, and A2A between the orchestrator and the agents."

---

## 5. Prompt injection and trust boundaries

**What.** *Direct* injection: the user tries to override the system's
instructions. *Indirect* injection: instructions are hidden in content the
agent retrieves (a web page, a document, another agent's reply).

**Why it is hard.** A model receives instructions and data in the same channel
and cannot reliably tell them apart. No prompt wording or classifier fixes
that. So the design question is not "how do I detect every attack?" but "if
the model is fooled, what can it actually do?"

**Trust boundaries in NexusAI.**

| Source | Trust | Handling |
| --- | --- | --- |
| System prompts, code | Trusted | — |
| User request | Mostly trusted | Validated; attempts to extract internals rejected |
| Web pages, search snippets, dataset rows | Untrusted | Sanitised, fenced, never treated as instructions |
| Another agent's response | Untrusted | Schema-validated and re-checked by the receiver |

**How (defence in depth).**
1. Least privilege (`permissions.py`): no agent has an outbound-data tool; secrets are never in a prompt; the Strategy Agent has no web access.
2. Plan-then-execute (`agents/base.py`): tools are called by code before untrusted text is read.
3. Separation (`wrap_untrusted`): evidence is fenced with a random nonce the content cannot guess.
4. Detection (`neutralize`): instruction-like sentences are removed and logged; the rest of the page is kept.
5. Output validation (`output_validation.py`): only retrieved URLs may be cited; other links are stripped; secrets are redacted.

**Proof it does not depend on the detector.** `tests/test_security.py`
includes a test where the model *obeys* the attacker and returns an
exfiltration link, a fake citation and an API key. Output validation removes
all three.

**In an interview.** "We assume injection will sometimes succeed, so the
defence is limiting consequences: least privilege, tool calls decided before
reading untrusted text, and validating what comes out. Detection is the last
and weakest layer."

---

## 6. Least privilege and tool authorisation

**What.** Each agent can call only the tools its job requires.

**Why.** If an agent is manipulated, the damage is bounded by what it is
allowed to do.

**How.** `TOOL_POLICY` is an allow-list of agent → server → tools. `ToolClient`
checks it before every call; a denial raises `PermissionDenied` and writes an
audit event. Discovery is filtered by the same policy, so an agent does not
even see tools it cannot use.

**Limitation.** The per-agent policy is enforced on the client side. MCP
servers do check a shared service token (section 14), which proves the caller
is part of the system but not which agent it is. The fix is per-agent
credentials checked by the MCP server.

---

## 7. Human approval

**What.** A person confirms before the system acts.

**Why.** Some steps cost money or have consequences, and a model's plan can be
wrong.

**How.** Submit with `require_approval`. The request stops at
`awaiting_approval` with its plan visible; `POST /api/research/{id}/approve`
starts it. The transition is one atomic `UPDATE ... WHERE status =
'awaiting_approval'`, so two approvals cannot start two runs.

**Where it would go next.** Any future tool with side effects (sending email,
buying data) would require approval per call.

---

## 8. Structured output and validation

**What.** Agents return typed objects, not free text.

**Why.** Downstream code (the Strategy Agent, the report, the UI) needs fields
it can rely on, and a schema is the only way to check an LLM's answer
mechanically.

**How.** Pydantic models in `state.py` are the contract. `LLMClient.structured`
asks the provider for that schema, validates the reply, and retries on
failure. A finding must declare itself `sourced_fact` or `hypothesis`; a
"sourced fact" with no grounded citation is downgraded automatically.

---

## 9. Reliability: timeouts, retries, fallbacks

**Retry vs fallback.** A *retry* repeats the same action, hoping a transient
fault has passed. A *fallback* does something different and usually weaker.
Retry first, fall back when retries are exhausted.

| Idea | Meaning | In NexusAI |
| --- | --- | --- |
| Timeout | Never wait forever | Every LLM call, tool call and agent task has a deadline |
| Bounded retry | Limited attempts with backoff | `max_retries`, used by `Orchestrator.run_task`, `ToolClient`, `LLMClient` |
| Retry only what can succeed | Do not repeat a request that was refused | Rejected tasks and tool validation errors are not retried |
| Fallback | Weaker answer instead of none | LLM failure → extractive summary, flagged |
| Fault isolation | One failure stays local | Each research task succeeds or fails on its own |
| Graceful degradation | Deliver less, and say so | Report without the failed section, `degraded`, low confidence |
| Idempotency | Doing it twice equals doing it once | Unique key per normalised request |

**Idempotency detail.** The key is a hash of the normalised request, with a
unique constraint in the database. Checking first and inserting second would
have a race; the constraint makes the database the arbiter.

**In an interview.** "Timeouts everywhere, bounded retries only for failures
that can recover, a fallback when they don't, and the report always tells the
reader what is missing."

---

## 10. Evaluation

**What.** Measuring quality with a fixed dataset and rules written down before
measuring.

**Why.** LLM systems fail quietly. Without measurement you cannot tell whether
a change made things better.

**How.**
- `agent_eval.py`: each agent alone. Completion, schema validity, citation coverage, grounding, a lexical source-relevance proxy.
- `workflow_eval.py`: the whole pipeline, eight injected failure scenarios, and a single-agent baseline.
- `security_eval.py`: 27 fixed cases including benign text that must *not* be flagged, since false positives are failures too.

**Honest reading.** Deterministic checks prove structure and behaviour. They do
not judge whether an analysis is insightful. That needs an LLM judge or human
review, and a larger dataset than five to eight questions.

---

## 11. Observability

**What.** Being able to see what the system did, how long it took and what it
cost.

**Why.** With several agents and tools, "the report is wrong" or "it is slow"
is unanswerable without a record of each step.

**How.**
- Spans (`tracing.py`): each plan, delegation, tool call and analysis records name, duration and status, stored with the report.
- Task records: one row per delegated task with state history, attempts and latency.
- Tool call records: every MCP call, including failures, cache hits and denials.
- Tokens and cost (`metrics.py`): token counts come from the provider's response. Cost is computed only if you configure your model's price; otherwise it says "not configured" rather than guessing.
- Langfuse (optional): LLM calls are also sent there when keys are set.

**Limitation.** In A2A mode, spans inside agent processes are not joined into
one trace. The tool-call records returned with each result cover that gap. A
production system would propagate a trace id (OpenTelemetry) across the A2A
call.

---

## 13. Multi-tenancy and accounts

**What.** Many users share one deployment, and each sees only their own data.

**Why.** Without it you would run one copy of the system per customer, or
worse, leak one customer's research to another.

**How.**
- Every research request row has a `user_id`. `database/repository.py` puts it in the WHERE clause of every read and write, so the check cannot be forgotten in a route.
- A request that belongs to someone else returns **404, not 403**. A 403 would confirm the id exists.
- The idempotency key includes the user id, so two users asking the same question get separate reports.
- Security events and metrics are scoped the same way; only an administrator can ask for everyone's.

**Passwords and sessions.**
- Passwords: scrypt with a random salt per user. scrypt is deliberately slow and memory-hungry, which makes guessing stolen hashes expensive.
- Sessions: a random 256-bit token in an HttpOnly, SameSite=Lax cookie. The database stores only its SHA-256 hash.
- Why not a JWT? A JWT is valid until it expires and cannot be revoked without extra machinery. A server-side session is deleted the moment the user signs out, changes password or is disabled.
- API keys reuse the mechanism, without expiry, as a Bearer token. They can run research but not manage the account, so a leaked key cannot take the account over.

**Attacks considered.**

| Attack | Defence |
| --- | --- |
| Password guessing | Rate limit per email and address; slow hash |
| Finding out which emails have accounts | Same error and same timing for unknown email and wrong password |
| Script steals the session (XSS) | HttpOnly cookie; report text rendered as text, never as markup |
| Another site submits a request as you (CSRF) | SameSite cookie, plus a custom header required on state-changing calls |
| Database leak | Only hashes of passwords, sessions and keys are stored |
| Reading another user's report by guessing its id | Ownership filter in every query |

**On failure.** An expired or revoked session gives 401 and the app returns
to the sign-in page.

**Scaling.** Sessions live in the database, so any backend instance can
validate them. The rate-limit counters are in process memory and would move to
Redis with more than one instance.

**In an interview.** "Tenant isolation is enforced in the data layer, not in
each route. Sessions are server-side so they can be revoked, and the cookie is
HttpOnly so a script cannot read it."

---

## 14. Plans, quotas and service authentication

**Plans and quotas.**

*What.* Limits per account: reports per month, concurrent runs, requests per
minute, number of API keys.

*Why.* Each report costs LLM tokens and tool calls. Without limits one account
can exhaust the budget or starve everyone else.

*How.* `backend/plans.py` defines the limits; `ResearchService._enforce_limits`
checks them in order of cheapness: rate limit (memory), concurrent runs and
monthly usage (one count query each). A duplicate submission is answered
before the checks, because it costs nothing. Different limits return different
status codes: 429 with `Retry-After` for "slow down", 402 for "plan
exhausted", so a client can react correctly.

*Not built.* Billing. The plan is a column on the user; a payment provider's
webhook would update it.

**Service authentication.**

*What.* Agents and MCP servers only accept calls that carry a shared secret.

*Why.* A2A and MCP endpoints are plain HTTP services. Without a credential,
anything that can reach the network can call a tool or an agent directly and
skip validation, permissions and audit.

*How.* `security/internal_auth.py` is an ASGI middleware in front of each
agent and MCP server. The backend's A2A client and the agents' MCP client send
`Authorization: Bearer <token>`. Each Agent Card declares the requirement in
`securitySchemes`, which is how A2A tells a client what credentials it needs.
The card and health endpoints stay public; rejected calls are audited.

*Limitation.* One shared secret gives authentication, not identity. The next
step is a credential per agent, so the MCP server itself can enforce which
agent may call which tool.

---

## 15. Scalability basics

| Technique | Where |
| --- | --- |
| Async I/O | Everything is `async`: one process handles many waiting calls |
| Concurrency | Research agents run in parallel; so do searches and page fetches inside an agent |
| Service boundaries | Agents and MCP servers are separate, stateless, independently scalable |
| Caching | Tool results cached by (server, tool, arguments), in Redis when configured |
| Containers | Eleven services in `docker-compose.yml`: one Python image for backend, agents and tools, one nginx image for the React dashboard |

**Deliberately left out:** Kubernetes, message queues, a distributed workflow
engine. Nothing in this project needs them yet, and adding them would hide the
concepts the project exists to teach.
