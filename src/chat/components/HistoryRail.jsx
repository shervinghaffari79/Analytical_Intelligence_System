// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useEffect, useRef, useState } from 'react';
import { relTime, fmtNum } from '../format.js';

export default function HistoryRail({
  conversations, activeId, onNew, onOpen, onDelete, onClearAll, open, onClose,
  tables, onOpenData, models, model, setModelId, agentMode, setAgentMode,
}) {
  const [confirming, setConfirming] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const clearAll = () => {
    if (confirming) { setConfirming(false); clearTimeout(timer.current); onClearAll(); return; }
    setConfirming(true);
    timer.current = setTimeout(() => setConfirming(false), 3000);
  };
  const openItem = id => { onOpen(id); onClose && onClose(); };
  const rows = tables.reduce((a, t) => a + (t.row_count || 0), 0);

  return (
    <aside className={'rail' + (open ? ' open' : '')} aria-label="Conversation history">
      <div className="rail-logo">
        <div className="spark" aria-hidden="true">✦</div>
        <div>Agentic Analytics<small>Data Formulator</small></div>
      </div>
      <button className="new-chat" onClick={() => { onNew(); onClose && onClose(); }}>＋&nbsp; New conversation</button>

      <button className="data-btn" onClick={() => { onOpenData(); onClose && onClose(); }}>
        <span className="db-ico" aria-hidden="true">▦</span>
        <span className="db-main"><b>Data &amp; sources</b><small>{tables.length ? `${tables.length} table${tables.length === 1 ? '' : 's'} · ${fmtNum(rows)} rows` : 'No data loaded — connect or attach'}</small></span>
        <span aria-hidden="true">›</span>
      </button>

      <div className="rail-section">History
        <button className={confirming ? 'confirm' : ''} onClick={clearAll}>{confirming ? 'Click to confirm' : 'Clear all'}</button>
      </div>
      <div className="history-list" role="list">
        {conversations.length === 0 && <div className="history-empty">Your conversations will appear here.<br />Ask the agent anything to start.</div>}
        {conversations.map(c => (
          <div key={c.id} role="listitem" tabIndex={0}
               className={'history-item' + (c.id === activeId ? ' active' : '')}
               onClick={() => openItem(c.id)}
               onKeyDown={e => { if (e.key === 'Enter') openItem(c.id); }}
               aria-label={c.title}>
            <div className="hi-title" dir="auto">{c.title}</div>
            <div className="hi-meta">{relTime(c.updatedAt)} · {c.messages.length} message{c.messages.length === 1 ? '' : 's'}</div>
            <button className="hi-del" title="Delete conversation" aria-label="Delete conversation"
                    onClick={e => { e.stopPropagation(); onDelete(c.id); }}>×</button>
          </div>
        ))}
      </div>
      <div className="rail-foot">
        <label className="rail-select"><span>Model</span>
          <select value={model ? model.id : ''} onChange={e => setModelId(e.target.value)} disabled={!models.length}>
            {!models.length && <option value="">No model configured</option>}
            {models.map(m => <option key={m.id} value={m.id}>{m.model}</option>)}
          </select>
        </label>
        <label className="rail-select"><span>Analyst</span>
          <select value={agentMode} onChange={e => setAgentMode(e.target.value)}>
            <option value="standard">Thorough (multi-step)</option>
            <option value="mini">Fast (single chart)</option>
          </select>
        </label>
        <a className="rail-link" href="/">Open the full workspace ↗</a>
      </div>
    </aside>
  );
}
