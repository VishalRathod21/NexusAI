# NexusAI: interview guide

What the project is, what every file does and why, the numbers you can quote,
and resume bullets. No code here: this is the story you tell.

For deeper What / Why / How on each concept (A2A, MCP, prompt injection,
retries, multi-tenancy), read `docs/CONCEPTS.md` after this.

---

## 1. The 60-second pitch

> NexusAI is a multi-user business research platform. You ask a business
> question, for example "I want to launch an AI HR product in India". An
> orchestrator splits it into tasks and hands them to four specialised AI
> agents: market, competitors, customers and strategy. The agents talk to the
> orchestrator over the A2A protocol and use tools through MCP. You get one
> report where every finding is labelled as a sourced fact or a hypothesis,
> with the pages it came from.
>
> I focused on three things beyond "it works": security against prompt
> injection, predictable behaviour when something fails, and measuring it with
> tests and an evaluation set. Then I added the product layer: accounts, data
> isolation per user, plans with limits, API keys and an admin area.

**The one-line version:** "Four AI agents research a business idea and return
one report with sources you can check."

---

## 2. How it works, as a story

1. A signed-in user types a question in the React app.
2. The **backend** checks who they are, checks their plan limits, and checks the question is a real research question and not an attack.
3. If the same user already asked the same thing, they get the existing report. Nothing runs twice.
4. The **planner** makes a plan: three research tasks and one strategy task that must wait for them.
5. The **orchestrator** finds the agents by reading their Agent Cards, then sends the three research tasks at the same time over **A2A**.
6. Each **agent** searches the web and reads pages through **MCP tools**, cleans what it read, asks the language model for an answer in a fixed format, and checks that answer.
7. The **Strategy Agent** reads the three results and writes opportunities, risks and recommendations. It has no web access.
8. The orchestrator checks every agent's answer again, builds the report, gives it a confidence level, and saves it with the full history of what happened.

Two protocols, and the difference interviewers always ask about:

| | A2A | MCP |
| --- | --- | --- |
| Connects | agent to agent | agent to tool |
| You send | a goal ("analyse the competitors") | arguments ("search for X") |
| You get back | a task that runs and finishes later | a result |
| In NexusAI | orchestrator to the four agents | agents to search, data and calculator |

---

## 3. Numbers you can quote

Every number below comes from this project and can be reproduced. The last
column is how to say it honestly.

| Number | What it is | How to check | Say it like this |
| --- | --- | --- | --- |
| **4** | Specialised agents, each its own service | `agents/` | "four agents, each with one job" |
| **3 / 9** | MCP servers / tools on them (plus 1 resource and 1 prompt) | `protocols/mcp/servers/` | "three MCP servers exposing nine tools" |
| **20** | HTTP API endpoints | `backend/api/routes.py` + `/health` | "a 20-endpoint REST API" |
| **91** | Automated tests, all passing | `python -m pytest` | "91 automated tests" |
| **27 of 27** | Security test cases handled correctly | `python -m evaluation.security_eval` | "passed all 27 cases in my security test set" |
| **8 of 8** | Injected failure scenarios handled as designed | `python -m evaluation.workflow_eval` | "handled all 8 failure scenarios I simulated" |
| **28** | Steps in the automated UI flow test | run during development | "a 28-step end-to-end UI flow" |
| **6** | Defence layers against prompt injection | `README.md`, Security model | "six layers of defence" |
| **7** | Attack patterns the detector knows | `security/prompt_injection.py` | only if asked about detection |
| **3** | Plans (Free, Pro, Team) with 4 limits each | `backend/plans.py` | "three plans with enforced limits" |
| **9** | Separate processes when running locally | `scripts/run_all.py` | "nine services running independently" |
| **~5,800** | Lines of Python application code, in 44 files | counted | "about 6,000 lines of Python" |
| **~5,200** | Lines of frontend code, in 16 files | counted | "about 5,000 lines of React and CSS" |
| **12** | Sections on the landing page | `frontend/src/pages/Landing.jsx` | only for a frontend role |

