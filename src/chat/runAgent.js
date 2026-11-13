// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/* Turn orchestration for the chat UI.
 *
 * One composer, two backend agents:
 *   - the data-loading agent (/api/agent/data-loading-chat) finds, previews and
 *     loads data and can propose a new connection;
 *   - the analyst agent (/api/agent/analyst-streaming) answers questions over
 *     the tables already in the workspace and produces charts and reports.
 *
 * Both stream newline-delimited JSON. Each event is folded into the assistant
 * message as it arrives, so text, trace steps and charts appear in real time.
 *
 * Assistant message shape (persisted):
 *   { id, role:'assistant', agent:'data'|'analyst', question, status, startedAt,
 *     seconds, steps:[{label, detail, status}], blocks:[...] }
 * Block types: text | report | chart | load_plan | preview | connect_form |
 *              questions | error | notice
 */
import { api, apiForm, stream, errorText, URLS } from './api.js';

const TABULAR = /\.(csv|tsv|xlsx|xls|json|parquet)$/i;
const IMAGE = /\.(png|jpe?g|gif|webp)$/i;
const DATA_INTENT = /\b(load|import|connect|connection|data ?source|database|find (the )?(data|table)|what data|which tables|sample data(set)?s?)\b|بارگذاری|بارگیری|اتصال|متصل|پایگاه ?داده|دیتابیس|چه داده/i;
export const MAX_ROWS_KEPT = 5000;

const TOOL_LABELS = {
  find_data: 'Searched the catalog', list_data: 'Browsed data sources', describe_data: 'Read table metadata',
  probe_data: 'Queried the source', propose_load_plan: 'Proposed a load plan', list_connectors: 'Listed connector types',
  describe_connector: 'Read connector setup', propose_connection: 'Prepared a connection form',
  execute_python: 'Ran Python', read_file: 'Read a file', write_file: 'Wrote a file', list_directory: 'Listed files',
  fetch_url: 'Fetched a URL', show_user_data_preview: 'Prepared a data preview',
  inspect_source_data: 'Inspected the data', explore: 'Explored the data', inspect_chart: 'Checked the chart',
  search_knowledge: 'Searched knowledge', read_knowledge: 'Read knowledge', load_skill: 'Loaded a skill',
};
const ACTION_LABELS = {
  visualize: 'Building a chart', write_report: 'Writing a report', ask_user: 'Asking a question', delegate: 'Handing off',
};

const short = (v, n = 600) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v, null, 1);
  return s && s.length > n ? s.slice(0, n) + '…' : (s || '');
};

function toolDetail(tool, args) {
  if (!args || typeof args !== 'object') return '';
  if (args.code) return String(args.code);
  if (tool === 'find_data') return `query: ${args.query || ''}` + (args.scope ? ` · scope: ${args.scope}` : '');
  if (tool === 'describe_data' || tool === 'probe_data') {
    return [args.source_id, args.table_key].filter(Boolean).join(' › ') + (args.query ? '\n' + short(args.query) : '');
  }
  return short(args);
}

function resultDetail(ev) {
  if (ev.error) return 'Error: ' + short(ev.error, 400);
  if (typeof ev.result === 'string') return short(ev.result, 900);
  if (Array.isArray(ev.results)) return `${ev.results.length} match(es): ` + ev.results.slice(0, 8).map(r => r.name).join(', ');
  if (Array.isArray(ev.tables) || Array.isArray(ev.folders)) {
    const names = [...(ev.folders || []), ...(ev.tables || [])].map(x => x.name);
    return `${names.length} item(s): ${names.slice(0, 12).join(', ')}`;
  }
  if (Array.isArray(ev.rows)) return `${ev.rows.length} row(s)` + (ev.rows[0] ? ': ' + short(ev.rows.slice(0, 3), 400) : '');
  if (ev.stdout) return short(ev.stdout, 600);
  if (ev.content && typeof ev.content === 'string') return short(ev.content, 600);
  return '';
}

/* ---------- message mutation helpers (immutable) ---------- */

const appendText = (m, type, text) => {
  const blocks = m.blocks.slice();
  const last = blocks[blocks.length - 1];
  if (last && last.type === type) blocks[blocks.length - 1] = { ...last, text: last.text + text };
  else blocks.push({ type, text });
  return { ...m, blocks };
};
const addBlock = (m, block) => ({ ...m, blocks: [...m.blocks, block] });
const addStep = (m, step) => ({ ...m, steps: [...m.steps, { status: 'running', ...step }] });
const finishStep = (m, tool, patch) => {
  const steps = m.steps.slice();
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].status === 'running' && (!tool || steps[i].tool === tool)) {
      const detail = [steps[i].detail, patch.detail].filter(Boolean).join('\n\n');
      steps[i] = { ...steps[i], ...patch, detail };
      return { ...m, steps };
    }
  }
  return m;
};
const settleSteps = m => ({ ...m, steps: m.steps.map(s => (s.status === 'running' ? { ...s, status: 'done' } : s)) });

