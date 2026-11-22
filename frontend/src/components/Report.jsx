import { skillLabel } from "../api.js";
import { Badge, Card, Empty, List, Notice, SafeLink, Stat, Table, formatMs } from "./ui.jsx";

const SECTIONS = [
  ["market", "Market"],
  ["competitors", "Competitors"],
  ["customers", "Customers"],
  ["strategy", "Strategy"],
  ["sources", "Sources"],
  ["caveats", "Assumptions & limits"],
];

function Findings({ items }) {
  if (!items?.length) return null;
  return (
    <div className="findings">
      {items.map((finding, index) => {
        const sourced = finding.kind === "sourced_fact";
        return (
          <article className={`finding ${sourced ? "sourced" : "hypothesis"}`} key={index}>
            <div className="row">
              <Badge tone={sourced ? "good" : "warn"}>{sourced ? "Sourced fact" : "Hypothesis"}</Badge>
              <span className="muted small">confidence {finding.confidence}</span>
            </div>
            <p>{finding.claim}</p>
            {finding.source_urls?.map((url) => (
              <div className="small" key={url}>
                <SafeLink url={url} />
              </div>
            ))}
          </article>
        );
      })}
    </div>
  );
}

function Section({ id, number, title, analysis, children }) {
  return (
    <div id={`report-${id}`} className="report-section">
      <Card title={`${number}. ${title}`}>
        {analysis ? (
          <>
            <p>{analysis.summary}</p>
            {children}
            <Findings items={analysis.findings} />
          </>
        ) : (
          <Notice tone="warn">This section is missing: the agent did not return a usable result.</Notice>
        )}
      </Card>
    </div>
  );
}

