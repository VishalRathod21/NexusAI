// Administrator view: every account, platform-wide metrics and security events.

import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { Badge, Card, Empty, Notice, Stat, Table, formatMs } from "../components/ui.jsx";

export default function Admin() {
  const { user: me, refresh: refreshProfile } = useAuth();
  const [users, setUsers] = useState(null);
  const [plans, setPlans] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [userRows, planRows, summary, eventRows] = await Promise.all([
        api.adminUsers(),
        api.plans(),
        api.metrics("all"),
        api.securityEvents("all"),
      ]);
      setUsers(userRows);
      setPlans(planRows);
      setMetrics(summary);
      setEvents(eventRows);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function update(id, changes) {
    setError("");
    try {
      await api.adminUpdateUser(id, changes);
      await load();
      if (id === me.id) await refreshProfile();
    } catch (failure) {
      setError(failure.message);
    }
  }

  const emailOf = (id) => users?.find((row) => row.id === id)?.email || "";
  const percent = (value) => (value == null ? "n/a" : `${Math.round(value * 100)}%`);

  return (
    <div className="page stack">
      <div>
        <h1 className="page-title">Admin</h1>
        <p className="muted">Accounts, platform usage and security events across all users.</p>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}

      <Card title="Platform">
        {metrics ? (
          <div className="stats">
            <Stat label="Accounts" value={users?.length ?? 0} />
            <Stat label="Reports" value={metrics.reports} />
            <Stat label="Task completion" value={percent(metrics.task_completion_rate)} />
            <Stat label="Average time" value={metrics.reports ? formatMs(metrics.avg_latency_ms) : "n/a"} />
            <Stat label="Degraded reports" value={metrics.degraded_reports ?? 0} />
            <Stat
              label="Avg cost / report"
              value={metrics.avg_cost_usd == null ? "n/a" : `$${metrics.avg_cost_usd.toFixed(4)}`}
              hint={metrics.avg_cost_usd == null ? "model prices not set" : undefined}
            />
          </div>
        ) : (
          <Empty>Loading…</Empty>
        )}
      </Card>

      <Card title="Accounts">
        <Table
          empty="No accounts."
          rows={users || []}
          columns={[
            {
              key: "email",
              label: "Account",
              render: (row) => (
                <>
                  <div className="strong">{row.email}</div>
                  <div className="muted small">{row.name}</div>
                </>
              ),
            },
            {
              key: "plan",
              label: "Plan",
              render: (row) => (
                <select
                  value={row.plan}
                  onChange={(event) => update(row.id, { plan: event.target.value })}
                  aria-label={`Plan for ${row.email}`}
                >
                  {plans.map((plan) => (
                    <option key={plan.id} value={plan.id}>
                      {plan.name}
                    </option>
                  ))}
                </select>
              ),
            },
            { key: "requests_total", label: "Requests" },
            { key: "created_at", label: "Joined", className: "nowrap", render: (row) => (row.created_at || "").slice(0, 10) },
            {
              key: "role",
              label: "Role",
              render: (row) => (
                <Badge tone={row.is_admin ? "warn" : "muted"}>{row.is_admin ? "admin" : "member"}</Badge>
              ),
            },
            {
              key: "status",
              label: "Status",
              render: (row) => (
                <Badge tone={row.is_active ? "good" : "bad"}>{row.is_active ? "active" : "disabled"}</Badge>
              ),
            },
            {
              key: "actions",
              label: "",
              render: (row) => (
                <div className="row">
                  <button className="button small" onClick={() => update(row.id, { is_admin: !row.is_admin })}>
                    {row.is_admin ? "Remove admin" : "Make admin"}
                  </button>
                  <button
                    className="button small danger"
                    onClick={() => update(row.id, { is_active: !row.is_active })}
                  >
                    {row.is_active ? "Disable" : "Enable"}
                  </button>
                </div>
              ),
            },
          ]}
        />
        <p className="muted small">
          Disabling an account signs it out everywhere. Billing is not connected: plans are assigned
          here.
        </p>
      </Card>

      <Card title="Security events (all accounts)">
        <Table
          empty="No security events recorded."
          rows={events}
          columns={[
            {
              key: "timestamp",
              label: "Time",
              className: "mono",
              render: (row) => row.timestamp.slice(0, 19).replace("T", " "),
            },
            { key: "severity", label: "Severity", render: (row) => <Badge>{row.severity}</Badge> },
            { key: "event_type", label: "Event", className: "strong" },
            { key: "agent", label: "Source" },
            { key: "user_id", label: "Account", render: (row) => emailOf(row.user_id) },
            {
              key: "detail",
              label: "Detail",
              render: (row) => (
                <details className="details inline">
                  <summary>view</summary>
                  <pre>{JSON.stringify(row.detail, null, 2)}</pre>
                </details>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
