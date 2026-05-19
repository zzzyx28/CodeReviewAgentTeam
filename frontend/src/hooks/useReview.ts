import { useState, useCallback, useRef } from 'react';
import type { AgentRole, AgentProgress, Finding, ReviewReport } from '../types/review';
import { useSSE } from './useSSE';
import { startReview, getStreamUrl, submitVerdict } from '../api/client';

function initAgents(): Record<AgentRole, AgentProgress> {
  return {
    security: { agent: 'security', status: 'idle', findingCount: 0, streamedText: '' },
    performance: { agent: 'performance', status: 'idle', findingCount: 0, streamedText: '' },
    maintainability: { agent: 'maintainability', status: 'idle', findingCount: 0, streamedText: '' },
    api_design: { agent: 'api_design', status: 'idle', findingCount: 0, streamedText: '' },
  };
}

export function useReview() {
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('idle');
  const [language, setLanguage] = useState<string>('');
  const [agents, setAgents] = useState<Record<AgentRole, AgentProgress>>(initAgents());
  const [findings, setFindings] = useState<Finding[]>([]);
  const [summary, setSummary] = useState<string>('');
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState<string>('');
  const [activeAgents, setActiveAgents] = useState<AgentRole[]>([]);
  const verdictsRef = useRef<Record<string, 'confirm' | 'dismiss'>>({});

  const handleSSEEvent = useCallback((event: string, data: any) => {
    switch (event) {
      case 'agent_event': {
        const payload = data;
        switch (payload.event) {
          case 'parsed':
            setLanguage(payload.language);
            setStatus('dispatching');
            break;
          case 'dispatch':
            setActiveAgents(payload.agents || []);
            setStatus('dispatching');
            break;
          case 'agent_started': {
            setAgents(prev => ({
              ...prev,
              [payload.agent as AgentRole]: { ...prev[payload.agent as AgentRole], status: 'running', streamedText: '' },
            }));
            setStatus('reviewing');
            break;
          }
          case 'agent_chunk': {
            setAgents(prev => {
              const agent = payload.agent as AgentRole;
              return {
                ...prev,
                [agent]: {
                  ...prev[agent],
                  streamedText: prev[agent].streamedText + (payload.content || ''),
                },
              };
            });
            break;
          }
          case 'agent_completed': {
            setAgents(prev => ({
              ...prev,
              [payload.agent as AgentRole]: { ...prev[payload.agent as AgentRole], status: 'completed', findingCount: payload.finding_count || 0 },
            }));
            break;
          }
          case 'summarized':
            setStatus('awaiting_human');
            break;
          case 'human_review_applied':
            break;
          case 'report_generated':
            if (payload.report) {
              setReport(payload.report);
              setFindings(payload.report.findings || []);
              setSummary(payload.report.summary || '');
            }
            break;
        }
        break;
      }
      case 'node_complete': {
        const nodeName = data.node;
        if (nodeName === 'supervisor_summary') {
          setStatus('awaiting_human');
        } else if (nodeName === 'generate_report') {
          setStatus('completed');
        }
        break;
      }
      case 'review_complete': {
        setStatus('completed');
        if (data.report) {
          setReport(data.report);
          setFindings(data.report.findings || []);
          setSummary(data.report.summary || '');
        }
        break;
      }
      case 'review_error': {
        setStatus('error');
        setError(data.error || 'Unknown error');
        break;
      }
    }
  }, []);

  const streamUrl = reviewId ? getStreamUrl(reviewId) : null;
  const { disconnect } = useSSE(streamUrl, handleSSEEvent);

  const start = useCallback(async (code: string, lang?: string) => {
    // Reset
    setStatus('parsing');
    setAgents(initAgents());
    setFindings([]);
    setSummary('');
    setReport(null);
    setError('');
    setActiveAgents([]);
    verdictsRef.current = {};

    try {
      const { review_id } = await startReview(code, lang);
      setReviewId(review_id);
    } catch (err: any) {
      setStatus('error');
      setError(err.message);
    }
  }, []);

  const confirmFinding = useCallback((findingId: string) => {
    verdictsRef.current[findingId] = 'confirm';
  }, []);

  const dismissFinding = useCallback((findingId: string) => {
    verdictsRef.current[findingId] = 'dismiss';
  }, []);

  const submitHumanReview = useCallback(async () => {
    if (!reviewId) return;
    const verdicts = Object.entries(verdictsRef.current).map(([finding_id, action]) => ({
      finding_id,
      action,
    }));
    try {
      setStatus('generating_report');
      await submitVerdict(reviewId, verdicts);
    } catch (err: any) {
      setError(err.message);
    }
  }, [reviewId]);

  const reset = useCallback(() => {
    disconnect();
    setReviewId(null);
    setStatus('idle');
    setAgents(initAgents());
    setFindings([]);
    setSummary('');
    setReport(null);
    setError('');
    setActiveAgents([]);
    verdictsRef.current = {};
  }, [disconnect]);

  return {
    reviewId, status, language, agents, findings, summary, report, error, activeAgents,
    start, confirmFinding, dismissFinding, submitHumanReview, reset,
  };
}
