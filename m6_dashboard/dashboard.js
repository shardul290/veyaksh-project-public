"use strict";
const $ = selector => document.querySelector(selector);
const historyPage = location.pathname === "/history";
const labels = {pending:"Awaiting review",false_alert:"False alert",action_taken:"Action taken",authority_requested:"Authority alert requested",archived:"Archived"};
let nodes = [], cameras = [], events = [], selectedCamera = null, selectedEvent = null;
let markers = new Map(), map, initialized = false, lastId = 0, polling = false, more = false;
let soundEnabled = false, audioContext, reviewBusy = false;
const fmt = value => {
  if (!value) return "Not recorded";
  const parsed = new Date(/^[\d-]+ [\d:]+$/.test(value) ? value.replace(" ","T")+"Z" : value);
  return Number.isNaN(parsed.getTime()) ? "Invalid timestamp" : new Intl.DateTimeFormat("en-IN",{timeZone:"Asia/Kolkata",day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(parsed) + " IST";
};
const typeName = value => value === "wrong_way" ? "Possible wrong way" : value === "encroachment" ? "Encroachment" : value;
const nodeName = id => nodes.find(n => n.node_id === id)?.name || id;
function element(tag, text, className) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(className) el.className = className; return el; }
function badge(status) { return element("span",labels[status] || status,"badge "+status); }
function time(value) { const t=element("time",fmt(value)); if(value) t.dateTime=value; return t; }
function mediaUrl(value) {
  if(typeof value !== "string" || !value.trim()) return null;
  const [path, fragment=""] = value.replaceAll("\\","/").split("#",2);
  const parts = path.split("/").filter(p => p && p !== ".");
  if(parts.some(p => p === ".." || p.includes(":"))) return null;
  if(parts[0] === "media") parts.shift();
  if(!parts.length) return null;
  return "/media/" + parts.map(encodeURIComponent).join("/") + (/^t=\d+(?:\.\d+)?$/.test(fragment) ? "#"+fragment : "");
}
async function api(url, options={}) {
  const response = await fetch(url,{cache:"no-store",...options,headers:{Accept:"application/json",...options.headers}});
  if(!response.ok) { let detail; try { detail=(await response.json()).detail; } catch {} throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`); }
  return response.json();
}
function beep() {
  if(!soundEnabled || !audioContext) return;
  audioContext.resume().then(() => {
    for(const delay of [0,.2]) {
      const oscillator=audioContext.createOscillator(), gain=audioContext.createGain(), at=audioContext.currentTime+delay;
      oscillator.frequency.value=800; gain.gain.setValueAtTime(.001,at);gain.gain.exponentialRampToValueAtTime(.12,at+.015);gain.gain.exponentialRampToValueAtTime(.001,at+.15);
      oscillator.connect(gain);gain.connect(audioContext.destination);oscillator.start(at);oscillator.stop(at+.16);
    }
  }).catch(() => { $("#sound").textContent="Click to enable sound";soundEnabled=false;$("#sound").setAttribute("aria-pressed","false"); });
}
$("#sound").addEventListener("click",async()=>{
  if(soundEnabled) {soundEnabled=false;$("#sound").textContent="Enable sound";$("#sound").setAttribute("aria-pressed","false");return;}
  try {const AC=window.AudioContext||window.webkitAudioContext;if(!AC) throw new Error("This browser does not support alert sound.");audioContext ||= new AC();await audioContext.resume();soundEnabled=true;$("#sound").textContent="Sound on · Mute";$("#sound").setAttribute("aria-pressed","true");beep();} catch(error){$("#error").textContent=error.message;}
});
function initializeMap() {
  if(historyPage) return;
  if(!window.L) {$("#map").textContent="The map library could not load. Camera selectors are available alongside the map.";return;}
  map=L.map("map",{scrollWheelZoom:false}).setView([29.86444,77.89812],16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
  for(const node of nodes) {
    const marker=L.marker([node.lat,node.lon],{title:`${node.name} · ${node.node_id}`,alt:node.name,icon:L.divIcon({className:"camera-marker",html:'<span class="camera-pin"></span>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);
    marker.bindTooltip(node.name,{permanent:true,direction:"top",offset:[0,-12]});
    marker.on("click",()=>selectCamera(node.node_id));markers.set(node.node_id,marker);
  }
  if(nodes.length) map.fitBounds(nodes.map(n=>[n.lat,n.lon]),{padding:[50,50],maxZoom:17});
}
function updateMarkers() {
  for(const [id,marker] of markers) {
    const active = events.some(e=>e.node_id===id && e.status==="pending" && !e.archived);
    marker.getElement()?.classList.toggle("is-alert",active);
  }
}
function selectCamera(id) {
  selectedCamera=id;
  const camera=cameras.find(c=>c.node_id===id), node=nodes.find(n=>n.node_id===id);
  $("#camera-title").textContent=`${node?.name || id} · ${camera?.cam_id || ""}`;
  for(const button of $("#camera-selector").children) button.classList.toggle("selected",button.dataset.node===id);
  const player=$("#camera-video"); player.pause();player.removeAttribute("src");player.load();player.hidden=true;
  $("#camera-empty").hidden=false;$("#camera-empty strong").textContent="Awaiting recording";$("#camera-empty p").textContent="The recording for this location has not been added yet.";
  $("#recordings").replaceChildren();
  $("#recording-info").textContent=`${node?.lat.toFixed(7)}° N, ${node?.lon.toFixed(7)}° E`;
  for(const recording of camera?.recordings || []) {
    const button=element("button",recording.label || recording.path);button.type="button";
    const play=()=>{const url=mediaUrl(recording.path);if(!url) return;player.src=url;player.hidden=false;$("#camera-empty").hidden=true;$("#recording-info").textContent=`Recording start: ${fmt(recording.start_ts)} · ${node.lat.toFixed(7)}° N, ${node.lon.toFixed(7)}° E`;player.load();};
    button.addEventListener("click",play);$("#recordings").append(button);if($("#recordings").children.length===1) play();
  }
}
$("#camera-video").addEventListener("error",()=>{$("#camera-video").hidden=true;$("#camera-empty").hidden=false;$("#camera-empty strong").textContent="Recording unavailable";$("#camera-empty p").textContent="Use an MP4 with H.264 video, then refresh this page.";});
function render() {
  const pending=events.filter(e=>e.status==="pending" && !e.archived);
  $("#evidence-count").textContent=pending.length;
  const queue=$("#evidence");queue.replaceChildren();
  if(!pending.length) {const empty=element("div",undefined,"empty");empty.append(element("strong","No evidence awaiting review"),element("span","New camera events will appear here. Reviewed events remain in history."));queue.append(empty);}
  for(const event of pending) {
    const card=element("article",undefined,"evidence-card"), thumb=element("div",undefined,"evidence-thumb"), content=element("div",undefined,"evidence-content");
    const src=mediaUrl(event.snapshot);
    if(src) {const img=element("img");img.src=src;img.alt="Event frame snapshot";img.loading="lazy";img.onerror=()=>{thumb.textContent="Frame unavailable";};thumb.append(img);} else thumb.textContent="No frame attached";
    const review=element("button","Review evidence →");review.type="button";review.addEventListener("click",()=>showEvent(event));
    const stamp=element("p");stamp.append(time(event.ts));
    content.append(badge(event.status),element("h3",typeName(event.type)),element("p",`${nodeName(event.node_id)} · ${event.cam_id} · Event #${event.id}`),stamp,review);card.append(thumb,content);queue.append(card);
  }
  const rows=$("#history-rows");rows.replaceChildren();const filter=$("#status-filter").value;
  for(const event of events.filter(e=>!filter || e.status===filter)) {
    const row=element("tr"), name=element("td",typeName(event.type));name.append(element("small",`${event.archived ? "Legacy recording" : nodeName(event.node_id)} · ${event.cam_id} · #${event.id}`));
    const timestamp=element("td");timestamp.append(time(event.ts));const statusAt=element("td");if(event.status_at) statusAt.append(time(event.status_at));else statusAt.textContent="—";
    const status=element("td");status.append(badge(event.archived ? "archived" : event.status));const action=element("td"), button=element("button","Review");button.addEventListener("click",()=>showEvent(event));action.append(button);
    row.append(name,timestamp,element("td",event.video_ts_s == null ? "Not recorded" : `${event.video_ts_s.toFixed(3)} s`),status,statusAt,action);rows.append(row);
  }
  if(!rows.children.length){const row=element("tr"), cell=element("td","No events match this view.");cell.colSpan=6;row.append(cell);rows.append(row);}
  $("#history-note").textContent=`${events.length} events loaded${more ? " · older events available" : ""}`;$("#load-more").hidden=!more;
  updateMarkers();
}
async function showEvent(event) {
  selectedEvent=event;$("#event-title").textContent=`${typeName(event.type)} · #${event.id}`;$("#review-error").textContent="";$("#media-error").textContent="";
  const video=$("#event-video"), img=$("#event-image");video.pause();video.removeAttribute("src");video.load();video.hidden=true;img.hidden=true;img.removeAttribute("src");
  if(event.archived) {$("#media-error").textContent="Legacy recording retained for audit. Its footage is no longer valid for the surveyed locations.";}
  else {
    const clip=mediaUrl(event.clip);if(clip){video.src=clip;video.hidden=false;} else $("#media-error").textContent="No playable clip is attached.";
    const snapshot=mediaUrl(event.snapshot);if(snapshot){img.src=snapshot;img.hidden=false;}
  }
  const fields=$("#event-details");fields.replaceChildren();
  for(const [label,value] of [["Clip timestamp",fmt(event.ts)],["Video offset",event.video_ts_s == null ? "Not recorded" : `${event.video_ts_s.toFixed(3)} seconds`],["Camera",`${event.cam_id} / ${event.archived ? "Legacy location" : nodeName(event.node_id)}`],["Status",event.archived ? "Archived legacy recording" : labels[event.status]],["Status timestamp",fmt(event.status_at)],["Received",fmt(event.received_at)],["Vehicle class",event.class],["Detector confidence",`${(event.conf*100).toFixed(1)}%`],["Plate",event.plate?.text || "Not recorded"]]) fields.append(element("dt",label),element("dd",value));
  $("#false-alert").hidden=event.status!=="pending" || Boolean(event.archived);$("#take-action").hidden=!["pending","action_taken"].includes(event.status) || Boolean(event.archived);$("#take-action").textContent=event.status==="action_taken" ? "Response options" : "Take action";
  $("#activity").replaceChildren(element("li",`Event recorded · ${fmt(event.ts)}`));
  if(!$("#evidence-dialog").open) $("#evidence-dialog").showModal();
  try {const actions=await api(`/events/${event.id}/actions`);if(selectedEvent?.id!==event.id)return;for(const action of actions)$("#activity").append(element("li",`${labels[action.status]} · ${fmt(action.ts)}`));}catch(error){$("#review-error").textContent=error.message;}
}
$("#event-video").addEventListener("error",()=>{$("#media-error").textContent="The event clip could not be loaded. Check that its file is available.";});
$("#event-image").addEventListener("error",()=>{$("#event-image").hidden=true;});
for(const button of document.querySelectorAll("[data-close]"))button.addEventListener("click",()=>document.getElementById(button.dataset.close).close());
$("#evidence-dialog").addEventListener("close",()=>$("#event-video").pause());
async function setStatus(status) {
  if(reviewBusy || !selectedEvent)return false;reviewBusy=true;
  for(const id of ["false-alert","take-action","alert-authorities"])$("#"+id).disabled=true;
  try {const result=await api(`/events/${selectedEvent.id}/status`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status})});Object.assign(selectedEvent,result);render();refreshSummary().catch(error=>{$("#error").textContent=error.message;});return true;}
  catch(error){$("#review-error").textContent=error.message;$("#action-error").textContent=error.message;return false;}
  finally{reviewBusy=false;for(const id of ["false-alert","take-action","alert-authorities"])$("#"+id).disabled=false;}
}
$("#false-alert").addEventListener("click",async()=>{if(await setStatus("false_alert"))$("#evidence-dialog").close();});
$("#take-action").addEventListener("click",async()=>{if(selectedEvent.status==="action_taken" || await setStatus("action_taken")){$("#evidence-dialog").close();$("#action-error").textContent="";$("#alert-authorities").hidden=false;$("#action-dialog").showModal();}});
$("#alert-authorities").addEventListener("click",async()=>{if(await setStatus("authority_requested")){$("#action-error").textContent="Request saved with timestamp. No external alert has been sent.";$("#alert-authorities").hidden=true;}});
$("#status-filter").addEventListener("change",render);
async function refreshSummary(){const summary=await api("/events-summary");$("#pending-count").textContent=summary.pending;$("#action-count").textContent=summary.action_taken+summary.authority_requested;$("#false-count").textContent=summary.false_alert;}
async function refresh() {
  if(polling)return;polling=true;
  try {
    const [latest,pending]=await Promise.all([api("/events?limit=100"),api("/events?status=pending&active=true&limit=500")]);
    if(initialized && latest.some(e=>e.id>lastId && !e.archived && e.status==="pending")){beep();}
    const fresh=new Map([...latest,...pending].map(e=>[e.id,e]));
    // Re-fetch loaded pages so reviews made by another operator are reflected too.
    if(initialized && events.length) {
      const oldest=Math.min(...events.map(e=>e.id));let before=null;
      while(true){const page=await api(`/events?limit=100${before ? "&before_id="+before : ""}`);for(const event of page)fresh.set(event.id,event);if(page.length<100 || page.at(-1).id<=oldest)break;before=page.at(-1).id;}
    }
    const nextEvents=[...fresh.values()].sort((a,b)=>b.id-a.id);const changed=JSON.stringify(events)!==JSON.stringify(nextEvents);events=nextEvents;lastId=Math.max(lastId,...latest.map(e=>e.id));
    if(!initialized)more=latest.length===100;
    initialized=true;if(changed)render();await refreshSummary();
    $("#connection").textContent="System connected";$("#connection").dataset.state="connected";$("#error").textContent="";
    const updated=await api("/cameras");if(JSON.stringify(updated)!==JSON.stringify(cameras)){cameras=updated;if(selectedCamera)selectCamera(selectedCamera);}
  }catch(error){$("#connection").textContent="Connection interrupted";$("#connection").dataset.state="error";$("#error").textContent=error.message;}
  finally{polling=false;}
}
$("#load-more").addEventListener("click",async()=>{
  const button=$("#load-more");button.disabled=true;
  try{const oldest=Math.min(...events.map(e=>e.id));const page=await api(`/events?limit=100&before_id=${oldest}`);events=[...new Map([...events,...page].map(e=>[e.id,e])).values()].sort((a,b)=>b.id-a.id);more=page.length===100;render();}catch(error){$("#error").textContent=error.message;}finally{button.disabled=false;}
});
async function start(){
  $(historyPage?"#nav-history":"#nav-dashboard").classList.add("active");$("#overview").hidden=historyPage;$("#history").hidden=!historyPage;
  if(historyPage){document.title="Event history | VEYAKSH";$("#page-title").textContent="Event history";$("#breadcrumb").textContent="EVENT HISTORY";$("#page-description").textContent="A permanent record of events and the decisions made on each one.";}
  try{[nodes,cameras]=await Promise.all([api("/nodes"),api("/cameras")]);$("#camera-count").textContent=nodes.length;initializeMap();for(const node of nodes){const button=element("button",node.name);button.dataset.node=node.node_id;button.addEventListener("click",()=>selectCamera(node.node_id));$("#camera-selector").append(button);}if(!historyPage && nodes.length)selectCamera(nodes[0].node_id);}catch(error){$("#error").textContent=error.message;}
  await refresh();setInterval(refresh,2000);
}
start();
