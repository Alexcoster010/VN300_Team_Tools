// Run with: node tests/test_trace_view.js
const assert = require('node:assert/strict');
const trace = require('../web/trace.js');
const timing = {current_start_s: 10, best_lap_s: 3, current_elapsed_s: 1,
  current_trace: [{lat: 35, lon: -97, elapsed_s: 0, distance_m: 0, speed_mph: 12.3},
    {lat: 35, lon: -96.999, elapsed_s: 1, distance_m: 90, speed_mph: 29.2}],
  best_trace: [{lat: 35.0002, lon: -97, elapsed_s: 0, speed_mph: 14},
    {lat: 35.0002, lon: -96.999, elapsed_s: 2, speed_mph: 32}]};
const layout = trace.layoutTrace(timing, 500, 360), [a, b] = layout[0].points;
assert.ok(a.px >= 32 - 1e-9 && b.px <= 468 + 1e-9);
const hit = trace.nearestTracePoint(layout, {x: a.px + 0.7 * (b.px - a.px), y: b.py});
assert.equal(hit.kind, 'current'); assert.equal(hit.point.speed_mph, 29.2);
assert.equal(trace.nearestTracePoint(layout, {x: -100, y: -100}), null);
assert.equal(trace.projectTrace([{lat: NaN, lon: -97}], timing.current_trace[0]).length, 0);
const halfway = trace.pointAtElapsed(layout[1].points, 1);
assert.ok(Math.abs(halfway.px - (layout[1].points[0].px + layout[1].points[1].px) / 2) < 1e-9);
assert.match(trace.formatSample('best', {t: 1, speed_mph: null}), /Speed unavailable/);
assert.match(trace.formatSample('current', {t: 1, speed_mph: 0}), /0.0 mph/);
const recent = new trace.RecentHistory();
const packet = (time, speed, age = 0) => ({session: 'run1', live_capture_age_s: age, fields: {Sample_Time_s: time, Speed_mph: speed}});
recent.add(packet(0, 12)); recent.add(packet(0, 99)); recent.add(packet(1, 15, 3));
assert.equal(recent.points.length, 1); assert.equal(recent.points[0].speed_mph, 12);
recent.add(packet(61, 25)); assert.equal(recent.points.length, 1);
recent.add({...packet(62, 30), session: 'run2'}); assert.equal(recent.points.length, 1);
assert.equal(recent.series()[0].points[0].t, 0);

// Exercise the actual canvas handlers and linked selection through live updates.
const calls = [];
const ctx = new Proxy({}, {get: (target, key) => target[key] || ((...args) => {calls.push([key, args]);})});
const doc = {defaultView: {devicePixelRatio: 1}};
const canvas = (left, width) => ({ownerDocument: doc, width, height: 360, listeners: {},
  getBoundingClientRect: () => ({left, top: 0, width, height: 360}), getContext: () => ctx,
  addEventListener(name, handler) {this.listeners[name] = handler;}});
const map = canvas(0, 500), speed = canvas(516, 360);
const tooltip = {hidden: true, style: {}, offsetWidth: 220, offsetHeight: 40,
  parentElement: {getBoundingClientRect: () => ({left: 0, top: 0, width: 876, height: 360})}};
const readout = {};
const view = trace.createView({traceCanvas: map, speedCanvas: speed, tooltip, readout});
view.update(timing, packet(0, 99));
map.listeners.pointermove({clientX: b.px, clientY: b.py, pointerType: 'mouse'});
assert.equal(tooltip.hidden, false); assert.match(readout.textContent, /Current · 29.2 mph · 1.0 s/);
assert.ok(calls.some(([name, args]) => name === 'arc' && args[0] > 42));
view.update(timing, packet(1, 120)); assert.match(readout.textContent, /29.2 mph/);
map.listeners.pointerleave(); assert.equal(tooltip.hidden, true);
const best = layout[1].points[1];
map.listeners.pointerdown({clientX: best.px, clientY: best.py, pointerType: 'touch'});
map.listeners.pointerleave(); assert.equal(tooltip.hidden, false); assert.match(readout.textContent, /Best · 32.0 mph/);
map.listeners.keydown({key: 'Home', preventDefault() {}}); assert.match(readout.textContent, /Best · 14.0 mph/);
map.listeners.keydown({key: 'ArrowRight', preventDefault() {}}); assert.match(readout.textContent, /Best · 32.0 mph/);
map.listeners.keydown({key: 'Escape', preventDefault() {}}); assert.equal(tooltip.hidden, true);
speed.listeners.pointermove({clientX: 516 + 344, clientY: 32, pointerType: 'mouse'});
assert.match(readout.textContent, /Best · 32.0 mph/);
view.update({...timing, current_start_s: 15}, packet(2, 200)); assert.equal(tooltip.hidden, true);
view.update({...timing, current_trace: [{...timing.current_trace[0], speed_mph: null}], best_trace: []});
map.listeners.keydown({key: 'Home', preventDefault() {}}); assert.match(readout.textContent, /Speed unavailable/);
assert.equal(calls.filter(([name, args]) => ['moveTo', 'lineTo', 'arc'].includes(name)).some(([, args]) => args.some(value => typeof value === 'number' && !Number.isFinite(value))), false);
console.log('Trace speed capture, hit testing, linked plots, history freshness, live refresh, touch, and keyboard inspection passed');
