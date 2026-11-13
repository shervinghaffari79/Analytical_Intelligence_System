// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

const LS_KEY = 'agentic-analytics-chat-v2';
const PREFS_KEY = 'agentic-analytics-chat-prefs';
const PERSIST_ROWS = 400; // per chart; localStorage is ~5 MB

/** Conversations as stored: interrupted runs are closed, heavy rows trimmed. */
function slim(state) {
  return {
    active: state.active,
    convs: state.convs.slice(0, 40).map(c => ({
      ...c,
      messages: c.messages.map(m => (m.role !== 'assistant' ? m : {
        ...m,
        blocks: (m.blocks || []).map(b => (b.type === 'chart' && b.rows.length > PERSIST_ROWS
          ? { ...b, rows: b.rows.slice(0, PERSIST_ROWS), trimmed: true } : b)),
      })),
    })),
  };
}

export function loadConversations() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
    if (parsed && Array.isArray(parsed.convs)) {
      // A run that was streaming when the page closed cannot be resumed.
      parsed.convs.forEach(c => c.messages.forEach(m => { if (m.role === 'assistant' && m.status === 'running') m.status = 'stopped'; }));
      return parsed;
    }
  } catch { /* corrupt or unavailable storage */ }
  return { convs: [], active: null };
}

export function saveConversations(state) {
  let data = slim(state);
  for (let attempt = 0; attempt < 4; attempt++) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(data)); return; } catch {
      // Over quota: drop the oldest conversations and retry.
      data = { ...data, convs: data.convs.slice(0, Math.max(1, Math.floor(data.convs.length / 2))) };
    }
  }
}

export function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {}; } catch { return {}; }
}
export function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
}

export const newId = () => 'c' + Date.now().toString(36) + Math.floor(Math.random() * 1e5).toString(36);
export const msgId = () => 'm' + Date.now().toString(36) + Math.floor(Math.random() * 1e5).toString(36);
