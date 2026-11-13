// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { vlAllTemplateDefs, vlAdaptChart, vlGetTemplateChannels, vlRecommendEncodings } from 'flint-chart';
import VegaChart from './VegaChart.jsx';
import { fmtCell, fmtNum } from '../format.js';

const QUICK = ['Bar Chart', 'Grouped Bar Chart', 'Stacked Bar Chart', 'Line Chart', 'Area Chart', 'Scatter Plot', 'Pie Chart', 'Heatmap'];
const ALL_TYPES = vlAllTemplateDefs.map(d => d.chart);
const shortName = t => t.replace(/ (Chart|Plot)$/, '');

const fieldOf = v => (Array.isArray(v) ? v.map(fieldOf).filter(Boolean).join(', ') : (v && typeof v === 'object' ? v.field : v)) || '';

const JALALI_STR = /^1[34]\d\d[\/\-.](0?[1-9]|1[0-2])([\/\-.](0?[1-9]|[12]\d|3[01]))?$/;
const NUM_STR = /^[\d\u06f0-\u06f9\u0660-\u0669]+$/;

/* Jalali dates and codes must not be read as Gregorian dates or measures.
 * The chart engine guesses types from values: "1402/03/14" becomes a Gregorian
 * date (and is labelled "March"), and the text "1401" becomes a number. Mark
 * such fields as categories unless the backend already said what they are. */
function withJalaliAwareTypes(semantic, columns, rows) {
  const out = { ...(semantic || {}) };
  const sample = rows.slice(0, 300);
  for (const col of columns) {
    if (out[col]) continue;
    const vals = sample.map(r => r[col]).filter(v => v !== null && v !== undefined && v !== '');
    if (!vals.length) continue;
    if (vals.every(v => typeof v === 'string')) {
      if (vals.every(v => JALALI_STR.test(v.trim())) || vals.every(v => NUM_STR.test(v.trim()))) out[col] = 'Category';
    } else if (vals.every(v => Number.isInteger(v))) {
      const lo = Math.min(...vals), hi = Math.max(...vals);
      if ((lo >= 1300 && hi <= 1499) || (lo >= 13000101 && hi <= 14991231)) out[col] = 'Category';
    }
  }
  return out;
}

const PIE_ALIASES = { x: 'color', category: 'color', label: 'color', y: 'size', theta: 'size', value: 'size', angle: 'size' };

/* Models sometimes describe a chart with channels the template does not have
 * (a pie given x/y instead of color/size). Keep what is valid, translate the
 * usual aliases, and let the engine fill what is still missing. */
function repairEncodings(type, encodings, rows, semantic) {
  const channels = vlGetTemplateChannels(type) || [];
  const out = {};
  for (const [ch, v] of Object.entries(encodings || {})) {
    if (!fieldOf(v)) continue;
    if (channels.includes(ch)) out[ch] = v;
    else if (type === 'Pie Chart' && PIE_ALIASES[ch] && !out[PIE_ALIASES[ch]] && !(encodings || {})[PIE_ALIASES[ch]]) out[PIE_ALIASES[ch]] = v;
  }
  const required = type === 'Pie Chart' ? ['color', 'size'] : [];
  if (Object.keys(out).length === 0 || required.some(ch => !out[ch])) {
    let rec = {};
    try { rec = vlRecommendEncodings(type, rows, semantic || {}) || {}; } catch { rec = {}; }
    const used = new Set(Object.values(out).map(fieldOf));
    for (const [ch, f] of Object.entries(rec)) if (!out[ch] && f && !used.has(fieldOf(f))) out[ch] = f;
  }
  return out;
}

function normalizeType(type, encodings, rows, semantic) {
  if (ALL_TYPES.includes(type)) return { type, encodings: repairEncodings(type, encodings, rows, semantic) };
  // "Auto" or an unknown name: let the engine pick fields for a sensible default.
  const fallback = 'Bar Chart';
  try { return { type: fallback, encodings: vlRecommendEncodings(fallback, rows, semantic || {}) }; } catch { return { type: fallback, encodings }; }
}

function DataTable({ columns, rows, total }) {
  const shown = rows.slice(0, 200);
  return (
    <div className="table-scroll">
      <table className="chart-table">
        <thead><tr>{columns.map(c => <th key={c} dir="auto">{c}</th>)}</tr></thead>
        <tbody>{shown.map((r, i) => <tr key={i}>{columns.map(c => <td key={c} dir="auto">{fmtCell(r[c])}</td>)}</tr>)}</tbody>
      </table>
      {(rows.length > shown.length || total > rows.length) && (
        <div className="table-note">Showing {fmtNum(shown.length)} of {fmtNum(total || rows.length)} rows — download the CSV for everything kept in this chat.</div>
      )}
    </div>
  );
}

