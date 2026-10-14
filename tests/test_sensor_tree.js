// Run with: node tests/test_sensor_tree.js
const assert = require('node:assert/strict');
const tree = require('../web/sensors.js');
const flatten = nodes => nodes.flatMap(node => [node, ...flatten(node.children || [])]);
const packet = {status: 'streaming', live_capture_age_s: 0.01, checksum_ok: true,
  steering: {enabled: true, status: 'online', calibrated: true, angle_deg: -12.4, voltage_v: 1.23, raw: 252, last_age_s: 0.01, calibration: {raw_left: 20, raw_center: 315, raw_right: 688}},
  airspeed: {enabled: true, status: 'online', voltage_v: 1.5, raw: 307, speed_mps: 3, speed_mph: 6.71, last_age_s: 0.01},
  fields: {Imu_UncompAccel_X_mps2: 1.5, Imu_Accel_X_mps2: 1.25, Imu_UncompGyro_Z_rps: 0.4, Common_Imu_Gyro_Z_rps: 0.3,
    Common_Imu_Mag_X: 0.01, Common_Imu_Temp_C: 25.2, Common_YPR_Yaw_deg: 41.2, Gnss1PosLla_Latitude_deg: 35.20349,
    Gnss2NumSats: 12, Ins_VelBody_X_mps: 20, Time_GpsWeek: 2400, Checksum_OK: true, Custom_Channel: 123}};
const can = {status: {enabled: true, capturing: true, status: 'online'}, signals: [
  {key: 'steering-can', id: '0x10', channel: 'Steering_Angle', name: 'Steering angle', value: -13, raw: 87, age_s: 0.01, unit: 'deg'},
  {key: 'brake-can', id: '0x11', channel: 'Brake_Pressure', name: 'Brake pressure', value: 0.1, raw: 1, age_s: 0.01, unit: 'bar'},
  {key: 'rpm', id: '0x20', channel: 'Engine_Speed', name: 'Engine speed', value: 6000, raw: 500, age_s: 0.01, unit: 'rpm'},
  {key: 'unknown', id: '0x30', channel: 'Vendor_Thing', name: '<img src=x onerror=bad()>', value: 4, raw: 2, age_s: 0.01, message: 'Vendor message'},
  {key: 'unknown-ext', id: '0x30', extended: true, channel: 'Vendor_Thing', name: 'Extended vendor', value: 5, raw: 3, age_s: 3, message: 'Vendor message'}],
  frames: [{id: '0x30', extended: false, data: '02', dlc: 1, age_s: 0.01}, {id: '0x30', extended: true, data: '03', dlc: 1, age_s: 3}]};
let nodes = tree.buildTree(packet, can);
let all = flatten(nodes), get = id => all.find(node => node.id === id);
assert.equal(get('steering/voltage').value, '1.23'); assert.equal(get('steering/angle').value, '-12.4');
assert.equal(get('steering/health/calibration/raw_center').value, '315');
assert.ok(get('controls/steering').children.some(node => node.id === 'can/signal/steering-can'));
assert.ok(get('controls/brakes')); assert.ok(get('powertrain/engine'));
for (const key of Object.keys(packet.fields)) assert.equal(all.filter(node => node.source === key).length, 1, key);
assert.ok(get('motion/imu/acceleration').children.some(node => node.source === 'Imu_UncompAccel_X_mps2'));
assert.ok(get('motion/imu/acceleration').children.some(node => node.source === 'Imu_Accel_X_mps2'));
assert.ok(get('motion/imu/gyro').children.some(node => node.source === 'Imu_UncompGyro_Z_rps'));
assert.ok(get('motion/imu/gyro').children.some(node => node.source === 'Common_Imu_Gyro_Z_rps'));
assert.ok(get('motion/imu/magnetometer'));
assert.ok(get('motion/gps/gnss1')); assert.ok(get('motion/gps/gnss2'));
assert.equal(get('vn/Gnss1PosLla_Latitude_deg').value, '35.2034900');
assert.equal(get('vn/Imu_Accel_X_mps2').unit, 'm/s²');
assert.equal(get('can/frame/0x30/false/data').value, '02'); assert.equal(get('can/frame/0x30/true/data').value, '03');
assert.equal(get('can/signal/unknown-ext').stale, true); assert.ok(get('motion/other-vn'));
all = flatten(tree.buildTree(packet, can, {query: 'Imu_UncompAccel'}));
assert.deepEqual(all.map(node => node.id), ['motion', 'motion/imu', 'motion/imu/acceleration', 'vn/Imu_UncompAccel_X_mps2']);
all = flatten(tree.buildTree(packet, can, {query: 'steering'}));
assert.ok(get('steering/raw')); assert.ok(get('can/signal/steering-can')); assert.equal(get('air'), undefined);
assert.deepEqual(tree.buildTree(packet, can, {query: 'does-not-exist'}), []);
all = flatten(tree.buildTree(packet, can, {telemetryOffline: true, canOffline: true}));
assert.equal(get('steering/arduino').status, 'Panel offline'); assert.equal(get('vn/Imu_Accel_X_mps2').stale, true);
assert.equal(get('can-bus').status, 'Connection unavailable'); assert.equal(get('can/signal/rpm').stale, true);