### What these numbers do NOT prove

Say this before the interviewer finds it. It makes you look more senior, not less.

- The 27 security cases are attacks **I wrote the defences for**. They show the layers work as designed. They do not show the system resists new attacks.
- The 8 failure scenarios are faults **I injected on purpose**. "Handled" means the planned behaviour happened (retry, degrade, flag), not that the fault disappeared.
- The evaluation ran in **offline mode on a synthetic test corpus**. So there is no number yet for research quality, citation accuracy, cost per report or real response time.
- Docker, PostgreSQL and Redis are configured but were **never run**.

### Numbers you do not have yet

These need a run with a real language model (`LLM_PROVIDER` set, `--web ddgs`).
Until you run it yourself, do not put them on a resume.

| Metric | How to get it |
| --- | --- |
| Cost per report | set model prices in `.env`, run `python -m evaluation.workflow_eval --web ddgs` |
| Average time per report | same run, `avg_latency_ms` |
| Citation coverage | `python -m evaluation.agent_eval --web ddgs` |
| Multi-agent vs single-agent comparison | same workflow run, the baseline section |

---

## 4. Resume bullets

**NexusAI: multi-agent business research platform** (Python, FastAPI, LangGraph, A2A, MCP, React)

Pick three or four. Every number here is in the table above.

- Built a multi-agent research platform where **4 specialised agents** run as independent services and are orchestrated over the **A2A protocol**, with parallel fan-out and fan-in in LangGraph.
- Exposed **9 tools across 3 MCP servers** (web search, company data, calculator) with tool discovery, argument validation, caching, timeouts and bounded retries.
- Designed **6 layers of defence** against direct and indirect prompt injection, including least-privilege tool access and output validation; the system passed **27 of 27** cases in a reproducible attack test set.
- Engineered failure handling (timeouts, retries, fallback, graceful degradation, idempotency) and verified it against **8 of 8** injected failure scenarios such as agent outage and malformed agent output.
- Added a multi-tenant SaaS layer behind a **20-endpoint API**: accounts, revocable sessions, API keys, per-user data isolation, and **3 plans** with enforced quotas and rate limits.
- Covered the system with **91 automated tests** and a **28-step** end-to-end UI flow, all runnable with no network or API key.
- Built the React frontend: a 12-section landing page, a research dashboard with live run progress, and account and admin areas, with light, dark and mobile layouts.

**Shorter, if you have two lines:**

- Built a multi-agent business research platform: 4 agents orchestrated over A2A, 9 tools over MCP, with a 6-layer prompt-injection defence that passed 27 of 27 attack cases.
- Added multi-tenant accounts, plans and API keys behind a 20-endpoint API; 91 automated tests and 8 of 8 simulated failure scenarios handled.

**Do not write:** "improved accuracy by X%", "reduced latency by X%", "used by
N users", "production deployment". None of these was measured or is true yet.

**If asked how you built it:** say plainly that you used an AI coding assistant
and directed the design, then show you understand it. Interviewers test
understanding, not typing. Section 5 and 6 are what you need to be able to
explain without notes.

---

## 5. Every file: what it does and why

### Project root

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `README.md` | Explains the project, how to run it, what is verified and what is not | "I documented the limits, not only the features" |
| `requirements.txt` | Exact Python package versions | Pinned versions make the build repeatable |
| `.env.example` | Template of every setting, with no secrets | Real secrets go in `.env`, which is never committed |
| `Dockerfile` | One image for all Python services | Same image, different start command per service |
| `docker-compose.yml` | Describes 11 containers on one private network | Only the app and API are exposed; agents and tools are internal |
| `pytest.ini` | Test settings | Lets `pytest` run async tests |
| `scripts/run_all.py` | Starts all 9 services with one command | Also generates the shared service token for that run |

