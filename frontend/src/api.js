// Thin client for the NexusAI backend. The UI talks only to this API, never
// to agents or tools directly.
//
// Authentication is a session cookie the browser sends automatically. It is
// HttpOnly, so this code never sees or stores it. Every call carries a custom
// header that the backend requires on state-changing requests (CSRF defence).

const BASE = import.meta.env.VITE_API_BASE_URL || "";

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      credentials: "same-origin",
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "nexusai",
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiError("Cannot reach the server. Check that the backend is running.", 0, "network");
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const detail = body?.detail;
    const isObject = detail && typeof detail === "object" && !Array.isArray(detail);
    const message =
      (isObject && detail.message) ||
      (typeof detail === "string" && detail) ||
      (Array.isArray(detail) && "Please check the form and try again.") ||
      `Request failed (HTTP ${response.status})`;
    throw new ApiError(message, response.status, isObject ? detail.code : undefined);
  }
  return body;
}

const post = (path, payload) =>
  request(path, { method: "POST", body: payload === undefined ? undefined : JSON.stringify(payload) });

export const api = {
  health: () => request("/health"),
  plans: () => request("/api/plans"),

  // account
  me: () => request("/api/auth/me"),
  signup: (payload) => post("/api/auth/signup", payload),
  login: (payload) => post("/api/auth/login", payload),
  logout: () => post("/api/auth/logout"),
  changePassword: (payload) => post("/api/auth/password", payload),
  deleteAccount: (password) =>
    request("/api/account", { method: "DELETE", body: JSON.stringify({ password }) }),
  listKeys: () => request("/api/keys"),
  createKey: (name) => post("/api/keys", { name }),
  deleteKey: (id) => request(`/api/keys/${id}`, { method: "DELETE" }),

  // research
  listResearch: () => request("/api/research?limit=30"),
  getResearch: (id) => request(`/api/research/${id}`),
  createResearch: (payload) => post("/api/research", payload),
  approve: (id) => post(`/api/research/${id}/approve`),
  agents: () => request("/api/agents"),
  securityEvents: (scope = "mine") => request(`/api/security/events?limit=200&scope=${scope}`),
  metrics: (scope = "mine") => request(`/api/metrics?scope=${scope}`),

  // admin
  adminUsers: () => request("/api/admin/users"),
  adminUpdateUser: (id, changes) =>
    request(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(changes) }),
};

export const ACTIVE_STATUSES = ["planning", "approved", "running"];

export const SKILL_LABELS = {
  market_research: "Market research",
  competitor_analysis: "Competitors",
  customer_insights: "Customers",
  business_strategy: "Strategy",
};

export const skillLabel = (skill) => SKILL_LABELS[skill] || skill;
