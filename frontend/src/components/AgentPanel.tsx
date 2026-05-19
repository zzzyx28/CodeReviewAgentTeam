import type { AgentRole, AgentProgress } from '../types/review';

interface Props {
  agents: Record<AgentRole, AgentProgress>;
  activeAgents: AgentRole[];
  status: string;
}

const AGENT_META: Record<AgentRole, { label: string; icon: string; color: string }> = {
  security: { label: 'Security', icon: '🛡️', color: '#ef4444' },
  performance: { label: 'Performance', icon: '⚡', color: '#f59e0b' },
  maintainability: { label: 'Maintainability', icon: '🔧', color: '#3b82f6' },
  api_design: { label: 'API Design', icon: '🔌', color: '#8b5cf6' },
};

export default function AgentPanel({ agents, activeAgents, status }: Props) {
  const showAgents = status === 'dispatching' || status === 'reviewing' || status === 'awaiting_human';

  if (!showAgents) return null;

  const runningCount = activeAgents.filter(a => agents[a]?.status === 'running').length;
  const completedCount = activeAgents.filter(a => agents[a]?.status === 'completed').length;

  return (
    <div className="agent-panel">
      <div className="panel-header">
        <h3>Agent Progress</h3>
        <span className="progress-badge">
          {completedCount}/{activeAgents.length} completed
          {runningCount > 0 && ` • ${runningCount} running`}
        </span>
      </div>
      <div className="agent-grid">
        {activeAgents.map((role) => {
          const agent = agents[role];
          const meta = AGENT_META[role];
          const isActive = activeAgents.includes(role);

          if (!isActive) return null;

          return (
            <div key={role} className={`agent-card ${agent.status}`} style={{ borderLeftColor: meta.color }}>
              <div className="agent-card-header">
                <span className="agent-icon">{meta.icon}</span>
                <span className="agent-label">{meta.label}</span>
                <span className={`agent-status-badge ${agent.status}`}>
                  {agent.status === 'running' ? 'Analyzing...' :
                   agent.status === 'completed' ? `${agent.findingCount} findings` :
                   'Waiting...'}
                </span>
              </div>
              {agent.status === 'running' && agent.streamedText && (
                <div className="agent-stream">
                  <pre>{agent.streamedText.slice(-300)}</pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
