export const fmt$ = n => '$' + Math.round(Number(n) || 0).toLocaleString('en-US');
export const fmtK = n => {
  const a = Math.abs(Number(n) || 0);
  if (a >= 1e6) return '$' + ((n / 1e6).toFixed(2)) + 'M';
  if (a >= 1000) return '$' + (n / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k';
  return '$' + Math.round(n || 0);
};
export const fmtNum = n => Math.round(Number(n) || 0).toLocaleString('en-US');
export const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const relTime = ts => {
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};
export const fmtBytes = b => {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
  return (b / (1024 * 1024)).toFixed(1) + ' MB';
};
export const fmtCell = v => {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isInteger(v) ? (Math.abs(v) < 10000 ? String(v) : v.toLocaleString('en-US')) : v.toLocaleString('en-US', { maximumFractionDigits: 4 });
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  const s = String(v);
  return s.length > 120 ? s.slice(0, 119) + '…' : s;
};
