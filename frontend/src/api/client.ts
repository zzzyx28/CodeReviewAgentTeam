const BASE = 'http://localhost:8000/api';

export async function startReview(code: string, language?: string, title?: string) {
  const res = await fetch(`${BASE}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, language, title }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<{ review_id: string; status: string }>;
}

export async function submitVerdict(reviewId: string, verdicts: { finding_id: string; action: 'confirm' | 'dismiss' }[]) {
  const res = await fetch(`${BASE}/review/${reviewId}/verdict`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(verdicts),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getReport(reviewId: string) {
  const res = await fetch(`${BASE}/review/${reviewId}/report`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getStatus(reviewId: string) {
  const res = await fetch(`${BASE}/review/${reviewId}/status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function getStreamUrl(reviewId: string) {
  return `${BASE}/review/${reviewId}/stream`;
}
