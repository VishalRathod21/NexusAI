// Public landing page.
//
// Rules this page follows: everything it claims is something the product does;
// plan limits come from the backend so they cannot drift; the product visuals
// are labelled as samples; there are no invented customers, quotes or numbers.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { Brand } from "../components/Shell.jsx";

const PROBLEMS = [
  {
    icon: "1",
    title: "Research takes days",
    body: "Sizing a market, mapping competitors and understanding customers means dozens of tabs and a week of reading before you can form a view.",
  },
  {
    icon: "2",
    title: "AI answers can't be checked",
    body: "A chatbot gives a confident paragraph with no way to tell which sentences came from a source and which were made up.",
  },
  {
    icon: "3",
    title: "Gaps stay hidden",
    body: "When a tool fails or finds nothing, most systems fill the hole with plausible text. You only find out when a decision goes wrong.",
  },
];

const STEPS = [
  {
    title: "Ask",
    body: "Describe the business idea and what you need to know, in plain language.",
  },
  {
    title: "Review the plan",
    body: "See how the question is split into tasks. Approve it first if you want to stay in control.",
  },
  {
    title: "Agents research",
    body: "Three agents work in parallel on market, competitors and customers. A fourth synthesises.",
  },
  {
    title: "Read the report",
    body: "One structured report: findings, sources, assumptions, limitations and recommendations.",
  },
];

const AGENTS = [
  {
    glyph: "M",
    color: "#3b38d1",
    name: "Market Research Agent",
    body: "Finds category trends, demand signals and market context. Numbers go to a calculator tool, never to the model's own arithmetic.",
    returns: "Trends, demand signals, sizing inputs",
    tools: "Web search, page reader, calculator",
  },
  {
    glyph: "C",
    color: "#0f8a5f",
    name: "Competitor Agent",
    body: "Identifies who you are up against and builds a profile for each: positioning, target customers and differentiators, with evidence.",
    returns: "Competitor profiles with sources",
    tools: "Web search, page reader, company dataset",
  },
  {
    glyph: "I",
    color: "#b4690e",
    name: "Customer Insights Agent",
    body: "Finds customer segments, their pain points and needs, and says which are reported in sources and which are its own hypotheses.",
    returns: "Segments, pain points, needs",
    tools: "Web search, page reader",
  },
  {
    glyph: "S",
    color: "#a12a6b",
    name: "Strategy Agent",
    body: "Reads the other three agents' findings and turns them into opportunities, risks, a positioning and prioritised recommendations.",
    returns: "Opportunities, risks, recommendations",
    tools: "None on the web, by design",
  },
];

const LAYERS = [
  ["Least privilege", "Each agent can call only the tools its job needs. None can send data anywhere, and none holds a secret."],
  ["Plan, then execute", "Tool calls are decided before any web page is read, so a page cannot trigger one."],
  ["Content is data", "Retrieved text is fenced off and never treated as instructions."],
  ["Detection", "Text that looks like an instruction to the AI is removed and logged."],
  ["Output checks", "Citations must match pages that were retrieved. Stray links and key-shaped strings are stripped."],
  ["Human approval", "You can require sign-off on the plan before any agent starts."],
];

const FAQS = [
  [
    "How reliable are the reports?",
    "A finding can only cite a page the agents actually retrieved, and anything without a source is labelled a hypothesis. That makes a report checkable, not infallible: sources can be wrong and models can misread them. Treat it as a well-organised first draft and verify what matters before acting.",
  ],
  [
    "Where does the information come from?",
    "Public web search and the pages it finds, plus a small curated company dataset. Calculations such as market size are done by a calculator tool that shows its formula and steps.",
  ],
  [
    "Can NexusAI take actions on my behalf?",
    "No. It researches and recommends. It has no ability to buy, pay, sign up, send messages or commit you to anything.",
  ],
  [
    "Who can see my research?",
    "Only your account. Other users cannot open your requests or reports. Administrators of your deployment can see accounts, usage totals and security events in the app, but not your reports.",
  ],
  [
    "Which AI model does it use?",
    "Whoever runs your deployment chooses: Groq, Google Gemini or OpenAI. It can also run with no model at all, in which case reports are sentences extracted from sources and are marked low confidence.",
  ],
  [
    "What happens if something fails midway?",
    "Failed steps are retried. If an agent still cannot finish, you get the report without that section, with the gap named and the confidence lowered. Nothing is invented to fill it.",
  ],
  [
    "How do the paid plans work?",
    "Plans differ only in limits. Online payment is not connected in this version, so an administrator assigns plans to accounts.",
  ],
  [
    "Can I use it from my own code?",
    "Yes. Create an API key on your Account page and call the same API the app uses. A key can run research but cannot change account settings.",
  ],
];

