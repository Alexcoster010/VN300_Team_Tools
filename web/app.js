const traceView=SRTTrace.createView({traceCanvas:document.getElementById('trackPlot'),speedCanvas:document.getElementById('plot'),tooltip:document.getElementById('traceTooltip'),readout:document.getElementById('traceReadout')});
const ids=['start_lat1','start_lon1','start_lat2','start_lon2','finish_lat1','finish_lon1','finish_lat2','finish_lon2','min_speed_mph','min_gap_s'];
const metadataIds=['driver','test_type','course','test_location','car_config','tire_compound','cold_fl_psi','cold_fr_psi','cold_rl_psi','cold_rr_psi','brake_bias','aero_config','valid_run','notes'];
const steeringIds=['raw_left','raw_center','raw_right','left_angle_deg','right_angle_deg'];
let setupStreamActive=false;
let latestSteering=null, steeringLogging=false, steeringCalibrationLoaded=false;
function fmt(v,n=2){return Number.isFinite(v)?v.toFixed(n):'--'}
function timeFmt(v){if(!Number.isFinite(v))return'--'; const m=Math.floor(v/60),s=v-m*60; return m?`${m}:${s.toFixed(3).padStart(6,'0')}`:s.toFixed(3)}
function deltaFmt(v){if(v===null||v===undefined||!Number.isFinite(v))return'--'; return (v>=0?'+':'')+v.toFixed(3)}
function setModeClass(){document.getElementById('setupPanel').className='panel '+document.getElementById('mode').value}
document.getElementById('mode').addEventListener('change',setModeClass); setModeClass();
function drawTrack(t,d){traceView.update(t,d)}
function configPayload(){const p={mode:document.getElementById('mode').value}; ids.forEach(id=>p[id]=Number(document.getElementById(id).value)); return p}
function metadataPayload(){const p={}; metadataIds.forEach(id=>p[id]=document.getElementById(id).value); return p}
function setTimingSaveStatus(text,state=''){const el=document.getElementById('timingConfigSaveStatus'); el.textContent=text; el.className='save-status '+state}
function setMetadataSaveStatus(text,state=''){const el=document.getElementById('runMetadataSaveStatus'); el.textContent=text; el.className='save-status '+state}
function setSteeringSaveStatus(text,state=''){const el=document.getElementById('steeringCalibrationSaveStatus'); el.textContent=text; el.className='save-status '+state}
function fillSteeringCalibration(cal){if(!cal)return; steeringIds.forEach(id=>document.getElementById(id).value=cal[id]??''); steeringCalibrationLoaded=true}
steeringIds.forEach(id=>document.getElementById(id).addEventListener('input',()=>setSteeringSaveStatus('Unsaved calibration changes','dirty')));
document.querySelectorAll('.steering-capture').forEach(btn=>btn.onclick=()=>{
 if(!latestSteering||latestSteering.status!=='online'||!Number.isFinite(latestSteering.raw)||!Number.isFinite(latestSteering.last_age_s)||latestSteering.last_age_s>2){setSteeringSaveStatus('No fresh steering reading to capture','error');return}
 const id=btn.dataset.point; document.getElementById(id).value=latestSteering.raw; setSteeringSaveStatus(`${id.replace('raw_','')} captured at ${latestSteering.raw} raw. Unsaved calibration changes`,'dirty');
});
document.getElementById('saveSteeringCalibration').onclick=async()=>{
 const btn=document.getElementById('saveSteeringCalibration'); btn.disabled=true; setSteeringSaveStatus('Saving steering calibration...','saving');
 const payload={}; steeringIds.forEach(id=>payload[id]=Number(document.getElementById(id).value));
 if(steeringIds.some(id=>document.getElementById(id).value.trim()===''||!Number.isFinite(payload[id]))){setSteeringSaveStatus('Enter all five finite calibration values','error');btn.disabled=false;return}
 try{const r=await fetch('/api/steering_calibration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok){setSteeringSaveStatus(d.error||'Steering calibration save failed','error')}else{fillSteeringCalibration(d.steering.calibration);setSteeringSaveStatus('Saved and applied to live steering','saved')}}
 catch(e){setSteeringSaveStatus('Steering calibration save failed','error')}
 finally{btn.disabled=false}
};
['mode'].concat(ids).forEach(id=>{const el=document.getElementById(id); el.addEventListener('input',()=>setTimingSaveStatus('Unsaved timing changes','dirty')); el.addEventListener('change',()=>setTimingSaveStatus('Unsaved timing changes','dirty'))});
metadataIds.forEach(id=>document.getElementById(id).addEventListener('input',()=>setMetadataSaveStatus('Unsaved metadata changes','dirty')));
document.getElementById('saveConfig').onclick=async()=>{
 const btn=document.getElementById('saveConfig'); btn.disabled=true; setTimingSaveStatus('Saving timing setup...','saving');
 try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(configPayload())}); const d=await r.json(); if(!r.ok){setTimingSaveStatus(d.error||'Timing setup save failed','error'); alert(d.error||'Config failed')} else {fillConfig((d.timing&&d.timing.config)||d.config); setTimingSaveStatus('Timing setup saved','saved')}}
 catch(e){setTimingSaveStatus('Timing setup save failed','error'); alert('Config failed')}
 finally{btn.disabled=false}
};
document.getElementById('resetTiming').onclick=async()=>{await fetch('/api/reset_timing',{method:'POST'}); setTimingSaveStatus('Timing laps reset','saved')};
document.getElementById('setupStream').onclick=async()=>{
 const btn=document.getElementById('setupStream'), enable=!setupStreamActive; btn.disabled=true; btn.textContent=enable?'Starting...':'Stopping...';
 try{const r=await fetch('/api/setup_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enable})}); const d=await r.json(); if(!r.ok)alert(d.error||'Setup stream failed'); else setupStreamActive=!!d.setup_streaming}
 catch(e){alert('Setup stream failed')}
 finally{btn.textContent=setupStreamActive?'Stop stream':'Start stream'; btn.disabled=false}
};
document.getElementById('saveRunMetadata').onclick=async()=>{
 const btn=document.getElementById('saveRunMetadata'); btn.disabled=true; setMetadataSaveStatus('Saving metadata...','saving');
 try{const r=await fetch('/api/run_metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(metadataPayload())}); const d=await r.json(); if(!r.ok){setMetadataSaveStatus(d.error||'Metadata save failed','error'); alert(d.error||'Metadata failed')} else {fillRunMetadata(d); setMetadataSaveStatus(`Saved for ${d.next_run_id||'next run'}`,'saved')}}
 catch(e){setMetadataSaveStatus('Metadata save failed','error'); alert('Metadata failed')}
 finally{btn.disabled=false}
};
function fillConfig(cfg){if(!cfg||!cfg.mode)return; document.getElementById('mode').value=cfg.mode; setModeClass(); const s=cfg.start_line||{},f=cfg.finish_line||{};
 [['start_lat1',s.lat1],['start_lon1',s.lon1],['start_lat2',s.lat2],['start_lon2',s.lon2],['finish_lat1',f.lat1],['finish_lon1',f.lon1],['finish_lat2',f.lat2],['finish_lon2',f.lon2],['min_speed_mph',cfg.min_speed_mph],['min_gap_s',cfg.min_gap_s]].forEach(([id,v])=>{if(v!==undefined&&v!==null)document.getElementById(id).value=v}); if(typeof gateEditor!=='undefined')gateEditor.restore()}
async function loadConfig(){try{const d=await (await fetch('/api/config')).json(); fillConfig(d.config)}catch(e){}}
function fillRunMetadata(d){const m=(d&&d.metadata)||{}; metadataIds.forEach(id=>{if(m[id]!==undefined&&m[id]!==null)document.getElementById(id).value=m[id]}); document.getElementById('nextRun').textContent=d.next_run_id||'--'}
async function loadRunMetadata(){try{const d=await (await fetch('/api/run_metadata')).json(); fillRunMetadata(d)}catch(e){}}
document.getElementById('editRunInfo').onclick=()=>{
 const form=document.getElementById('runInfo'), button=document.getElementById('editRunInfo');
 form.hidden=!form.hidden; button.setAttribute('aria-expanded',String(!form.hidden));
};
let logControlBusy=false, logRequested=false;
let trackCompletedCount=0;
function updateTrackLayout(d){
 const t=d.timing||{}, age=d.live_capture_age_s??d.age_s;
 const timing=document.getElementById('timingStatus');
 timing.textContent=t.configured?(t.status||''):'Timing gates aren’t configured. Set them in Timing setup.';
 timing.className='track-timing-status'+(t.configured?'':' needs-setup'); timing.hidden=!timing.textContent;
 const count=(t.completed||[]).length, completed=document.getElementById('completedRuns');
 if(!count&&trackCompletedCount)completed.open=false; else if(count&&!trackCompletedCount)completed.open=true;
 trackCompletedCount=count;
 document.getElementById('completedSummary').textContent=count?`${count} completed`:'No completed runs yet';
 document.getElementById('completedEmpty').hidden=!!count;
 const faults=[];
 if(['error','write error','low disk space'].includes(d.log_health)&&!d.log_write_error)faults.push(`Log: ${d.log_health}`);
 if((d.logging||d.setup_streaming)&&Number.isFinite(age)&&age>=2)faults.push(`Sensor data stale · ${fmt(age,1)} s old`);
 if(d.binary_crc_errors)faults.push(`${d.binary_crc_errors} CRC errors`);
 if(d.binary_time_gaps)faults.push(`${d.binary_time_gaps} sample gaps`);
 if(d.capture_queue_full_events)faults.push(`${d.capture_queue_full_events} queue overflows`);
 const fault=document.getElementById('trackFaults');
 const text=faults.join(' · '); if(fault.textContent!==text)fault.textContent=text; fault.hidden=!text;
 const alertCount=faults.length+Number(!!(d.warning||d.log_write_error)), health=document.getElementById('trackHealth');
 health.textContent=alertCount?`${alertCount} ${alertCount===1?'alert':'alerts'}`:Number.isFinite(age)&&age<2?('Receiving'+(d.binary_effective_hz>0?` · ${fmt(d.binary_effective_hz,1)} Hz`:'')):(d.logging||d.setup_streaming)?'Waiting for sensor data':'Idle';
 health.className='track-disclosure-status'+(alertCount?' bad':'');
}
function showLogControl(requested){
 logRequested=!!requested;
 const btn=document.getElementById('logControl');
 btn.textContent=logRequested?'Stop Log':'Start Log';
 btn.disabled=logControlBusy;
}
document.getElementById('logControl').onclick=async()=>{
 if(logControlBusy)return;
 logControlBusy=true; document.getElementById('logControl').disabled=true;
 const message=document.getElementById('logControlStatus');
 const enabled=!logRequested, controller=new AbortController();
 const timeout=setTimeout(()=>controller.abort(),5000);
 message.textContent=enabled?'Requesting start...':'Requesting stop...';
 try{
  const r=await fetch('/api/logging',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled}),signal:controller.signal});
  const d=await r.json(); if(!r.ok)throw new Error(d.error||'Log request failed');
  showLogControl(d.logging_requested);
  message.textContent=d.logging_requested?'Start requested':'Stop requested; finishing files';
 }catch(e){message.textContent=e.name==='AbortError'?'Request timed out; check logging status':e.message}
 finally{clearTimeout(timeout); logControlBusy=false;}
};
async function tick(){
 const started=performance.now(),controller=new AbortController();
 const timeout=setTimeout(()=>controller.abort(),2000);
 try{const r=await fetch('/api/latest',{cache:'no-store',signal:controller.signal}); if(!r.ok)throw new Error('Telemetry request failed'); const d=await r.json(); const f=d.fields||{},t=d.timing||{};
 if(typeof updateSharedState==='function')updateSharedState(d);
 const st=document.getElementById('status'); st.textContent=d.status; st.className='pill '+(d.logging?'on':'off');
 if(!logControlBusy)showLogControl(d.logging_requested);
 setupStreamActive=!!d.setup_streaming; const setupBtn=document.getElementById('setupStream'); setupBtn.textContent=setupStreamActive?'Stop stream':'Start stream'; setupBtn.disabled=!!d.logging;
 updateTrackLayout(d);
 document.getElementById('speed').textContent=fmt(f.Speed_mph,1)+' mph'; document.getElementById('longG').textContent=fmt(f.Longitudinal_G,2)+' g'; document.getElementById('latG').textContent=fmt(f.Lateral_G,2)+' g'; document.getElementById('lapCount').textContent=String(t.lap_count||0);
 document.getElementById('currentTime').textContent=timeFmt(t.current_elapsed_s); document.getElementById('bestTime').textContent=timeFmt(t.best_lap_s);
 const deltaEl=document.getElementById('deltaTime'); deltaEl.className='value';
 if(t.live_delta_error){deltaEl.textContent=t.live_delta_error; deltaEl.className='value small bad'} else {deltaEl.textContent=t.live_delta_available?deltaFmt(t.live_delta_s):'--'}
 document.getElementById('gps').textContent=(fmt(f.Latitude_deg,5)+', '+fmt(f.Longitude_deg,5));
 document.getElementById('streamHz').textContent=fmt(d.binary_effective_hz,1);
 document.getElementById('streamQueue').textContent=String(d.capture_queue_depth||0);
 document.getElementById('streamAge').textContent=fmt(d.live_capture_age_s??d.age_s,2);
 document.getElementById('streamErrors').textContent=`CRC ${d.binary_crc_errors||0} · gaps ${d.binary_time_gaps||0} · full ${d.capture_queue_full_events||0}`;
 document.getElementById('streamErrors').className='health-errors'+((d.binary_crc_errors||d.binary_time_gaps||d.capture_queue_full_events)?' bad':'');
 document.getElementById('stream').title=`Queue peak ${d.capture_queue_peak||0} · ${d.binary_packets||0} binary packets · sample age ${fmt(d.live_capture_age_s??d.age_s,2)} s`;
 document.getElementById('logHealth').textContent=`${d.log_health||'unknown'} · ${d.log_destination||'No log device'}`;
 document.getElementById('cpuTemp').textContent=Number.isFinite(d.cpu_temp_c)?`${fmt(d.cpu_temp_c,1)} °C`:'--';
 const steering=d.steering||{};
 latestSteering=steering; steeringLogging=!!d.logging; if(!steeringCalibrationLoaded&&steering.calibration)fillSteeringCalibration(steering.calibration);
 document.querySelectorAll('.steering-capture').forEach(btn=>btn.disabled=!steering.enabled||steeringLogging);
 document.getElementById('saveSteeringCalibration').disabled=!steering.enabled||steeringLogging;
 document.getElementById('laps').innerHTML=(t.completed||[]).slice().reverse().map(l=>`<tr><td>${l.number}</td><td>${typeof esc==='function'?esc(l.type):l.type}</td><td>${timeFmt(l.duration_s)}</td><td>${deltaFmt(l.delta_to_best_s)}</td><td class="${l.warning?'bad':''}">${typeof esc==='function'?esc(l.warning||''):l.warning||''}</td></tr>`).join('');
 if(d.run_metadata&&d.run_metadata.next_run_id) document.getElementById('nextRun').textContent=d.run_metadata.next_run_id;
 drawTrack(t,d);
 }catch(e){
  if(typeof sharedOffline==='function')sharedOffline();
  document.getElementById('status').textContent='offline';document.getElementById('logControl').disabled=true;
  const health=document.getElementById('trackHealth');health.textContent='Panel offline';health.className='track-disclosure-status bad';
  const fault=document.getElementById('trackFaults');fault.textContent='Panel offline. Live readings may be outdated.';fault.hidden=false;
 }
 finally{clearTimeout(timeout); setTimeout(tick,Math.max(0,250-(performance.now()-started)));}
}
loadConfig(); loadRunMetadata(); tick();

// Shared shell and real session / analysis / setup pages. No build step or remote assets.
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const routes = {
 track:'Track', sensors:'Sensors', sessions:'Sessions', analysis:'Analysis',
 models:'Models', setup:'Setup', help:'Help'
};
let page = location.pathname.split('/')[1] || 'track';
if(page==='live')page='track';
if (!routes[page]) page = 'track';
document.querySelectorAll('[data-page]').forEach(el => el.hidden = el.dataset.page !== page);
document.querySelector(`[data-route="${page}"]`).setAttribute('aria-current','page');
$('main').setAttribute('aria-label',routes[page]);
document.title = `${routes[page]} · Sooner Racing Team`;
let library = {sessions:[],inputs:[]}, commands = {}, lastJobRender = '', savedSelection = new Set(), openedReports = {};
try { savedSelection = new Set(JSON.parse(localStorage.getItem('srt-sessions') || '[]')); } catch(e) {}
const initialSession = new URLSearchParams(location.search).get('session');
if(initialSession) savedSelection.add(initialSession);
function rememberSelection(){try{localStorage.setItem('srt-sessions',JSON.stringify([...savedSelection]));}catch(e){}}
function notice(message){$('pageNotice').textContent=message; $('pageNotice').hidden=!message;}
async function api(url, body, options={}){
 const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),options.timeout||15000);
 try {
  const r=await fetch(url,{cache:'no-store',...options,signal:controller.signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const d=await r.json(); if(!r.ok)throw new Error(d.error||`Request failed (${r.status})`); return d;
 } finally {clearTimeout(timer);}
}
function bytes(n){return n>=1024*1024?`${(n/1024/1024).toFixed(1)} MiB`:n>=1024?`${(n/1024).toFixed(1)} KiB`:`${n} B`;}
function updateSharedState(d){
 const age=d.live_capture_age_s??d.age_s, fresh=Number.isFinite(age)&&age<2;
 $('connectionState').textContent=fresh?'Sensors online':Number.isFinite(age)?'Sensor data stale':'Panel online · no sensor data';
 $('connectionDot').className='status-dot'+(fresh?' online':'');
 const m=d.logging?(d.active_run_metadata||d.run_metadata?.metadata||{}):(d.run_metadata?.metadata||{});
 $('globalDriver').textContent=m.driver||'Unassigned';
 $('globalRun').textContent=d.logging?(d.session||'Starting'):(d.run_metadata?.next_run_id||'—').replace('VN300_','');
 $('globalStorage').className=(d.log_write_error||['error','low disk space'].includes(d.log_health))?'bad':'';
 if(d.web_only){$('connectionState').textContent=fresh?'Bench · simulated data':'Bench · hardware off';$('storageLabel').textContent='System storage';}
 gateEditor.update(d);
 if(page==='sensors')renderSensorChannels(d);
 if(d.warning||d.log_write_error){notice(d.log_write_error||d.warning);}else if($('pageNotice').dataset.kind==='telemetry'){notice('');}
 $('pageNotice').dataset.kind='telemetry';
 document.querySelectorAll('#settingsForm button,#airForm button').forEach(el=>el.disabled=!!d.logging||!!d.logging_requested);
 ['speed','longG','latG'].forEach(id=>$(id).classList.toggle('stale-value',!fresh));
}
function sharedOffline(){ gateEditor.offline(); $('setupStream').disabled=true; $('connectionState').textContent='Panel offline';$('connectionDot').className='status-dot'; telemetryOffline=true;refreshSensorTree(); }
function renderSessions(){
 const query=$('sessionSearch').value.toLowerCase(), origin=$('sessionOrigin').value;
 const sessions=library.sessions.filter(s=>(!origin||s.origin===origin)&&JSON.stringify([s.name,s.metadata]).toLowerCase().includes(query));
 $('sessionList').innerHTML=sessions.length?sessions.map(s=>{
  const m=s.metadata.run_metadata||{}, stopped=s.metadata.clean_stop;
  return `<article class="session-card"><div class="section-heading"><div><h3>${esc(s.name)}</h3><p class="session-meta">${esc(m.driver||'Unassigned driver')} · ${esc(m.course||m.test_type||'No course metadata')} · ${esc(s.origin)} · ${s.files.length} files · ${bytes(s.files.reduce((n,f)=>n+f.size,0))}</p></div><div class="actions">${s.active?'<span class="job-state">Recording / flushing</span>':`<a class="button secondary" href="/api/sessions/${s.id}/download">Download session</a>`}${s.analyzable&&!s.active?`<a class="button" href="/analysis?session=${s.id}">Analyze →</a>`:''}</div></div>${stopped===false?'<p class="bad">Session was not marked as a clean stop. Inspect recording metadata before comparing.</p>':''}${s.metadata_error?`<p class="bad">${esc(s.metadata_error)}</p>`:''}<details><summary>Metadata &amp; available files</summary><pre class="document-text">${esc(JSON.stringify(s.metadata,null,2))}</pre><div class="file-list">${s.files.map(f=>`<a href="/api/files/${f.id}">${esc(f.name)} · ${bytes(f.size)}</a>`).join('')}</div></details></article>`;
 }).join(''):`<div class="empty">${library.sessions.length?'No sessions match this filter.':'No saved sessions found. Record a run on Track, or import saved logs below.'}</div>`;
}
function renderSessionPickers(){
 ['analysis','models'].forEach(prefix=>{
  const sessions=library.sessions.filter(s=>s.analyzable);
  $(prefix+'Sessions').innerHTML=sessions.length?sessions.map(s=>`<label class="session-option"><input type="checkbox" value="${s.id}" ${savedSelection.has(s.id)?'checked':''} ${s.active?'disabled':''}><span>${esc(s.name)}<small>${esc(s.metadata.run_metadata?.driver||'Unassigned')} · ${esc(s.origin)}${s.active?' · recording':''}</small></span></label>`).join(''):'<div class="empty">No analyzable sessions. Import a *_BINARY.csv or *_VNINS.csv in Sessions, or finish a recording.</div>';
  $(prefix+'Sessions').querySelectorAll('input').forEach(input=>input.onchange=()=>{if(input.checked)savedSelection.add(input.value);else savedSelection.delete(input.value);rememberSelection();});
 });
}
async function loadLibrary(){ library=await api('/api/sessions'); renderSessions(); renderSessionPickers(); }
$('sessionSearch').oninput=renderSessions; $('sessionOrigin').onchange=renderSessions;
$('refreshSessions').onclick=async()=>{try{await loadLibrary();}catch(e){notice(e.message);}};
$('importButton').onclick=async()=>{
 const files=[...$('importFiles').files], btn=$('importButton'); if(!files.length){$('importStatus').textContent='Choose saved files first.';return;}
 btn.disabled=true;
 try{for(const file of files){$('importStatus').textContent=`Importing ${file.name}…`;await api('/api/import?name='+encodeURIComponent(file.name),undefined,{method:'POST',body:file,timeout:120000});}
  $('importStatus').textContent='Imported. Sessions and model inputs are ready.'; $('importFiles').value=''; await loadLibrary();
 }catch(e){$('importStatus').textContent=e.message;}finally{btn.disabled=false;}
};
const toolDescriptions={
 laps:'Produces lap/run CSVs, sector splits, overlays, quality reports, driver/session comparisons, and measured-envelope lap predictions. Saved timing gates are discovered automatically; override them below. Without gates, the report compares whole sessions.',
 gg:'Produces G-G plots and measured driver envelopes after speed, GPS uncertainty, and acceleration filters. Use gg_envelope_by_driver.csv as the theoretical tire-model input. CSVs must contain Speed_mph, Longitudinal_G, Lateral_G, and PosUncertainty_m.',
 'tire-model':'Requires a fitted LC0 MF6.1 TIR file and a saved driver-envelope CSV from G-G analysis. This first-order envelope omits power, load transfer, temperature, and pavement correlation beyond the grip multiplier.',
 'minimum-lap':'Requires a TIR file and a reference telemetry CSV, with its zero-based starting row, sample count, and duration. The original CSV must contain Speed_mps, Longitudinal_G, Lateral_G, GPS coordinates, and Common_AngularRate_Z_rps or Imu_AngularRate_Z_rps. The model computes a same-line quasi-steady-state lower bound. It is not an optimized racing line or transient simulation.',
 'tire-comparison':'Requires LC0 and R20 fitted TIR files. Compares matched pressure, camber, load, and grip, including a load sweep.'
};
function fieldLabel(name){const labels={laps:'Lap & sector analysis',gg:'G-G plots', 'tire-model':'Theoretical tire envelope','minimum-lap':'Minimum-lap model','tire-comparison':'Tire comparison',normal_load_n:'Normal load per tire (N)',pressure_kpa:'Pressure (kPa)',camber_deg:'Camber (deg)',reference_start_row:'Reference start row (zero-based)',reference_samples:'Reference sample count',reference_duration_s:'Reference duration (s)',tir:'LC0 tire fit (.TIR)',lc0_tir:'LC0 tire fit (.TIR)',r20_tir:'R20 tire fit (.TIR)',driver_envelope:'Measured driver envelope CSV',reference_csv:'Reference telemetry CSV',auto_sectors:'Sector count',min_lap_seconds:'Minimum lap / gate gap (s)',ellipse_exponent:'Ellipse exponent',driver_order:'Driver names in sorted session order',can_bitrate:'CAN bitrate (bit/s)',arduino_reference_voltage:'Arduino reference voltage (V)',steering_baud:'Arduino baud (bit/s)',baud:'VN300 baud (bit/s)',port:'VN300 serial device',steering_port:'Arduino serial device',mass_flow_enable:'Enable Pi I²C mass flow',mass_flow_i2c_bus:'Mass flow I²C bus',mass_flow_sample_hz:'Mass flow sample rate (Hz)'};return labels[name]||name.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());}
function inputFor(field,prefix,value){
 const id=prefix+'_'+field.name, common=`id="${id}" name="${field.name}" ${field.required?'required':''}`;
 if(field.type==='bool')return `<input type="checkbox" ${common} ${value?'checked':''}>`;
 if(field.choices)return `<select ${common}><option value="">Automatic / saved</option>${field.choices.map(v=>`<option value="${esc(v)}" ${value===v?'selected':''}>${esc(v)}</option>`).join('')}</select>`;
 if(field.type==='file'){
  const options=field.name==='propulsion_root'?library.sessions.filter(s=>s.analyzable&&!s.active).map(s=>({id:s.id,name:s.name})):library.inputs.filter(f=>{
   if(field.name.includes('tir'))return f.name.toLowerCase().endsWith('.tir');
   if(field.name==='driver_envelope')return ['angle_center_deg','lateral_g','longitudinal_g'].every(column=>(f.columns||[]).includes(column));
   if(field.name==='reference_csv')return ['Speed_mps','Longitudinal_G','Lateral_G','Latitude_deg','Longitude_deg'].every(column=>(f.columns||[]).includes(column))&&(f.columns||[]).some(column=>['Common_AngularRate_Z_rps','Imu_AngularRate_Z_rps'].includes(column));
   if(field.name==='lap_summary')return ['lap_number','lap_time_s','sector_1_s','sector_2_s','sector_3_s'].every(column=>(f.columns||[]).includes(column));
   return f.name.toLowerCase().endsWith('.csv');
  });
  return `<select ${common}><option value="">${options.length?'Select input file':'Missing input — import in Sessions'}</option>${options.map(f=>`<option value="${f.id}">${esc(f.name)}${f.origin?' · '+esc(f.origin):''}</option>`).join('')}</select>`;
 }
 return `<input ${common} type="${field.type==='number'&&!field.multiple?'number':'text'}" ${field.type==='number'&&!field.multiple?`step="${field.integer?'1':'any'}"`:''} value="${esc(value??'')}" ${field.multiple?'placeholder="Comma-separated values"':''}>`;
}
function fieldHTML(field,prefix,value){return `<label ${field.type==='bool'?'class="check-field"':''}>${esc(fieldLabel(field.name))}${field.required?'<span class="required">Required</span>':''}${inputFor(field,prefix,value)}${field.help?`<span class="field-help" title="${esc(field.help)}">${esc(field.help)}</span>`:''}</label>`;}
function renderTool(prefix){
 const command=$(prefix+'Command').value, spec=commands[command]; if(!spec)return;
 $(prefix+'Intro').innerHTML=`<details><summary>Inputs &amp; model assumptions</summary>${esc(toolDescriptions[command])}</details>${spec.missing_dependencies.length?`<p class="notice">Unavailable optional dependencies: ${esc(spec.missing_dependencies.join(', '))}. Install numerical model dependencies on this host before running.</p>`:''}`;
 const primaryNames=['auto_sectors','driver_order','metadata','driver_map','speed_min_mph','speed_max_mph','gps_uncertainty_max_m','normal_load_n','pressure_kpa','camber_deg','ellipse_exponent','grip_scales','grip_scale','track_grip_scale'];
 const primary=spec.fields.filter(f=>f.required||primaryNames.includes(f.name)), advanced=spec.fields.filter(f=>!primary.includes(f));
 const html=fields=>fields.map(f=>fieldHTML(f,prefix,Array.isArray(f.default)?f.default.join(', '):f.default)).join('');
 $(prefix+'Parameters').innerHTML=`<div class="form parameter-form">${html(primary)}</div>${advanced.length?`<details><summary>Timing overrides &amp; advanced parameters (${advanced.length})</summary><div class="form parameter-form">${html(advanced)}</div></details>`:''}`;
 $(prefix==='models'?'runModel':'runAnalysis').disabled=!!spec.missing_dependencies.length;
}
function readFields(container,fields,prefix){const params={}; for(const field of fields){const el=$(prefix+'_'+field.name);if(!el)continue;
 if(field.type==='bool'){params[field.name]=el.checked;continue;}
 if(el.value.trim()==='')continue;
 params[field.name]=field.type==='number'?(field.multiple?el.value.split(/[\s,]+/).filter(Boolean).map(Number):Number(el.value)):el.value;
 }return params;}
