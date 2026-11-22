// The research workspace: history on the left, tabs on the right.

import { useCallback, useEffect, useState } from "react";
import { ACTIVE_STATUSES, api } from "../api.js";
import { useAuth } from "../auth.jsx";
import NewResearch from "../components/NewResearch.jsx";
import { Activity, Agents, Metrics, Security } from "../components/Panels.jsx";
import Report from "../components/Report.jsx";
import { Badge, Notice } from "../components/ui.jsx";

const TABS = [
  { id: "new", label: "New research" },
  { id: "report", label: "Report" },
  { id: "activity", label: "Agent activity" },
  { id: "agents", label: "Agents" },
  { id: "security", label: "Security" },
  { id: "metrics", label: "Metrics" },
];
const POLL_MS = 1500;

export default function Dashboard() {
  const { refresh: refreshProfile, clear } = useAuth();
  const [tab, setTab] = useState("new");
  const [health, setHealth] = useState(null);
  const [history, setHistory] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [request, setRequest] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0); // bump to restart polling

  // A 401 anywhere means the session ended: drop to the sign-in page.
  const guard = useCallback(
    (failure) => {
      if (failure?.status === 401) clear();
      return failure;
    },
    [clear]
  );

  const refreshHistory = useCallback(async () => {
    try {
      const rows = await api.listResearch();
      setHistory(rows);
      setSelectedId((current) => current || rows[0]?.id || null);
    } catch (failure) {
      guard(failure);
    }
  }, [guard]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    refreshHistory();
  }, [refreshHistory]);

  // Load the selected request, and keep polling while it is still running.
  useEffect(() => {
    if (!selectedId) {
      setRequest(null);
      return undefined;
    }
    let cancelled = false;
    let timer;
    async function load() {
      try {
        const data = await api.getResearch(selectedId);
        if (cancelled) return;
        setRequest(data);
        setLoadError("");
        if (ACTIVE_STATUSES.includes(data.status)) {
          timer = setTimeout(load, POLL_MS);
        } else {
          refreshHistory();
          refreshProfile(); // usage numbers may have changed
        }
      } catch (failure) {
        if (!cancelled) setLoadError(guard(failure).message);
      }
    }
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [selectedId, reload, refreshHistory, refreshProfile, guard]);

  async function submit(payload) {
    try {
      const created = await api.createResearch(payload);
      setRequest(created);
      setSelectedId(created.id);
      setReload((count) => count + 1);
      await Promise.all([refreshHistory(), refreshProfile()]);
      return created;
    } catch (failure) {
      throw guard(failure);
    }
  }

  async function approve(id) {
    try {
      const updated = await api.approve(id);
      setRequest(updated);
      setReload((count) => count + 1);
      await refreshHistory();
    } catch (failure) {
      throw guard(failure);
    }
  }

  const report = request?.report || null;
  const refreshKey = `${request?.id}:${request?.status}`;

  return (
    <div className="workspace">
      <aside className="sidebar">
        <button className="button primary block" onClick={() => setTab("new")}>
          + New research
        </button>
        <div className="status">
          {health ? (
            <>
              <div className="row">
                <span className="dot dot-good" /> System online
              </div>
              <dl>
                <dt>Agents</dt>
                <dd>{health.orchestration_mode === "a2a" ? "A2A services" : "in-process"}</dd>
                <dt>Model</dt>
                <dd>{health.llm}</dd>
              </dl>
            </>
          ) : (
            <div className="row">
              <span className="dot dot-bad" /> System unreachable
            </div>
          )}
        </div>

        <div className="history">
          <div className="row between">
            <h4>Your research</h4>
            <button className="button ghost small" onClick={refreshHistory}>
              Refresh
            </button>
          </div>
          {history.length === 0 && (
            <p className="muted small">
              Nothing yet. Your questions and reports will be listed here.
            </p>
          )}
          {history.map((row) => (
            <button
              key={row.id}
              className={`history-item ${row.id === selectedId ? "active" : ""}`}
              onClick={() => setSelectedId(row.id)}
            >
              <Badge>{row.status}</Badge>
              <span className="history-query">{row.query}</span>
            </button>
          ))}
        </div>
      </aside>

      <main className="main">
        <nav className="tabs" role="tablist">
          {TABS.map((item) => (
            <button
              key={item.id}
              role="tab"
              aria-selected={tab === item.id}
              className={`tab ${tab === item.id ? "active" : ""}`}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="content">
          {loadError && <Notice tone="bad">{loadError}</Notice>}
          {tab === "new" && (
            <NewResearch
              request={request}
              onSubmit={submit}
              onApprove={approve}
              onOpenReport={() => setTab("report")}
            />
          )}
          {tab === "report" && <Report report={report} />}
          {tab === "activity" && <Activity request={request} />}
          {tab === "agents" && <Agents />}
          {tab === "security" && <Security refreshKey={refreshKey} />}
          {tab === "metrics" && <Metrics report={report} refreshKey={refreshKey} />}
        </div>
      </main>
    </div>
  );
}