function HeroPreview() {
  return (
    <div className="preview" aria-label="Sample of a NexusAI report">
      <div className="preview-bar">
        <i />
        <i />
        <i />
        <span>Sample report · illustrative content</span>
      </div>
      <div className="preview-body">
        <div className="preview-side">
          {["Market research", "Competitors", "Customers", "Strategy"].map((name) => (
            <div className="preview-agent" key={name}>
              {name}
              <small>done</small>
            </div>
          ))}
        </div>
        <div className="preview-main">
          <div className="row">
            <span className="pill info">Confidence: medium</span>
            <span className="pill">12 sources</span>
          </div>
          <h3>AI-powered HR software for mid-sized companies</h3>
          <div className="preview-finding">
            <span className="pill">Sourced fact</span>
            Buyers prefer payroll and HR in one product over separate tools.
            <small>source: example.com/hr-buyer-survey</small>
          </div>
          <div className="preview-finding hyp">
            <span className="pill hyp">Hypothesis</span>
            An assistant that answers employee policy questions would be a differentiator.
            <small>no source: the agent's own reasoning</small>
          </div>
        </div>
      </div>
    </div>
  );
}

function PlanCard({ plan, signedIn }) {
  const free = plan.id === "free";
  return (
    <div className={`plan-card ${plan.id === "pro" ? "featured" : ""}`}>
      {plan.id === "pro" && <span className="plan-tag">For regular use</span>}
      <h3>{plan.name}</h3>
      <p className="muted">{plan.description}</p>
      <div className="plan-price">{free ? "Free" : "Contact us"}</div>
      <ul className="list">
        <li>{plan.reports_per_month} reports per month</li>
        <li>
          {plan.concurrent_runs} {plan.concurrent_runs === 1 ? "report" : "reports"} running at once
        </li>
        <li>
          {plan.api_keys} API {plan.api_keys === 1 ? "key" : "keys"}
        </li>
        <li>All four agents and every view</li>
      </ul>
      {free ? (
        <Link className="button primary block" to={signedIn ? "/app" : "/signup"}>
          {signedIn ? "Open dashboard" : "Start free"}
        </Link>
      ) : (
        <p className="muted small">Assigned by your workspace administrator.</p>
      )}
    </div>
  );
}

