// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/* Schema file for one source: download a template, upload what you wrote, review the
 * validation report, apply. With a schema the agents read your descriptions instead of
 * crawling the database table by table. */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiForm, errorText, URLS } from '../api.js';

const MAX_SHOWN = 8;

function Issues({ items, kind }) {
  if (!items || !items.length) return null;
  return (
    <ul className={'schema-issues ' + kind}>
      {items.slice(0, MAX_SHOWN).map((i, k) => (
        <li key={k} dir="auto"><code>{i.path || 'file'}</code> {i.message}</li>
      ))}
      {items.length > MAX_SHOWN && <li>… and {items.length - MAX_SHOWN} more</li>}
    </ul>
  );
}

export default function SchemaBox({ connector, showToast }) {
  const [info, setInfo] = useState(null);       // schema/get response
  const [pending, setPending] = useState(null); // {file, report}
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const refresh = useCallback(async () => {
    try { setInfo(await api(URLS.SCHEMA_GET, { connector_id: connector.id })); setError(null); }
    catch (err) { setError(errorText(err)); }
  }, [connector.id]);
  useEffect(() => { refresh(); }, [refresh]);

  const run = async (label, fn) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (err) { setError(errorText(err)); } finally { setBusy(''); }
  };

  const download = () => run('Preparing…', async () => {
    if (!connector.connected) await api(URLS.CONNECT, { connector_id: connector.id });
    const t = await api(URLS.SCHEMA_TEMPLATE, { connector_id: connector.id });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([t.content], { type: 'text/yaml;charset=utf-8' }));
    a.download = t.filename;
    a.click();
    URL.revokeObjectURL(a.href);
    showToast(`Downloaded ${t.filename} — fill it in and upload it back`);
  });

  const form = file => { const f = new FormData(); f.append('connector_id', connector.id); f.append('file', file); return f; };

  const pick = file => run('Checking…', async () => {
    if (!connector.connected) await api(URLS.CONNECT, { connector_id: connector.id });
    const report = await apiForm(URLS.SCHEMA_VALIDATE, form(file));
    setPending({ file, report });
  });

  const apply = () => run('Applying…', async () => {
    const out = await apiForm(URLS.SCHEMA_SAVE, form(pending.file));
    if (!out.saved) { setPending({ file: pending.file, report: out.report }); return; }
    showToast(`Schema applied · ${out.report.matched_tables} of ${out.report.tables_in_catalog} tables documented`);
    setPending(null);
    await refresh();
  });

  const remove = () => run('Removing…', async () => {
    await api(URLS.SCHEMA_DELETE, { connector_id: connector.id });
    showToast('Schema removed');
    await refresh();
  });

  const report = pending && pending.report;
  const r = info && info.report;
  return (
    <div className="schema-box" dir="ltr">
      <div className="schema-head">
        <b>Schema file</b>
        {info && info.present
          ? <span className="schema-status ok">✓ {r.matched_tables} of {r.tables_in_catalog} tables documented{info.rules ? ` · ${info.rules} rule${info.rules === 1 ? '' : 's'}` : ''}{info.glossary ? ` · ${info.glossary} term${info.glossary === 1 ? '' : 's'}` : ''}</span>
          : <span className="schema-status">none — the agent has to explore the tables itself</span>}
        {busy && <span className="ri-busy"><span className="mini-spin" /> {busy}</span>}
      </div>
      <div className="schema-help">
        Describe your tables, columns, value meanings and rules once; the agents use it instead of crawling.
        Formats: YAML, JSON, or a CSV data dictionary (<code>table, column, description</code>).
      </div>
      {error && <div className="ac-error">{error}</div>}

      {report && (
        <div className="schema-report">
          {report.ok
            ? <div className="schema-ok">Valid — {report.matched_tables} of {report.tables_in_file} tables in the file match this source{report.undocumented_tables && report.undocumented_tables.length ? `; ${report.undocumented_tables.length} source tables are not described` : ''}.</div>
            : <div className="schema-bad">This file cannot be applied yet.</div>}
          <Issues items={report.errors} kind="err" />
          <Issues items={report.warnings} kind="warn" />
          <div className="ac-actions">
            <button className="btn primary small" disabled={!!busy || !report.ok} onClick={apply}>Apply schema</button>
            <button className="btn small" disabled={!!busy} onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      )}

      {!report && (
        <div className="ac-actions">
          <button className="btn small" disabled={!!busy} onClick={download} title="A YAML file listing your tables and columns, pre-filled with what the system already knows">Download template</button>
          <button className="btn small" disabled={!!busy} onClick={() => fileRef.current && fileRef.current.click()}>{info && info.present ? 'Replace schema…' : 'Upload schema…'}</button>
          <input ref={fileRef} type="file" hidden accept=".yaml,.yml,.json,.csv,.txt" onChange={e => { const f = e.target.files[0]; e.target.value = ''; if (f) pick(f); }} />
          {info && info.present && <button className="btn small danger" disabled={!!busy} onClick={remove}>Remove schema</button>}
        </div>
      )}
    </div>
  );
}