/* ---------- data-loading agent ---------- */

function actionBlocks(actions, seen) {
  const out = [];
  for (const a of actions || []) {
    const key = JSON.stringify([a.type, a.name, a.source_type, (a.candidates || []).map(c => c.table_key)]);
    if (seen.has(key)) continue; // the same action arrives in tool_result and in the final `actions` event
    seen.add(key);
    if (a.type === 'load_plan') {
      if (!(a.candidates || []).length) continue; // a plan with nothing to load is not worth a card
      out.push({
        type: 'load_plan', status: 'pending', reasoning: a.reasoning || '',
        candidates: (a.candidates || []).map(c => ({
          sourceId: c.source_id, tableKey: c.table_key, displayName: c.display_name || c.table_key,
          sourceTable: c.source_table, filters: c.filters || [], sortBy: c.sort_by, sortOrder: c.sort_order,
          error: c.resolution_error || null, selected: !c.resolution_error && c.selected !== false,
        })),
      });
    } else if (a.type === 'preview_table') {
      out.push({
        type: 'preview', status: a.error ? 'error' : 'pending', name: a.name, error: a.error || null,
        columns: a.columns || [], rows: a.sample_rows || [], totalRows: a.total_rows || 0, scratchPath: a.csv_scratch_path || '',
      });
    } else if (a.type === 'connect_form') {
      out.push({ type: 'connect_form', status: 'pending', sourceType: a.source_type, prefilled: a.prefilled || {} });
    }
  }
  return out;
}

async function runDataAgent({ messages, model, tables, update, signal }) {
  const seen = new Set();
  for await (const ev of stream(URLS.DATA_LOADING_CHAT, {
    model, messages, workspace_tables: tables.map(t => t.name),
  }, signal)) {
    switch (ev.type) {
      case 'text_delta': update(m => appendText(m, 'text', ev.content || '')); break;
      case 'tool_start':
        update(m => addStep(m, { tool: ev.tool, label: TOOL_LABELS[ev.tool] || ev.tool, detail: toolDetail(ev.tool, ev.args) }));
        break;
      case 'tool_result': {
        const blocks = actionBlocks(ev.actions, seen);
        update(m => {
          let next = finishStep(m, ev.tool, { status: ev.error ? 'error' : 'done', detail: resultDetail(ev) });
          for (const b of blocks) next = addBlock(next, b);
          return next;
        });
        break;
      }
      case 'actions': {
        const blocks = actionBlocks(ev.actions, seen);
        if (blocks.length) update(m => blocks.reduce(addBlock, m));
        break;
      }
      case 'continue_prompt':
        update(m => addBlock(m, { type: 'notice', text: 'I reached the step limit for this turn.', action: 'Continue' }));
        break;
      case 'error':
        update(m => addBlock(m, { type: 'error', text: (ev.error && ev.error.message) || ev.message || 'The agent reported an error.' }));
        break;
      default: break;
    }
  }
}

/* ---------- analyst agent ---------- */

/** Prior steps of this conversation, in the shape the analyst prompt expects. */
function focusedThread(history) {
  const steps = [];
  let question = '';
  for (const m of history) {
    if (m.role === 'user') { question = m.text || question; continue; }
    if (m.agent !== 'analyst') continue;
    question = m.question || question; // covers attachment-only turns with no typed text
    const summary = m.blocks.filter(b => b.type === 'text' || b.type === 'report').map(b => b.text).join('\n').slice(0, 600);
    const charts = m.blocks.filter(b => b.type === 'chart');
    if (!charts.length && summary) steps.push({ user_question: question, agent_summary: summary });
    charts.forEach((c, i) => steps.push({
      user_question: question, display_instruction: c.title, table_name: c.tableName,
      columns: c.columns, row_count: c.rowCount, chart_type: c.chartType, encodings: c.encodings,
      agent_summary: i === charts.length - 1 ? summary : '',
    }));
  }
  return steps.slice(-8);
}

