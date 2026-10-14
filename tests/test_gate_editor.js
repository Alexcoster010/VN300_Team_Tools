// Exercise the georeferencing and freshness rules used by the real offline editor.
const assert=require('node:assert/strict'),fs=require('node:fs');
const gates=require('../web/gates.js');
const areas=JSON.parse(fs.readFileSync(require('node:path').join(__dirname,'../web/assets/map-areas.json')));
for(const area of areas){
 const e=area.extent,center=gates.unproject((e.xmin+e.xmax)/2,(e.ymin+e.ymax)/2);
 const p=gates.toPixel(center,e,1100,600);assert.ok(Math.abs(p.x-550)<1e-6&&Math.abs(p.y-300)<1e-6);
 const back=gates.fromPixel(p.x,p.y,e,1100,600);assert.ok(Math.abs(back.lat-center.lat)<1e-9&&Math.abs(back.lon-center.lon)<1e-9);
 assert.ok(Math.abs(gates.fromPixel(0,0,e,1100,600).lat-gates.unproject(e.xmin,e.ymax).lat)<1e-9);
}
const packet={live_capture_age_s:.2,fields:{Latitude_deg:35.2,Longitude_deg:-97.45,PosUncertainty_m:.4,Yaw_deg:90}};
const fix=gates.gpsFix(packet,1000,1200);assert.equal(fix.heading,90);
assert.throws(()=>gates.gpsFix(packet,1000,2900),/Fresh GPS/);
assert.throws(()=>gates.gpsFix({...packet,fields:{...packet.fields,PosUncertainty_m:11}},0,0),/uncertainty/);
assert.throws(()=>gates.gpsFix({...packet,fields:{...packet.fields,Gnss1Fix:1}},0,0),/GPS fix/);
assert.throws(()=>gates.gpsFix({...packet,fields:{...packet.fields,Latitude_deg:NaN}},0,0),/GPS fix/);
for(const heading of [0,90,45,359]){
 const [a,b]=gates.centeredGate(fix,12,heading),ap=gates.project(a.lat,a.lon),bp=gates.project(b.lat,b.lon),cp=gates.project(fix.lat,fix.lon);
 const scale=Math.cos(fix.lat*Math.PI/180),dx=(bp.x-ap.x)*scale,dy=(bp.y-ap.y)*scale;
 assert.ok(Math.abs(Math.hypot(dx,dy)-12)<1e-6);
 assert.ok(Math.abs((ap.x+bp.x)/2-cp.x)<1e-6&&Math.abs((ap.y+bp.y)/2-cp.y)<1e-6);
 assert.ok(Math.abs(dx*Math.sin(heading*Math.PI/180)+dy*Math.cos(heading*Math.PI/180))<1e-6);
}
assert.throws(()=>gates.centeredGate(fix,0,90),/Width/);
console.log('Gate georeferencing, GPS freshness, and perpendicular gate geometry passed.');
