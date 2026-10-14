/* Coordinated GPS trace and speed inspection. No external chart library. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SRTTrace = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const colors = {current: '#841617', best: '#777777', recent: '#841617'};
  const labels = {current: 'Current', best: 'Best', recent: 'Recent speed'};
  const hint = 'Hover or tap the trace to inspect speed. Arrow keys also select samples.';
  const finite = Number.isFinite;
  function projectTrace(points, origin) {
    if (!origin) return [];
    const radians = Math.PI / 180, radius = 6371000, cos = Math.cos(origin.lat * radians);
    return points.filter(p => finite(p.lat) && finite(p.lon)).map(p => ({...p, t: p.elapsed_s, key: p.elapsed_s,
      x: (p.lon - origin.lon) * radians * radius * cos, y: (p.lat - origin.lat) * radians * radius}));
  }
  function layoutTrace(timing, width, height) {
    const raw = (timing.current_trace || []).concat(timing.best_trace || []);
    const origin = raw.find(p => finite(p.lat) && finite(p.lon));
    const series = ['current', 'best'].map(kind => ({kind, points: projectTrace(timing[kind + '_trace'] || [], origin)}));
    const all = series.flatMap(s => s.points);
    if (!all.length) return series;
    const xs = all.map(p => p.x), ys = all.map(p => p.y);
    const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
    const scale = Math.min(Math.max(1, width - 64) / Math.max(2, maxx - minx), Math.max(1, height - 64) / Math.max(2, maxy - miny));
    return series.map(s => ({...s, points: s.points.map(p => ({...p,
      px: width / 2 + (p.x - (minx + maxx) / 2) * scale,
      py: height / 2 - (p.y - (miny + maxy) / 2) * scale}))}));
  }
  function nearestTracePoint(series, position, tolerance = 18) {
    let hit = null, distance = tolerance;
    for (const {kind, points} of series) {
      for (let i = 0; i < points.length; i++) {
        const a = points[Math.max(0, i - 1)], b = points[i], dx = b.px - a.px, dy = b.py - a.py;
        const length = dx * dx + dy * dy;
        const fraction = length ? Math.max(0, Math.min(1, ((position.x - a.px) * dx + (position.y - a.py) * dy) / length)) : 0;
        const d = Math.hypot(position.x - a.px - fraction * dx, position.y - a.py - fraction * dy);
        if (d < distance) {
          distance = d;
          // Show a captured sample, including its captured speed, rather than live speed.
          hit = {kind, point: fraction < 0.5 ? a : b};
        }
      }
    }
    return hit;
  }
  function nearestTimePoint(points, time) {
    let selected = null, distance = Infinity;
    for (const p of points) if (finite(p.t) && Math.abs(p.t - time) < distance) {selected = p; distance = Math.abs(p.t - time);}
    return selected;
  }
  function pointAtElapsed(points, elapsed) {
    if (!points.length || !finite(elapsed)) return null;
    if (elapsed <= points[0].t) return points[0];
    for (let i = 1; i < points.length; i++) if (points[i].t >= elapsed) {
      const a = points[i - 1], b = points[i], fraction = (elapsed - a.t) / (b.t - a.t || 1);
      return {...b, px: a.px + fraction * (b.px - a.px), py: a.py + fraction * (b.py - a.py)};
    }
    return points[points.length - 1];
  }
  function formatSample(kind, point) {
    const speed = finite(point.speed_mph) ? point.speed_mph.toFixed(1) + ' mph' : 'Speed unavailable';
    const time = finite(point.t) ? (kind === 'recent' ? (-point.t).toFixed(1) + ' s before latest' : point.t.toFixed(1) + ' s') : 'Time unavailable';
    return labels[kind] + ' · ' + speed + ' · ' + time + (finite(point.distance_m) ? ' · ' + point.distance_m.toFixed(0) + ' m' : '');
  }
  class RecentHistory {
    constructor() {this.points = []; this.session = undefined;}
    add(packet) {
      const fields = packet.fields || {}, age = packet.live_capture_age_s ?? packet.age_s;
      const time = fields.Sample_Time_s ?? fields.Pi_Elapsed_Time_s;
      if (!finite(fields.Speed_mph) || !finite(time) || !finite(age) || age >= 2) return;
      const session = packet.session || 'setup', last = this.points[this.points.length - 1];
      if (session !== this.session || last && time < last.time) this.points = [];
      this.session = session;
      if (this.points[this.points.length - 1]?.time === time) return;
      this.points.push({time, key: time, speed_mph: fields.Speed_mph});
      this.points = this.points.filter(p => p.time >= time - 60).slice(-300);
    }
    series() {
      const latest = this.points[this.points.length - 1]?.time || 0;
      return [{kind: 'recent', points: this.points.map(p => ({...p, t: p.time - latest}))}];
    }
  }
  function createView({traceCanvas, speedCanvas, tooltip, readout}) {
    const doc = traceCanvas.ownerDocument, view = doc.defaultView, recent = new RecentHistory();
    let timing = {}, traceSeries = [], speedSeries = [], selection = null, anchor = null, sticky = false;
    let traceSize, speedSize, domain;
    function size(canvas) {
      const rect = canvas.getBoundingClientRect(), width = rect.width, height = rect.height;
      const ratio = view?.devicePixelRatio || 1, ctx = canvas.getContext('2d');
      if (canvas.width !== Math.round(width * ratio)) canvas.width = Math.round(width * ratio);
      if (canvas.height !== Math.round(height * ratio)) canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
      ctx.font = '11px Arial, sans-serif'; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      return {width, height, rect, ctx};
    }
    function line(ctx, points, kind, speed = false) {
      ctx.strokeStyle = colors[kind]; ctx.lineWidth = 2.5; ctx.setLineDash(kind === 'best' ? [5, 4] : []);
      ctx.beginPath(); let started = false;
      for (const point of points) {
        if (speed && !finite(point.speed_mph)) {started = false; continue;}
        started ? ctx.lineTo(point.px, point.py) : ctx.moveTo(point.px, point.py); started = true;
      }
      ctx.stroke(); ctx.setLineDash([]);
    }
    function dot(ctx, point, color, radius = 4) {
      if (!point) return;
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(point.px, point.py, radius, 0, Math.PI * 2); ctx.fill();
    }
    function selected() {
      if (!selection) return null;
      const series = speedSeries.find(s => s.kind === selection.kind);
      const point = series?.points.find(p => p.key === selection.key);
      return point ? {kind: selection.kind, point} : null;
    }
    function draw() {
      if (!traceCanvas.getBoundingClientRect().width) return;
      traceSize = size(traceCanvas); speedSize = size(speedCanvas);
      traceSeries = layoutTrace(timing, traceSize.width, traceSize.height);
      const ctx = traceSize.ctx;
      ctx.strokeStyle = '#eeeeeb'; ctx.lineWidth = 1;
      for (let x = 32; x < traceSize.width; x += 50) {ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, traceSize.height); ctx.stroke();}
      for (let y = 32; y < traceSize.height; y += 50) {ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(traceSize.width, y); ctx.stroke();}
      const hasTrace = traceSeries.some(s => s.points.length);
      if (!hasTrace) {
        ctx.fillStyle = '#666'; ctx.fillText('No active timing trace', 20, 30);
        ctx.fillText('A trace appears after the timing gate is crossed.', 20, 49);
      }
      for (const s of [...traceSeries].reverse()) line(ctx, s.points, s.kind);
      dot(ctx, traceSeries[0].points.at(-1), colors.current);
      dot(ctx, pointAtElapsed(traceSeries[1].points, timing.current_elapsed_s), colors.best);
      const raw = hasTrace ? traceSeries : recent.series();
      const samples = raw.flatMap(s => s.points).filter(p => finite(p.t));
      const speeds = samples.map(p => p.speed_mph).filter(finite);
      const minT = raw[0].kind === 'recent' ? -60 : Math.min(0, ...samples.map(p => p.t));
      const maxT = raw[0].kind === 'recent' ? 0 : Math.max(1, ...samples.map(p => p.t));
      const maxSpeed = Math.max(10, Math.ceil(Math.max(0, ...speeds) / 10) * 10);
      domain = {minT, maxT, maxSpeed, left: 42, right: speedSize.width - 16, top: 32, bottom: speedSize.height - 32};
      const px = time => domain.left + (time - minT) / (maxT - minT) * (domain.right - domain.left);
      const py = value => domain.bottom - value / maxSpeed * (domain.bottom - domain.top);
      speedSeries = raw.map(s => ({...s, points: s.points.filter(p => finite(p.t)).map(p => ({...p, px: px(p.t), py: py(p.speed_mph)}))}));
      const sc = speedSize.ctx; sc.fillStyle = '#666';
      sc.fillText('Speed · mph', 12, 17); sc.textAlign = 'right';
      for (let i = 0; i <= 4; i++) {
        const value = i * maxSpeed / 4, y = py(value);
        sc.strokeStyle = '#e4e4df'; sc.lineWidth = 1; sc.beginPath(); sc.moveTo(domain.left, y); sc.lineTo(domain.right, y); sc.stroke();
        sc.fillText(value.toFixed(value % 1 ? 1 : 0), domain.left - 8, y + 4);
      }
      sc.textAlign = 'center';
      for (let i = 0; i <= 3; i++) {
        const time = minT + (maxT - minT) * i / 3;
        sc.fillText(time.toFixed(0), px(time), speedSize.height - 12);
      }
      sc.textAlign = 'right'; sc.fillText(raw[0].kind === 'recent' ? 'Last 60 s' : 'Elapsed · s', domain.right, 17); sc.textAlign = 'left';
      for (const s of [...speedSeries].reverse()) line(sc, s.points, s.kind, true);
      if (!speeds.length) sc.fillText('Waiting for speed samples', domain.left + 10, domain.top + 25);
      const hit = selected();
      if (!hit) {selection = null; tooltip.hidden = true; if (readout.textContent !== hint) readout.textContent = hint; return;}
      const mapPoint = traceSeries.find(s => s.kind === hit.kind)?.points.find(p => p.key === hit.point.key);
      if (mapPoint) {dot(ctx, mapPoint, '#fff', 7); dot(ctx, mapPoint, colors[hit.kind], 4);}
      if (finite(hit.point.speed_mph)) {
        sc.strokeStyle = '#999'; sc.lineWidth = 1; sc.setLineDash([3, 3]);
        sc.beginPath(); sc.moveTo(hit.point.px, domain.top); sc.lineTo(hit.point.px, domain.bottom); sc.stroke(); sc.setLineDash([]);
        dot(sc, hit.point, colors[hit.kind], 5);
      }
      const description = formatSample(hit.kind, hit.point);
      if (tooltip.textContent !== description) tooltip.textContent = description;
      if (readout.textContent !== description) readout.textContent = description;
      tooltip.hidden = false;
      const parent = tooltip.parentElement.getBoundingClientRect();
      const position = anchor || {x: traceSize.rect.left - parent.left + (mapPoint?.px || 20), y: traceSize.rect.top - parent.top + (mapPoint?.py || 20)};
      tooltip.style.left = Math.max(8, Math.min(position.x + 12, parent.width - tooltip.offsetWidth - 8)) + 'px';
      tooltip.style.top = Math.max(8, Math.min(position.y + 12, parent.height - tooltip.offsetHeight - 8)) + 'px';
    }
    function inspect(event, canvas) {
      if (!traceSize || !domain) return;
      const rect = canvas.getBoundingClientRect(), position = {x: event.clientX - rect.left, y: event.clientY - rect.top};
      let hit = null;
      if (canvas === traceCanvas) hit = nearestTracePoint(traceSeries, position);
      else if (position.x >= domain.left && position.x <= domain.right && position.y >= domain.top && position.y <= domain.bottom) {
        const time = domain.minT + (position.x - domain.left) / (domain.right - domain.left) * (domain.maxT - domain.minT);
        let distance = Infinity;
        for (const s of speedSeries) {
          const point = nearestTimePoint(s.points, time);
          if (point && finite(point.speed_mph)) {
            const d = Math.hypot(position.x - point.px, position.y - point.py);
            if (d < distance) {distance = d; hit = {kind: s.kind, point};}
          }
        }
      }
      selection = hit ? {kind: hit.kind, key: hit.point.key} : null;
      const parent = tooltip.parentElement.getBoundingClientRect();
      anchor = {x: event.clientX - parent.left, y: event.clientY - parent.top}; draw();
    }
    for (const canvas of [traceCanvas, speedCanvas]) {
      canvas.addEventListener('pointermove', event => {sticky = event.pointerType === 'touch'; inspect(event, canvas);});
      canvas.addEventListener('pointerdown', event => {sticky = event.pointerType === 'touch'; inspect(event, canvas);});
      canvas.addEventListener('pointerleave', () => {if (!sticky) {selection = null; anchor = null; draw();}});
      canvas.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
        event.preventDefault(); anchor = null;
        if (event.key === 'Escape') {selection = null; sticky = false; draw(); return;}
        const series = speedSeries.find(s => s.kind === selection?.kind) || speedSeries.find(s => s.points.length);
        if (!series?.points.length) return;
        const index = series.points.findIndex(p => p.key === selection?.key);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? series.points.length - 1 :
          Math.max(0, Math.min(series.points.length - 1, index < 0 ? 0 : index + (event.key === 'ArrowLeft' ? -1 : 1)));
        selection = {kind: series.kind, key: series.points[next].key}; sticky = true; draw();
      });
    }
    const observer = view?.ResizeObserver ? new view.ResizeObserver(draw) : null;
    observer?.observe(traceCanvas); observer?.observe(speedCanvas);
    return {update: (nextTiming, packet = {}) => {
      const next = nextTiming || {};
      if (next.current_start_s !== timing.current_start_s || next.best_lap_s !== timing.best_lap_s) {selection = null; sticky = false;}
      timing = next; recent.add(packet); draw();},
      clear: () => {selection = null; sticky = false; draw();}};
  }
  return {projectTrace, layoutTrace, nearestTracePoint, nearestTimePoint, pointAtElapsed, formatSample, RecentHistory, createView};
});
