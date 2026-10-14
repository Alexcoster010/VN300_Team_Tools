/* Offline map geometry and gate editor; no map SDK, credentials, or internet. */
(function(root){
'use strict';
const R=6378137,rad=Math.PI/180;
function project(lat,lon){return {x:R*lon*rad,y:R*Math.log(Math.tan(Math.PI/4+lat*rad/2))};}
function unproject(x,y){return {lat:(2*Math.atan(Math.exp(y/R))-Math.PI/2)/rad,lon:x/R/rad};}
function toPixel(point,view,width,height){const p=project(point.lat,point.lon);return {x:(p.x-view.xmin)/(view.xmax-view.xmin)*width,y:(view.ymax-p.y)/(view.ymax-view.ymin)*height};}
function fromPixel(x,y,view,width,height){return unproject(view.xmin+x/width*(view.xmax-view.xmin),view.ymax-y/height*(view.ymax-view.ymin));}
function gpsFix(packet,receivedAt,now){
 const f=packet?.fields||{},age=packet?.live_capture_age_s??packet?.age_s;
 if(!Number.isFinite(age)||age+(now-receivedAt)/1000>=2||age<0)throw new Error('Fresh GPS required. Start Setup Stream.');
 if(!Number.isFinite(f.Latitude_deg)||Math.abs(f.Latitude_deg)>85||!Number.isFinite(f.Longitude_deg)||Math.abs(f.Longitude_deg)>180||!Number.isFinite(f.PosUncertainty_m)||f.PosUncertainty_m<0||f.PosUncertainty_m>10||(Number.isFinite(f.Gnss1Fix)&&f.Gnss1Fix<2))throw new Error('GPS fix unavailable or uncertainty > 10 m.');
 return {lat:f.Latitude_deg,lon:f.Longitude_deg,uncertainty:f.PosUncertainty_m,heading:f.Yaw_deg};
}
function centeredGate(point,width,heading){
 if(!Number.isFinite(width)||width<1||width>100||!Number.isFinite(heading)||heading<0||heading>=360)throw new Error('Width: 1–100 m. Heading: 0–359.99°.');
 const c=project(point.lat,point.lon),half=width/(2*Math.cos(point.lat*rad)),angle=heading*rad;
 const dx=Math.cos(angle)*half,dy=-Math.sin(angle)*half;
 return [unproject(c.x-dx,c.y-dy),unproject(c.x+dx,c.y+dy)];
}
function createEditor({onDirty}){
 const $=id=>document.getElementById(id),canvas=$('gateMap'),ctx=canvas.getContext('2d');
 let areas=[],area=null,image=null,view=null,zoom=1,mode='pan',first=null,hover=null,drag=null,packet=null,receivedAt=0,restorePending=false;
 const W=canvas.width;let H=canvas.height;
 function say(text){$('gateMapStatus').textContent=text;}
 function fix(){return gpsFix(packet,receivedAt,performance.now());}
 function setPoint(gate,n,p){$(gate+'_lat'+n).value=p.lat.toFixed(8);$(gate+'_lon'+n).value=p.lon.toFixed(8);}
 function readPoint(gate,n){const lat=$(gate+'_lat'+n).value,lon=$(gate+'_lon'+n).value;return lat!==''&&lon!==''&&Number.isFinite(Number(lat))&&Number.isFinite(Number(lon))?{lat:Number(lat),lon:Number(lon)}:null;}
 function edited(){onDirty();draw();}
 function fit(){if(!area)return;const e=area.extent,cx=(e.xmin+e.xmax)/2,cy=(e.ymin+e.ymax)/2,w=Math.max(e.xmax-e.xmin,(e.ymax-e.ymin)*W/H),h=w*H/W;view={xmin:cx-w/2,xmax:cx+w/2,ymin:cy-h/2,ymax:cy+h/2};zoom=1;draw();}
 function setMode(next){mode=next;first=null;hover=null;['drawStart','drawFinish','mapPan'].forEach((id,i)=>$(id).setAttribute('aria-pressed',String(next===['start','finish','pan'][i])));canvas.style.cursor=next==='pan'?'grab':'crosshair';say(next==='pan'?'Drag to pan. Select a gate to draw.':`Tap endpoint 1 of the ${next} gate.`);draw();}
 function line(points,color,label){if(points.some(p=>!p))return;const [a,b]=points.map(p=>toPixel(p,view,W,H));ctx.strokeStyle='#fff';ctx.lineWidth=8;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();ctx.strokeStyle=color;ctx.lineWidth=4;ctx.stroke();[a,b].forEach(p=>{ctx.fillStyle=color;ctx.beginPath();ctx.arc(p.x,p.y,7,0,2*Math.PI);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();});ctx.font='bold 17px Arial';const tx=(a.x+b.x)/2+12,ty=(a.y+b.y)/2-12;ctx.fillStyle='#fff';ctx.fillRect(tx-5,ty-19,ctx.measureText(label).width+10,25);ctx.fillStyle=color;ctx.fillText(label,tx,ty);}
 function draw(){
  if(canvas.offsetParent===null)return;
  ctx.clearRect(0,0,W,H);ctx.fillStyle='#ecece8';ctx.fillRect(0,0,W,H);if(!view||!area||!image?.complete||!image.naturalWidth)return;
  const e=area.extent,a=toPixel(unproject(e.xmin,e.ymax),view,W,H),b=toPixel(unproject(e.xmax,e.ymin),view,W,H);ctx.drawImage(image,a.x,a.y,b.x-a.x,b.y-a.y);
  line([readPoint('start',1),readPoint('start',2)],'#841617','START');if($('mode').value==='autocross')line([readPoint('finish',1),readPoint('finish',2)],'#171717','FINISH');
  if(first){const p=toPixel(first,view,W,H);ctx.fillStyle='#841617';ctx.beginPath();ctx.arc(p.x,p.y,8,0,2*Math.PI);ctx.fill();if(hover)line([first,hover],'#841617','DRAFT');}
  try{const p=toPixel(fix(),view,W,H);ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(p.x,p.y,10,0,2*Math.PI);ctx.fill();ctx.fillStyle='#171717';ctx.beginPath();ctx.arc(p.x,p.y,5,0,2*Math.PI);ctx.fill();}catch(e){}
  ctx.fillStyle='#fff';ctx.fillRect(W-48,12,36,32);ctx.fillStyle='#171717';ctx.font='bold 18px Arial';ctx.fillText('N ↑',W-44,34);
  const lat=unproject((view.xmin+view.xmax)/2,(view.ymin+view.ymax)/2).lat,metersPerPixel=(view.xmax-view.xmin)/W*Math.cos(lat*rad),scale=Math.pow(10,Math.floor(Math.log10(metersPerPixel*120))),px=scale/metersPerPixel;ctx.fillStyle='#fff';ctx.fillRect(12,H-48,px+24,36);ctx.strokeStyle='#171717';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(24,H-22);ctx.lineTo(24+px,H-22);ctx.stroke();ctx.font='bold 14px Arial';ctx.fillStyle='#171717';ctx.fillText(scale+' m',24,H-29);
 }
 new ResizeObserver(()=>{const r=canvas.getBoundingClientRect();if(!r.width||!r.height)return;const next=Math.round(W*r.height/r.width);if(next===H)return;H=next;canvas.height=H;if(view){const cy=(view.ymin+view.ymax)/2,h=(view.xmax-view.xmin)*H/W;view.ymin=cy-h/2;view.ymax=cy+h/2;}draw();}).observe(canvas);
 function changeZoom(factor){if(!view)return;const next=Math.max(1,Math.min(32,zoom*factor)),f=zoom/next,cx=(view.xmin+view.xmax)/2,cy=(view.ymin+view.ymax)/2,w=(view.xmax-view.xmin)*f,h=(view.ymax-view.ymin)*f;zoom=next;view={xmin:cx-w/2,xmax:cx+w/2,ymin:cy-h/2,ymax:cy+h/2};draw();}
 function screen(e){const r=canvas.getBoundingClientRect();return {x:(e.clientX-r.left)*W/r.width,y:(e.clientY-r.top)*H/r.height};}
 canvas.onpointerdown=e=>{if(!view||!image?.naturalWidth)return;const p=screen(e);canvas.setPointerCapture(e.pointerId);drag={...p,clientX:e.clientX,clientY:e.clientY,view:{...view}};};
 canvas.onpointermove=e=>{if(!view)return;const p=screen(e);if(drag&&mode==='pan'){const dx=(p.x-drag.x)/W*(drag.view.xmax-drag.view.xmin),dy=(p.y-drag.y)/H*(drag.view.ymax-drag.view.ymin);view={xmin:drag.view.xmin-dx,xmax:drag.view.xmax-dx,ymin:drag.view.ymin+dy,ymax:drag.view.ymax+dy};}else if(first){hover=fromPixel(p.x,p.y,view,W,H);}draw();};
 canvas.onpointerup=e=>{if(!drag)return;const old=drag;drag=null;if(mode==='pan'||Math.hypot(e.clientX-old.clientX,e.clientY-old.clientY)>12)return;const p=screen(e),point=fromPixel(p.x,p.y,view,W,H),merc=project(point.lat,point.lon),ext=area.extent;if(merc.x<ext.xmin||merc.x>ext.xmax||merc.y<ext.ymin||merc.y>ext.ymax){say('Choose a point inside the aerial image.');return;}if(!first){first=point;say(`Tap endpoint 2 of the ${mode} gate.`);draw();}else{const a=project(first.lat,first.lon),distance=Math.hypot(a.x-merc.x,a.y-merc.y)*Math.cos(point.lat*rad);if(distance<1){say('Gate endpoints must be at least 1 m apart.');return;}const gate=mode;setPoint(gate,1,first);setPoint(gate,2,point);setMode('pan');edited();say(`${gate==='start'?'Start':'Finish'} gate changed · save timing.`);}};
 canvas.onpointercancel=()=>{drag=null;};
 $('drawStart').onclick=()=>setMode('start');$('drawFinish').onclick=()=>setMode('finish');$('mapPan').onclick=()=>setMode('pan');$('mapZoomIn').onclick=()=>changeZoom(2);$('mapZoomOut').onclick=()=>changeZoom(.5);$('mapReset').onclick=fit;
 $('gateArea').onchange=()=>{try{localStorage.setItem('srt-gate-area',$('gateArea').value);}catch(e){} area=areas.find(a=>a.id===$('gateArea').value);if(!area)return;setMode('pan');image=new Image();image.onload=draw;image.onerror=()=>say('Offline image unavailable. Use GPS or gate coordinates.');image.src=area.image;$('mapAttribution').textContent=area.attribution;fit();};
 $('gateLocate').onclick=()=>{try{const p=fix(),m=project(p.lat,p.lon),matching=areas.find(a=>m.x>=a.extent.xmin&&m.x<=a.extent.xmax&&m.y>=a.extent.ymin&&m.y<=a.extent.ymax);if(!matching)throw new Error('GPS is outside the bundled OU / Riverwind maps.');if(area!==matching){$('gateArea').value=matching.id;$('gateArea').onchange();}changeZoom(8/zoom);const w=view.xmax-view.xmin,h=view.ymax-view.ymin;view={xmin:m.x-w/2,xmax:m.x+w/2,ymin:m.y-h/2,ymax:m.y+h/2};draw();}catch(e){say(e.message);}};
 document.querySelectorAll('.gps-capture').forEach(btn=>btn.onclick=()=>{try{setPoint(btn.dataset.gate,btn.dataset.endpoint,fix());setMode('pan');edited();say('GPS endpoint captured · save timing.');}catch(e){say(e.message);}});
 document.querySelectorAll('.gps-center').forEach(btn=>btn.onclick=()=>{try{const points=centeredGate(fix(),Number($('gateWidth').value),Number($('gateHeading').value));points.forEach((p,i)=>setPoint(btn.dataset.gate,i+1,p));setMode('pan');edited();say('Gate centered on GPS · save timing.');}catch(e){say(e.message);}});
 $('useGpsHeading').onclick=()=>{try{const p=fix();if(!Number.isFinite(p.heading))throw new Error('Heading unavailable. Enter travel heading.');$('gateHeading').value=((p.heading%360+360)%360).toFixed(1);}catch(e){say(e.message);}};
 document.querySelectorAll('#setupPanel input[id^="start_"],#setupPanel input[id^="finish_"]').forEach(el=>el.addEventListener('input',draw));
 $('mode').addEventListener('change',()=>{setMode('pan');draw();});
 function update(packetValue){packet=packetValue;receivedAt=performance.now();let valid=false;try{const p=fix();$('gateGpsStatus').textContent=`GPS ±${p.uncertainty.toFixed(1)} m`;valid=true;}catch(e){$('gateGpsStatus').textContent=e.message;}
 document.querySelectorAll('.gps-capture,.gps-center,#gateLocate,#useGpsHeading').forEach(btn=>btn.disabled=!valid);draw();}
 function offline(){packet=null;update(null);}
 function restore(){if(!areas.length){restorePending=true;return;}restorePending=false;const p=readPoint('start',1),m=p?project(p.lat,p.lon):null,match=m?areas.find(a=>m.x>=a.extent.xmin&&m.x<=a.extent.xmax&&m.y>=a.extent.ymin&&m.y<=a.extent.ymax):null;if(match&&area!==match){$('gateArea').value=match.id;$('gateArea').onchange();}else draw();}
 try{const saved=localStorage.getItem('srt-gate-area');if(['ou','riverwind'].includes(saved))$('gateArea').value=saved;}catch(e){}
 offline();fetch('/assets/map-areas.json').then(r=>{if(!r.ok)throw new Error('Offline maps unavailable');return r.json();}).then(data=>{areas=data;$('gateArea').onchange();if(restorePending)restore();}).catch(e=>say(e.message));
 return {update,draw,offline,restore};
}
const api={project,unproject,toPixel,fromPixel,gpsFix,centeredGate,createEditor};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.SRTGates=api;
})(typeof window==='undefined'?globalThis:window);
