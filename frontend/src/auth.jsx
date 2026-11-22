// Who is signed in. One place loads the profile; every page reads it from here.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [profile, setProfile] = useState(null); // { user, usage } or null
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setProfile(await api.me());
    } catch {
      setProfile(null); // not signed in, or the session expired
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // These must keep the same identity between renders. Pages put them in
  // effect dependency lists; a new function each time would re-run the effect
  // and re-fetch in a loop.
  const login = useCallback(
    async (email, password) => setProfile(await api.login({ email, password })),
    []
  );
  const signup = useCallback(
    async (name, email, password) => setProfile(await api.signup({ name, email, password })),
    []
  );
  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setProfile(null);
    }
  }, []);
  const clear = useCallback(() => setProfile(null), []);

  const value = useMemo(
    () => ({
      user: profile?.user || null,
      usage: profile?.usage || null,
      loading,
      refresh,
      login,
      signup,
      logout,
      clear,
    }),
    [profile, loading, refresh, login, signup, logout, clear]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

/** Wrap a page that needs a signed-in user (and optionally an admin). */
export function Protected({ children, admin = false }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="center-screen muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (admin && !user.is_admin) return <Navigate to="/app" replace />;
  return children;
}
