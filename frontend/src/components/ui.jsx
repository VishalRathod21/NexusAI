// Small shared building blocks.
//
// Report text originates from the web. React escapes everything rendered as
// text, so nothing retrieved can inject markup. URLs become links only when
// they are plain http(s).

const TONES = {
  completed: "good",
  ok: "good",
  high: "bad",
  failed: "bad",
  unavailable: "bad",
  invalid_output: "bad",
  running: "info",
  planning: "info",
  approved: "info",
  awaiting_approval: "warn",
  medium: "warn",
  low: "muted",
  info: "muted",
};

export function Badge({ children, tone }) {
  const resolved = tone || TONES[String(children).toLowerCase()] || "muted";
  return <span className={`badge badge-${resolved}`}>{children}</span>;
}

export function Card({ title, action, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Notice({ tone = "info", children }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

export function Empty({ children }) {
  return <p className="empty">{children}</p>;
}

export function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  );
}

export function SafeLink({ url }) {
  if (!/^https?:\/\//i.test(url || "")) return <span className="mono">{url}</span>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="link">
      {url}
    </a>
  );
}

export function List({ items }) {
  if (!items?.length) return null;
  return (
    <ul className="list">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

// columns: [{ key, label, render?, className? }]
export function Table({ columns, rows, empty = "Nothing to show." }) {
  if (!rows?.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key} className={column.className}>
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const formatMs = (ms) =>
  ms == null ? "n/a" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
