// Sign in and sign up share one form, beside a panel that says what you get.

import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { Brand } from "../components/Shell.jsx";
import { Notice } from "../components/ui.jsx";

const MIN_PASSWORD = 10;
const POINTS = [
  ["Four agents on every question", "Market, competitors, customers and strategy, researched in parallel."],
  ["Sources on every finding", "Each claim is labelled as a sourced fact or a hypothesis."],
  ["Private to your account", "No other user can open your research or reports."],
];

export default function AuthPage({ mode }) {
  const isSignup = mode === "signup";
  const { user, loading, login, signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const destination = location.state?.from || "/app";
  if (!loading && user) return <Navigate to={destination} replace />;

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (isSignup && password.length < MIN_PASSWORD) {
      setError(`Use a password of at least ${MIN_PASSWORD} characters.`);
      return;
    }
    setBusy(true);
    try {
      if (isSignup) await signup(name, email, password);
      else await login(email, password);
      navigate(destination, { replace: true });
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <aside className="auth-aside night">
        <Brand />
        <div>
          <h2>Research a business idea, with sources you can check.</h2>
          <ul>
            {POINTS.map(([title, body]) => (
              <li key={title}>
                <span className="tick">✓</span>
                <div>
                  <strong>{title}</strong>
                  {body}
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="muted small">Decision support only. Verify reports before acting on them.</p>
      </aside>

      <main className="auth-main">
        <div className="auth-card">
          <h1>{isSignup ? "Create your account" : "Welcome back"}</h1>
          <p className="muted">
            {isSignup
              ? "Start on the Free plan. No payment details needed."
              : "Sign in to your research workspace."}
          </p>

          <form onSubmit={submit} className="stack">
            {isSignup && (
              <label className="field">
                <span>Name</span>
                <input
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="name"
                  maxLength={120}
                />
              </label>
            )}
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={isSignup ? "new-password" : "current-password"}
                required
              />
              {isSignup && <small className="muted">At least {MIN_PASSWORD} characters.</small>}
            </label>
            {error && <Notice tone="bad">{error}</Notice>}
            <button type="submit" className="button primary large block" disabled={busy}>
              {busy ? "Please wait…" : isSignup ? "Create account" : "Sign in"}
            </button>
          </form>

          <p className="muted small">
            {isSignup ? (
              <>
                Already have an account? <Link to="/login">Sign in</Link>
              </>
            ) : (
              <>
                New here? <Link to="/signup">Create an account</Link>
              </>
            )}
            {" · "}
            <Link to="/">Back to home</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
