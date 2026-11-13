// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/* Data panel: workspace tables, connected sources and new connections. */
import React, { useCallback, useEffect, useState } from 'react';
import { api, errorText, URLS } from '../api.js';
import { ConnectForm } from './Cards.jsx';
import { fmtNum } from '../format.js';

export default function DataPanel({ open, onClose, tables, refreshTables, model, showToast, onAsk }) {
  const [connectors, setConnectors] = useState([]);
  const [loaders, setLoaders] = useState([]);
  const [adding, setAdding] = useState('');
  const [busy, setBusy] = useState({});
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [c, l] = await Promise.all([api(URLS.CONNECTORS), api(URLS.DATA_LOADERS)]);
      setConnectors(c.connectors || []);
      setLoaders(l.loaders || []);
      setError(null);
    } catch (err) { setError(errorText(err)); }
  }, []);

  useEffect(() => { if (open) { refresh(); refreshTables(); } }, [open, refresh, refreshTables]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const withBusy = async (key, label, fn) => {
    setBusy(b => ({ ...b, [key]: label }));
    try { await fn(); } catch (err) { showToast(errorText(err)); } finally { setBusy(b => { const n = { ...b }; delete n[key]; return n; }); }
  };

  const sync = c => withBusy(c.id, 'Syncing…', async () => {
    if (!c.connected) await api(URLS.CONNECT, { connector_id: c.id });
    const r = await api(URLS.SYNC_CATALOG, { connector_id: c.id });
    showToast(`${c.display_name}: ${(r.sync_summary && r.sync_summary.total) || 0} tables in the catalog`);
    refresh();
  });

  // Profile the source and, with a model, write descriptions — repeat while the
  // (time-boxed) endpoint reports work remaining.
  const describe = c => withBusy(c.id, 'Describing tables…', async () => {
    if (!c.connected) await api(URLS.CONNECT, { connector_id: c.id });
    let described = 0, profiled = 0;
    for (let i = 0; i < 20; i++) {
      const r = await api(URLS.PROFILE_CATALOG, { connector_id: c.id, model: model || undefined });
      profiled += (r.profile && r.profile.profiled) || 0;
      described += (r.describe && r.describe.described) || 0;
      setBusy(b => ({ ...b, [c.id]: `Describing tables… ${described} done` }));
      if (!r.remaining) break;
    }
    showToast(model ? `Profiled ${profiled} and described ${described} tables` : `Profiled ${profiled} tables (no model selected for descriptions)`);
  });

  const remove = c => withBusy(c.id, 'Removing…', async () => {
    await api(`${URLS.CONNECTORS}/${encodeURIComponent(c.id)}`, undefined, { method: 'DELETE' });
    refresh();
  });

  const dropTable = t => withBusy('t:' + t.name, 'Removing…', async () => {
    await api(URLS.DELETE_TABLE, { table_name: t.name });
    await refreshTables();
  });

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Data" onClick={e => e.stopPropagation()}>
        <div className="modal-head"><h2>Data</h2><button className="icon-btn" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body">
          {error && <div className="ac-error">{error}</div>}

          <h3>Loaded tables <span className="count">{tables.length}</span></h3>
          {tables.length === 0 && <div className="empty-note">Nothing loaded yet. Attach a file in the chat, or ask the agent to find data in a connected source.</div>}
          {tables.map(t => (
            <div key={t.name} className="row-item">
              <div className="ri-main"><b dir="auto">{t.name}</b><span>{fmtNum(t.row_count)} rows · {(t.columns || []).length} columns{t.source_metadata && t.source_metadata.source_table_name ? ` · from ${t.source_metadata.source_table_name}` : ''}</span></div>
              <button className="btn small" onClick={() => { onClose(); onAsk(`Give me an overview of ${t.name} with the most informative chart.`, 'analyst'); }}>Analyze</button>
              <button className="btn small danger" disabled={!!busy['t:' + t.name]} onClick={() => dropTable(t)}>{busy['t:' + t.name] || 'Remove'}</button>
            </div>
          ))}

          <h3>Data sources <span className="count">{connectors.length}</span></h3>
          {connectors.map(c => (
            <div key={c.id} className="row-item">
              <div className="ri-main"><b>{c.display_name}</b><span>{c.type_name} · <i className={'dot ' + (c.connected ? 'on' : 'off')} /> {c.connected ? 'connected' : 'not connected'}</span></div>
              {busy[c.id] ? <span className="ri-busy"><span className="mini-spin" /> {busy[c.id]}</span> : (
                <>
                  <button className="btn small" title="Refresh the list of tables" onClick={() => sync(c)}>Sync</button>
                  {c.source === 'user' && <button className="btn small" title="Profile the tables and generate descriptions so the agent can find undocumented data" onClick={() => describe(c)}>Describe tables</button>}
                  <button className="btn small" onClick={() => { onClose(); onAsk(`What data is available in ${c.display_name}?`, 'data'); }}>Browse</button>
                  {c.deletable && <button className="btn small danger" onClick={() => remove(c)}>Remove</button>}
                </>
              )}
            </div>
          ))}

          <h3>Connect a new source</h3>
          <div className="loader-grid">
            {loaders.map(l => <button key={l.type} className={'chip' + (adding === l.type ? ' on' : '')} onClick={() => setAdding(a => (a === l.type ? '' : l.type))}>{l.name}</button>)}
          </div>
          {adding && <ConnectForm key={adding} sourceType={adding} loaders={loaders} showToast={showToast} onConnected={() => { setAdding(''); refresh(); }} />}
        </div>
      </div>
    </div>
  );
}
