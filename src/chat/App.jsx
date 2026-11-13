// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorText, URLS } from './api.js';
import { runTurn } from './runAgent.js';
import { loadConversations, saveConversations, loadPrefs, savePrefs, newId, msgId } from './store.js';
import HistoryRail from './components/HistoryRail.jsx';
import AgentMessage from './components/AgentMessage.jsx';
import Composer from './components/Composer.jsx';
import DataPanel from './components/DataPanel.jsx';

const NO_DATA_SUGGESTIONS = ['What data is available?', 'Load a sample dataset and show me something interesting', 'Connect to my database'];

export default function App() {
  const initial = useMemo(() => loadConversations(), []);
  const prefs = useMemo(() => loadPrefs(), []);
  const [conversations, setConversations] = useState(initial.convs);
  const [activeId, setActiveId] = useState(initial.active);
  const [toast, setToast] = useState(null);
  const [railOpen, setRailOpen] = useState(false);
  const [dataOpen, setDataOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [tables, setTables] = useState([]);
  const [models, setModels] = useState([]);
  const [modelId, setModelId] = useState(prefs.modelId || '');
  const [agentMode, setAgentMode] = useState(prefs.agentMode || 'standard');
  const [mode, setMode] = useState('auto');
  const [serverError, setServerError] = useState(null);

  const dragDepth = useRef(0);
  const toastTimer = useRef(null);
  const scrollRef = useRef(null);
  const stickRef = useRef(true);
  const convRef = useRef(conversations);
  const tablesRef = useRef(tables);
  const aborts = useRef(new Map());
  convRef.current = conversations;
  tablesRef.current = tables;

  const model = models.find(m => m.id === modelId) || models[0] || null;
  const active = conversations.find(c => c.id === activeId) || null;
  const sorted = useMemo(() => [...conversations].sort((a, b) => b.updatedAt - a.updatedAt), [conversations]);
  const running = !!active && active.messages.some(m => m.role === 'assistant' && m.status === 'running');

  useEffect(() => {
    const t = setTimeout(() => saveConversations({ convs: conversations, active: activeId }), 400);
    return () => clearTimeout(t);
  }, [conversations, activeId]);
  useEffect(() => { savePrefs({ modelId, agentMode }); }, [modelId, agentMode]);
  useEffect(() => () => clearTimeout(toastTimer.current), []);

  const showToast = useCallback(msg => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3200);
  }, []);

  const refreshTables = useCallback(async () => {
    try {
      const data = await api(URLS.LIST_TABLES);
      const list = (data.tables || []).filter(t => !/^d[-_]/.test(t.name)); // derived chart tables stay internal
      setTables(list);
      tablesRef.current = list;
      return list;
    } catch (err) { setServerError(errorText(err)); return tablesRef.current; }
  }, []);

  useEffect(() => {
    refreshTables();
    api(URLS.LIST_MODELS).then(list => setModels(Array.isArray(list) ? list : [])).catch(err => setServerError(errorText(err)));
  }, [refreshTables]);

  /* ---- scrolling: follow the stream unless the user scrolled up ---- */
  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  };
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [conversations, activeId]);
  useEffect(() => { stickRef.current = true; }, [activeId]);

  /* ---- sending ---- */
  const sendQuery = useCallback((text, files = [], forcedMode) => {
    const trimmed = (text || '').trim();
    if (!trimmed && files.length === 0) return;
    const existing = convRef.current.find(c => c.id === activeIdRef.current);
    if (existing && existing.messages.some(m => m.role === 'assistant' && m.status === 'running')) return;

    const convId = existing ? existing.id : newId();
    const userMsg = { id: msgId(), role: 'user', text: trimmed, ts: Date.now(), attachments: files.map(f => ({ name: f.name, size: f.size })) };
    const asstId = msgId();
    const asst = { id: asstId, role: 'assistant', ts: Date.now(), startedAt: Date.now(), status: 'running', agent: '', question: trimmed, steps: [], blocks: [] };
    const history = existing ? existing.messages : [];

    setConversations(prev => {
      const convs = [...prev];
      const idx = convs.findIndex(c => c.id === convId);
      if (idx >= 0) convs[idx] = { ...convs[idx], messages: [...convs[idx].messages, userMsg, asst], updatedAt: Date.now() };
      else convs.unshift({ id: convId, title: (trimmed || files[0].name).slice(0, 70), createdAt: Date.now(), updatedAt: Date.now(), messages: [userMsg, asst] });
      return convs;
    });
    setActiveId(convId);
    activeIdRef.current = convId;
    stickRef.current = true;

    const update = fn => setConversations(prev => prev.map(c => (c.id !== convId ? c : {
      ...c, updatedAt: Date.now(), messages: c.messages.map(m => (m.id === asstId ? fn(m) : m)),
    })));
    const controller = new AbortController();
    aborts.current.set(asstId, controller);
    runTurn({
      text: trimmed, files, mode: forcedMode || modeRef.current, history, model: modelRef.current, agentMode: agentModeRef.current,
      tables: tablesRef.current, update, refreshTables, signal: controller.signal,
    }).finally(() => { aborts.current.delete(asstId); refreshTables(); });
  }, [refreshTables]);

  // Latest values for the stable `sendQuery` callback.
  const activeIdRef = useRef(activeId); activeIdRef.current = activeId;
  const modeRef = useRef(mode); modeRef.current = mode;
  const modelRef = useRef(model); modelRef.current = model;
  const agentModeRef = useRef(agentMode); agentModeRef.current = agentMode;

  const stop = useCallback(id => {
    if (id) { const c = aborts.current.get(id); if (c) c.abort(); return; }
    aborts.current.forEach(c => c.abort());
  }, []);

  const patchBlock = useCallback((messageId, index, block) => {
    setConversations(prev => prev.map(c => ({
      ...c, messages: c.messages.map(m => (m.id === messageId ? { ...m, blocks: m.blocks.map((b, i) => (i === index ? block : b)) } : m)),
    })));
  }, []);

  // A card finished loading data or connecting a source: carry on with the task.
  const onLoaded = useCallback(async (names, question, connected) => {
    await refreshTables();
    if (connected) sendQuery(`I connected "${connected.name}". Continue with what I asked for.`, [], 'data');
    else if (question) sendQuery(question, [], 'analyst');
  }, [refreshTables, sendQuery]);

  const ask = useCallback((text, forcedMode) => sendQuery(text, [], forcedMode), [sendQuery]);
  const newConversation = useCallback(() => setActiveId(null), []);
  const deleteConversation = useCallback(id => {
    setConversations(prev => prev.filter(c => c.id !== id));
    setActiveId(prev => (prev === id ? null : prev));
    showToast('Conversation deleted');
  }, [showToast]);
  const clearAll = useCallback(() => { stop(); setConversations([]); setActiveId(null); showToast('All conversations cleared'); }, [showToast, stop]);

  const onDrop = e => {
    e.preventDefault(); dragDepth.current = 0; setDragging(false);
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) window.dispatchEvent(new CustomEvent('agentic-drop-files', { detail: { files } }));
  };

  const suggestions = tables.length
    ? [...tables.slice(0, 3).map(t => `Give me an overview of ${t.name}`), 'What data is available?']
    : NO_DATA_SUGGESTIONS;

  return (
    <div className={'app' + (railOpen ? ' rail-open' : '')}>
      <header className="mobile-bar">
        <button className="mb-btn" onClick={() => setRailOpen(o => !o)} aria-label="Toggle history" aria-expanded={railOpen}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="4" y1="7" x2="20" y2="7" /><line x1="4" y1="12" x2="20" y2="12" /><line x1="4" y1="17" x2="20" y2="17" /></svg>
        </button>
        <div className="mb-title"><span className="mb-spark">✦</span> Agentic Analytics</div>
        <button className="mb-btn" onClick={newConversation} aria-label="New conversation">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </header>

      <HistoryRail conversations={sorted} activeId={activeId} onNew={newConversation} onOpen={setActiveId}
                   onDelete={deleteConversation} onClearAll={clearAll} open={railOpen} onClose={() => setRailOpen(false)}
                   tables={tables} onOpenData={() => setDataOpen(true)} models={models} model={model} setModelId={setModelId}
                   agentMode={agentMode} setAgentMode={setAgentMode} />
      {railOpen && <div className="rail-backdrop" onClick={() => setRailOpen(false)} aria-hidden="true" />}

      <main className="chat-wrap"
            onDragEnter={e => { e.preventDefault(); dragDepth.current += 1; setDragging(true); }}
            onDragOver={e => e.preventDefault()}
            onDragLeave={e => { e.preventDefault(); dragDepth.current -= 1; if (dragDepth.current <= 0) setDragging(false); }}
            onDrop={onDrop}>
        {dragging && <div className="drop-overlay" aria-hidden="true"><div className="drop-box">📎<br />Drop files to attach &amp; analyze</div></div>}
        {(serverError || (!model && models.length === 0)) && (
          <div className="banner" role="alert">
            {serverError ? `Cannot reach the server: ${serverError}` : 'No model is configured on the server yet — set one in .env and restart.'}
            {serverError && <button className="link-btn" onClick={() => { setServerError(null); refreshTables(); }}>Retry</button>}
          </div>
        )}
        <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="chat-inner">
            {!active || active.messages.length === 0 ? (
              <div className="welcome">
                <div className="glyph" aria-hidden="true">✦</div>
                <h1>Ask your data anything</h1>
                <p>The agent finds the right tables in your connected sources, loads them, and builds <b>interactive charts</b> as it works. <b>Click</b> any mark to ask a follow-up about it, switch chart types, or view the table and code. <b>Attach a CSV / Excel file</b> to analyze it directly.</p>
                <div className="suggestions" role="list">
                  {suggestions.map(s => <button key={s} className="chip" role="listitem" dir="auto" onClick={() => sendQuery(s)}>✦ {s}</button>)}
                  <button className="chip" role="listitem" onClick={() => setDataOpen(true)}>▦ Manage data &amp; sources</button>
                </div>
              </div>
            ) : (
              active.messages.map((m, i) => m.role === 'user' ? (
                <div key={m.id} className="msg-user">
                  <div className="bubble-col">
                    {m.attachments && m.attachments.length > 0 && (
                      <div className="bubble-attachments">
                        {m.attachments.map((a, k) => <span key={k} className="bubble-attach">📄 {a.name}</span>)}
                      </div>
                    )}
                    {m.text && <div className="bubble-user" dir="auto">{m.text}</div>}
                  </div>
                </div>
              ) : (
                <AgentMessage key={m.id} message={m} onAsk={ask} onStop={stop} onPatchBlock={patchBlock} onLoaded={onLoaded}
                              showToast={showToast} isLast={i === active.messages.length - 1} />
              ))
            )}
          </div>
        </div>
        <Composer onSend={sendQuery} onStop={() => stop()} running={running} mode={mode} setMode={setMode} showToast={showToast} />
      </main>

      <DataPanel open={dataOpen} onClose={() => setDataOpen(false)} tables={tables} refreshTables={refreshTables}
                 model={model} showToast={showToast} onAsk={ask} />
      {toast && <div className="toast show" role="status">{toast}</div>}
    </div>
  );
}
