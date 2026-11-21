// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/* API client for the chat UI.
 *
 * Speaks the same protocol as the main app's `fetchWithIdentity` / `apiRequest`
 * / `streamRequest` (src/app/apiClient.ts): identity + workspace headers, the
 * `{status, data | error}` envelope, and newline-delimited JSON streams. It is
 * a separate, dependency-free copy because the main client is bound to the
 * Redux store, which this page does not load.
 */

const BROWSER_ID_KEY = 'df_browser_id';       // shared with src/app/identity.ts
const WORKSPACE_KEY = 'df_chat_workspace_id';

function uuid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function stored(key, make) {
  try {
    let v = localStorage.getItem(key);
    if (!v) { v = make(); localStorage.setItem(key, v); }
    return v;
  } catch { return make(); }
}

export const getBrowserId = () => stored(BROWSER_ID_KEY, uuid);
export const getWorkspaceId = () => stored(WORKSPACE_KEY, () => 'chat_' + uuid().slice(0, 8));

export class ApiError extends Error {
  constructor(error, httpStatus) {
    super((error && error.message) || 'Request failed');
    this.code = (error && error.code) || 'HTTP_ERROR';
    this.detail = error && error.detail;
    this.httpStatus = httpStatus;
  }
}

/** Human-readable text for an error thrown by this module or by fetch. */
export function errorText(err) {
  if (!err) return 'Unknown error';
  if (err.name === 'AbortError') return 'Stopped.';
  const detail = err.detail ? ` — ${String(err.detail).replace(/&quot;|&#x27;|&amp;quot;|&amp;#x27;/g, "'")}` : '';
  return (err.message || String(err)) + detail;
}

// The agents answer in the language named by Accept-Language. That should be the
// language the person is writing in, not the browser's UI language: a Persian
// question asked from an English browser must get a Persian answer.
let replyLanguage = null;
const ARABIC_SCRIPT = /[\u0600-\u06FF\u0750-\u077F]/g;
export function setReplyLanguageFrom(text) {
  const letters = String(text || '').replace(/[^\p{L}]/gu, '');
  const arabic = (String(text || '').match(ARABIC_SCRIPT) || []).length;
  // Persian vs Arabic: ی/ک/پ/چ/گ/ژ are Persian-only letters.
  replyLanguage = letters && arabic / letters.length > 0.3
    ? (/[\u06CC\u06A9\u067E\u0686\u06AF\u0698]/.test(text) ? 'fa' : 'ar')
    : null;
}

function headers(extra) {
  const h = new Headers(extra || {});
  h.set('X-Identity-Id', 'browser:' + getBrowserId());
  h.set('X-Workspace-Id', getWorkspaceId());
  h.set('Accept-Language', replyLanguage || (navigator.language || 'en').split('-')[0]);
  return h;
}

async function parseEnvelope(resp) {
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON body */ }
  if (body && body.status === 'error') throw new ApiError(body.error, resp.status);
  if (!resp.ok) throw new ApiError({ code: 'HTTP_ERROR', message: `HTTP ${resp.status}` }, resp.status);
  if (!body || body.status !== 'success') throw new ApiError({ code: 'MALFORMED_RESPONSE', message: 'Malformed response' }, resp.status);
  return body.data;
}

/** JSON request. `body` undefined → GET, otherwise POST (or `method`). */
export async function api(path, body, { method, signal } = {}) {
  const init = { method: method || (body === undefined ? 'GET' : 'POST'), headers: headers(), signal };
  if (body !== undefined) {
    init.headers.set('Content-Type', 'application/json');
    init.body = JSON.stringify(body);
  }
  return parseEnvelope(await fetch(path, init));
}

/** multipart/form-data POST. */
export async function apiForm(path, form, { signal } = {}) {
  return parseEnvelope(await fetch(path, { method: 'POST', headers: headers(), body: form, signal }));
}

/** POST and yield each NDJSON event as it arrives. */
export async function* stream(path, body, signal) {
  const h = headers({ 'Content-Type': 'application/json' });
  const resp = await fetch(path, { method: 'POST', headers: h, body: JSON.stringify(body), signal });
  const ctype = resp.headers.get('content-type') || '';
  if (!resp.ok || ctype.includes('application/json')) {
    // Pre-stream failure: the backend answered with a JSON envelope instead.
    await parseEnvelope(resp);
    throw new ApiError({ code: 'MALFORMED_STREAM_RESPONSE', message: 'Expected a streaming response' }, resp.status);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const emit = function* (line) {
    const t = line.trim();
    if (!t) return;
    try { const ev = JSON.parse(t); if (ev && ev.type) yield ev; } catch { /* partial or non-JSON line */ }
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) yield* emit(line);
    }
    yield* emit(buffer);
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
}

export const URLS = {
  APP_CONFIG: '/api/app-config',
  LIST_MODELS: '/api/agent/list-global-models',
  DATA_LOADING_CHAT: '/api/agent/data-loading-chat',
  ANALYST: '/api/agent/analyst-streaming',
  SCRATCH: '/api/agent/workspace/scratch',
  LIST_TABLES: '/api/tables/list-tables',
  CREATE_TABLE: '/api/tables/create-table',
  DELETE_TABLE: '/api/tables/delete-table',
  DATA_LOADERS: '/api/data-loaders',
  CONNECTORS: '/api/connectors',
  CONNECT: '/api/connectors/connect',
  SYNC_CATALOG: '/api/connectors/sync-catalog-metadata',
  PROFILE_CATALOG: '/api/connectors/profile-catalog',
  IMPORT_DATA: '/api/connectors/import-data',
  SCHEMA_GET: '/api/connectors/schema/get',
  SCHEMA_VALIDATE: '/api/connectors/schema/validate',
  SCHEMA_SAVE: '/api/connectors/schema/save',
  SCHEMA_DELETE: '/api/connectors/schema/delete',
  SCHEMA_TEMPLATE: '/api/connectors/schema/template',
};
