import { useState } from "react";
import { ACTIVE_STATUSES, skillLabel } from "../api.js";
import { Badge, Card, List, Notice, formatMs } from "./ui.jsx";

const EXAMPLES = [
  "I want to launch an AI-powered HR SaaS platform in India. Analyze the market, identify competitors, understand customer demand, and recommend a business strategy.",
  "We plan to build payroll and compliance software for small businesses in India. Who are the competitors and what do customers struggle with?",
  "Evaluate the opportunity for an AI assistant that answers employee HR policy questions for mid-sized companies.",
];
const RESEARCH_SKILLS = ["market_research", "competitor_analysis", "customer_insights"];
const MAX_LENGTH = 2000;

/** Where a request is in its life, derived from real task records. */
function RunProgress({ request }) {
  const tasks = request.tasks || [];
  const bySkill = Object.fromEntries(tasks.map((task) => [task.skill, task]));
  const status = request.status;
  const running = status === "running";
  const finished = status === "completed";
  const failed = status === "failed";

  const researchDone = RESEARCH_SKILLS.filter((skill) => bySkill[skill]).length;
  const strategy = bySkill.business_strategy;

  const planState = status === "planning" ? "active" : "done";
  const researchState =
    researchDone === RESEARCH_SKILLS.length ? "done" : running ? "active" : failed ? "failed" : "todo";
  const strategyState = strategy
    ? strategy.state === "completed"
      ? "done"
      : "failed"
    : running && researchDone === RESEARCH_SKILLS.length
      ? "active"
      : failed
        ? "failed"
        : "todo";
  const reportState = finished ? "done" : failed ? "failed" : "todo";

  const icon = (state) => (state === "done" ? "✓" : state === "failed" ? "!" : "");
  const agentState = (skill) => {
    const task = bySkill[skill];
    if (task) return task.state === "completed" ? "done" : task.state;
    return running ? "working…" : "waiting";
  };

  return (
    <div className="progress" aria-label="Run progress">
      <div className={`progress-step ${planState}`}>
        <div className="step-title">
          <span className="step-icon">{icon(planState)}</span> Plan
        </div>
        <div className="step-detail">
          {status === "planning"
            ? "Splitting the question into tasks"
            : status === "awaiting_approval"
              ? "Ready, waiting for your approval"
              : "Tasks defined"}
        </div>
      </div>

      <div className={`progress-step ${researchState}`}>
        <div className="step-title">
          <span className="step-icon">{icon(researchState)}</span> Research
        </div>
        <div className="agent-lines">
          {RESEARCH_SKILLS.map((skill) => (
            <div className="agent-line" key={skill}>
              <span>{skillLabel(skill)}</span>
              <span className="muted">{agentState(skill)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className={`progress-step ${strategyState}`}>
        <div className="step-title">
          <span className="step-icon">{icon(strategyState)}</span> Strategy
        </div>
        <div className="step-detail">
          {strategy
            ? strategy.state === "completed"
              ? `Synthesised in ${formatMs(strategy.latency_ms)}`
              : "Could not be completed"
            : strategyState === "active"
              ? "Synthesising the findings"
              : "Runs after the three research tasks"}
        </div>
      </div>

      <div className={`progress-step ${reportState}`}>
        <div className="step-title">
          <span className="step-icon">{icon(reportState)}</span> Report
        </div>
        <div className="step-detail">
          {finished ? "Checked and saved" : failed ? "Not produced" : "Assembled and validated last"}
        </div>
      </div>
    </div>
  );
}

export default function NewResearch({ request, onSubmit, onApprove, onOpenReport }) {
  const [query, setQuery] = useState("");
  const [requireApproval, setRequireApproval] = useState(false);
  const [forceNew, setForceNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const created = await onSubmit({
        query,
        require_approval: requireApproval,
        force_new: forceNew,
      });
      setQuery(""); // the request now lives in the card below
      if (created.duplicate) {
        setInfo("You already asked this. Showing the existing result. Tick \"Run again\" to repeat it.");
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    setError("");
    try {
      await onApprove(request.id);
    } catch (failure) {
      setError(failure.message);
    }
  }

  const plan = request?.plan;
  const active = request && ACTIVE_STATUSES.includes(request.status);

  return (
    <div className="stack">
      <Card className="composer">
        <div>
          <h2>What do you want to research?</h2>
          <p className="muted">
            Describe the business idea and what you need to know. Four agents will research it and
            return one report.
          </p>
        </div>
        <form onSubmit={submit} className="stack">
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={MAX_LENGTH}
            aria-label="Research question"
            placeholder="e.g. I want to launch a subscription meal-kit service in Bengaluru. Analyze the market, competitors and customer demand."
          />
          {!query && (
            <div className="examples">
              <span className="muted small">Or start from an example:</span>
              <div className="chips">
                {EXAMPLES.map((example) => (
                  <button type="button" className="chip" key={example} onClick={() => setQuery(example)}>
                    {example.length > 78 ? `${example.slice(0, 78)}…` : example}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="row wrap">
            <label className="check">
              <input
                type="checkbox"
                checked={requireApproval}
                onChange={(event) => setRequireApproval(event.target.checked)}
              />
              Let me review the plan before it runs
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={forceNew}
                onChange={(event) => setForceNew(event.target.checked)}
              />
              Run again even if I already asked this
            </label>
          </div>
          <div className="row">
            <button type="submit" className="button primary" disabled={busy || query.trim().length < 15}>
              {busy ? "Submitting…" : "Start research"}
            </button>
            <span className="muted small">
              {query.length} / {MAX_LENGTH}
            </span>
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
          {info && <Notice>{info}</Notice>}
        </form>
      </Card>

      {request && (
        <Card title={active ? "In progress" : "Selected request"} action={<Badge>{request.status}</Badge>}>
          <p className="quote">{request.query}</p>

          <RunProgress request={request} />

          {request.status === "awaiting_approval" && (
            <Notice tone="warn">
              Nothing has been delegated yet. Approve the plan to start the agents.
              <div className="notice-action">
                <button className="button primary" onClick={approve}>
                  Approve and run
                </button>
              </div>
            </Notice>
          )}
          {request.status === "failed" && <Notice tone="bad">Run failed: {request.error}</Notice>}
          {request.status === "completed" && (
            <Notice tone="good">
              Report ready.
              <div className="notice-action">
                <button className="button" onClick={onOpenReport}>
                  Open report
                </button>
              </div>
            </Notice>
          )}

          {plan && (
            <details className="details" open={request.status === "awaiting_approval"}>
              <summary>Research plan</summary>
              <p className="muted small" style={{ margin: "8px 0" }}>
                Topic: {plan.topic} · planner: {plan.planner}
              </p>
              <ol className="plan">
                {plan.tasks.map((task) => (
                  <li key={task.skill}>
                    <strong>{skillLabel(task.skill)}</strong>
                    {task.depends_on?.length > 0 && (
                      <span className="muted small"> (after {task.depends_on.map(skillLabel).join(", ")})</span>
                    )}
                    <div>{task.instruction}</div>
                    <List items={task.focus_questions} />
                  </li>
                ))}
              </ol>
            </details>
          )}
        </Card>
      )}
    </div>
  );
}
