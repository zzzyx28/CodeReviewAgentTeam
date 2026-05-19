export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type AgentRole = 'security' | 'performance' | 'maintainability' | 'api_design';
export type ReviewStatus =
  | 'parsing' | 'dispatching' | 'reviewing'
  | 'awaiting_human' | 'generating_report' | 'completed' | 'error';

export interface Finding {
  id: string;
  agent: AgentRole;
  severity: Severity;
  title: string;
  description: string;
  line_start?: number;
  line_end?: number;
  code_snippet?: string;
  suggestion?: string;
  cwe_id?: string;
}

export interface AgentProgress {
  agent: AgentRole;
  status: 'idle' | 'running' | 'completed';
  findingCount: number;
  streamedText: string;
}

export interface ReviewReport {
  review_id: string;
  title: string;
  status: ReviewStatus;
  language: string;
  findings: Finding[];
  summary: string;
  stats: Record<Severity, number>;
  agents_involved: string[];
  created_at: string;
  completed_at?: string;
}

export interface SSEEvent {
  event: string;
  data: string;
}