function downloadJson(report) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `nexusai-report-${report.request_id.slice(0, 8)}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function Report({ report }) {
  if (!report) {
    return (
      <Card>
        <h3>No report selected</h3>
        <Empty>No report yet. Ask a question on the New research tab, or pick one from your history.</Empty>
      </Card>
    );
  }
  const metrics = report.metrics || {};
  const { market, competitors, customers, strategy } = report;
  const offline = String(metrics.llm || "").startsWith("offline");
  const confidenceTone = { high: "good", medium: "info", low: "warn" }[report.confidence] || "muted";

  return (
    <div className="report-layout">
      <div className="stack">
        <Card className="report-head">
          <div className="row between">
            <Badge tone={confidenceTone}>Confidence: {report.confidence}</Badge>
            <div className="row report-actions">
              <button className="button small" onClick={() => window.print()}>
                Print / save as PDF
              </button>
              <button className="button small" onClick={() => downloadJson(report)}>
                Download JSON
              </button>
            </div>
          </div>
          <h2 className="report-title">{report.topic || "Research report"}</h2>
          <p className="muted">{report.query}</p>
          <div className="stats">
            <Stat label="Sources" value={report.sources?.length ?? 0} />
            <Stat label="Agent tasks" value={`${metrics.tasks_completed ?? 0}/${metrics.tasks_total ?? 0}`} />
            <Stat label="Tool calls" value={metrics.tool_calls_total ?? 0} />
            <Stat label="Time" value={formatMs(metrics.total_latency_ms)} />
          </div>
          <p className="muted small">Why this confidence: {report.confidence_reason}</p>
          {report.degraded && (
            <Notice tone="warn">
              Degraded report.
              {report.missing_sections?.length > 0 &&
                ` Missing: ${report.missing_sections.map(skillLabel).join(", ")}.`}{" "}
              See Limitations below.
            </Notice>
          )}
          {offline && (
            <Notice>
              Offline mode: this workspace is running without a language model, so findings are
              sentences extracted from sources rather than a synthesised analysis.
            </Notice>
          )}
        </Card>

        <Section id="market" number={1} title="Market research and trends" analysis={market}>
          {market?.trends?.length > 0 && (
            <>
              <h4>Trends</h4>
              <List items={market.trends} />
            </>
          )}
          {market?.demand_signals?.length > 0 && (
            <>
              <h4>Demand signals</h4>
              <List items={market.demand_signals} />
            </>
          )}
          {report.calculations?.map((calc, index) => (
            <details className="details" key={index}>
              <summary>Calculation: {calc.tool}</summary>
              <List items={calc.result?.steps} />
              <pre>{JSON.stringify(calc.result, null, 2)}</pre>
            </details>
          ))}
        </Section>

        <Section id="competitors" number={2} title="Competitors" analysis={competitors}>
          <Table
            empty="No competitor profiles were returned."
            rows={competitors?.competitors}
            columns={[
              { key: "name", label: "Competitor", className: "strong" },
              { key: "positioning", label: "Positioning" },
              { key: "target_customers", label: "Target customers" },
              {
                key: "differentiators",
                label: "Differentiators",
                render: (row) => (row.differentiators || []).join("; "),
              },
              {
                key: "source_urls",
                label: "Sources",
                render: (row) =>
                  (row.source_urls || []).map((url) => (
                    <div key={url}>
                      <SafeLink url={url} />
                    </div>
                  )),
              },
            ]}
          />
        </Section>

        <Section id="customers" number={3} title="Customer demand and pain points" analysis={customers}>
          {customers?.segments?.map((segment, index) => (
            <div className="segment" key={index}>
              <div className="row">
                <strong>{segment.name}</strong>
                <Badge tone={segment.evidence_kind === "sourced_fact" ? "good" : "warn"}>
                  {segment.evidence_kind === "sourced_fact" ? "sourced" : "hypothesis"}
                </Badge>
              </div>
              <div className="two-col">
                <div>
                  <h4>Pain points</h4>
                  <List items={segment.pain_points} />
                </div>
                <div>
                  <h4>Needs</h4>
                  <List items={segment.needs} />
                </div>
              </div>
            </div>
          ))}
        </Section>

        <Section id="strategy" number={4} title="Opportunities, risks and strategy" analysis={strategy}>
          <div className="two-col">
            <div>
              <h4>Opportunities</h4>
              {strategy?.opportunities?.length ? (
                <List items={strategy.opportunities} />
              ) : (
                <Empty>None identified.</Empty>
              )}
            </div>
            <div>
              <h4>Risks</h4>
              {strategy?.risks?.length ? <List items={strategy.risks} /> : <Empty>None identified.</Empty>}
            </div>
          </div>
          {strategy?.positioning && (
            <>
              <h4>Positioning</h4>
              <p>{strategy.positioning}</p>
            </>
          )}
          <h4>Recommendations (reasoned, not sourced facts)</h4>
          <div className="findings">
            {strategy?.recommendations?.map((rec, index) => (
              <article className="finding" key={index}>
                <div className="row">
                  <strong>{rec.title}</strong>
                  <Badge tone={rec.priority === "high" ? "bad" : rec.priority === "medium" ? "warn" : "muted"}>
                    {rec.priority} priority
                  </Badge>
                </div>
                <p>{rec.rationale}</p>
              </article>
            ))}
          </div>
        </Section>

        <div id="report-sources" className="report-section">
          <Card title="5. Sources">
            <Table
              empty="No sources were retrieved."
              rows={report.sources}
              columns={[
                { key: "title", label: "Title" },
                { key: "url", label: "URL", render: (row) => <SafeLink url={row.url} /> },
                {
                  key: "via",
                  label: "Retrieved via",
                  className: "mono",
                  render: (row) => `${row.server}.${row.tool}`,
                },
              ]}
            />
          </Card>
        </div>

        <div id="report-caveats" className="report-section two-col">
          <Card title="Assumptions">
            {report.assumptions?.length ? <List items={report.assumptions} /> : <Empty>None stated.</Empty>}
          </Card>
          <Card title="Limitations">
            {report.limitations?.length ? <List items={report.limitations} /> : <Empty>None stated.</Empty>}
          </Card>
        </div>
      </div>

      <nav className="report-toc" aria-label="Report sections">
        <span>In this report</span>
        {SECTIONS.map(([id, label]) => (
          <a key={id} href={`#report-${id}`}>
            {label}
          </a>
        ))}
      </nav>
    </div>
  );
}