for(const prefix of ['analysis','models']){
 $(prefix+'Command').onchange=()=>renderTool(prefix);
 $(prefix+'Form').onsubmit=async event=>{
  event.preventDefault(); const command=$(prefix+'Command').value, button=$(prefix==='models'?'runModel':'runAnalysis');button.disabled=true;
  const selected=[...$(prefix+'Sessions').querySelectorAll('input:checked:not(:disabled)')].map(el=>el.value);
  try{const d=await api('/api/jobs',{command,sessions:selected,params:readFields(prefix+'Parameters',commands[command].fields,prefix)});$(prefix+'Status').textContent=`Job ${d.id.slice(0,8)} queued. It will wait while recording.`;await loadJobs();}
  catch(e){$(prefix+'Status').textContent=e.message;}finally{button.disabled=false;}
 };
}
function jobHTML(job){
 const done=job.state==='complete', active=['queued','running','paused'].includes(job.state);
 const source=job.sessions.map(s=>s.name).join(' / ')||job.inputs.map(f=>f.name).join(' / ')||'Model';
 const renderFile=f=>`<a href="${esc(f.url)}?download=1">Export ${esc(f.name)}</a>${f.name.endsWith('.html')?`<button type="button" class="secondary view-result" data-job="${job.id}" data-url="${esc(f.url)}">View ${esc(f.name)}</button>`:''}`;
 const reports=job.files.filter(f=>f.name.endsWith('.html'));
 const exports=job.files.filter(f=>!f.name.endsWith('.html'));
 const files=reports.map(renderFile).join('')+(exports.length?`<details class="exports"><summary>CSV, JSON &amp; other exports (${exports.length})</summary><div class="file-list">${exports.map(renderFile).join('')}</div></details>`:'');
 const lastLine=(job.log||'').trim().split('\n').slice(-1)[0];
 return `<article class="job-card" data-job="${job.id}"><div class="job-title"><h3>${esc(fieldLabel(job.command))} <small>/${job.id.slice(0,8)}</small></h3><span class="job-state">${esc(job.state)}</span></div><p>${esc(source)}<br>${esc(new Date(job.created*1000).toLocaleString())} · ${esc(job.progress)}</p>${active?`<progress aria-label="${esc(job.progress)}"></progress><p>${esc(lastLine||'Waiting for the worker')}</p><button type="button" class="secondary cancel-job" data-job="${job.id}">Cancel job</button>`:''}${job.error?`<p class="bad">${esc(job.error)}</p>`:''}<div class="file-list">${done?files:''}</div><details class="worker-details"><summary>Parameters, inputs &amp; worker output</summary><pre>${esc(JSON.stringify(job.params,null,2))}\n${esc(job.log||'No worker output yet.')}</pre></details>${openedReports[job.id]?`<iframe class="result-view" title="Analysis result" sandbox="allow-scripts" src="${esc(openedReports[job.id])}"></iframe>`:''}</article>`;
}
async function loadJobs(){
 const d=await api('/api/jobs'); const key=JSON.stringify(d); if(key===lastJobRender)return; lastJobRender=key;
 const open=new Set([...document.querySelectorAll('.job-card .worker-details[open]')].map(el=>el.closest('.job-card').dataset.job));
 for(const prefix of ['analysis','models']){
  const jobs=d.jobs.filter(j=>prefix==='analysis'?['laps','gg'].includes(j.command):!['laps','gg'].includes(j.command));
  $(prefix+'Jobs').innerHTML=jobs.length?jobs.map(jobHTML).join(''):'<div class="empty">No saved results yet. Choose inputs and queue a job above. Reports and CSV exports will appear here.</div>';
 }
 document.querySelectorAll('.job-card .worker-details').forEach(el=>el.open=open.has(el.closest('.job-card').dataset.job));
 document.querySelectorAll('.cancel-job').forEach(btn=>btn.onclick=async()=>{btn.disabled=true;try{await api(`/api/jobs/${btn.dataset.job}/cancel`,{});await loadJobs();}catch(e){notice(e.message);btn.disabled=false;}});
 document.querySelectorAll('.view-result').forEach(btn=>btn.onclick=()=>{openedReports[btn.dataset.job]=btn.dataset.url;lastJobRender='';loadJobs().catch(e=>notice(e.message));});
}
function renderDeviceSettings(data){
 const primary=['port','baud','parse_mode','steering_port','steering_baud','arduino_format','steering_calibration','airspeed_calibration','mass_flow_enable','mass_flow_i2c_bus','mass_flow_sample_hz'];
 for(const [id,predicate] of [['deviceFields',f=>primary.includes(f.name)],['canFields',f=>f.name.startsWith('can_')],['advancedDeviceFields',f=>!primary.includes(f.name)&&!f.name.startsWith('can_')]]){
  $(id).innerHTML=data.fields.filter(predicate).map(f=>fieldHTML(f,'settings',data.saved[f.name]??f.default)).join('');
 }
 $('effectiveSettings').innerHTML=`Settings: <b>${esc(data.settings_file)}</b><br>Imports &amp; results: <b>${esc(data.runtime_directory)}</b><details><summary>Active service configuration (may differ from saved settings)</summary><pre class="document-text">${esc(JSON.stringify(data.effective,null,2))}</pre></details>`;
 $('settingsForm').onsubmit=async event=>{event.preventDefault();const button=$('settingsForm').querySelector('button');button.disabled=true;try{
  const params=readFields('',data.fields,'settings');for(const f of data.fields){if(!$('settings_'+f.name).value.trim()&&f.type!=='bool')params[f.name]=null;}
  const response=await api('/api/settings',params);$('deviceSettingsStatus').textContent=response.message;
 }catch(e){$('deviceSettingsStatus').textContent=e.message;}finally{button.disabled=false;}};
}
$('airForm').onsubmit=async event=>{event.preventDefault();try{const d=await api('/api/airspeed_calibration',JSON.parse($('airCalibration').value));$('airStatus').textContent=d.message+' '+d.path;}catch(e){$('airStatus').textContent=e.message;}};
$('airExample').onclick=async()=>{try{$('airCalibration').value=JSON.stringify(await api('/examples/airspeed_calibration.example.json'),null,2);$('airStatus').textContent='Typical-curve example loaded. Review and save to apply.';}catch(e){$('airStatus').textContent=e.message;}};
let documents=[];
function renderDocuments(){
 const query=$('helpSearch').value.toLowerCase();
 $('helpDocuments').innerHTML=documents.filter(d=>d.path.toLowerCase().includes(query)).map(d=>`<article class="document-card"><small>${d.historical?'HISTORICAL ARCHIVE':'CURRENT SETUP'}</small>${d.type==='.md'?`<button data-document="${d.id}">${esc(d.name)}</button>`:`<a href="/api/history?id=${d.id}" target="_blank" rel="noopener">${esc(d.name)}</a>`}<small>${esc(d.path)}</small></article>`).join('')||'<div class="empty">No matching documents.</div>';
 document.querySelectorAll('[data-document]').forEach(btn=>btn.onclick=async()=>{try{const d=await api('/api/history?id='+btn.dataset.document);$('documentTitle').textContent=d.name;$('documentText').textContent=d.text;$('documentPanel').hidden=false;$('documentPanel').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){notice(e.message);}});
}
$('helpSearch').oninput=renderDocuments;
async function initializePage(){
 try{
  if(['sessions','analysis','models','setup'].includes(page))await loadLibrary();
  if(['analysis','models'].includes(page)){commands=(await api('/api/analysis')).commands;renderTool('analysis');renderTool('models');await loadJobs();}
  if(page==='setup'){
   renderDeviceSettings(await api('/api/settings'));
   const air=await api('/api/airspeed_calibration');$('airCalibration').value=air.calibration?JSON.stringify(air.calibration,null,2):'';$('airCalibration').placeholder='Load the typical-curve example, or enter your own sensor calibration.';
   $('canLibrary').innerHTML=library.inputs.filter(f=>f.name.toLowerCase().endsWith('.dbc')||(f.columns||[]).includes('can_id')).map(f=>`<p class="inline-file">${esc(f.name)} <button type="button" class="secondary use-can-file" data-id="${f.id}" data-kind="${f.name.toLowerCase().endsWith('.dbc')?'can_dbc':'can_signal_map'}">Use for CAN</button></p>`).join('')||'<p>No saved CAN input files. Import a DBC or CSV map in Sessions.</p>';
   document.querySelectorAll('.use-can-file').forEach(btn=>btn.onclick=async()=>{try{const d=await api('/api/input_path?id='+btn.dataset.id);$('settings_'+btn.dataset.kind).value=d.path;$('deviceSettingsStatus').textContent='Input selected. Save device settings to keep it.';}catch(e){notice(e.message);}});
  }
  if(page==='help'){documents=(await api('/api/history')).documents;renderDocuments();}
 }catch(e){notice(e.message);}
}
async function pollJobs(){if(['analysis','models'].includes(page)){try{await loadJobs();}catch(e){notice(e.message);}setTimeout(pollJobs,2000);}}
initializePage().then(()=>{if(['analysis','models'].includes(page))pollJobs();if(page==='setup'&&location.hash==='#setupPanel')$('setupPanel').scrollIntoView({block:'start'});});

