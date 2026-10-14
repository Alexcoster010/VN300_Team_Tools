// Run with: node tests/test_mass_flow_panel.js
const assert=require('node:assert/strict');
const tree=require('../web/sensors.js');
const good={enabled:true,sensor:'SFM3505',status:'online',sample_status:'ok',air_slm:0,o2_slm:-12.5,air_raw:8388608,o2_raw:8288608,last_age_s:0.02,range:'in_range',range_min_slm:-150,range_max_slm:300,reference_temperature_c:20,reference_pressure_hpa:1013.25,samples:15,sensor_errors:0,recorded_samples:12,write_error:null};
const flatten=nodes=>nodes.flatMap(n=>[n,...flatten(n.children||[])]);
let model;
const render=(flow,options={})=>model=flatten(tree.buildTree({mass_flow:flow},{},options));
const get=id=>model.find(n=>n.id===id);
render(good);
assert.equal(get('flow/air').value,'0');assert.equal(get('flow/o2').value,'-12.5');
assert.equal(get('air/mass-flow').status,'Online');assert.equal(get('flow/air').stale,false);
assert.equal(get('flow/air-raw').value,'8388608');
assert.equal(get('flow/health/reference_temperature_c').value,'20');
assert.match(get('flow/health').note,/alternative calibrations/);
for(const flow of [undefined,null,{enabled:false}]){render(flow);assert.equal(get('air/mass-flow').status,'Disabled');assert.equal(get('flow/air').value,'—');}
render({...good,air_slm:null,o2_slm:null,last_age_s:null});assert.equal(get('air/mass-flow').status,'Waiting for data');
for(const status of ['stale','i2c_error','crc_error','warming_up','connecting','waiting for data','disconnected','stopped']){
 render({...good,status,sample_status:status==='stale'?'ok':status,last_age_s:2.5});
 assert.equal(get('flow/air').value,'—',status);assert.equal(get('flow/o2').value,'—',status);
 assert.equal(get('flow/air').stale,true,status);assert.equal(get('flow/age').value,'2.5');
 if(['i2c_error','crc_error','disconnected','stale'].includes(status))assert.equal(get('air/mass-flow').bad,true,status);
 if(status==='i2c_error'){assert.equal(get('air/mass-flow').status,'Sensor not responding');assert.match(get('air/mass-flow').note,/I²C/);}
 if(status==='disconnected')assert.equal(get('air/mass-flow').status,'Pi I²C unavailable');
}
render({...good,last_age_s:2});assert.equal(get('air/mass-flow').status,'Stale');assert.equal(get('flow/air').value,'—');
render({...good,air_slm:350,range:'outside_range',write_error:'disk full'});
assert.equal(get('flow/air').value,'350');assert.equal(get('air/mass-flow').bad,true);
assert.equal(get('flow/health/write_error').value,'disk full');
render(good,{telemetryOffline:true});assert.equal(get('air/mass-flow').status,'Panel offline');assert.equal(get('flow/air').value,'—');
render(good);assert.equal(get('flow/air').value,'0');
render({enabled:false});assert.match(get('air/mass-flow').note,/Pi I²C/);
render({...good,status:'disconnected'});assert.match(get('air/mass-flow').note,/Pi I²C device/);
console.log('Mass flow tree display, status, null, and stale-data checks passed');