### `backend/` : the API and the orchestrator

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `main.py` | Creates the web app and wires everything together | Adds the CSRF check and security headers to every request |
| `config/settings.py` | The one place that reads environment variables | Secrets are a special type that never prints in logs |
| `api/routes.py` | The 19 API endpoints | Each one first checks who is calling |
| `api/schemas.py` | The shape of each request and response | Bad input is rejected before any logic runs |
| `auth.py` | Sign-up, sign-in, sessions, API keys, admin actions | Passwords are hashed; sessions can be revoked on the server |
| `plans.py` | The three plans and their limits | Billing is not connected; a plan is just a set of limits |
| `rate_limit.py` | Counts recent attempts and says "wait" | Used for sign-in attempts and for submissions |
| `service.py` | The life of one research request | Checks limits, removes duplicates, handles approval, saves results |
| `llm/provider.py` | One doorway to Groq, Gemini or OpenAI | Change provider in one setting; handles timeouts and retries |
| `orchestrator/state.py` | The data contracts everything shares | Every agent must return this exact shape |
| `orchestrator/planner.py` | Turns a question into a plan | Fixed shape (3 research + 1 strategy) so the system is predictable |
| `orchestrator/delegation.py` | Two ways to call an agent: in-process or over A2A | Same interface, so the rest of the code does not care which |
| `orchestrator/orchestrator.py` | Runs tasks with timeout and retry, then builds the report | One failed agent does not fail the report |
| `orchestrator/graph.py` | The workflow as a LangGraph state machine | Three agents run together; strategy waits for all three |

### `agents/` : the four agents

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `base.py` | The steps every agent follows: gather, clean, analyse, check | Tools are chosen by code before reading any web page, so a page cannot trigger a tool |
| `a2a_server.py` | Turns an agent into an A2A service | Publishes task states: submitted, working, completed or failed |
| `market_research/agent.py` | Trends, demand signals, market context | Sends arithmetic to the calculator tool, not to the model |
| `competitor/agent.py` | Finds competitors and builds profiles | Uses two sources: the web and a company dataset |
| `customer_insights/agent.py` | Customer segments, pain points, needs | Separates what sources say from its own guesses |
| `strategy/agent.py` | Turns the three results into recommendations | Has no web tools on purpose, to limit damage if it is fooled |
| `*/server.py` (4 files) | Starts each agent as its own service | Tiny files: each agent can be deployed and scaled alone |

### `protocols/` : A2A and MCP

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `a2a/agent_cards/__init__.py` | Builds each agent's public card | The card says who the agent is, what it can do, and that it needs a token |
| `a2a/discovery.py` | Reads every card and indexes skills | The orchestrator asks "who can do competitor analysis?", it does not hard-code addresses |
| `a2a/client.py` | Sends a task and follows it to the end | Cancels the remote task if it takes too long |
| `mcp/servers/web_research.py` | Search the web, fetch a page | Refuses internal addresses (protection against SSRF) |
| `mcp/servers/business_data.py` | Small company dataset | Shows all three MCP types: tools, a resource, a prompt |
| `mcp/servers/market_calculator.py` | Percentages, growth rate, market size | Returns the formula and steps so a person can check |
| `mcp/servers/runner.py` | Starts an MCP server over HTTP | Requires the shared service token |
| `mcp/servers/data/companies.json` | 12 real companies, basic facts | Marked as a snapshot to verify |
| `mcp/servers/data/web_fixtures.json` | 7 invented pages for offline tests | One page contains an attack, to test the defences |
| `mcp/clients/tool_client.py` | The one path every tool call takes | Permission, discovery, cache, timeout, retry, logging, in that order |

### `security/`

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `input_validation.py` | Checks the user's question | Blocks "ignore your instructions" style requests |
| `prompt_injection.py` | Finds and removes instructions hidden in web text | The weakest layer on purpose; I do not rely on it |
| `permissions.py` | Which agent may use which tool | Allow-list: anything not listed is refused |
| `output_validation.py` | Checks what an agent produced | Removes invented citations, unknown links and secrets |
| `audit.py` | Records every security event | Events travel back from agents to the backend |
| `internal_auth.py` | Shared token between backend, agents and tools | Stops anyone on the network calling an agent directly |