function ChartCardInner({ block, onAsk, showToast }) {
  const { title, rows, columns, displayNames, config, code, rowCount } = block;
  const semantic = useMemo(() => withJalaliAwareTypes(block.semantic, columns, rows), [block.semantic, columns, rows]);
  const initial = useMemo(() => normalizeType(block.chartType, block.encodings, rows, semantic), [block.chartType, block.encodings, rows, semantic]);
  const [type, setType] = useState(initial.type);
  const [encodings, setEncodings] = useState(initial.encodings);
  const [view, setView] = useState('chart'); // chart | table | code
  const [pin, setPin] = useState(null);
  const [max, setMax] = useState(false);
  const vegaView = useRef(null);

  useEffect(() => {
    if (!max) return undefined;
    const onKey = e => { if (e.key === 'Escape') setMax(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [max]);

  const switchType = useCallback(target => {
    setPin(null);
    setView('chart');
    if (target === type) return;
    let next;
    try { next = vlAdaptChart(type, target, encodings, rows, semantic || {}); } catch { next = null; }
    if (!next || !Object.values(next).some(Boolean)) {
      try { next = vlRecommendEncodings(target, rows, semantic || {}); } catch { next = encodings; }
    }
    setType(target);
    setEncodings(next);
  }, [type, encodings, rows, semantic]);

  const onDatum = useCallback(d => {
    // Keep the user's own fields; Vega adds helper fields (stack offsets, bins).
    const entries = Object.entries(d).filter(([k, v]) => columns.includes(k) && v !== null && typeof v !== 'object');
    if (entries.length) setPin(entries.slice(0, 6));
  }, [columns]);

  const csvDownload = () => {
    const esc = v => (v === null || v === undefined ? '' : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
    const csv = '﻿' + [columns.map(esc).join(','), ...rows.map(r => columns.map(c => esc(r[c])).join(','))].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    a.download = (title.replace(/[^\p{L}\p{N}]+/gu, '_') || 'chart') + '.csv';
    a.click();
    URL.revokeObjectURL(a.href);
    showToast && showToast('CSV downloaded');
  };

  const pngDownload = async () => {
    if (!vegaView.current) { showToast && showToast('Switch to the chart view first'); return; }
    try {
      const url = await vegaView.current.toImageURL('png', 2);
      const a = document.createElement('a');
      a.href = url;
      a.download = (title.replace(/[^\p{L}\p{N}]+/gu, '_') || 'chart') + '.png';
      a.click();
      showToast && showToast('Image downloaded');
    } catch { showToast && showToast('Could not export the image'); }
  };

  const used = Object.entries(encodings || {}).map(([ch, v]) => [ch, fieldOf(v)]).filter(([, f]) => f);
  const pinText = pin ? pin.map(([k, v]) => `${k} = ${v}`).join(', ') : '';
  const askPin = pin ? `In "${title}", tell me more about ${pinText}.` : '';
  const moreTypes = ALL_TYPES.filter(t => !QUICK.includes(t));

  return (
    <div className={'chart-card' + (max ? ' max' : '')}>
      {max && <div className="chart-backdrop open" onClick={() => setMax(false)} />}
      <div className="chart-head">
        <div className="chart-title" dir="auto">{title}</div>
        <span className="date-pill static-pill" title={used.map(([c, f]) => `${c}: ${f}`).join(' · ')}>
          <b>{shortName(type)}</b> {fmtNum(rowCount || rows.length)} rows
        </span>
        <div className="chart-actions">
          {code && <button className={'icon-btn' + (view === 'code' ? ' on' : '')} title="Show the code that built this data" aria-label="Show code" onClick={() => setView(v => (v === 'code' ? 'chart' : 'code'))}>{'</>'}</button>}
          <button className="icon-btn" title={max ? 'Restore' : 'Maximize chart'} aria-label={max ? 'Restore chart' : 'Maximize chart'} onClick={() => setMax(m => !m)}>⤢</button>
        </div>
      </div>
      <div className="chart-body">
        {view === 'chart' && (
          <VegaChart chartType={type} encodings={encodings} config={config} rows={rows} semantic={semantic}
                     displayNames={displayNames} height={max ? 460 : 320} onDatum={onDatum} viewRef={vegaView} />
        )}
        {view === 'table' && <DataTable columns={columns} rows={rows} total={rowCount} />}
        {view === 'code' && <pre className="code-block" dir="ltr">{code}</pre>}
      </div>
      {pin && (
        <div className="chart-detail show" role="status">
          <div className="cd-title">📌 Selected</div>
          <div className="cd-stats">{pin.map(([k, v], i) => <span className="cd-stat" key={i} dir="auto">{k} <b>{fmtCell(v)}</b></span>)}</div>
          <button className="cd-ask" onClick={() => onAsk && onAsk(askPin)}>Ask agent about this →</button>
          <button className="cd-close" onClick={() => setPin(null)} aria-label="Unpin">✕</button>
        </div>
      )}
      <div className="chart-foot">
        <div className="type-switch" role="tablist" aria-label="Chart type">
          {QUICK.map(t => (
            <button key={t} role="tab" aria-selected={view === 'chart' && type === t} className={view === 'chart' && type === t ? 'active' : ''} onClick={() => switchType(t)}>{shortName(t)}</button>
          ))}
          <select className={'type-more' + (view === 'chart' && moreTypes.includes(type) ? ' active' : '')} aria-label="More chart types"
                  value={moreTypes.includes(type) ? type : ''} onChange={e => e.target.value && switchType(e.target.value)}>
            <option value="">More…</option>
            {moreTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <button role="tab" aria-selected={view === 'table'} className={view === 'table' ? 'active' : ''} onClick={() => setView(v => (v === 'table' ? 'chart' : 'table'))}>Table</button>
        </div>
        <div className="spacer" />
        <button className="mini-btn" onClick={csvDownload}>⬇ CSV</button>
        <button className="mini-btn" onClick={pngDownload}>⬇ PNG</button>
      </div>
    </div>
  );
}

// Memoised: streaming text re-renders the message many times a second, and a
// chart must not be rebuilt unless its own block changes.
const ChartCard = React.memo(ChartCardInner);
export default ChartCard;
