// Agent activity, Agents, Security and Metrics tabs.

import { useEffect, useState } from "react";
import { api, skillLabel } from "../api.js";
import { Badge, Card, Empty, Notice, Stat, Table, formatMs } from "./ui.jsx";

/** Load data when the tab mounts (and whenever `dependency` changes). */
function useLoad(loader, dependency) {
  const [state, setState] = useState({ data: null, error: "", loading: true });
  useEffect(() => {
    let cancelled = false;
    setState((previous) => ({ ...previous, loading: true }));
    loader()
      .then((data) => !cancelled && setState({ data, error: "", loading: false }))
      .catch((failure) => !cancelled && setState({ data: null, error: failure.message, loading: false }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dependency]);
  return state;
}

// ---------------------------------------------------------------- activity
export function Activity({ request }) {
  if (!request) return <Empty>Pick a request to see what the agents did.</Empty>;
  const report = request.report || {};
  const tasks = report.tasks?.length ? report.tasks : request.tasks || [];
  const spans = [...(report.spans || [])].sort((a, b) => a.started_at - b.started_at);
  const origin = spans.length ? spans[0].started_at : 0;
  const total = spans.reduce(
    (max, span) => Math.max(max, (span.started_at - origin) * 1000 + span.duration_ms),
    1
  );

  return (
    <div className="stack">
      <Card title="Delegated tasks (A2A)">
        <Table
          empty="No tasks have been delegated yet."
          rows={tasks}
          columns={[
            { key: "skill", label: "Skill", render: (row) => skillLabel(row.skill), className: "strong" },
            { key: "agent_name", label: "Agent" },
            { key: "transport", label: "Transport" },
            { key: "state", label: "State", render: (row) => <Badge>{row.state}</Badge> },
            {
              key: "lifecycle",
              label: "Lifecycle",
              render: (row) => (row.lifecycle || []).join(" → "),
            },
            { key: "attempts", label: "Attempts" },
            { key: "latency_ms", label: "Latency", render: (row) => formatMs(row.latency_ms) },
            { key: "a2a_task_id", label: "Task id", className: "mono", render: (row) => (row.a2a_task_id || "").slice(0, 8) },
            { key: "error", label: "Error", render: (row) => row.error || "" },
          ]}
        />
      </Card>

      <Card title="MCP tool calls">
        <Table
          empty="No tool calls recorded."
          rows={report.tool_calls}
          columns={[
            { key: "agent", label: "Agent" },
            { key: "server", label: "Server" },
            { key: "tool", label: "Tool", className: "mono" },
            {
              key: "ok",
              label: "Result",
              render: (row) => <Badge tone={row.ok ? "good" : "bad"}>{row.ok ? "ok" : "failed"}</Badge>,
            },
            { key: "cached", label: "Cached", render: (row) => (row.cached ? "yes" : "") },
            { key: "attempts", label: "Attempts" },
            { key: "latency_ms", label: "Latency", render: (row) => formatMs(row.latency_ms) },
            {
              key: "arguments",
              label: "Arguments",
              className: "mono clip",
              render: (row) => JSON.stringify(row.arguments),
            },
            { key: "error", label: "Error", render: (row) => row.error || "" },
          ]}
        />
      </Card>

      {spans.length > 0 && (
        <Card title="Trace (orchestrator process)">
          <p className="muted small">
            In A2A mode, work inside the agent services is covered by the tool-call table above.
          </p>
          <div className="timeline">
            {spans.map((span, index) => {
              const start = ((span.started_at - origin) * 1000 * 100) / total;
              const width = Math.max((span.duration_ms * 100) / total, 0.6);
              return (
                <div className="timeline-row" key={index}>
                  <span className="timeline-name mono" title={span.name}>
                    {span.name}
                  </span>
                  <div className="timeline-track">
                    <div
                      className={`timeline-bar ${span.status === "ok" ? "" : "error"}`}
                      style={{ marginLeft: `${start}%`, width: `${width}%` }}
                    />
                  </div>
                  <span className="timeline-ms">{formatMs(span.duration_ms)}</span>
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ agents
export function Agents() {
  const { data, error, loading } = useLoad(api.agents);
  if (loading) return <Empty>Discovering agents…</Empty>;
  if (error) return <Notice tone="bad">{error}</Notice>;
  const unreachable = Object.entries(data.unreachable || {});

  return (
    <div className="stack">
      <p className="muted">
        Discovered by fetching each agent's Agent Card from{" "}
        <span className="mono">/.well-known/agent-card.json</span>.
      </p>
      {unreachable.map(([url, reason]) => (
        <Notice tone="bad" key={url}>
          Unreachable: {url}
          <div className="small">{reason}</div>
        </Notice>
      ))}
      <div className="grid">
        {(data.agents || []).map((agent) => (
          <Card key={agent.base_url + agent.name} title={agent.name} action={<Badge tone="good">online</Badge>}>
            <p>{agent.description}</p>
            <div className="chips">
              {agent.skills.map((skill) => (
                <span className="chip mono" key={skill}>
                  {skill}
                </span>
              ))}
            </div>
            <p className="muted small mono">{agent.base_url}</p>
            {agent.card && (
              <details className="details">
                <summary>Agent Card</summary>
                <pre>{JSON.stringify(agent.card, null, 2)}</pre>
              </details>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- security
export function Security({ refreshKey }) {
  const { data, error, loading } = useLoad(api.securityEvents, refreshKey);
  if (loading && !data) return <Empty>Loading security events…</Empty>;
  if (error) return <Notice tone="bad">{error}</Notice>;

  return (
    <Card title="Security events">
      <Table
        empty="No security events recorded."
        rows={data}
        columns={[
          { key: "timestamp", label: "Time", className: "mono", render: (row) => row.timestamp.slice(0, 19).replace("T", " ") },
          { key: "severity", label: "Severity", render: (row) => <Badge>{row.severity}</Badge> },
          { key: "event_type", label: "Event", className: "strong" },
          { key: "agent", label: "Agent" },
          {
            key: "detail",
            label: "Detail",
            render: (row) => (
              <details className="details inline">
                <summary>{row.detail?.action || row.detail?.origin || "view"}</summary>
                <pre>{JSON.stringify(row.detail, null, 2)}</pre>
              </details>
            ),
          },
          { key: "request_id", label: "Request", className: "mono", render: (row) => (row.request_id || "").slice(0, 8) },
        ]}
      />
    </Card>
  );
}

// ----------------------------------------------------------------- metrics
export function Metrics({ report, refreshKey }) {
  const { data, error, loading } = useLoad(api.metrics, refreshKey);
  if (loading && !data) return <Empty>Loading metrics…</Empty>;
  if (error) return <Notice tone="bad">{error}</Notice>;
  const run = report?.metrics;
  const percent = (value) => (value == null ? "n/a" : `${Math.round(value * 100)}%`);
  const cost = (value) => (value == null ? "n/a" : `$${value.toFixed(4)}`);
  const costHint = (value) => (value == null ? "model prices not set" : undefined);

  return (
    <div className="stack">
      <Card title="Across all stored reports">
        {data.reports ? (
          <div className="stats">
            <Stat label="Reports" value={data.reports} />
            <Stat label="Average time" value={formatMs(data.avg_latency_ms)} hint={`max ${formatMs(data.max_latency_ms)}`} />
            <Stat label="Task completion" value={percent(data.task_completion_rate)} />
            <Stat label="Avg cost / report" value={cost(data.avg_cost_usd)} hint={costHint(data.avg_cost_usd)} />
            <Stat label="Tool calls" value={data.tool_calls_total} hint={`${data.tool_cache_hits} from cache`} />
            <Stat label="Tokens" value={data.total_input_tokens + data.total_output_tokens} />
            <Stat label="Degraded reports" value={data.degraded_reports} />
          </div>
        ) : (
          <Empty>No completed reports yet.</Empty>
        )}
        {data.requests_by_status && (
          <div className="chips">
            {Object.entries(data.requests_by_status).map(([status, count]) => (
              <span className="chip" key={status}>
                <Badge>{status}</Badge> {count}
              </span>
            ))}
          </div>
        )}
      </Card>

      {run && (
        <Card title="Selected report">
          <div className="stats">
            <Stat label="Total time" value={formatMs(run.total_latency_ms)} />
            <Stat label="Tasks" value={`${run.tasks_completed}/${run.tasks_total}`} hint={`${run.task_attempts} attempts`} />
            <Stat label="Tool calls" value={run.tool_calls_total} hint={`${run.tool_calls_failed} failed · ${run.tool_cache_hits} cached`} />
            <Stat label="LLM calls" value={run.llm_calls} hint={run.llm} />
            <Stat label="Tokens" value={run.input_tokens + run.output_tokens} hint={`${run.input_tokens} in · ${run.output_tokens} out`} />
            <Stat label="Cost" value={cost(run.estimated_cost_usd)} hint={costHint(run.estimated_cost_usd)} />
          </div>
          <h4>Latency per agent</h4>
          <Table
            rows={Object.entries(run.per_agent_latency_ms || {}).map(([skill, ms]) => ({ skill, ms }))}
            columns={[
              { key: "skill", label: "Agent", render: (row) => skillLabel(row.skill) },
              { key: "ms", label: "Latency", render: (row) => formatMs(row.ms) },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
