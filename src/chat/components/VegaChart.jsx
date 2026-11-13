// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React, { useEffect, useRef, useState } from 'react';
import embed from 'vega-embed';
import { assembleVegaLite } from 'flint-chart';

/* Renders a backend chart spec ({chartType, encodings, config} + rows) with the
 * same engine the main app uses (flint-chart → Vega-Lite), so every chart type
 * the agents can emit is supported here too. */
export default function VegaChart({ chartType, encodings, config, rows, semantic, displayNames, height = 320, onDatum, viewRef }) {
  const hostRef = useRef(null);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState(null);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    const measure = () => setWidth(w => {
      const next = Math.max(240, Math.floor(el.clientWidth));
      return Math.abs(next - w) > 8 ? next : w;
    });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = hostRef.current;
    if (!el || !width) return undefined;
    let view = null;
    let cancelled = false;
    setError(null);
    try {
      const names = displayNames && Object.keys(displayNames).length ? { field_display_names: displayNames } : {};
      const spec = assembleVegaLite({
        data: { values: rows },
        semantic_types: semantic || {},
        chart_spec: {
          chartType, encodings,
          baseSize: { width: Math.max(200, width - 150), height },
          canvasSize: { width: Math.max(220, width - 40), height: Math.round(height * 1.5) },
          chartProperties: config || {},
        },
        options: { addTooltips: true },
        ...names,
      });
      if (!spec) throw new Error(`"${chartType}" cannot be drawn with these fields.`);
      spec.background = 'transparent';
      spec.config = { ...(spec.config || {}), font: 'Vazirmatn, "Segoe UI", Tahoma, Inter, Roboto, Arial, sans-serif' };
      embed(el, spec, { actions: false, renderer: 'svg', tooltip: { theme: 'light' } })
        .then(res => {
          if (cancelled) { res.finalize(); return; }
          view = res;
          if (viewRef) viewRef.current = res.view;
          if (onDatum) {
            res.view.addEventListener('click', (_e, item) => {
              const d = item && item.datum;
              if (d && typeof d === 'object') onDatum(d);
            });
          }
        })
        .catch(e => { if (!cancelled) setError(String((e && e.message) || e)); });
    } catch (e) {
      setError(String((e && e.message) || e));
    }
    return () => {
      cancelled = true;
      if (viewRef) viewRef.current = null;
      if (view) view.finalize();
    };
  }, [chartType, encodings, config, rows, semantic, displayNames, width, height, onDatum, viewRef]);

  return (
    <div className="vega-wrap">
      {error && <div className="chart-error">This chart type could not be drawn: {error}. Try another type or the table view.</div>}
      <div ref={hostRef} className="vega-host" style={error ? { display: 'none' } : undefined} />
    </div>
  );
}
