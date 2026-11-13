// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/* Interactive cards the agents can put in a reply: a load plan, a table
 * preview, a connection form and clarifying questions. */
import React, { useEffect, useState } from 'react';
import { api, errorText, URLS } from '../api.js';
import { importLoadPlan, loadPreview } from '../runAgent.js';
import { fmtCell, fmtNum } from '../format.js';

const OPS = { EQ: '=', NEQ: '≠', GT: '>', GTE: '≥', LT: '<', LTE: '≤', ILIKE: 'contains', LIKE: 'like', IN: 'in', NOT_IN: 'not in', BETWEEN: 'between', IS_NULL: 'is empty', IS_NOT_NULL: 'is not empty' };
const filterText = f => `${f.column} ${OPS[f.operator] || f.operator}${f.value === undefined ? '' : ' ' + (Array.isArray(f.value) ? f.value.join(f.operator === 'BETWEEN' ? ' – ' : ', ') : f.value)}`;

export function LoadPlanCard({ block, onChange, onLoaded, showToast, canAnalyze }) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({});
  const [error, setError] = useState(null);
  const done = block.status === 'loaded';
  const selected = block.candidates.filter(c => c.selected && !c.error);

  const toggle = i => onChange({ ...block, candidates: block.candidates.map((c, j) => (j === i ? { ...c, selected: !c.selected } : c)) });

  const load = async analyze => {
    setBusy(true); setError(null);
    try {
      const names = await importLoadPlan(selected, (c, state, data) => setProgress(p => ({ ...p, [c.tableKey]: state === 'loaded' ? `${fmtNum(data.row_count)} rows` : 'loading…' })));
      onChange({ ...block, status: 'loaded', loadedTables: names });
      showToast && showToast(`Loaded ${names.length} table${names.length === 1 ? '' : 's'}`);
      onLoaded && onLoaded(names, analyze);
    } catch (err) {
      setError(errorText(err));
    } finally { setBusy(false); }
  };

  return (
    <div className="action-card">
      <div className="ac-head"><span className="ac-ico">⇩</span> Load plan {done && <span className="ac-badge ok">Loaded</span>}</div>
      {block.reasoning && <div className="ac-note" dir="auto">{block.reasoning}</div>}
      <div className="ac-list">
        {block.candidates.map((c, i) => (
          <label key={c.tableKey + i} className={'ac-row' + (c.error ? ' bad' : '')}>
            <input type="checkbox" checked={!!c.selected && !c.error} disabled={done || busy || !!c.error} onChange={() => toggle(i)} />
            <span className="ac-main">
              <span className="ac-name" dir="auto">{c.displayName}</span>
              <span className="ac-sub">{c.sourceId}{c.error ? ` · ${c.error}` : ''}</span>
              {c.filters && c.filters.length > 0 && (
                <span className="ac-filters">{c.filters.map((f, k) => <span key={k} className="ac-filter" dir="auto">{filterText(f)}</span>)}</span>
              )}
            </span>
            {progress[c.tableKey] && <span className="ac-state">{progress[c.tableKey]}</span>}
          </label>
        ))}
      </div>
      {error && <div className="ac-error">Could not load: {error}</div>}
      {!done && (
        <div className="ac-actions">
          {canAnalyze && <button className="btn primary" disabled={busy || !selected.length} onClick={() => load(true)}>{busy ? 'Loading…' : `Load ${selected.length} & analyze`}</button>}
          <button className={'btn' + (canAnalyze ? '' : ' primary')} disabled={busy || !selected.length} onClick={() => load(false)}>{busy && !canAnalyze ? 'Loading…' : 'Load only'}</button>
        </div>
      )}
    </div>
  );
}