// Exercise actual keyed DOM rendering: polls must preserve open branches,
// focused inputs and drafts while updating the values beside them.
class Element {
  constructor(tag, doc) {this.tagName = tag; this.ownerDocument = doc; this.children = []; this.dataset = {}; this.listeners = {}; this.className = ''; this._text = ''; this.open = false;
    this.classList = {toggle: (name, enabled) => {const set = new Set(this.className.split(' ').filter(Boolean)); enabled ? set.add(name) : set.delete(name); this.className = [...set].join(' ');}};}
  set textContent(value) {this._text = String(value); this.children.forEach(child => child.parentNode = null); this.children = [];}
  get textContent() {return this._text + this.children.map(child => child.textContent).join('');}
  appendChild(child) {return this.insertBefore(child, null);}
  insertBefore(child, before) {if (child.parentNode) child.parentNode.removeChild(child); const index = before ? this.children.indexOf(before) : this.children.length; this.children.splice(index, 0, child); child.parentNode = this; return child;}
  removeChild(child) {this.children.splice(this.children.indexOf(child), 1); child.parentNode = null;}
  setAttribute(name, value) {this[name] = value;}
  addEventListener(name, fn) {this.listeners[name] = fn;}
  contains(node) {return node === this || this.children.some(child => child.contains(node));}
}
const doc = {activeElement: null, createElement: tag => new Element(tag, doc), createTextNode: value => {const node = new Element('#text', doc); node.textContent = value; return node;}};
const container = new Element('ul', doc), view = tree.createTree(container);
const descendants = element => [element, ...element.children.flatMap(descendants)];
const domNode = id => descendants(container).find(el => el.dataset.nodeId === id);
view.render(nodes);
assert.ok(flatten(nodes).filter(node => node.children).every(node => !node.open));
assert.ok(descendants(container).filter(el => el.tagName === 'details').every(el => !el.open));
const controlsDetails = domNode('controls').children[0];
controlsDetails.open = true; controlsDetails.listeners.toggle();
const steeringDetails = domNode('controls/steering').children[0];
assert.equal(steeringDetails.open, false); steeringDetails.open = true; steeringDetails.listeners.toggle();
assert.equal(domNode('steering/arduino').children[0].open, false);
steeringDetails.open = false; steeringDetails.listeners.toggle();
packet.steering.angle_deg = 14; view.render(tree.buildTree(packet, can));
assert.strictEqual(domNode('controls/steering').children[0], steeringDetails); assert.equal(steeringDetails.open, false);
view.expandAll(); assert.equal(steeringDetails.open, true);
const input = descendants(domNode('can/signal/rpm/scaling')).find(el => el.name === 'multiplier');
doc.activeElement = input; input.value = '23.'; can.signals[2].value = 6200;
view.render(tree.buildTree(packet, can));
assert.strictEqual(descendants(domNode('can/signal/rpm/scaling')).find(el => el.name === 'multiplier'), input);
assert.equal(input.value, '23.'); assert.match(domNode('can/signal/rpm/decoded').textContent, /6200/);
const malicious = domNode('can/signal/unknown'); assert.match(malicious.textContent, /<img/);
assert.equal(descendants(malicious).some(el => el.tagName === 'img'), false);
view.collapseAll(); assert.equal(steeringDetails.open, false);
const filtered = tree.buildTree(packet, can, {query: 'steering'});
view.render(filtered, {filtering: true, query: 'steering'}); assert.equal(steeringDetails.open, true);
view.collapseAll(); assert.equal(steeringDetails.open, false);
view.render(filtered, {filtering: true, query: 'steering'}); assert.equal(steeringDetails.open, false);
view.expandAll(); assert.equal(steeringDetails.open, true);
view.render(tree.buildTree(packet, can)); assert.equal(steeringDetails.open, false);
// Setup can change nullable calibration metadata into an object while polling.
view.expandAll(); packet.steering.calibration = null; view.render(tree.buildTree(packet, can));
assert.equal(domNode('steering/health/calibration').children[0].tagName, 'div');
packet.steering.calibration = {raw_center: 315}; view.render(tree.buildTree(packet, can));
assert.equal(domNode('steering/health/calibration').children[0].tagName, 'details');
view.expandAll(); assert.match(domNode('steering/health/calibration/raw_center').textContent, /315/);
console.log('Sensor categorization, complete channel coverage, tree filtering, expansion, and focused editor preservation passed');