export default function Landing() {
  const { user } = useAuth();
  const [plans, setPlans] = useState([]);
  const start = user ? "/app" : "/signup";
  const startLabel = user ? "Open dashboard" : "Start free";
  const freePlan = plans.find((plan) => plan.id === "free");

  useEffect(() => {
    api.plans().then(setPlans).catch(() => setPlans([]));
  }, []);

  return (
    <div className="landing">
      {/* ------------------------------------------------------------ nav */}
      <header className="landing-nav">
        <div className="container">
          <Brand />
          <nav aria-label="Sections">
            <a href="#how">How it works</a>
            <a href="#agents">Agents</a>
            <a href="#report">Reports</a>
            <a href="#security">Security</a>
            <a href="#pricing">Plans</a>
            <a href="#faq">FAQ</a>
          </nav>
          <div className="row night">
            {user ? (
              <Link className="button primary" to="/app">
                Open dashboard
              </Link>
            ) : (
              <>
                <Link className="button ghost" to="/login">
                  Sign in
                </Link>
                <Link className="button primary" to="/signup">
                  Get started
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ----------------------------------------------------------- hero */}
      <section className="hero night">
        <div className="container">
          <span className="eyebrow">
            <b>Four agents.</b> One sourced report.
          </span>
          <h1>
            Research a business idea, with sources <em>you can check.</em>
          </h1>
          <p className="lead">
            NexusAI sends your question to four specialised AI agents. They research the market,
            competitors and customers, then return one structured report with evidence, stated
            assumptions and recommendations.
          </p>
          <div className="row center">
            <Link className="button primary large" to={start}>
              {startLabel}
            </Link>
            <a className="button large" href="#how">
              See how it works
            </a>
          </div>
          <p className="hero-note">Free plan · no payment details · your research stays private to you</p>
          <HeroPreview />
        </div>
      </section>

      <div className="strip">
        <div className="container">
          <span>Built on open standards:</span>
          <span>
            <strong>A2A</strong> for agent-to-agent delegation
          </span>
          <span>
            <strong>MCP</strong> for tool access
          </span>
          <span>
            <strong>Your choice of model</strong>: Groq, Gemini or OpenAI
          </span>
        </div>
      </div>

      {/* -------------------------------------------------------- problem */}
      <section className="section">
        <div className="container">
          <div className="section-head">
            <span className="kicker">The problem</span>
            <h2>Good decisions need research. Research is slow, and fast answers are hard to trust.</h2>
          </div>
          <div className="cards-3">
            {PROBLEMS.map((item) => (
              <div className="tile" key={item.title}>
                <span className="tile-icon bad">{item.icon}</span>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* --------------------------------------------------- how it works */}
      <section id="how" className="section tint">
        <div className="container">
          <div className="section-head">
            <span className="kicker">How it works</span>
            <h2>From a question to a report in four steps</h2>
            <p>You write one paragraph. The orchestrator plans, delegates and assembles the rest.</p>
          </div>
          <div className="flow">
            {STEPS.map((step) => (
              <div className="flow-step" key={step.title}>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            ))}
          </div>

          <div className="pipeline" role="img" aria-label="Your question goes to an orchestrator, which delegates to three research agents in parallel, then to a strategy agent, producing a report.">
            <div className="pipe-node">
              Your question
              <small>validated first</small>
            </div>
            <span className="pipe-arrow">→</span>
            <div className="pipe-group">
              <div className="pipe-node accent">Market research</div>
              <div className="pipe-node accent">Competitors</div>
              <div className="pipe-node accent">Customers</div>
            </div>
            <span className="pipe-arrow">→</span>
            <div className="pipe-node accent">
              Strategy
              <small>waits for all three</small>
            </div>
            <span className="pipe-arrow">→</span>
            <div className="pipe-node">
              Report
              <small>checked, then stored</small>
            </div>
            <p className="pipe-caption">
              The three research agents run at the same time. If one fails, the others still finish.
            </p>
          </div>
        </div>
      </section>

      {/* --------------------------------------------------------- agents */}
      <section id="agents" className="section">
        <div className="container">
          <div className="section-head">
            <span className="kicker">The agents</span>
            <h2>Four specialists, each with one job</h2>
            <p>
              A narrow job means a focused prompt, a strict output format, and only the tools that
              job needs.
            </p>
          </div>
          <div className="cards-2">
            {AGENTS.map((agent) => (
              <article className="agent-card" key={agent.name}>
                <header>
                  <span className="agent-glyph" style={{ background: agent.color }}>
                    {agent.glyph}
                  </span>
                  <h3>{agent.name}</h3>
                </header>
                <p>{agent.body}</p>
                <dl className="agent-meta">
                  <dt>Returns</dt>
                  <dd>{agent.returns}</dd>
                  <dt>Tools</dt>
                  <dd>{agent.tools}</dd>
                </dl>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* --------------------------------------------------------- report */}
      <section id="report" className="section tint">
        <div className="container split">
          <div>
            <span className="kicker">The report</span>
            <h2>Every claim says where it came from</h2>
            <p>
              A report is not a wall of text. Each finding is a separate statement with a label, a
              confidence level and its sources, so you can check the ones that matter.
            </p>
            <ul className="checks">
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Sourced fact or hypothesis</strong>
                  <span>A finding is a fact only if a retrieved page supports it. Otherwise it is labelled as the agent's reasoning.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Citations that cannot be invented</strong>
                  <span>A link that was not actually retrieved is removed, and the claim is downgraded.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Assumptions and limitations, stated</strong>
                  <span>What was taken for granted, and what could not be checked.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>A confidence rating with a reason</strong>
                  <span>Calculated from how much of the report is sourced, not from the model's opinion of itself.</span>
                </div>
              </li>
            </ul>
          </div>
          <div className="mock">
            <div className="mock-head">
              2. Competitors <span className="badge badge-info">3 findings</span>
            </div>
            <div className="mock-body">
              <div className="finding sourced">
                <div className="row">
                  <span className="badge badge-good">Sourced fact</span>
                  <span className="muted small">confidence high</span>
                </div>
                <p>Vendor A targets companies of 50 to 500 employees with payroll-first positioning.</p>
                <span className="small link">example.com/vendor-comparison</span>
              </div>
              <div className="finding hypothesis">
                <div className="row">
                  <span className="badge badge-warn">Hypothesis</span>
                  <span className="muted small">confidence low</span>
                </div>
                <p>None of the vendors found appears to offer a mature AI assistant.</p>
              </div>
              <div className="notice notice-warn small">
                Limitation: pricing pages for two vendors could not be retrieved.
              </div>
            </div>
            <div className="mock-sample">Sample · illustrative content</div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------- security */}
      <section id="security" className="section night">
        <div className="container split reverse">
          <div className="layers">
            {LAYERS.map(([title, body]) => (
              <div className="layer" key={title}>
                <div>
                  <strong>{title}</strong>
                  <span>{body}</span>
                </div>
              </div>
            ))}
          </div>
          <div>
            <span className="kicker">Security</span>
            <h2>Built on the assumption that the web will try to trick it</h2>
            <p>
              Agents read pages written by strangers. Some of those pages contain text aimed at AI
              systems: "ignore your instructions", "send this data here". No filter catches all of
              it, so NexusAI limits what a fooled agent could do.
            </p>
            <ul className="checks">
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Every incident is recorded</strong>
                  <span>Blocked requests, removed instructions and denied tool calls appear in your Security view.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Your account is yours</strong>
                  <span>Hashed passwords, sessions you can revoke, and research no other user can open.</span>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------- visibility */}
      <section className="section">
        <div className="container split">
          <div>
            <span className="kicker">Visibility</span>
            <h2>See exactly what the agents did</h2>
            <p>
              Nothing happens in a black box. Alongside each report you get the full record of how
              it was produced.
            </p>
            <ul className="checks">
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Every delegated task</strong>
                  <span>Which agent, its state from submitted to completed, how many attempts, how long.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Every tool call</strong>
                  <span>Searches run, pages fetched, what failed and what came from cache.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Time and cost</strong>
                  <span>Latency per agent and token usage per report.</span>
                </div>
              </li>
            </ul>
          </div>
          <div className="mock">
            <div className="mock-head">
              Agent activity <span className="badge badge-good">4 of 4 completed</span>
            </div>
            <div className="mock-body">
              {[
                ["Market research", "submitted → working → completed", "good", "completed"],
                ["Competitors", "attempt 2 · retried after timeout", "good", "completed"],
                ["Customers", "submitted → working → completed", "good", "completed"],
                ["Strategy", "waited for 3 upstream tasks", "good", "completed"],
              ].map(([name, detail, tone, state]) => (
                <div className="mock-row" key={name}>
                  <div>
                    <div className="strong">{name}</div>
                    <div className="mono">{detail}</div>
                  </div>
                  <span className={`badge badge-${tone}`}>{state}</span>
                </div>
              ))}
            </div>
            <div className="mock-sample">Sample · illustrative content</div>
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------- developers */}
      <section id="developers" className="section tint">
        <div className="container split reverse">
          <div className="code">
            <div className="code-head">Submit research from a script</div>
            <pre>
              <span className="c"># 1. Start a research request</span>
              {"\n"}
              <span className="k">curl</span> -X POST https://your-host/api/research \{"\n"}
              {"  "}-H <span className="s">"Authorization: Bearer nxs_your_key"</span> \{"\n"}
              {"  "}-H <span className="s">"Content-Type: application/json"</span> \{"\n"}
              {"  "}-d <span className="s">{"'{\"query\": \"Analyze the market for ...\"}'"}</span>
              {"\n\n"}
              <span className="c"># 2. Poll until status is "completed"</span>
              {"\n"}
              <span className="k">curl</span> https://your-host/api/research/{"{id}"} \{"\n"}
              {"  "}-H <span className="s">"Authorization: Bearer nxs_your_key"</span>
            </pre>
          </div>
          <div>
            <span className="kicker">For developers</span>
            <h2>Everything in the app is in the API</h2>
            <p>
              Create an API key and run research from a script, a notebook or a scheduled job. The
              report comes back as structured JSON with the same sections you see in the app.
            </p>
            <ul className="checks">
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Scoped keys</strong>
                  <span>A key can run and read research. It cannot change your password or create more keys.</span>
                </div>
              </li>
              <li>
                <span className="tick">✓</span>
                <div>
                  <strong>Safe to retry</strong>
                  <span>Submitting the same question twice returns the existing report instead of running it again.</span>
                </div>
              </li>
            </ul>
          </div>
        </div>
      </section>

      {/* -------------------------------------------------------- pricing */}
      <section id="pricing" className="section">
        <div className="container">
          <div className="section-head">
            <span className="kicker">Plans</span>
            <h2>The whole product on every plan</h2>
            <p>Plans differ only in how much you can run. Start free and move up when you need more room.</p>
          </div>
          <div className="plan-grid">
            {plans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} signedIn={Boolean(user)} />
            ))}
          </div>
          {plans.length > 0 && (
            <div className="table-wrap compare">
              <table>
                <thead>
                  <tr>
                    <th>Limit</th>
                    {plans.map((plan) => (
                      <th key={plan.id}>{plan.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Reports per month", "reports_per_month"],
                    ["Reports running at once", "concurrent_runs"],
                    ["Requests per minute", "submissions_per_minute"],
                    ["API keys", "api_keys"],
                  ].map(([label, key]) => (
                    <tr key={key}>
                      <td>{label}</td>
                      {plans.map((plan) => (
                        <td key={plan.id}>{plan[key]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* ------------------------------------------------------------ faq */}
      <section id="faq" className="section tint">
        <div className="container">
          <div className="section-head">
            <span className="kicker">Questions</span>
            <h2>Straight answers</h2>
          </div>
          <div className="faq">
            {FAQS.map(([question, answer]) => (
              <details key={question}>
                <summary>{question}</summary>
                <p>{answer}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------ cta */}
      <section className="cta night">
        <div className="container">
          <h2>Ask your first question. Check the sources yourself.</h2>
          <p className="lead">
            {freePlan
              ? `The Free plan includes ${freePlan.reports_per_month} reports a month and every feature.`
              : "The Free plan includes every feature."}
          </p>
          <Link className="button primary large" to={start}>
            {startLabel}
          </Link>
        </div>
      </section>

      {/* --------------------------------------------------------- footer */}
      <footer className="landing-footer night">
        <div className="container">
          <div className="footer-grid">
            <div>
              <Brand />
              <p className="muted" style={{ marginTop: 12, maxWidth: 320 }}>
                Multi-agent business research with evidence you can check.
              </p>
            </div>
            <div>
              <h5>Product</h5>
              <ul>
                <li><a href="#how">How it works</a></li>
                <li><a href="#agents">Agents</a></li>
                <li><a href="#report">Reports</a></li>
                <li><a href="#pricing">Plans</a></li>
              </ul>
            </div>
            <div>
              <h5>Trust</h5>
              <ul>
                <li><a href="#security">Security</a></li>
                <li><a href="#faq">FAQ</a></li>
              </ul>
            </div>
            <div>
              <h5>Get started</h5>
              <ul>
                <li><Link to="/signup">Create an account</Link></li>
                <li><Link to="/login">Sign in</Link></li>
                <li><a href="#developers">API</a></li>
              </ul>
            </div>
          </div>
          <p className="footer-note">
            NexusAI is decision support. Reports should be verified before you act on them.
          </p>
        </div>
      </footer>
    </div>
  );
}
