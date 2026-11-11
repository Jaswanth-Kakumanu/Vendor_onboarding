'use strict';
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let examples = [], history = [], activeRun = null, timer = null;
const stages = [['intake','Submission intake'],['documents','Read supporting evidence'],['identity','Company & tax checks'],['banking','Payment safeguards'],['compliance','Compliance & duplicate checks'],['decision','Decision & follow-up']];
const terminal = ['approved','pending','rejected','error'];
const form = $('#vendor-form');
function notice(message) { $('#notice').textContent = message; $('#notice').hidden = !message; }
async function api(path, options = {}) {
  const token = sessionStorage.getItem('vendorflow-token');
  const response = await fetch(path, {...options, headers: {...(token ? {Authorization: `Bearer ${token}`} : {}), ...options.headers}});
  if (!response.ok) {
    let detail; try { detail = (await response.json()).error; } catch { detail = `Request failed (${response.status})`; }
    throw new Error(detail);
  }
  return response.json();
}
function show(view) {
  clearTimeout(timer); activeRun = null;
  for (const name of ['overview','submission','run']) $(`#${name}-view`).hidden = name !== view;
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#breadcrumb').textContent = {overview:'Overview', submission:'New submission', run:'Run details'}[view];
  notice('');
  if (view === 'overview') refresh().catch(e => notice(e.message));
  window.scrollTo({top:0,behavior:'smooth'});
}
function countryFields() {
  const us = form.elements.country.value === 'US';
  document.querySelectorAll('.us-bank').forEach(el => el.hidden = !us);
  document.querySelectorAll('.gb-bank').forEach(el => el.hidden = us);
}
function renderHistory() {
  const query = $('#search').value.toLowerCase(); const status = $('#status-filter').value;
  const rows = history.filter(r => (r.legal_name.toLowerCase().includes(query) || r.id.includes(query)) && (status === 'all' || r.status === status));
  $('#activity-count').textContent = rows.length;
  $('#activity').innerHTML = rows.length ? rows.map(r => `<tr><td><div class="vendor-cell"><span class="vendor-icon">${escapeHTML((r.legal_name || '?').split(' ').slice(0,2).map(s=>s[0]).join(''))}</span><div><b>${escapeHTML(r.legal_name || 'Unnamed vendor')}</b><small>${escapeHTML(r.id.slice(0,8))} · ${escapeHTML(r.email)}</small></div></div></td><td>${escapeHTML(new Date(r.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}))}</td><td>${escapeHTML(r.country || '—')}</td><td><span class="badge ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></td><td><button class="row-open" data-id="${escapeHTML(r.id)}" aria-label="View ${escapeHTML(r.legal_name)}">↗</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty">No submissions here yet. Start a new submission or explore a demo scenario.</td></tr>';
}
async function refresh() {
  const data = await api('/api/runs'); history = data.runs;
  $('#metric-total').textContent = Object.values(data.counts).reduce((a,b) => a+b,0);
  for (const status of ['approved','pending','rejected']) $(`#metric-${status}`).textContent = data.counts[status] || 0;
  renderHistory();
}
function loadExample(index) {
  const example = examples[index];
  form.reset();
  for (const [key,value] of Object.entries(example.submission)) if (key !== 'bank' && form.elements[key]) form.elements[key].value = value;
  for (const [key,value] of Object.entries(example.submission.bank)) form.elements[key].value = value;
  for (const kind of ['tax','bank','compliance']) $(`#text-${kind}`).value = example.documents[kind] || '';
  document.querySelectorAll('.scenario').forEach((el,i) => el.classList.toggle('selected',i===index));
  countryFields(); notice(`Loaded “${example.name}”. Review the details, then run the checks.`);
}
async function openRun(id) {
  show('run'); activeRun = id; $('#run-name').textContent = 'Loading submission…';
  $('#run-status').className = 'badge queued'; $('#run-status').textContent = 'queued';
  $('#run-meta').textContent = ''; $('#run-short').textContent = '';
  $('#timeline').innerHTML = ''; $('#result').innerHTML = '<h2>Loading run</h2>'; $('#run-evidence').innerHTML = '';
  const poll = async () => {
    try {
      const run = await api(`/api/runs/${id}`); if (activeRun !== id) return;
      renderRun(run);
      if (!terminal.includes(run.status)) timer = setTimeout(poll,350);
    } catch(e) { notice(e.message); }
  };
  await poll();
}
function renderRun(run) {
  $('#run-short').textContent = `/ ${run.id.slice(0,8)}`;
  $('#run-name').textContent = run.legal_name || 'Unnamed vendor';
  $('#run-meta').textContent = `${run.country} · ${run.email} · ${new Date(run.created_at).toLocaleString()}`;
  $('#run-status').className = `badge ${run.status}`; $('#run-status').textContent = run.status;
  $('#timeline').innerHTML = stages.map(([key,label]) => {
    const event = run.events.find(e => e.stage === key);
    return `<article class="stage ${event?'done':''}"><h3>${label}</h3><time>${event?escapeHTML(new Date(event.at).toLocaleTimeString()):'Awaiting execution'}</time>${event?`<ul>${event.findings.map(f=>`<li class="finding ${escapeHTML(f.outcome)}"><span>${f.outcome==='pass'?'✓':f.outcome==='rejected'?'!':'•'}</span>${escapeHTML(f.message)}</li>`).join('')}</ul>`:''}</article>`;
  }).join('');
  const result = run.result;
  if (run.status === 'error') {
    $('#result').innerHTML = '<h2>Processing interrupted</h2><p>The submission was retained. Retry processing safely.</p><button class="primary" id="retry">Retry this run</button>';
    $('#retry').onclick = async () => { try { await api(`/api/runs/${run.id}/retry`,{method:'POST'}); await openRun(run.id); } catch(e) {notice(e.message);} };
  } else if (result) {
    const titles = {approved:'Ready for the next step.', pending:'A few details need attention.', rejected:'This submission needs escalation.'};
    $('#result').innerHTML = `<span class="badge ${run.status}">${run.status}</span><h2>${titles[run.status]}</h2><p>${run.status==='approved'?'All internal policy checks passed. Payment-release controls remain with procurement.':`${result.findings.filter(f=>f.outcome!=='pass').length} checks need attention. The exact next steps are below.`}</p>${result.actions.length?`<ol>${result.actions.map(a=>`<li>${escapeHTML(a)}</li>`).join('')}</ol>`:''}<div class="actions"><button class="secondary" id="report-download">↓ Decision report</button><button class="secondary" id="followup-download">↓ Vendor follow-up</button></div><div class="communication"><h3>Vendor communication</h3><p><b>To:</b> ${escapeHTML(result.communication.to)}<br><b>Subject:</b> ${escapeHTML(result.communication.subject)}</p><pre>${escapeHTML(result.communication.body)}</pre><small>Available in-app · No external email sent · Policy ${escapeHTML(result.policy_version)}</small></div>`;
    $('#report-download').onclick = async () => {try {download(JSON.stringify(await api(`/api/runs/${run.id}/report`),null,2),`decision-${run.id}.json`,'application/json');} catch(e) {notice(e.message);}};
    $('#followup-download').onclick = () => download(`To: ${result.communication.to}\nSubject: ${result.communication.subject}\n\n${result.communication.body}`,`follow-up-${run.id}.txt`,'text/plain');
  } else $('#result').innerHTML = '<span class="badge running">Processing</span><h2>Checking the details.</h2><p>The policy is running. This view updates automatically from persisted stage results.</p>';
  $('#run-evidence').innerHTML = `<h3>Evidence & traceability</h3><dl><dt>Tax identifier</dt><dd>${escapeHTML(run.summary.tax_id)}</dd><dt>Payment reference</dt><dd>${escapeHTML(run.summary.payment_reference)}</dd><dt>Account holder</dt><dd>${escapeHTML(run.summary.account_holder)}</dd></dl>${run.documents.map(d=>`<div class="evidence-item"><b>${escapeHTML(d.kind)}</b> · ${escapeHTML(d.filename)}<small>SHA-256: ${escapeHTML(d.sha256)}</small></div>`).join('') || '<p class="muted">No supporting documents submitted.</p>'}`;
}
function download(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content],{type})); const link = document.createElement('a'); link.href=url; link.download=filename; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
for (const kind of ['tax','bank','compliance']) {
  $('#evidence-inputs').insertAdjacentHTML('beforeend', `<div class="evidence-slot"><label>${{tax:'Tax registration',bank:'Bank ownership',compliance:'Compliance declaration'}[kind]}<input type="file" id="file-${kind}" accept=".pdf,.txt"></label><small>Upload a document, or paste its labeled text below. Uploaded files take priority.</small><label><textarea id="text-${kind}" aria-label="${kind} evidence text" placeholder="Paste structured evidence text…" maxlength="100000"></textarea></label></div>`);
}
form.addEventListener('submit', async event => {
  event.preventDefault(); notice(''); $('#submit-button').disabled = true;
  try {
    const values = Object.fromEntries(new FormData(form));
    const submission = {legal_name:values.legal_name,country:values.country,tax_id:values.tax_id,email:values.email,address:values.address,bank:{account_holder:values.account_holder,routing_number:values.routing_number,account_number:values.account_number,iban:values.iban}};
    const body = new FormData(); body.append('submission',JSON.stringify(submission));
    for (const kind of ['tax','bank','compliance']) {
      const file = $(`#file-${kind}`).files[0]; const text = $(`#text-${kind}`).value;
      if (file) {if(file.size>2*1024*1024) throw new Error('Each document must be 2 MB or smaller.'); body.append(kind,file);}
      else if (text.trim()) body.append(kind,new Blob([text],{type:'text/plain'}),`${kind}.txt`);
    }
    const run = await api('/api/runs',{method:'POST',body}); await openRun(run.id);
  } catch(e) { notice(e.message); } finally { $('#submit-button').disabled=false; }
});
document.querySelectorAll('.nav').forEach(el=>el.onclick=()=>show(el.dataset.view));
$('#new-submission').onclick=()=>show('submission'); $('#try-demo').onclick=()=>{show('submission');$('#scenarios').scrollIntoView({behavior:'smooth',block:'center'});};
$('#back-overview').onclick=$('#run-back').onclick=()=>show('overview');
$('#refresh').onclick=()=>refresh().catch(e=>notice(e.message));
$('#search').oninput=renderHistory; $('#status-filter').onchange=renderHistory;
$('#activity').onclick=event=>{const button=event.target.closest('[data-id]');if(button)openRun(button.dataset.id);};
form.elements.country.onchange=countryFields;
$('#access').onclick=()=>$('#access-dialog').showModal();
$('#access-dialog').addEventListener('close',()=>{if($('#access-dialog').returnValue==='save'){sessionStorage.setItem('vendorflow-token',$('#token-input').value);$('#token-input').value='';boot();}});
async function boot() {
  try {
    examples=await api('/api/examples');
    $('#scenarios').innerHTML=examples.map((example,index)=>`<button class="scenario" type="button" data-index="${index}"><b>${escapeHTML(example.name)} ↗</b><small>${escapeHTML(example.description)}</small></button>`).join('');
    $('#scenarios').onclick=event=>{const button=event.target.closest('[data-index]');if(button)loadExample(Number(button.dataset.index));};
    await refresh(); notice('');
  } catch(e) {notice(e.message);}
}
countryFields(); boot();
