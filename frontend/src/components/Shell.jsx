// The signed-in frame: top bar with navigation, monthly usage and the account menu.

import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { Badge } from "./ui.jsx";

export function Brand({ to = "/" }) {
  return (
    <Link to={to} className="brand">
      <span className="brand-mark" aria-hidden="true">
        N
      </span>
      <span className="brand-name">NexusAI</span>
    </Link>
  );
}

export function UsageMeter({ usage, compact = false }) {
  if (!usage) return null;
  const percent = Math.min(100, Math.round((usage.reports_used / usage.reports_limit) * 100));
  const tone = percent >= 100 ? "bad" : percent >= 80 ? "warn" : "";
  return (
    <div className={`usage ${compact ? "compact" : ""}`} title="Reports used this month">
      <div className="usage-text">
        <span>
          {usage.reports_used} / {usage.reports_limit} reports
        </span>
        {!compact && <span className="muted">this month</span>}
      </div>
      <div className="usage-track">
        <div className={`usage-bar ${tone}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export default function Shell({ children, wide = false }) {
  const { user, usage, logout } = useAuth();
  const navigate = useNavigate();

  async function signOut() {
    // Leave the protected page first. Clearing the session while still on it
    // would trigger the "not signed in" redirect to /login instead.
    navigate("/");
    await logout();
  }

  return (
    <div className="shell">
      <header className="topbar">
        <Brand to="/app" />
        <nav className="topnav">
          <NavLink to="/app">Dashboard</NavLink>
          <NavLink to="/account">Account</NavLink>
          {user.is_admin && <NavLink to="/admin">Admin</NavLink>}
        </nav>
        <div className="topbar-right">
          <UsageMeter usage={usage} compact />
          <Badge tone="info">{usage?.plan_name || user.plan}</Badge>
          <span className="avatar" aria-hidden="true">
            {(user.name || user.email).charAt(0)}
          </span>
          <span className="user-email" title={user.email}>
            {user.name || user.email}
          </span>
          <button className="button small" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <div className={wide ? "shell-body wide" : "shell-body"}>{children}</div>
    </div>
  );
}