function chartBlock(ev) {
  const r = (ev.content && ev.content.result) || {};
  const goal = r.refined_goal || {};
  const chart = goal.chart || {};
  const all = (r.content && r.content.rows) || [];
  const virtual = (r.content && r.content.virtual) || {};
  const semantic = {};
  for (const [f, meta] of Object.entries(goal.field_metadata || {})) {
    if (meta && typeof meta === 'object' && meta.semantic_type) semantic[f] = meta.semantic_type;
  }
  return {
    type: 'chart', id: r.chart_id || ('chart-' + Date.now().toString(36)),
    title: goal.title || goal.display_instruction || (ev.content && ev.content.question) || 'Chart',
    instruction: goal.display_instruction || '',
    chartType: chart.chart_type || 'Auto', encodings: chart.encodings || {}, config: chart.config || {},
    semantic, displayNames: goal.field_display_names || {},
    columns: goal.output_fields || (all[0] ? Object.keys(all[0]) : []),
    rows: all.slice(0, MAX_ROWS_KEPT), rowCount: virtual.row_count ?? all.length,
    tableName: virtual.table_name || '', code: r.code || '',
  };
}

async function runAnalyst({ question, history, model, agentMode, tables, images, update, signal }) {
  let sawReport = false;
  let fatal = null;
  for await (const ev of stream(URLS.ANALYST, {
    model, agent_mode: agentMode === 'mini' ? 'mini' : 'standard', max_iterations: 5,
    input_tables: tables.map(t => ({ name: t.name })), user_question: question,
    focused_thread: focusedThread(history),
    attached_images: images && images.length ? images : undefined,
  }, signal)) {
    switch (ev.type) {
      case 'thinking_text':
        update(m => settleSteps(addStep(m, { label: 'Thinking', detail: ev.content || '', status: 'done' })));
        break;
      case 'action':
        update(m => addStep(settleSteps(m), {
          tool: ev.action, label: ACTION_LABELS[ev.action] || ev.action,
          detail: [ev.display_instruction, (ev.input_tables || []).length ? 'tables: ' + ev.input_tables.join(', ') : ''].filter(Boolean).join('\n'),
        }));
        break;
      case 'tool_start':
        update(m => addStep(m, { tool: ev.tool, label: TOOL_LABELS[ev.tool] || ev.tool, detail: toolDetail(ev.tool, ev.args || ev) }));
        break;
      case 'tool_result':
        update(m => finishStep(m, ev.tool, { status: ev.status === 'error' ? 'error' : 'done', detail: resultDetail(ev) }));
        break;
      case 'skill_loaded':
        update(m => addStep(m, { label: `Loaded skill: ${ev.skill}`, status: 'done' }));
        break;
      case 'result': {
        const block = chartBlock(ev);
        update(m => addBlock(finishStep(m, 'visualize', { status: 'done', detail: block.code }), block));
        break;
      }
      case 'text_delta':
        sawReport = true;
        update(m => appendText(m, ev.channel === 'report' ? 'report' : 'text', ev.content || ''));
        break;
      case 'interact':
        update(m => addBlock(settleSteps(m), { type: 'questions', questions: ev.questions || [], thought: ev.thought || '' }));
        break;
      case 'delegate':
        update(m => addBlock(settleSteps(m), {
          type: 'notice', text: ev.delegate_prompt || ev.reason || 'This needs data that is not loaded yet.',
          action: 'Find data', ask: ev.delegate_prompt || question, askMode: 'data',
        }));
        break;
      case 'error':
        // Usually recoverable (the agent retries); shown in the trace, and only
        // surfaced as a block if the run then ends without anything to show.
        fatal = ev.message || (ev.error && ev.error.message) || 'The agent reported an error.';
        update(m => finishStep(m, null, { status: 'error', detail: 'Error: ' + short(fatal, 500) }));
        break;
      case 'completion': {
        const summary = ev.content && ev.content.summary;
        update(m => {
          let next = settleSteps(m);
          if (summary && !sawReport && !next.blocks.some(b => b.type === 'text' && b.text.includes(summary))) {
            next = appendText(next, 'text', summary);
          }
          const empty = !next.blocks.some(b => b.type !== 'notice');
          if (empty) next = addBlock(next, { type: 'error', text: fatal || `The agent finished without a result (${ev.status || 'no output'}).` });
          return next;
        });
        break;
      }
      default: break;
    }
  }
}

/* ---------- attachments ---------- */

const fileToDataUrl = file => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(r.result);
  r.onerror = () => reject(r.error);
  r.readAsDataURL(file);
});

/** Upload tabular files into the workspace; inline images/text for the agent. */
async function ingestAttachments(files, update, signal) {
  const loaded = [], images = [], notes = [];
  for (const file of files) {
    if (TABULAR.test(file.name)) {
      update(m => addStep(m, { tool: 'upload', label: `Uploading ${file.name}` }));
      try {
        const form = new FormData();
        form.append('file', file);
        form.append('table_name', file.name.replace(/\.[^.]+$/, ''));
        const data = await apiForm(URLS.CREATE_TABLE, form, { signal });
        loaded.push(data.table_name);
        update(m => finishStep(m, 'upload', { status: 'done', detail: `${data.table_name}: ${data.row_count} rows × ${(data.columns || []).length} columns` }));
      } catch (err) {
        update(m => finishStep(m, 'upload', { status: 'error', detail: errorText(err) }));
        notes.push(`Could not load ${file.name}: ${errorText(err)}`);
      }
    } else if (IMAGE.test(file.name)) {
      images.push(await fileToDataUrl(file));
    } else {
      const text = (await file.text()).slice(0, 6000);
      notes.push(`Attached file ${file.name}:\n${text}`);
    }
  }
  return { loaded, images, notes };
}

