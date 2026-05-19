import { useReview } from './hooks/useReview';
import CodeInput from './components/CodeInput';
import AgentPanel from './components/AgentPanel';
import FindingsReview from './components/FindingsReview';
import ReportView from './components/ReportView';
import type { Finding } from './types/review';

function App() {
  const {
    status, agents, summary, report, error, activeAgents,
    start, confirmFinding, dismissFinding, submitHumanReview, reset,
  } = useReview();

  const isRunning = status !== 'idle' && status !== 'completed' && status !== 'error';

  return (
    <div className="app">
      <header className="app-header">
        <h1>Code Review Agent Team</h1>
        <span className="subtitle">Multi-Agent Review powered by LangGraph</span>
        {status !== 'idle' && (
          <button className="btn-secondary" onClick={reset}>New Review</button>
        )}
      </header>

      <main className="app-main">
        <CodeInput onSubmit={start} disabled={isRunning} />

        {error && <div className="error-banner">{error}</div>}

        {status === 'parsing' && (
          <div className="loading">Parsing code and extracting structure...</div>
        )}

        <AgentPanel agents={agents} activeAgents={activeAgents} status={status} />

        {status === 'awaiting_human' && (
          <FindingsReview
            findings={(report?.findings || []) as Finding[]}
            onConfirm={confirmFinding}
            onDismiss={dismissFinding}
            onSubmit={submitHumanReview}
            status={status}
          />
        )}

        {status === 'generating_report' && (
          <div className="loading">Generating final report...</div>
        )}

        {status === 'completed' && report && (
          <ReportView report={report} findings={report.findings || []} summary={summary} />
        )}
      </main>
    </div>
  );
}

export default App;
