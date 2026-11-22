// Account settings: plan and usage, API keys, password, delete account.

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { UsageMeter } from "../components/Shell.jsx";
import { Badge, Card, Empty, Notice, Stat, Table } from "../components/ui.jsx";

function PlanSection() {
  const { user, usage } = useAuth();
  if (!usage) return null;
  return (
    <Card title="Plan and usage" action={<Badge tone="info">{usage.plan_name}</Badge>}>
      <UsageMeter usage={usage} />
      <div className="stats">
        <Stat label="Reports left this month" value={usage.reports_remaining} />
        <Stat label="Running now" value={`${usage.active_runs} / ${usage.concurrent_runs_limit}`} />
        <Stat label="Requests per minute" value={usage.submissions_per_minute} />
        <Stat label="API keys allowed" value={usage.api_keys_limit} />
      </div>
      <p className="muted small">
        Usage resets on the first day of each month (UTC). Failed runs and repeat submissions of the
        same question do not count.{" "}
        {user.is_admin
          ? "As an administrator you can change plans on the Admin page."
          : "To change plan, contact your workspace administrator."}
      </p>
    </Card>
  );
}

function ApiKeys() {
  const [keys, setKeys] = useState([]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    api.listKeys().then(setKeys).catch((failure) => setError(failure.message));
  }, []);
  useEffect(load, [load]);

  async function create(event) {
    event.preventDefault();
    setError("");
    try {
      setCreated(await api.createKey(name || "API key"));
      setCopied(false);
      setName("");
      load();
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function remove(id) {
    setError("");
    try {
      await api.deleteKey(id);
      setCreated(null);
      load();
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Card title="API keys">
      <p className="muted small">
        Use a key to submit research from scripts:{" "}
        <span className="mono">Authorization: Bearer &lt;key&gt;</span>. A key can run research but
        cannot change account settings.
      </p>
      {created && (
        <Notice tone="good">
          <strong>Copy this key now.</strong> It will not be shown again.
          <pre className="key">{created.token}</pre>
          <button className="button small" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
        </Notice>
      )}
      <Table
        empty="You have no API keys."
        rows={keys}
        columns={[
          { key: "name", label: "Name", className: "strong" },
          { key: "prefix", label: "Key", className: "mono", render: (row) => `${row.prefix}…` },
          { key: "created_at", label: "Created", render: (row) => (row.created_at || "").slice(0, 10) },
          {
            key: "last_used_at",
            label: "Last used",
            render: (row) => (row.last_used_at ? row.last_used_at.slice(0, 16).replace("T", " ") : "never"),
          },
          {
            key: "actions",
            label: "",
            render: (row) => (
              <button className="button small danger" onClick={() => remove(row.id)}>
                Revoke
              </button>
            ),
          },
        ]}
      />
      <form onSubmit={create} className="row wrap">
        <input
          type="text"
          placeholder="Key name, e.g. Reporting script"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={80}
        />
        <button type="submit" className="button">
          Create key
        </button>
      </form>
      {error && <Notice tone="bad">{error}</Notice>}
    </Card>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setMessage(null);
    try {
      await api.changePassword({ current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setMessage({ tone: "good", text: "Password changed. Other devices have been signed out." });
    } catch (failure) {
      setMessage({ tone: "bad", text: failure.message });
    }
  }

  return (
    <Card title="Password">
      <form onSubmit={submit} className="stack narrow">
        <label className="field">
          <span>Current password</span>
          <input
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            autoComplete="new-password"
            required
          />
          <small className="muted">At least 10 characters.</small>
        </label>
        <div>
          <button type="submit" className="button">
            Change password
          </button>
        </div>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
      </form>
    </Card>
  );
}

function DangerZone() {
  const { clear } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function remove(event) {
    event.preventDefault();
    setError("");
    try {
      await api.deleteAccount(password);
      navigate("/"); // leave the protected page before clearing the session
      clear();
    } catch (failure) {
      setError(failure.message);
    }
  }

  return (
    <Card title="Delete account">
      <p className="muted small">
        Permanently deletes your account, all research and reports, API keys and security events.
        This cannot be undone.
      </p>
      {!open ? (
        <div>
          <button className="button danger" onClick={() => setOpen(true)}>
            Delete my account…
          </button>
        </div>
      ) : (
        <form onSubmit={remove} className="stack narrow">
          <label className="field">
            <span>Confirm with your password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <div className="row">
            <button type="submit" className="button danger solid">
              Permanently delete
            </button>
            <button type="button" className="button" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
        </form>
      )}
    </Card>
  );
}

export default function Account() {
  const { user } = useAuth();
  return (
    <div className="page stack">
      <div>
        <h1 className="page-title">Account</h1>
        <p className="muted">
          {user.name ? `${user.name} · ` : ""}
          {user.email}
          {user.is_admin && (
            <>
              {" "}
              <Badge tone="warn">administrator</Badge>
            </>
          )}
        </p>
      </div>
      <PlanSection />
      <ApiKeys />
      <PasswordSection />
      <DangerZone />
      {!user && <Empty>Not signed in.</Empty>}
    </div>
  );
}
