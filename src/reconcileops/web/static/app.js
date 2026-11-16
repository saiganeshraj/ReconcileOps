'use strict';
const $ = s => document.querySelector(s);
let page = 'overview', batchId = null, currentFinding = null, findingsOffset = 0, auditOffset = 0, refreshBusy = false, refreshQueued = false;
const csrf = document.cookie.split('; ').find(s => s.startsWith('reconcileops_csrf='))?.split('=')[1];
const node = (tag, value, cls) => { const el = document.createElement(tag); if(value !== undefined && value !== null) el.textContent = value; if(cls) el.className = cls; return el; };
function notify(message, error=false) { const el=$('#notice'); el.textContent=message; el.hidden=false; el.className=error?'error':''; }
async function api(url, options={}) {
  const headers = {...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), 'X-CSRF-Token':csrf, ...options.headers};
  const response = await fetch(url, {...options, headers});
  if(!response.ok) { const data=await response.json().catch(()=>({detail:response.statusText})); throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)); }
  return response.json();
}
function action(label, callback) { const b=node('button',label,'text-button'); b.type='button'; b.onclick=()=>guard(callback); return b; }
async function guard(callback) { try { await callback(); } catch(e) { notify(e.message,true); } }
function badge(value) { return node('span',value,`badge ${value}`); }
function table(target, columns, rows, empty='No records yet. Start with the baseline demo or upload your orders.') {
  const holder=typeof target==='string'?$(target):target; const snapshot=JSON.stringify(rows);if(holder._snapshot===snapshot)return;holder._snapshot=snapshot;holder.replaceChildren();
  if(!rows.length) { holder.append(node('div',empty,'empty')); return; }
  const wrap=node('div',null,'table-scroll'), t=node('table'), head=node('thead'), hr=node('tr');
  for(const [title] of columns) hr.append(node('th',title)); head.append(hr); t.append(head);
  const body=node('tbody'); for(const row of rows) { const tr=node('tr'); for(const [,value] of columns) { const td=node('td'), content=typeof value==='function'?value(row):row[value]; if(content instanceof Node) td.append(content); else td.textContent=content??'—'; tr.append(td); } body.append(tr); } t.append(body); wrap.append(t); holder.append(wrap);
}
const short = id => id?.slice(0,8);
const when = value => value ? new Date(value).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}):'—';
const runColumns = [['Run',r=>action(short(r.id),()=>openRun(r.id))],['Created',r=>when(r.created_at)],['Status',r=>badge(r.status)],['Orders','orders_count'],['Events','events_count'],['Findings','findings_count'],['Actions',r=>{const box=node('span'); const a=node('a','Export CSV ↓'); a.href=`/api/runs/${r.id}/findings.csv`; box.append(a); if(r.status==='FAILED') box.append(action('Retry',async()=>{await api(`/api/runs/${r.id}/retry`,{method:'POST'});notify('Retry queued with the same snapshot.');await refresh();}));return box;}]];
async function refresh() {
  if(refreshBusy){refreshQueued=true;return;} refreshBusy=true;
  try {
    if(page==='overview') {
      const [stats,runs]=await Promise.all([api('/api/overview'),api('/api/runs?limit=5')]);
      const metrics=$('#metrics'); metrics.replaceChildren();
      for(const [label,value,detail,attention] of [['Orders imported',stats.orders,'Unique order records'],['Payment events',stats.events,'Deduplicated provider events'],['Open findings',stats.open_findings,'Across all persisted runs',true],['Reconciliation runs',stats.runs,'Durable, reproducible snapshots']]){const el=node('div',null,'metric'+(attention?' attention':''));el.append(node('div',label,'metric-label'),node('strong',value),node('small',detail));metrics.append(el);}
      const currencies=$('#currencies');currencies.replaceChildren();for(const [currency,amount] of Object.entries(stats.captured_by_currency)){const row=node('div',null,'currency-row');row.append(node('span',currency),node('strong',amount));currencies.append(row);}if(!currencies.children.length)currencies.append(node('p','Synchronize provider events to see captured amounts.'));
      table('#recent-runs',runColumns,runs);
    } else if(page==='imports') {
      table('#imports-table',[['File',r=>action(r.filename,()=>openImport(r.id))],['Status',r=>badge(r.status)],['Accepted','accepted_rows'],['Unchanged','unchanged_rows'],['Rejected','rejected_rows'],['Conflicts','conflict_rows'],['Created',r=>when(r.created_at)]],await api('/api/imports'));
    } else if(page==='runs') {
      const [runs,syncs]=await Promise.all([api('/api/runs'),api('/api/syncs')]);table('#runs-table',runColumns,runs);
      table('#syncs-table',[['Sync',r=>action(short(r.id),()=>openSync(r.id))],['Status',r=>badge(r.status)],['Imported','records_imported'],['Duplicates','records_duplicate'],['Conflicts','records_conflicted'],['Retries','retry_count'],['Failure','failure_category'],['Message','error_message']],syncs);
    } else if(page==='findings') {
      const params=new URLSearchParams(new FormData($('#filters')));params.set('limit',25);params.set('offset',findingsOffset);
      const rows=await api('/api/findings?'+params);table('#findings-table',[['Run',r=>action(short(r.run_id),()=>openRun(r.run_id))],['Rule','rule_code'],['Entity','entity_id'],['Currency','currency'],['Expected','expected_amount'],['Actual','actual_amount'],['Status',r=>badge(r.status)],['Evidence',r=>action('Inspect →',()=>openFinding(r.id))]],rows,'No findings match these filters.');$('#finding-page').textContent=`Page ${findingsOffset/25+1}`;$('#prev-findings').disabled=!findingsOffset;$('#next-findings').disabled=rows.length<25;
    } else if(page==='audit') {
      const rows=await api(`/api/audit?limit=50&offset=${auditOffset}`);table('#audit-table',[['Time',r=>when(r.timestamp)],['Actor','actor'],['Action','action'],['Entity',r=>`${r.entity_type} ${short(r.entity_id)}`],['Before','before_state'],['After','after_state'],['Note','note']],rows);$('#audit-page').textContent=`Page ${auditOffset/50+1}`;$('#prev-audit').disabled=!auditOffset;$('#next-audit').disabled=rows.length<50;
    }
  } finally {refreshBusy=false;if(refreshQueued){refreshQueued=false;await refresh();}}
}
async function navigate(next) { $('#notice').hidden=true; page=next;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==page);document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));$('#breadcrumb').textContent=document.querySelector(`[data-page="${page}"] span`)?.textContent??page;location.hash=page;await refresh(); }
async function openRun(id) { await navigate('runs'); const run=await api('/api/runs/'+id);const holder=$('#run-detail-body');holder.replaceChildren(node('p',`Run ${run.id} · Rule ${run.rule_version} · Cutoff ${run.cutoff_time}`),node('p',`${run.orders_count} orders · ${run.events_count} events · ${run.findings_count} findings`)); const stages=node('p',`Snapshot captured → ${run.attempt_count?'Execution attempted':'Awaiting worker'} → ${run.status}`);holder.append(stages);if(run.error_message)holder.append(node('p',`${run.failure_category}: ${run.error_message}`));const list=node('div');holder.append(list);table(list,[['Attempt','attempt_number'],['Started',r=>when(r.started_at)],['Finished',r=>when(r.finished_at)],['Status',r=>badge(r.status)],['Error','error_message']],run.attempts,'Waiting for the worker.');$('#run-detail').hidden=false;$('#run-detail').scrollIntoView({behavior:'smooth'}); }
async function openImport(id) { const rows=await api(`/api/imports/${id}/rows?limit=500`);batchId=id;$('#preview').hidden=false;$('#preview-summary').textContent='Source rows (up to 500 shown). Original rows and normalized values are retained in the database.';table('#preview-rows',[['Row','row_number'],['Order','order_id'],['Disposition',r=>badge(r.disposition)],['Message','error_message']],rows);$('#commit-import').hidden=true; }
async function openSync(id) { const sync=await api('/api/syncs/'+id); $('#sync-detail').hidden=false; $('#sync-detail-body').textContent=JSON.stringify(sync,null,2); $('#sync-detail').scrollIntoView({behavior:'smooth'}); }
async function openFinding(id) { currentFinding=await api('/api/findings/'+id);const f=currentFinding;$('#evidence').hidden=false;$('#evidence-title').textContent=`${f.rule_code} · ${f.entity_id}`;$('#evidence-explanation').textContent=f.evidence.condition;$('#evidence-amounts').textContent=`${f.currency??''} · Expected ${f.expected_amount??'—'} · Actual ${f.actual_amount??'—'} · Difference ${f.discrepancy_amount??'—'}`;$('#evidence-json').textContent=JSON.stringify(f.evidence,null,2);$('#source-json').textContent=JSON.stringify(f.source_records,null,2);$('#review-note').value='';$('#review-action').textContent=f.status==='RESOLVED'?'Reopen finding':'Resolve finding';table('#review-history',[['Time',r=>when(r.timestamp)],['Actor','actor'],['Action','action'],['Before','before_state'],['After','after_state'],['Note','note']],f.audit_events,'No review decisions yet.');$('#evidence').scrollIntoView({behavior:'smooth'}); }
document.querySelectorAll('[data-page],[data-go]').forEach(b=>b.onclick=()=>guard(()=>navigate(b.dataset.page??b.dataset.go)));
$('#import-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const body=new FormData();body.append('file',$('#csv-file').files[0]);const result=await api('/api/imports/preview',{method:'POST',body});batchId=result.batch_id;$('#preview').hidden=false;$('#commit-import').hidden=false;$('#preview-summary').textContent=`${result.total_rows} rows · ${result.accepted_rows} accepted · ${result.unchanged_rows} unchanged · ${result.rejected_rows} rejected · ${result.conflict_rows} conflicts. Preview shows first 20 rows.`;table('#preview-rows',[['Row','row_number'],['Order','order_id'],['Disposition',r=>badge(r.disposition)],['Message','error_message']],result.sample_rows);await refresh();});};
$('#commit-import').onclick=()=>guard(async()=>{const result=await api(`/api/imports/${batchId}/commit`,{method:'POST'});notify(`Import ${result.status.toLowerCase()}: ${result.accepted_rows} orders accepted.`);$('#preview').hidden=true;await refresh();});
$('#run-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const run=await api('/api/runs',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({cutoff_time:$('#cutoff').value})});notify(`Run ${short(run.id)} queued. The worker will process its frozen snapshot.`);await refresh();});};
$('#sync-provider').onclick=()=>guard(async()=>{await api('/api/syncs',{method:'POST'});notify('Provider synchronization queued.');await refresh();});
$('#filters').onsubmit=e=>{e.preventDefault();findingsOffset=0;guard(refresh);};
$('#prev-findings').onclick=()=>{findingsOffset=Math.max(0,findingsOffset-25);guard(refresh);};$('#next-findings').onclick=()=>{findingsOffset+=25;guard(refresh);};
$('#prev-audit').onclick=()=>{auditOffset=Math.max(0,auditOffset-50);guard(refresh);};$('#next-audit').onclick=()=>{auditOffset+=50;guard(refresh);};
$('#close-evidence').onclick=()=>$('#evidence').hidden=true;
$('#review-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const f=currentFinding;await api(`/api/findings/${f.id}/${f.status==='RESOLVED'?'reopen':'resolve'}`,{method:'POST',body:JSON.stringify({note:$('#review-note').value,version:f.version})});notify('Review decision recorded with an audit event.');await openFinding(f.id);await refresh();});};
if($('#seed-demo'))$('#seed-demo').onclick=()=>guard(async()=>{const result=await api('/api/demo/seed',{method:'POST'});notify(`Baseline loaded: ${result.orders_imported} new orders, ${result.events_synced} provider events seen.`);});
if($('#fault-form'))$('#fault-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const result=await api('/api/demo/fault',{method:'POST',body:JSON.stringify({mode:$('#fault-mode').value})});notify(`Simulated provider mode: ${result.mode}`);});};
guard(()=>navigate(document.querySelector(`.page[id="${location.hash.slice(1)}"]`) ? location.hash.slice(1):'overview'));
setInterval(()=>guard(refresh),5000);