/* ---------- entry point ---------- */

export function chooseAgent({ text, mode, tables, hasTabular }) {
  if (mode === 'data') return 'data';
  if (mode === 'analyst') return 'analyst';
  if (hasTabular) return 'analyst';
  if (!tables.length) return 'data';
  return DATA_INTENT.test(text || '') ? 'data' : 'analyst';
}

/**
 * Run one assistant turn. `update(fn)` applies an immutable change to the
 * assistant message; `refreshTables()` re-reads the workspace and returns it.
 */
export async function runTurn({ text, files = [], mode, history, model, agentMode, tables, update, refreshTables, signal }) {
  const started = Date.now();
  let status = 'done';
  try {
    if (!model) throw new Error('No model is configured on the server. Set one in .env (for example LOCAL_ENABLED=true) and restart.');
    let question = text;
    let images = [];
    let current = tables;
    if (files.length) {
      const { loaded, images: imgs, notes } = await ingestAttachments(files, update, signal);
      images = imgs;
      if (loaded.length) current = await refreshTables();
      if (!question) question = loaded.length ? `Give me an overview of ${loaded.join(', ')} with the most informative chart.` : 'Describe the attachment.';
      if (notes.length) question += '\n\n' + notes.join('\n\n');
    }
    const agent = chooseAgent({ text: question, mode, tables: current, hasTabular: files.some(f => TABULAR.test(f.name)) });
    update(m => ({ ...m, agent, question }));

    if (agent === 'data') {
      const msgs = history.filter(m => m.role === 'user' || m.agent === 'data').map(m => (m.role === 'user'
        ? { role: 'user', content: m.text || '' }
        : { role: 'assistant', content: m.blocks.filter(b => b.type === 'text').map(b => b.text).join('\n') || '(shown in the UI)' }));
      msgs.push({ role: 'user', content: question });
      await runDataAgent({ messages: msgs, model, tables: current, update, signal });
    } else {
      if (!current.length) throw new Error('There is no data loaded yet. Ask me to find or load data first, or attach a file.');
      await runAnalyst({ question, history, model, agentMode, tables: current, images, update, signal });
    }
  } catch (err) {
    if (err && err.name === 'AbortError') status = 'stopped';
    else { status = 'error'; update(m => addBlock(m, { type: 'error', text: errorText(err) })); }
  }
  update(m => ({ ...settleSteps(m), status, seconds: Math.max(1, Math.round((Date.now() - started) / 1000)) }));
  return status;
}

/* ---------- actions triggered from cards ---------- */

/** Import the selected candidates of a load plan. Returns loaded table names. */
export async function importLoadPlan(candidates, onProgress) {
  const loaded = [];
  for (const c of candidates) {
    onProgress && onProgress(c, 'loading');
    const data = await api(URLS.IMPORT_DATA, {
      connector_id: c.sourceId, source_table: c.sourceTable,
      // Name the workspace table after the source table, not the agent's
      // free-text display label (which may carry a description).
      table_name: String(c.tableKey || c.sourceTable || c.displayName).split('.').pop(),
      import_options: {
        source_filters: c.filters || [],
        sort_columns: c.sortBy ? [c.sortBy] : undefined, sort_order: c.sortOrder,
      },
    });
    loaded.push(data.table_name);
    onProgress && onProgress(c, 'loaded', data);
  }
  return loaded;
}

/** Load a previewed scratch table (an agent-produced CSV) into the workspace. */
export async function loadPreview(block) {
  const path = String(block.scratchPath || '').replace(/^scratch\//, '');
  const resp = await fetch(`${URLS.SCRATCH}/${path}`, { headers: { 'X-Workspace-Id': localStorage.getItem('df_chat_workspace_id') || '', 'X-Identity-Id': 'browser:' + (localStorage.getItem('df_browser_id') || '') } });
  if (!resp.ok) throw new Error(`Could not read the preview file (HTTP ${resp.status})`);
  const blob = await resp.blob();
  const form = new FormData();
  form.append('file', new File([blob], `${block.name}.csv`, { type: 'text/csv' }));
  form.append('table_name', block.name);
  return apiForm(URLS.CREATE_TABLE, form);
}
