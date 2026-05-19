import type { ReviewReport, Finding, Severity } from '../types/review';

interface Props {
  report: ReviewReport;
  findings: Finding[];
  summary: string;
}

const SEVERITY_COLORS: Record<Severity, string> = {
  critical: '#dc2626',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#6b7280',
  info: '#3b82f6',
};

export default function ReportView({ report, findings, summary }: Props) {
  const stats = report.stats || {} as Record<Severity, number>;
  const total = Object.values(stats).reduce((a: number, b: number) => a + b, 0);

  return (
    <div className="report-view">
      <div className="report-header">
        <h2>Review Report</h2>
        <div className="report-meta">
          <span>Language: {report.language}</span>
          <span>Agents: {report.agents_involved?.join(', ')}</span>
          <span>Completed: {report.completed_at ? new Date(report.completed_at).toLocaleString() : ''}</span>
        </div>
      </div>

      <div className="summary-box">
        <h3>Executive Summary</h3>
        <p>{summary || report.summary}</p>
      </div>

      <div className="stats-bar">
        {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((sev) => {
          const count = stats[sev] || 0;
          if (count === 0) return null;
          return (
            <div key={sev} className="stat-chip" style={{ background: SEVERITY_COLORS[sev] }}>
              {sev}: {count}
            </div>
          );
        })}
        <span className="stat-total">{total} total</span>
      </div>

      <div className="findings-list">
        {findings.map((f, i) => (
          <div key={f.id || i} className="finding-card">
            <div className="finding-header">
              <span className="severity-badge" style={{ background: SEVERITY_COLORS[f.severity] || '#888' }}>
                {f.severity.toUpperCase()}
              </span>
              <span className="agent-tag">{f.agent}</span>
              <strong>{f.title}</strong>
              {f.line_start && (
                <span className="line-ref">L{f.line_start}{f.line_end ? `-L${f.line_end}` : ''}</span>
              )}
              {f.cwe_id && <span className="cwe-tag">{f.cwe_id}</span>}
            </div>
            <p className="finding-desc">{f.description}</p>
            {f.code_snippet && (
              <pre className="finding-code"><code>{f.code_snippet}</code></pre>
            )}
            {f.suggestion && (
              <div className="finding-suggestion">
                <strong>Suggestion:</strong> {f.suggestion}
              </div>
            )}
          </div>
        ))}
        {findings.length === 0 && (
          <p className="no-findings">No issues found. All agents completed review successfully.</p>
        )}
      </div>
    </div>
  );
}
