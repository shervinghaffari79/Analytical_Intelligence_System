// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { fmtBytes } from '../format.js';

export const ACCEPT = '.csv,.tsv,.xlsx,.xls,.json,.parquet,.txt,.md,.png,.jpg,.jpeg,.gif,.webp';
const kindIcon = name => (/\.(csv|tsv|xlsx|xls|parquet)$/i.test(name) ? '▦' : /\.json$/i.test(name) ? '{ }' : /\.(png|jpe?g|gif|webp)$/i.test(name) ? '🖼️' : '¶');
const MODES = [['auto', 'Auto'], ['analyst', 'Analyze'], ['data', 'Find data']];
const MAX_FILE = 100 * 1024 * 1024;

export default function Composer({ onSend, onStop, running, mode, setMode, showToast }) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState([]); // {id, file}
  const inputRef = useRef(null);
  const fileRef = useRef(null);

  const addFiles = useCallback(list => {
    const arr = Array.from(list || []);
    const ok = arr.filter(f => f.size <= MAX_FILE);
    if (ok.length < arr.length) showToast && showToast('Files over 100 MB were skipped');
    setFiles(prev => [...prev, ...ok.map(file => ({ id: 'f' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7), file }))]);
  }, [showToast]);

  useEffect(() => {
    const handler = e => { if (e.detail && e.detail.files) addFiles(e.detail.files); };
    window.addEventListener('agentic-drop-files', handler);
    return () => window.removeEventListener('agentic-drop-files', handler);
  }, [addFiles]);

  const autosize = el => { el.style.height = 'auto'; el.style.height = Math.min(140, el.scrollHeight) + 'px'; };
  const canSend = (text.trim().length > 0 || files.length > 0) && !running;

  const send = () => {
    if (!canSend) return;
    const t = text.trim();
    const fs = files.map(f => f.file);
    setText(''); setFiles([]);
    if (inputRef.current) inputRef.current.style.height = 'auto';
    onSend(t, fs);
  };

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        {files.length > 0 && (
          <div className="attach-row" aria-label="Attachments">
            {files.map(({ id, file }) => (
              <span key={id} className="attach-chip ready" title={file.name}>
                <span className="att-ico">{kindIcon(file.name)}</span>
                <span className="att-name">{file.name.length > 28 ? file.name.slice(0, 27) + '…' : file.name}</span>
                <span className="att-size">{fmtBytes(file.size)}</span>
                <button className="att-remove" onClick={() => setFiles(prev => prev.filter(f => f.id !== id))} aria-label={'Remove ' + file.name}>×</button>
              </span>
            ))}
          </div>
        )}
        <div className="composer" onDragOver={e => e.preventDefault()}>
          <button className="attach-btn" onClick={() => fileRef.current && fileRef.current.click()} aria-label="Attach files" title="Attach files (CSV, Excel, JSON, Parquet, text, images)">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.2-9.19a4 4 0 0 1 5.65 5.66l-9.2 9.19a2 2 0 0 1-2.82-2.83l8.48-8.48"/></svg>
          </button>
          <input ref={fileRef} type="file" hidden multiple accept={ACCEPT} onChange={e => { addFiles(e.target.files); e.target.value = ''; }} />
          <textarea
            ref={inputRef} rows="1" value={text} dir="auto"
            placeholder={mode === 'data' ? 'Describe the data you need, or ask to connect a source…' : 'Ask anything, or attach a CSV/Excel…'}
            aria-label="Message the agent"
            onChange={e => { setText(e.target.value); autosize(e.target); }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
            onPaste={e => { if (e.clipboardData && e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
          />
          {running
            ? <button className="send-btn stop" onClick={onStop} aria-label="Stop generating" title="Stop">■</button>
            : <button className="send-btn" onClick={send} disabled={!canSend} aria-label="Send message" title="Send">↑</button>}
        </div>
        <div className="composer-hint">
          <span className="mode-switch" role="radiogroup" aria-label="Agent">
            {MODES.map(([m, label]) => <button key={m} role="radio" aria-checked={mode === m} className={mode === m ? 'active' : ''} onClick={() => setMode(m)}>{label}</button>)}
          </span>
          <span className="hint-desktop">⏎ send · Shift+⏎ new line · click a chart mark to ask about it</span>
          <span>{text.length > 0 ? text.length + ' chars' : ''}</span>
        </div>
      </div>
    </div>
  );
}