### `database/`

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `models.py` | The five tables: users, tokens, requests, tasks, security events | Only hashes of passwords and tokens are stored |
| `connection.py` | Connects to SQLite or PostgreSQL | Detects an out-of-date database at startup |
| `repository.py` | All database reads and writes | Every query filters by owner: this is the tenant isolation |
| `cache.py` | Remembers tool results | Uses Redis if available, memory if not; a cache failure never breaks a request |

### `observability/`

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `tracing.py` | Records each step and how long it took | Optional Langfuse hook for model-level traces |
| `metrics.py` | Totals for time, tokens and cost | Cost shows "n/a" unless real prices are set: no guessed numbers |

### `evaluation/`

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `datasets/research_questions.json` | 8 fixed questions | Same questions every run, so results are comparable |
| `datasets/security_cases.json` | 20 attack and harmless texts | Harmless ones test for false alarms |
| `common.py` | Shared helpers | Runs everything in one process, so results are repeatable |
| `agent_eval.py` | Scores each agent alone | Completion, valid format, citation coverage |
| `workflow_eval.py` | Scores the whole pipeline, 8 failure scenarios, and a single-agent baseline | Scoring rules were written before measuring |
| `security_eval.py` | Runs all 27 security cases | Includes the worst case: the model obeys the attacker |

### `tests/`

| File | Tests | What it proves |
| --- | --- | --- |
| `conftest.py` | setup | Tests need no network, no API key, no running service |
| `test_mcp.py` | 9 | Tool discovery, validation, SSRF block, caching |
| `test_agents.py` | 12 | Output format, grounded citations, fallback when the model fails |
| `test_a2a.py` | 10 | Agent Cards, task lifecycle, a stopped agent, a malformed answer |
| `test_security.py` | 15 | Every defence layer, with real attack text |
| `test_workflow.py` | 4 | Saving, duplicates, human approval |
| `test_saas.py` | 16 | Accounts, isolation between users, quotas, API keys, admin |

Some tests run several cases each, which is how 66 test functions become 91 tests.

### `frontend/` : the React app

| File | What it does | Why it matters / what to say |
| --- | --- | --- |
| `index.html`, `src/main.jsx` | Entry point | Loads fonts and starts the app |
| `vite.config.js` | Dev server settings | Forwards `/api` to the backend, so the browser sees one origin |
| `src/App.jsx` | The six pages and which need sign-in | Guards protected pages |
| `src/api.js` | The only file that calls the backend | Adds the CSRF header to every call |
| `src/auth.jsx` | Knows who is signed in | The session is a cookie JavaScript cannot read |
| `src/pages/Landing.jsx` | The 12-section marketing page | Plan limits come from the API; no invented claims |
| `src/pages/AuthPage.jsx` | Sign in and sign up | One form for both |
| `src/pages/Dashboard.jsx` | The research workspace | Polls only while a request is running |
| `src/pages/Account.jsx` | Usage, API keys, password, delete account | A key is shown once |
| `src/pages/Admin.jsx` | Accounts, plans, platform events | Only administrators can open it |
| `src/components/NewResearch.jsx` | Question box and run progress | Progress comes from real task records, not a fake spinner |
| `src/components/Report.jsx` | The report | Web text is shown as text only, never as HTML |
| `src/components/Panels.jsx` | Activity, Agents, Security, Metrics tabs | Shows what the agents actually did |
| `src/components/Shell.jsx` | Top bar | Usage meter and sign-out |
| `src/components/ui.jsx` | Shared pieces: cards, badges, tables | Links are created only for http and https |
| `src/styles.css` | The whole design system | Tokens for light and dark, responsive, print |
| `Dockerfile`, `nginx.conf` | Builds and serves the app in Docker | nginx forwards `/api` to the backend |

### `docs/`

| File | What it is |
| --- | --- |
| `CONCEPTS.md` | Each concept as What / Why / How, with failure and scaling notes |
| `INTERVIEW_GUIDE.md` | This file |
| `screenshots/` | Landing page, dashboard, report, admin, dark mode, mobile |