// Shared storage is queried at 5 s; CAN details at 1 s only on Sensors.
const gateEditor=SRTGates.createEditor({onDirty:()=>setTimingSaveStatus('Unsaved gate changes','dirty')});
async function pollStorage(){try{const d=await api('/api/storage'),v=d.system;
 if(v?.total_bytes){$('globalStorage').textContent=`${(v.free_bytes/2**30).toFixed(1)} / ${(v.total_bytes/2**30).toFixed(1)} GiB free`;$('storageMeter').max=v.total_bytes;$('storageMeter').value=v.free_bytes;$('storageMeter').title=`${(100*v.free_bytes/v.total_bytes).toFixed(0)}% available`;$('storageMeter').classList.toggle('low-storage',v.free_bytes/v.total_bytes<.1);}
 else{$('globalStorage').textContent='Unavailable';$('storageMeter').value=0;}
 const logs=d.logs;$('logStorage').textContent=logs?.total_bytes?`Logs: ${(logs.free_bytes/2**30).toFixed(1)} GiB free`:'';$('logStorage').title=logs?.path||'';
 }catch(e){$('globalStorage').textContent='Unavailable';$('storageMeter').value=0;}finally{setTimeout(pollStorage,5000);}}
let sensorPacket=null,canSnapshot=null,telemetryOffline=false,canOffline=false;
const canScalingDrafts=new Map();
let canScalingBusy=false;
const sensorTreeView=SRTSensors.createTree($('sensorTree'));
function refreshSensorTree(){
 if(page!=='sensors')return;
 const query=$('sensorSearch').value;
 const nodes=SRTSensors.buildTree(sensorPacket||{},canSnapshot||{},{query,drafts:canScalingDrafts,scalingBusy:canScalingBusy,telemetryOffline,canOffline});
 sensorTreeView.render(nodes,{filtering:!!query.trim(),query:query.trim()});$('sensorTreeEmpty').hidden=nodes.length>0;
}
function renderSensorChannels(d){sensorPacket=d;telemetryOffline=false;refreshSensorTree();}
function renderCan(){refreshSensorTree();}
$('sensorTree').oninput=event=>{const form=event.target.closest('.can-scaling');if(form)canScalingDrafts.set(form.dataset.key,{multiplier:form.elements.multiplier.value,divider:form.elements.divider.value});};
$('sensorTree').onsubmit=async event=>{
 const form=event.target.closest('.can-scaling');if(!form)return;event.preventDefault();if(canScalingBusy)return;
 const key=form.dataset.key,multiplier=Number(form.elements.multiplier.value),divider=Number(form.elements.divider.value);
 if(!Number.isFinite(multiplier)||!Number.isFinite(divider)||divider===0){$('canScalingStatus').textContent='Use finite values; divider cannot be zero.';return;}
 canScalingBusy=true;form.querySelector('button').disabled=true;
 try{const response=await api('/api/can_scaling',{key,multiplier,divider});canScalingDrafts.delete(key);
  const row=canSnapshot?.signals.find(row=>row.key===key);if(row){row.scaling=response.scaling;row.display_value=row.value*multiplier/divider;}
  $('canScalingStatus').textContent='Display scaling saved.';
 }catch(e){$('canScalingStatus').textContent=e.message;}finally{canScalingBusy=false;form.querySelector('button').disabled=false;refreshSensorTree();}
};
async function pollCan(){try{canSnapshot=await api('/api/can',undefined,{timeout:2000});canOffline=false;renderCan();}catch(e){canOffline=true;renderCan();}finally{setTimeout(pollCan,1000);}}
$('sensorSearch').oninput=refreshSensorTree;
$('sensorExpand').onclick=()=>sensorTreeView.expandAll();
$('sensorCollapse').onclick=()=>sensorTreeView.collapseAll();
pollStorage();if(page==='sensors')pollCan();
