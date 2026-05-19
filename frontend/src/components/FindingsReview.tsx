import type { Finding } from '../types/review';

interface Props {
  findings: Finding[];
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  onSubmit: () => void;
  status: string;
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#dc2626',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#6b7280',
  info: '#3b82f6',
};

export default function FindingsReview({ findings, onConfirm, onDismiss, onSubmit, status }: Props) {
  if (status !== 'awaiting_human') return null;

  return (
    <div className="findings-review">
      <div className="panel-header">
        <h3>Review Findings ({findings.length})</h3>
        <button className="btn-primary" onClick={onSubmit}>
          Submit &amp; Generate Report
        </button>
      </div>
      <p className="hint">Confirm or dismiss each finding before generating the final report.</p>
      <div className="findings-list">
        {findings.map((f) => (
          <div key={f.id} className="finding-card">
            <div className="finding-header">
              <span className="severity-badge" style={{ background: SEVERITY_COLORS[f.severity] || '#888' }}>
                {f.severity.toUpperCase()}
              </span>
              <span className="agent-tag">{f.agent}</span>
              <strong>{f.title}</strong>
              {f.line_start && (
                <span className="line-ref">L{f.line_start}{f.line_end ? `-L${f.line_end}` : ''}</span>
              )}
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
            <div className="finding-actions">
              <button className="btn-confirm" onClick={() => onConfirm(f.id)}>✓ Confirm</button>
              <button className="btn-dismiss" onClick={() => onDismiss(f.id)}>✗ Dismiss</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