---

## 6. Questions you will be asked

**Why several agents and not one big prompt?**
Each agent has one job, one output format and only the tools it needs. That
makes each one testable alone and limits what a fooled agent can do. The cost
is more moving parts, so I built a single-agent baseline to compare.

**What is the difference between A2A and MCP?**
MCP connects an agent to a tool: I send arguments and get a result. A2A
connects an agent to another agent: I send a goal and it decides how to reach
it, possibly over a long time, with a task I can track.

**How does the orchestrator know which agent to call?**
It only knows a list of addresses. It reads each agent's card, indexes the
skills, and asks "who has this skill?". Replacing an agent needs no change in
the orchestrator.

**How do you stop prompt injection?**
I assume it will sometimes work, so I limit the damage. Agents have no tool
that can send data out and never hold secrets. Tools are chosen before any web
text is read. Web text is marked as data. Known attack phrases are removed.
Output is checked: citations must be pages that were really retrieved. I have
a test where the model obeys the attacker and the output check still stops it.

**What happens when an agent is down?**
The task is retried with a delay. If it still fails, the other agents finish,
the report is produced without that section, the missing section is named and
confidence is set to low. Nothing is invented to fill the gap.

**Retry or fallback?**
Retry repeats the same thing, hoping a temporary problem has passed. Fallback
does something weaker instead. I retry first and only retry failures that can
recover. A rejected request is not retried.

**How do you stop a user seeing another user's data?**
Every request row has an owner, and the data layer adds the owner to every
query. It is not left to each endpoint. Someone else's report returns "not
found", not "forbidden", so its existence is not revealed.

**Why sessions in the database and not JWT?**
A JWT stays valid until it expires. A stored session can be deleted at once on
sign-out, password change or account disable. The cookie cannot be read by
JavaScript.

**What does idempotency mean here?**
Submitting the same question twice returns the first request. It is enforced
by a unique key in the database, so it holds even if two requests arrive at
the same moment.

**How did you evaluate it?**
A fixed set of questions and attacks, with scoring rules written before
measuring. Per agent, end to end, security, and failure scenarios. I report
the sample size with every number.

**What would you do next?**
Run the evaluation with a real model to get quality and cost numbers. Add an
LLM-judged check that a cited source really supports the claim. Move research
runs to a job queue. Give each agent its own credential so the tool server
can enforce permissions itself. Add database migrations and billing.

**What is the weakest part?**
The injection detector, because it only knows phrases I wrote. That is why it
is one layer of six and not the defence. Also, quality is not measured yet.

---

## 7. Limits to be ready for

| Limit | Honest answer |
| --- | --- |
| No real model run | "The pipeline is tested with a scripted model. Quality numbers need a real run, which is my next step." |
| Docker not run | "The compose file is written and validated, but I have not built the images." |
| Billing not connected | "Plans and limits are enforced. Payment is one webhook that updates the plan." |
| No email | "No email verification or password reset, because nothing sends email yet." |
| One shared service token | "It proves a caller is part of the system, not which agent it is. Per-agent credentials are the next step." |
| Single backend instance | "Runs and rate-limit counters are in process memory. At scale I would use a job queue and Redis." |
| No migrations | "Schema changes are detected at startup, not migrated. I would add Alembic." |

---

## 8. Before the interview

- [ ] Run it: `python scripts/run_all.py --fixture`, sign up, submit the example question.
- [ ] Open the **Security** tab and find the blocked injection from the poisoned page. Be able to explain what happened.
- [ ] Stop one agent process and submit again with "Run again" ticked. Watch the degraded report.
- [ ] Run `python -m pytest` and the three evaluation commands so you have seen the numbers yourself.
- [ ] Practise the 60-second pitch out loud.
- [ ] Be able to draw the architecture diagram from memory (it is in `README.md`).
- [ ] Read `docs/CONCEPTS.md` once.
