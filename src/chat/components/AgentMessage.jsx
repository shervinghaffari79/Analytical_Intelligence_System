// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useEffect, useState } from 'react';
import Markdown from 'markdown-to-jsx';
import ChartCard from './ChartCard.jsx';
import { LoadPlanCard, PreviewCard, ConnectForm, QuestionsCard } from './Cards.jsx';

const md = { forceBlock: true, disableParsingRawHTML: true, overrides: { a: { props: { target: '_blank', rel: 'noopener noreferrer' } } } };

function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick(n => n + 1), 1000); return () => clearInterval(t); }, []);
  return <>{Math.max(1, Math.round((Date.now() - since) / 1000))}s</>;
}

export default function AgentMessage({ message, onAsk, onStop, onPatchBlock, onLoaded, showToast, isLast }) {
  const running = message.status === 'running';
  const [traceOpen, setTraceOpen] = useState(false);
  const [openSteps, setOpenSteps] = useState(() => new Set());
  const steps = message.steps || [];
  const blocks = message.blocks || [];
  const showTrace = traceOpen || running;
  const head = running
    ? <>Working{steps.length ? ` · ${steps[steps.length - 1].label}` : '…'} · <Elapsed since={message.startedAt} /></>
    : message.status === 'stopped' ? `Stopped after ${message.seconds || 1}s`
    : `${message.status === 'error' ? 'Failed' : 'Worked'} for ${message.seconds || 1} second${message.seconds === 1 ? '' : 's'} · ${steps.length} step${steps.length === 1 ? '' : 's'}`;

  const lastIsText = blocks.length > 0 && ['text', 'report'].includes(blocks[blocks.length - 1].type);

  return (
    <div className="msg-assistant">
      <div className="trace">
        <button className="trace-head" onClick={() => setTraceOpen(o => !o)} aria-expanded={showTrace}>
          {running ? <span className="spin" /> : <span className={'check' + (message.status === 'error' ? ' err' : '')}>{message.status === 'error' ? '!' : '✓'}</span>}
          <span>{head}</span>
          <span className="agent-tag">{message.agent === 'data' ? 'Data agent' : message.agent === 'analyst' ? 'Analyst' : ''}</span>
          <span className="trace-caret">{showTrace ? '▴' : '▾'}</span>
        </button>
        {running && <button className="stop-btn" onClick={() => onStop(message.id)}>■ Stop</button>}
        {showTrace && steps.length > 0 && (
          <div className="trace-steps">
            {steps.map((s, i) => (
              <div key={i}>
                <div className="step show">
                  <span className={'s-ico ' + s.status}>{s.status === 'running' ? <span className="mini-spin" /> : s.status === 'error' ? '!' : '✓'}</span>
                  <span>{s.label}</span>
                  {s.detail && <button className="expand" onClick={() => setOpenSteps(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; })}>{openSteps.has(i) ? 'Hide' : 'Details'}</button>}
                </div>
                {openSteps.has(i) && s.detail && <pre className="step-detail open" dir="auto">{s.detail}</pre>}
              </div>
            ))}
          </div>
        )}
      </div>

      {blocks.map((b, i) => {
        const key = message.id + '-' + i;
        const patch = nb => onPatchBlock(message.id, i, nb);
        switch (b.type) {
          case 'text':
          case 'report':
            return (
              <div key={key} className={'resp-text md' + (b.type === 'report' ? ' report' : '')} dir="auto">
                <Markdown options={md}>{b.text}</Markdown>
                {running && i === blocks.length - 1 && <span className="caret" aria-hidden="true" />}
              </div>
            );
          case 'chart': return <ChartCard key={b.id || key} block={b} onAsk={onAsk} showToast={showToast} />;
          case 'load_plan': return <LoadPlanCard key={key} block={b} onChange={patch} showToast={showToast} canAnalyze={!!message.question} onLoaded={(names, analyze) => onLoaded(names, analyze ? message.question : null)} />;
          case 'preview': return <PreviewCard key={key} block={b} onChange={patch} showToast={showToast} onLoaded={names => onLoaded(names, null)} />;
          case 'connect_form':
            return <ConnectForm key={key} sourceType={b.sourceType} prefilled={b.prefilled} status={b.status} showToast={showToast}
                                onConnected={info => { patch({ ...b, status: 'connected', prefilled: {} }); onLoaded([], null, info); }} />;
          case 'questions': return <QuestionsCard key={key} block={b} onAsk={onAsk} />;
          case 'error': return <div key={key} className="resp-error" role="alert" dir="auto">⚠ {b.text}</div>;
          case 'notice':
            return (
              <div key={key} className="resp-notice" dir="auto">{b.text}
                {b.action && isLast && !running && <button className="btn" onClick={() => onAsk(b.ask || 'Continue', b.askMode)}>{b.action}</button>}
              </div>
            );
          default: return null;
        }
      })}

      {running && !lastIsText && <div className="typing"><span /><span /><span /></div>}
    </div>
  );
}