export function PreviewCard({ block, onChange, onLoaded, showToast }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(block.error);
  const load = async () => {
    setBusy(true); setError(null);
    try {
      const data = await loadPreview(block);
      onChange({ ...block, status: 'loaded' });
      showToast && showToast(`Loaded ${data.table_name}`);
      onLoaded && onLoaded([data.table_name], false);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <div className="action-card">
      <div className="ac-head"><span className="ac-ico">▦</span> <span dir="auto">{block.name}</span>
        <span className="ac-sub">{fmtNum(block.totalRows)} rows · {block.columns.length} columns</span>
        {block.status === 'loaded' && <span className="ac-badge ok">Loaded</span>}
      </div>
      {block.columns.length > 0 && (
        <div className="table-scroll">
          <table className="chart-table">
            <thead><tr>{block.columns.map(c => <th key={c} dir="auto">{c}</th>)}</tr></thead>
            <tbody>{block.rows.map((r, i) => <tr key={i}>{block.columns.map(c => <td key={c} dir="auto">{fmtCell(r[c])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {error && <div className="ac-error">{error}</div>}
      {block.status === 'pending' && block.scratchPath && (
        <div className="ac-actions"><button className="btn primary" disabled={busy} onClick={load}>{busy ? 'Loading…' : 'Load this table'}</button></div>
      )}
    </div>
  );
}

/** Connection form for one loader type; used inline (agent-proposed) and in the data panel. */
export function ConnectForm({ sourceType, prefilled, status, onConnected, showToast, loaders }) {
  const [defs, setDefs] = useState(loaders || null);
  const [values, setValues] = useState({});
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (defs) return;
    api(URLS.DATA_LOADERS).then(d => setDefs(d.loaders || [])).catch(e => setError(errorText(e)));
  }, [defs]);

  const loader = (defs || []).find(l => l.type === sourceType);
  useEffect(() => {
    if (!loader) return;
    const init = {};
    for (const p of loader.params) init[p.name] = (prefilled && prefilled[p.name] !== undefined) ? prefilled[p.name] : (p.default ?? '');
    setValues(init);
    setName(n => n || loader.name);
  }, [loader, prefilled]);

  if (status === 'connected') return <div className="action-card"><div className="ac-head"><span className="ac-ico">⛓</span> {sourceType} <span className="ac-badge ok">Connected</span></div></div>;
  if (!defs) return <div className="action-card"><div className="ac-note">{error || 'Loading connector details…'}</div></div>;
  if (!loader) return <div className="action-card"><div className="ac-error">The connector “{sourceType}” is not available on this server.</div></div>;

  const connect = async e => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const params = {};
      for (const p of loader.params) if (values[p.name] !== '' && values[p.name] !== undefined) params[p.name] = values[p.name];
      const created = await api(URLS.CONNECTORS, { loader_type: sourceType, display_name: name || loader.name, params, persist: true });
      const id = created.id;
      await api(URLS.CONNECT, { connector_id: id, params });
      let tables = 0;
      try { const sync = await api(URLS.SYNC_CATALOG, { connector_id: id }); tables = (sync.sync_summary && sync.sync_summary.total) || 0; } catch { /* catalog sync is best-effort */ }
      showToast && showToast(`Connected · ${tables} tables found`);
      onConnected && onConnected({ id, name: name || loader.name, tables });
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };

  const shown = loader.params.filter(p => advanced || !p.advanced);
  return (
    <form className="action-card" onSubmit={connect}>
      <div className="ac-head"><span className="ac-ico">⛓</span> Connect {loader.name}</div>
      <div className="form-grid">
        <label className="field"><span>Name</span><input value={name} onChange={e => setName(e.target.value)} placeholder={loader.name} /></label>
        {shown.map(p => (
          <label key={p.name} className="field">
            <span>{p.name.replace(/_/g, ' ')}{p.required ? ' *' : ''}</span>
            <input type={p.sensitive ? 'password' : (p.type === 'int' ? 'number' : 'text')} required={!!p.required}
                   autoComplete={p.sensitive ? 'new-password' : 'off'} value={values[p.name] ?? ''} placeholder={p.description || ''}
                   onChange={e => setValues(v => ({ ...v, [p.name]: p.type === 'int' && e.target.value !== '' ? Number(e.target.value) : e.target.value }))} />
          </label>
        ))}
      </div>
      {loader.params.some(p => p.advanced) && <button type="button" className="link-btn" onClick={() => setAdvanced(a => !a)}>{advanced ? 'Hide' : 'Show'} advanced options</button>}
      {error && <div className="ac-error">{error}</div>}
      <div className="ac-actions"><button className="btn primary" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button></div>
    </form>
  );
}

export function QuestionsCard({ block, onAsk }) {
  return (
    <div className="action-card">
      {block.questions.map((q, i) => (
        <div key={i} className="q-item">
          <div className="q-text" dir="auto">{q.text}</div>
          {Array.isArray(q.options) && q.options.length > 0 && (
            <div className="followups">
              {q.options.map((o, k) => {
                const label = typeof o === 'string' ? o : (o.label || o.text || o.value);
                return <button key={k} className="followup" dir="auto" onClick={() => onAsk(String(label))}>↳ {label}</button>;
              })}
            </div>
          )}
        </div>
      ))}
      <div className="ac-sub">Pick an option or type your answer below.</div>
    </div>
  );
}
