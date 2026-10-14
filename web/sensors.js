/* Shared sensor tree: complete telemetry, grouped by measurement type. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SRTSensors = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const pretty = text => String(text).replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
  function valueText(value) {
    if (value == null) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return !Number.isFinite(value) ? '—' : Number.isInteger(value) ? String(value) :
      Math.abs(value) > 0 && Math.abs(value) < 0.0001 ? value.toExponential(4) : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  const numeric = (value, places) => Number.isFinite(value) ? value.toFixed(places) : '—';
  const leaf = (id, label, value, unit = '', extra = {}) => ({id, label, value: valueText(value), unit, ...extra});
  const branch = (id, label, children = [], extra = {}) => ({id, label, children, ...extra});
  const fault = status => ['stale', 'disconnected', 'i2c_error', 'crc_error'].includes(status);
  function channelStatus(channel, offline, disconnected = 'Arduino disconnected') {
    if (offline) return 'Panel offline';
    if (!channel.enabled) return 'Disabled';
    return ({online: 'Online', stale: 'Stale', disconnected, i2c_error: 'Sensor not responding',
      crc_error: 'CRC error', warming_up: 'Warming up', connecting: 'Connecting', stopped: 'Stopped',
      'waiting for data': 'Waiting for data'})[channel.status] || channel.status || 'Waiting for data';
  }
  function massFlowState(flow = {}, offline = false) {
    const status = channelStatus(flow, offline, 'Pi I²C unavailable');
    if (status !== 'Online') return status;
    if (Number.isFinite(flow.last_age_s) && flow.last_age_s >= 2) return 'Stale';
    return flow.sample_status === 'ok' && Number.isFinite(flow.air_slm) && Number.isFinite(flow.o2_slm) && Number.isFinite(flow.last_age_s) ? 'Online' : 'Waiting for data';
  }
  function objectNodes(id, object, excluded = [], stale = false) {
    return Object.entries(object || {}).filter(([key]) => !excluded.includes(key)).map(([key, value]) =>
      value && typeof value === 'object' ? branch(id + '/' + key, pretty(key), objectNodes(id + '/' + key, value, [], stale)) :
        leaf(id + '/' + key, pretty(key), value, '', {stale}));
  }
  function fieldUnit(key) {
    const suffix = key.match(/_(mps2|mps|rps|deg|mph|kPa|ns|s|m|C|G)$/)?.[1];
    return ({mps2: 'm/s²', mps: 'm/s', rps: 'rad/s', C: '°C', G: 'g', deg: '°'})[suffix] || suffix || '';
  }
  function vnPath(key) {
    if (/^Gnss1/.test(key)) return ['gps', 'GPS / position', 'gnss1', 'GNSS receiver 1'];
    if (/^Gnss2/.test(key)) return ['gps', 'GPS / position', 'gnss2', 'GNSS receiver 2'];
    if (/Utc|^Time|^Binary_Time|^Pi_Elapsed|^Sample_Time|^Sensor_UTC|^GpsTow|^GpsWeek|^GpsTime|^GpsPps/.test(key)) return ['time', 'Time & synchronization'];
    if (/Checksum/.test(key)) return ['packet', 'Packet integrity'];
    if (/Yaw|Pitch|Roll|Quat|YPR|AttUncertainty/.test(key)) return ['attitude', 'Attitude / orientation'];
    if (/Latitude|Longitude|Altitude|PosLla|PosU|PosAcc|PosUncertainty|GpsFix|NumSats|GpsDop/.test(key)) return ['gps', 'GPS / position'];
    if (/^InsStatus|^Common_InsStatus/.test(key)) return ['navigation', 'Navigation status'];
    if (/Vel|Speed/.test(key)) return ['velocity', 'Velocity / speed'];
    if (/Longitudinal|Lateral|Vertical/.test(key)) return ['dynamics', 'Vehicle acceleration'];
    if (/UncompAccel/.test(key)) return ['imu', 'IMU', 'acceleration', 'Acceleration'];
    if (/UncompGyro/.test(key)) return ['imu', 'IMU', 'gyro', 'Gyroscope / angular rate'];
    if (/Gyro|AngularRate/.test(key)) return ['imu', 'IMU', 'gyro', 'Gyroscope / angular rate'];
    if (/Accel|LinBodyAcc/.test(key)) return ['imu', 'IMU', 'acceleration', 'Acceleration'];
    if (/Mag/.test(key) && !/Pressure/.test(key)) return ['imu', 'IMU', 'magnetometer', 'Magnetometer'];
    if (/Temp|Pressure/.test(key)) return ['imu', 'IMU', 'environment', 'Temperature / pressure'];
    if (/Deltas/.test(key)) return ['imu', 'IMU', 'deltas', 'Inertial increments'];
    if (/^ImuStatus/.test(key)) return ['imu', 'IMU', 'status', 'IMU status'];
    return ['other-vn', 'Other VN300 channels'];
  }
  function canType(row) {
    const text = [row.channel, row.name, row.message].join(' ').toLowerCase();
    if (/steer/.test(text)) return ['controls', 'steering', 'Steering'];
    if (/brake/.test(text)) return ['controls', 'brakes', 'Brakes'];
    if (/throttle|pedal/.test(text)) return ['controls', 'pedals', 'Throttle / pedals'];
    if (/switch|button|driver.?input/.test(text)) return ['controls', 'switches', 'Switches / driver inputs'];
    if (/airspeed|mass.?flow|air.?flow/.test(text)) return ['air', 'can-air', 'Air / flow channels'];
    if (/gps|gnss|latitude|longitude/.test(text)) return ['motion', 'gps', 'GPS / position'];
    if (/acceleration|angular|gyro|imu|yaw|pitch|roll|magnet/.test(text)) return ['motion', 'can-motion', 'CAN motion channels'];
    if (/vehicle.?speed|wheel.?speed|distance|lap/.test(text)) return ['motion', 'can-vehicle', 'Vehicle motion / laps'];
    if (/battery|voltage|current|alternator|electrical/.test(text)) return ['electrical', 'power', 'Voltage / current'];
    if (/coolant|oil|cooling/.test(text)) return ['powertrain', 'cooling', 'Cooling / lubrication'];
    if (/fuel|ignition|inject/.test(text)) return ['powertrain', 'fuel', 'Fuel / ignition'];
    if (/gear|clutch|transmission|differential/.test(text)) return ['powertrain', 'drivetrain', 'Drivetrain'];
    if (/engine|rpm|boost|exhaust|intake/.test(text)) return ['powertrain', 'engine', 'Engine'];
    return ['other-can', 'message/' + (row.message || 'Uncategorized'), row.message || 'Other CAN channels'];
  }
  function canDisplayValue(row) {
    const scaling = row.scaling || {multiplier: 1, divider: 1}, value = row.display_value ?? row.value;
    return row.value_type === 'boolean' && scaling.multiplier === 1 && scaling.divider === 1 ? value ? 'On' : 'Off' : valueText(value);
  }
  function filterTree(nodes, query) {
    const q = query.trim().toLowerCase();
    if (!q) return nodes;
    return nodes.flatMap(node => {
      const own = [node.label, node.source, node.note, node.value, node.unit].join(' ').toLowerCase().includes(q);
      if (own) return [node];
      if (!node.children) return [];
      const children = filterTree(node.children, q);
      return children.length ? [{...node, children}] : [];
    });
  }
  function buildTree(packet = {}, can = {}, options = {}) {
    const offline = !!options.telemetryOffline, canOffline = !!options.canOffline;
    const s = packet.steering || {}, a = packet.airspeed || {}, f = packet.mass_flow || {};
    const analogStale = channel => offline || channel.status !== 'online' || channel.last_age_s >= 2;
    const steeringStale = analogStale(s), airStale = analogStale(a), flowState = massFlowState(f, offline), flowCurrent = flowState === 'Online';
    const categories = new Map([
      ['controls', branch('controls', 'Vehicle controls', [])],
      ['air', branch('air', 'Air & flow', [])],
      ['motion', branch('motion', 'Motion & navigation', [])],
      ['powertrain', branch('powertrain', 'Powertrain', [])],
      ['electrical', branch('electrical', 'Electrical', [])],
      ['other-can', branch('other-can', 'Other CAN sensors', [])]
    ]);
    const addGroup = (category, id, label, extra = {}) => {
      const parent = categories.get(category), key = category + '/' + id;
      let group = parent.children.find(node => node.id === key);
      if (!group) { group = branch(key, label, [], extra); parent.children.push(group); }
      return group;
    };
    const steering = addGroup('controls', 'steering', 'Steering');
    steering.children.push(branch('steering/arduino', 'Arduino input', [
      leaf('steering/angle', 'Calculated steering angle', s.angle_deg, '°', {stale: steeringStale, note: s.calibrated ? 'Measured steering calibration' : 'Uncalibrated'}),
      leaf('steering/voltage', 'Raw voltage · A0', s.voltage_v, 'V', {stale: steeringStale}),
      leaf('steering/raw', 'Raw ADC count', s.raw, 'counts', {stale: steeringStale}),
      leaf('steering/range', 'Outside calibration', s.outside_calibration),
      branch('steering/health', 'Health & calibration', objectNodes('steering/health', s, ['airspeed', 'mass_flow', 'angle_deg', 'voltage_v', 'raw', 'outside_calibration']))
    ], {status: channelStatus(s, offline), bad: offline || fault(s.status),
      preview: Number.isFinite(s.angle_deg) ? numeric(s.angle_deg, 1) + ' °' : Number.isFinite(s.raw) ? valueText(s.raw) + ' ADC' : ''}));
    const air = addGroup('air', 'airspeed', 'Airspeed', {preview: numeric(a.speed_mph, 1) + ' mph', status: channelStatus(a, offline), bad: offline || fault(a.status)});
    air.children.push(leaf('airspeed/mps', 'Calculated airspeed (estimate)', a.speed_mps, 'm/s', {stale: airStale}),
      leaf('airspeed/mph', 'Calculated airspeed (estimate)', a.speed_mph, 'mph', {stale: airStale}),
      leaf('airspeed/voltage', 'Raw voltage · A1', a.voltage_v, 'V', {stale: airStale}),
      leaf('airspeed/raw', 'Raw ADC count', a.raw, 'counts', {stale: airStale}),
      leaf('airspeed/range', 'Range', a.range),
      branch('airspeed/health', 'Health & calibration', objectNodes('airspeed/health', a, ['speed_mps', 'speed_mph', 'voltage_v', 'raw', 'range'])));
    const flowNote = f.status === 'i2c_error' ? 'Check sensor power and I²C connection.' : f.status === 'disconnected' ? 'Check the Pi I²C device and service permissions.' :
      flowState === 'Stale' ? 'No new readings for 2 s.' : !f.enabled ? 'Enable Pi I²C mass flow in Setup to receive readings.' : '';
    const flow = addGroup('air', 'mass-flow', 'Mass flow · SFM3505', {preview: numeric(flowCurrent ? f.air_slm : null, 2) + ' slm', status: flowState,
      bad: offline || fault(f.status) || flowState === 'Stale' || !!f.write_error || f.range === 'outside_range', note: flowNote});
    flow.children.push(leaf('flow/air', 'Air-calibrated flow', flowCurrent ? f.air_slm : null, 'slm', {stale: !flowCurrent}),
      leaf('flow/o2', 'O₂-calibrated flow', flowCurrent ? f.o2_slm : null, 'slm', {stale: !flowCurrent}),
      leaf('flow/air-raw', 'Raw air reading', flowCurrent ? f.air_raw : null, '24-bit', {stale: !flowCurrent}),
      leaf('flow/o2-raw', 'Raw O₂ reading', flowCurrent ? f.o2_raw : null, '24-bit', {stale: !flowCurrent}),
      leaf('flow/age', 'Last valid sample age', f.last_age_s, 's'),
      branch('flow/health', 'Health & reference conditions', objectNodes('flow/health', f, ['air_slm', 'o2_slm', 'air_raw', 'o2_raw', 'last_age_s']),
        {note: 'Air and O₂ are alternative calibrations of the same flow measurement; they do not measure gas composition. slm = standard liters per minute at 20 °C and 1013.25 hPa.'}));
    const fields = packet.fields || {}, vnAge = packet.live_capture_age_s ?? packet.age_s;
    const vnStale = offline || !Number.isFinite(vnAge) || vnAge >= 2;
    const vnStatus = offline ? 'Panel offline' : Object.keys(fields).length ? vnStale ? 'Stale' : 'Online' : 'Waiting for data';
    for (const [id, label] of [['imu', 'IMU'], ['attitude', 'Attitude / orientation'], ['gps', 'GPS / position'], ['velocity', 'Velocity / speed']])
      addGroup('motion', id, label, {status: vnStatus, bad: offline || Object.keys(fields).length > 0 && vnStale});
    for (const [key, value] of Object.entries(fields)) {
      const path = vnPath(key);
      let group = addGroup('motion', path[0], path[1], {status: vnStatus});
      if (path.length > 2) {
        const id = group.id + '/' + path[2];
        let subgroup = group.children.find(node => node.id === id);
        if (!subgroup) { subgroup = branch(id, path[3], []); group.children.push(subgroup); }
        group = subgroup;
      }
      const unit = fieldUnit(key), axis = key.match(/Uncomp(?:Accel|Gyro)_([XYZ])/)?.[1];
      const label = axis ? 'Raw ' + (/UncompAccel/.test(key) ? 'acceleration' : 'gyroscope') + ' · ' + axis + ' (uncompensated)' :
        pretty(key.replace(/_(mps2|mps|rps|deg|mph|kPa|ns|s|m|C|G)$/, ''));
      group.children.push(leaf('vn/' + key, label, /Latitude|Longitude/.test(key) && Number.isFinite(value) ? value.toFixed(7) : value, unit, {stale: vnStale, source: key}));
    }
    const status = can.status || packet.can || {}, signals = can.signals || [], frames = can.frames || [];
    for (const row of [...signals].sort((x, y) => parseInt(x.id, 16) - parseInt(y.id, 16) || String(x.name || x.channel).localeCompare(String(y.name || y.channel)))) {
      const [category, id, label] = canType(row), parent = addGroup(category, id, label);
      const key = row.key || [row.id, !!row.extended, row.channel || row.name].join('/'), nodeId = 'can/signal/' + key;
      const stale = canOffline || row.age_s >= 2 || !status.capturing, saved = row.scaling || {multiplier: 1, divider: 1}, draft = options.drafts?.get(key) || saved;
      parent.children.push(branch(nodeId, 'CAN · ' + (row.name || row.channel), [
        leaf(nodeId + '/display', 'Calculated / displayed value', canDisplayValue(row), row.unit, {stale}),
        leaf(nodeId + '/decoded', 'Decoded value', row.value, row.unit, {stale}),
        leaf(nodeId + '/raw', 'Raw signal value', row.raw, '', {stale}),
        leaf(nodeId + '/id', 'CAN identifier', row.id + (row.extended ? ' · extended' : ' · standard')),
        leaf(nodeId + '/channel', 'Channel', row.channel || row.name),
        leaf(nodeId + '/message', 'Message', row.message),
        leaf(nodeId + '/age', 'Sample age', row.age_s, 's'),
        {id: nodeId + '/scaling', label: 'Display scaling', editor: {key, name: row.name || row.channel, multiplier: draft.multiplier, divider: draft.divider, busy: !!options.scalingBusy}}
      ], {preview: canDisplayValue(row) + (row.unit ? ' ' + row.unit : ''), stale, status: canOffline ? 'Connection unavailable' : stale ? 'Stale' : 'Online', note: row.notes,
        source: [row.id, row.channel, row.message, row.extended ? 'extended' : 'standard'].join(' · ')}));
    }
    const bandwidth = can.bandwidth || {}, available = Number.isFinite(bandwidth.percent);
    const bus = branch('can-bus', 'CAN bus', [
      branch('can-bus/health', 'Connection & bandwidth', [
        leaf('can-bus/bandwidth', 'CAN bandwidth (estimate)', available && !canOffline ? numeric(bandwidth.percent, 1) : null, '%',
          {note: canOffline ? 'CAN connection unavailable' : bandwidth.reason || 'Classic CAN traffic estimate, including wire overhead and a 20% bit-stuffing allowance.'}),
        ...objectNodes('can-bus/bandwidth-details', bandwidth, ['percent', 'reason']),
        ...objectNodes('can-bus/status', status),
        leaf('can-bus/evictions', 'Latest-value cache evictions', can.evictions || 0),
        leaf('can-bus/frame-limit', 'Frame cache limit', can.frame_limit),
        leaf('can-bus/signal-limit', 'Signal cache limit', can.signal_limit)
      ], {note: status.decoder === 'MoTeC M1 reference' ? 'MoTeC M1 reference: assumes big-endian fields with MSB-first offsets and signed two’s complement. Confirm against ECU data. Source units follow the reference arithmetic.' : ''}),
      branch('can-bus/frames', 'Raw messages / frames', frames.map(row => {
        const id = 'can/frame/' + row.id + '/' + !!row.extended, stale = canOffline || row.age_s >= 2 || !status.capturing;
        return branch(id, row.id + (row.extended ? ' · extended' : ' · standard'), objectNodes(id, row, [], stale),
          {preview: row.data || '—', stale, status: stale ? 'Stale' : 'Online'});
      }), {preview: frames.length + ' IDs'})
    ], {preview: signals.length + ' signals · ' + frames.length + ' IDs', status: canOffline ? 'Connection unavailable' : !status.enabled ? 'Disabled' : status.capturing ? status.status || 'Online' : 'Capture inactive',
      note: 'Decoded CAN sensors appear above under their measurement type. Display scaling changes only the displayed value; recordings retain original decoding. Set multiplier and divider to 1 to reset.'});
    for (const group of categories.get('motion').children) if (!group.children.length) group.note = 'No VN300 channels received yet. Start stream to inspect live data.';
    const health = branch('device-health', 'Device & logger health', objectNodes('logger', packet,
      ['fields', 'steering', 'airspeed', 'mass_flow', 'can', 'timing', 'run_metadata', 'active_run_metadata']), {status: packet.status || 'Waiting for data'});
    const nodes = [...categories.values()].filter(group => group.children.length).concat(bus, health);
    return filterTree(nodes, options.query || '');
  }

  function createTree(container) {
    const doc = container.ownerDocument, entries = new Map(), openState = new Map(), filteredOpenState = new Map();
    let model = [], options = {}, rendering = false;
    const make = (tag, className, parent) => {const el = doc.createElement(tag); if (className) el.className = className; if (parent) parent.appendChild(el); return el;};
    const text = (el, value) => {const content = value || ''; if (el.textContent !== content) el.textContent = content;};
    function reconcile(parent, desired) {
      desired.forEach((el, index) => {if (parent.children[index] !== el) parent.insertBefore(el, parent.children[index] || null);});
      while (parent.children.length > desired.length) parent.removeChild(parent.children[parent.children.length - 1]);
    }
    function entryFor(node) {
      const kind = node.children ? 'branch' : node.editor ? 'editor' : 'value';
      if (entries.get(node.id)?.kind === kind) return entries.get(node.id);
      const item = make('li', node.children ? 'sensor-branch' : 'sensor-leaf'); item.dataset.nodeId = node.id;
      const entry = {item, kind, defaultOpen: !!node.open};
      if (node.children) {
        entry.details = make('details', '', item); entry.summary = make('summary', 'sensor-branch-row', entry.details);
        entry.label = make('span', 'sensor-branch-label', entry.summary); entry.preview = make('span', 'sensor-preview', entry.summary);
        entry.status = make('span', 'sensor-state', entry.summary); entry.note = make('p', 'sensor-tree-note', entry.details);
        entry.list = make('ul', 'sensor-children', entry.details);
        entry.details.addEventListener('toggle', () => {
          if (rendering) return;
          const state = options.filtering ? filteredOpenState : openState;
          const expected = state.get(node.id) ?? (options.filtering ? true : entry.defaultOpen);
          if (expected === entry.details.open) return;
          state.set(node.id, entry.details.open); render(model, options);
        });
      } else {
        entry.row = make('div', 'sensor-leaf-row', item); entry.caption = make('div', 'sensor-caption', entry.row);
        entry.label = make('span', 'sensor-leaf-label', entry.caption); entry.source = make('small', 'sensor-source', entry.caption);
        if (node.editor) {
          entry.form = make('form', 'can-scaling', entry.row); entry.form.dataset.key = node.editor.key;
          for (const name of ['multiplier', 'divider']) {
            const label = make('label', '', entry.form); label.appendChild(doc.createTextNode(name === 'multiplier' ? '×' : '÷'));
            const input = make('input', '', label); input.name = name; input.type = 'number'; input.step = 'any'; input.required = true;
            input.setAttribute('aria-label', pretty(name) + ' for ' + node.editor.name); entry[name] = input;
          }
          entry.button = make('button', 'secondary', entry.form); entry.button.type = 'submit'; text(entry.button, 'Save');
        } else {
          entry.reading = make('div', 'sensor-reading', entry.row); entry.value = make('span', 'sensor-number', entry.reading); entry.unit = make('small', 'sensor-unit', entry.reading);
        }
        entry.note = make('p', 'sensor-tree-note', item);
      }
      entries.set(node.id, entry); return entry;
    }
    function update(parent, nodes) {
      const desired = nodes.map(node => {
        const entry = entryFor(node); text(entry.label, node.label); text(entry.note, node.note); entry.note.hidden = !node.note;
        entry.item.classList.toggle('stale-value', !!node.stale); entry.item.classList.toggle('sensor-fault', !!node.bad);
        if (node.children) {
          entry.defaultOpen = !!node.open;
          text(entry.preview, node.preview); text(entry.status, node.status); entry.status.hidden = !node.status;
          entry.status.classList.toggle('bad', !!node.bad);
          entry.details.open = !!(options.filtering ? filteredOpenState.get(node.id) ?? true : openState.get(node.id) ?? node.open);
          if (entry.details.open) update(entry.list, node.children);
        } else {
          text(entry.source, node.source); entry.source.hidden = !node.source;
          if (node.editor) {
            if (!entry.form.contains(doc.activeElement)) {
              for (const name of ['multiplier', 'divider']) {
                const value = String(node.editor[name]); if (entry[name].value !== value) entry[name].value = value;
              }
            }
            entry.button.disabled = node.editor.busy;
          } else {text(entry.value, node.value); text(entry.unit, node.unit); entry.unit.hidden = !node.unit;}
        }
        return entry.item;
      });
      reconcile(parent, desired);
    }
    function render(nodes, nextOptions = {}) {
      if (nextOptions.filtering && (!options.filtering || nextOptions.query !== options.query)) filteredOpenState.clear();
      if (options.filtering && !nextOptions.filtering) entries.forEach((entry, id) => {
        if (entry.details) entry.details.open = !!(openState.get(id) ?? entry.defaultOpen);
      });
      model = nodes; options = nextOptions; rendering = true;
      update(container, nodes); rendering = false;
      if (!options.filtering) {
        const active = new Set();
        const walk = nodes => nodes.forEach(node => {active.add(node.id); if (node.children) walk(node.children);});
        walk(nodes);
        for (const id of entries.keys()) if (!active.has(id)) {entries.delete(id); openState.delete(id);}
      }
    }
    function setExpanded(open) {
      const state = options.filtering ? filteredOpenState : openState;
      const walk = nodes => nodes.forEach(node => {if (node.children) {state.set(node.id, open); walk(node.children);}});
      walk(model); render(model, options);
      entries.forEach((entry, id) => {if (entry.details && state.has(id)) entry.details.open = state.get(id);});
    }
    return {render, expandAll: () => setExpanded(true), collapseAll: () => setExpanded(false)};
  }
  return {buildTree, filterTree, createTree, valueText, massFlowState, canDisplayValue, canType, vnPath};
});
