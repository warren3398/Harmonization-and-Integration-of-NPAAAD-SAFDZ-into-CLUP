'use strict';
(() => {
 const $=id=>document.getElementById(id),form=$('mainForm'),run=$('runBtn'),msg=$('inspectMsg'),mapping=$('mapping'),cards=$('layerCards');
 const mode=$('referenceMode'),token=$('autoReferenceToken'),manualToggle=$('manualReferenceToggle');
 const file=name=>form.elements.namedItem(name),hasFile=name=>!!file(name)?.files?.length;
 const labels={elu:'Existing / Actual Land Use (ELU)',clup:'Approved / Proposed CLUP',npaaad:'NPAAAD',safdz:'SAFDZ',landcover:'Land Cover Map 2025'};
 const steps=['Upload Data','Process','Download Results'];
 let inspection=null,inspected=false,busy=false,sequence=0,debounce=null,controller=null,eluMap=Object.create(null);
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function notice(text,state='info'){msg.textContent=text;msg.dataset.state=state;}
 function setStep(index,complete=false){
  $('workflowStatus').textContent=complete?'Complete · Results ready':`Step ${index+1} of 3 · ${steps[index]}`;
  document.querySelectorAll('.steps li').forEach((el,i)=>{el.classList.toggle('active',!complete&&i===index);el.classList.toggle('done',complete||i<index);if(i===index&&!complete)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
 }
 function showReference(heading,text,state='idle'){
  $('autoReferenceHeading').textContent=heading;$('autoReferenceText').textContent=text;$('autoReferenceStatus').dataset.state=state;
 }
 function sourceReady(){return mode.value==='auto'||(hasFile('npaaad_zip')&&hasFile('safdz_zip'));}
 function fieldsReady(){return ['elu','npaaad','safdz',...['clup','landcover'].filter(role=>hasFile(role+'_zip'))].every(role=>!!file(role+'_field')?.value);}
 function pendingCount(){return Object.values(eluMap).filter(value=>!value).length;}
 function reviewReady(){return !hasFile('landcover_zip')||(Object.keys(eluMap).length>0&&!pendingCount()&&!!$('eluReviewed')?.checked);}
 function cleanupReady(){const report=inspection?._elu_cleanup;return !report?.requires_review&&(!report?.requires_change_confirmation||$('eluCleanupConfirmed').checked);}
 function ready(){return cleanupReady()&&inspected&&hasFile('elu_zip')&&sourceReady()&&(mode.value!=='auto'||!!token.value)&&inspection?._crs_summary?.master_ok&&fieldsReady()&&reviewReady();}
 function refresh(){
  run.disabled=busy||!ready();
  $('retryInspect').disabled=busy;
  $('retryInspect').classList.toggle('hidden',inspected||!hasFile('elu_zip'));
  form.setAttribute('aria-busy',String(busy));
  $('runHint').textContent=inspection?._elu_cleanup?.requires_review?'Review the ELU geometry conflicts before generation.':busy?'Processing your selected files. Inputs are locked until this job finishes.':run.disabled?hasFile('landcover_zip')?'Complete uploads and confirm the land-cover comparison categories.':'Upload your ELU and wait for reference detection. Check any geometry warnings.':'Ready to generate the Excel summary, GIS layer, and QA/QC report.';
 }
 function updateMapping(){
  $('eluMapping').value=JSON.stringify(eluMap);
  const total=Object.keys(eluMap).length,pending=pendingCount(),ignored=Object.values(eluMap).filter(v=>v==='OTHER').length;
  if(!hasFile('landcover_zip')){$('eluReviewed').checked=true;refresh();return;}
  $('mappingCount').textContent=`${total-pending} of ${total} reviewed · ${pending} pending · ${ignored} ignored`;
  $('eluReviewed').disabled=!!pending||busy;
  $('eluReviewed').checked=false;
  if(pending)notice(`Review ELU descriptions: ${pending} still need a category. AGR = agricultural; BU = built-up; Other / Ignore skips ELU-based comparison rules; empty decisions still use ELU_CLS.`,'warning');
  else notice('All ELU descriptions have categories. Confirm the mapping below to continue.','warning');
  refresh();
 }
 function renderElu(field){
  const rows=$('eluMapRows'),values=inspection?.elu?.field_values?.[field]||[];
  eluMap=Object.create(null);rows.replaceChildren();
  for(const value of values){
   const normalized=String(value).toUpperCase().replace(/[_-]/g,' ').trim();
   eluMap[value]=hasFile('landcover_zip')?'':(['AGR','AGRICULTURE','AGRICULTURAL'].includes(normalized)||normalized.startsWith('AGR ')?'AGR':(['BU','BUILT UP','BUILTUP'].includes(normalized)||normalized.includes('BUILT UP')?'BU':'OTHER'));const row=document.createElement('div');row.className='elu-map-row';
   row.innerHTML=`<span>${esc(value)}</span><select aria-label="Category for ${esc(value)}"><option value="">Choose category…</option><option value="AGR">AGR — Agricultural</option><option value="BU">BU — Built-up</option><option value="OTHER">Other / Ignore</option></select>`;
   row.querySelector('select').addEventListener('change',event=>{eluMap[value]=event.target.value;updateMapping();});rows.append(row);
  }
  $('eluMapBox').classList.toggle('hidden',!hasFile('landcover_zip'));
  $('eluReviewed').checked=false;updateMapping();
  if(!values.length)notice('The selected ELU field has no descriptions. Choose a populated classification field.','error');
 }
 function invalidate(){
  clearTimeout(debounce);controller?.abort();sequence++;inspected=false;inspection=null;eluMap=Object.create(null);
  $('eluMapping').value='{}';mapping.classList.add('hidden');cards.replaceChildren();$('eluCleanupConfirmed').checked=false;$('eluReviewDownload').classList.add('hidden');$('eluReviewDownload').removeAttribute('href');
  $('progressPanel').classList.add('hidden');$('downloadBtn').classList.add('hidden');$('downloadBtn').removeAttribute('href');
  setStep(0);refresh();
 }
 function inspectNow(){
  if(busy)return;invalidate();
  $('inspectMsg').setAttribute('aria-busy','true');
  if(!hasFile('elu_zip')){notice('Upload the required ELU shapefile ZIP to continue.');return;}
  if(!sourceReady()){notice('Manual mode: upload both NPAAAD and SAFDZ shapefile ZIPs.','warning');return;}
  if(mode.value==='auto')showReference('Detecting and cropping reference data…','Matching the ELU location and clipping NPAAAD / SAFDZ. Larger areas may take longer.','loading');
  notice(mode.value==='auto'?'Detecting NPAAAD / SAFDZ and cropping to your ELU boundary…':'Checking uploaded files and coordinate systems…');
  const current=sequence;debounce=setTimeout(()=>inspect(current),500);
 }
 async function readJSON(response,fallback){
  let data;try{data=JSON.parse(await response.text());}catch{throw Error(fallback);}
  if(!response.ok)throw Error(data.error||fallback);return data;
 }
 function waitForServer(current){
  debounce=setTimeout(async()=>{
   if(current!==sequence)return;
   try{const response=await fetch('/api/gis-status',{cache:'no-store'});const state=await response.json();if(current!==sequence)return;if(response.ok&&!state.busy){inspect(current);return;}}catch{}
   if(current===sequence)waitForServer(current);
  },2500);
 }
 async function inspect(current){
  controller=new AbortController();
  try{
   const data=await readJSON(await fetch('/inspect',{method:'POST',body:new FormData(form),signal:controller.signal}),'Unable to inspect the files. The service may have restarted; please retry.');
   if(current!==sequence)return;
   $('inspectMsg').setAttribute('aria-busy','false');
   if(!$('outputMunicipality').dataset.edited){$('outputMunicipality').value=data._municipality||'';previewFilename();}
   inspection=data;inspected=true;cards.replaceChildren();const crs=data._crs_summary||{};renderCleanup(data._elu_cleanup);
   if(mode.value==='auto'&&!data._elu_cleanup?.requires_review){
    const detected=data._auto_reference;
    if(!detected?.token)throw Error('Automatic reference detection did not complete. Upload the ELU again.');
    token.value=detected.token;
    const names=[...new Set((detected.sources||[]).map(row=>row.province.replaceAll('-',' ')))];
    const coverage=detected.coverage_percent||{};
    const suffix=Object.entries(coverage).map(([role,value])=>role.toUpperCase()+': '+value+'% of ELU covered').join(' · ');
    showReference('NPAAAD + SAFDZ cropped to your ELU',names.map(name=>name.toUpperCase()).join(', ')+(suffix?' · '+suffix:''),'ready');
   }
   $('crsBanner').className='crs-banner '+(crs.master_ok?'ok':'warn');
   $('crsBanner').textContent=crs.master_ok?`Final CRS: ${crs.master_crs}. Other layers will be reprojected to match NPAAAD / SAFDZ.`:(crs.error||'NPAAAD and SAFDZ must have matching, readable coordinate systems.');
   for(const [role,label] of Object.entries(labels)){
    const meta=data[role];if(!meta)continue;const card=document.createElement('div');card.className='layer-card';
    card.innerHTML=`<h3>${label}</h3><label>Classification field<select name="${role}_field">${(meta.fields||[]).map(f=>`<option value="${esc(f)}" ${f===meta.suggested_field?'selected':''}>${esc(f)}</option>`).join('')}</select></label>`;
    if(role==='elu'){
     card.innerHTML+='<div id="eluMapBox" class="elu-map"><h3>ELU Description Mapping</h3><p class="hint">Select a category for every description. Other / Ignore skips comparison rules. If no rule applies, Decision retains the ELU_CLS description.</p><div id="eluMapRows" class="elu-map-rows"></div><p id="mappingCount" class="mapping-count" aria-live="polite"></p><label class="mapping-review"><input type="checkbox" id="eluReviewed" name="elu_reviewed" value="yes"><span>I reviewed all categories, including any descriptions marked Other / Ignore.</span></label></div>';
    }
    cards.append(card);
    card.querySelector('select').addEventListener('change',e=>{if(role==='elu'){$('eluCleanupField').value=e.target.value;$('eluClassPriorities').value='{}';token.value='';inspectNow();}else refresh();});
   }
   mapping.classList.remove('hidden');$('layerSettings').open=hasFile('landcover_zip')||!!data._elu_cleanup?.requires_review;setStep(0);
   if(data.elu){$('eluCleanupField').value=file('elu_field').value;renderElu(file('elu_field').value);$('eluReviewed').addEventListener('change',()=>{
    if($('eluReviewed').checked){const allIgnored=Object.values(eluMap).every(v=>v==='OTHER');notice(allIgnored?'Mapping confirmed. All descriptions skip ELU comparison rules. Empty decisions will use the original ELU_CLS descriptions.':'Mapping confirmed. Ready to run harmonization.',allIgnored?'warning':'ready');}
    else notice('Confirm the ELU mapping to continue.','warning');refresh();
   });}
   if(data._elu_cleanup?.requires_review){showReference('Waiting for ELU geometry review','Resolve class conflicts or missing classifications before reference detection.','error');$('eluQualityPanel').open=true;}
   if(!crs.master_ok)notice(crs.error||'Upload NPAAAD and SAFDZ with matching coordinate systems.','error');
   if(!hasFile('landcover_zip')&&crs.master_ok&&!data._elu_cleanup?.requires_review)notice('Files checked. Coordinate systems will align automatically. Ready to process.','ready');
   refresh();
  }catch(error){if(current!==sequence||error.name==='AbortError')return;if(mode.value==='auto'&&error.message.includes('Another ELU reference detection')){notice('Waiting for the active GIS operation to finish…');showReference('Preparing reference data…','Another GIS operation is running. This upload will retry automatically.','loading');waitForServer(current);return;}$('inspectMsg').setAttribute('aria-busy','false');inspected=false;mapping.classList.add('hidden');notice(error.message,'error');if(mode.value==='auto')showReference('Reference detection needs attention',error.message,'error');refresh();}
 }
 function syncSources(){
  const manual=manualToggle.checked;
  mode.value=manual?'manual':'auto';token.value='';
  $('manualReferenceFiles').classList.toggle('hidden',!manual);
  for(const name of ['npaaad_zip','safdz_zip']){file(name).required=manual;file(name).disabled=!manual;}
  $('autoReferenceStatus').classList.toggle('hidden',manual);
  showReference('Ready to detect your area','No island group or province selection is needed.');
  inspectNow();
 }
 manualToggle.addEventListener('change',syncSources);
 form.addEventListener('change',event=>{
  if(event.target.type==='file'){
   if(event.target.name==='elu_zip'){delete $('outputMunicipality').dataset.edited;$('eluCleanupField').value='';$('eluClassPriorities').value='{}';token.value='';showReference('Ready to detect your area','The new ELU will be used to detect and crop reference data.');}
   inspectNow();
  }
 });
 function renderCleanup(report){
  if(!report)return;
  const priorities=JSON.parse($('eluClassPriorities').value||'{}');
  $('eluQualitySummary').textContent=`${report.invalid_features_repaired} invalid features repaired · ${report.same_class_overlap_removed_m2} m² of same-class overlap removed · ${report.enclosed_gaps_detected} enclosed gaps (${report.gaps_filled} filled, ${report.gaps_preserved} preserved) · ${report.overlap_conflicts.length} class conflicts (${!report.overlap_conflicts.length?'none':report.overlaps_resolved?'resolved by priority':'pending review'})`;
  $('eluConflictSummary').replaceChildren();
  if(report.missing_class_features){const p=document.createElement('p');p.textContent=`${report.missing_class_features} features have no class. Choose the correct classification field or correct the source file.`;$('eluConflictSummary').append(p);}
  for(const row of report.overlap_conflicts.slice(0,30)){const p=document.createElement('p');p.textContent=`${row.class_a} ↔ ${row.class_b}: ${row.area_m2} m²`;$('eluConflictSummary').append(p);}
  if(report.overlap_conflicts.length>30){const p=document.createElement('p');p.textContent='More conflicts are listed in the downloadable geometry review.';$('eluConflictSummary').append(p);}
  $('eluPriorityRows').replaceChildren();
  if($('eluOverlapPolicy').value==='priority'&&report.conflicting_classes.length){
   const help=document.createElement('p');help.className='hint';help.textContent='Assign a unique rank to each conflicting class. Rank 1 wins overlaps before rank 2, and so on. No priority is assigned automatically.';$('eluPriorityRows').append(help);
   for(const name of report.conflicting_classes){const label=document.createElement('label');label.innerHTML=`<span>${esc(name)}</span><input type="number" min="1" step="1" aria-label="Overlap priority for ${esc(name)}">`;const input=label.querySelector('input');input.value=priorities[name]||'';input.addEventListener('change',()=>{if(input.value)priorities[name]=Number(input.value);else delete priorities[name];$('eluClassPriorities').value=JSON.stringify(priorities);inspectNow();});$('eluPriorityRows').append(label);}
  }
  $('eluCleanupConfirmed').checked=false;$('eluCleanupConfirmRow').classList.toggle('hidden',!report.requires_change_confirmation);
  $('eluReviewDownload').href=report.review_url;$('eluReviewDownload').classList.remove('hidden');
  if(report.invalid_features_repaired||report.enclosed_gaps_detected||report.overlap_conflicts.length||report.missing_class_features)$('eluQualityPanel').open=true;
 }
 for(const id of ['eluFillGaps','eluGapMax','eluOverlapPolicy'])$(id).addEventListener('change',()=>{inspectNow();});
 $('eluCleanupConfirmed').addEventListener('change',refresh);
 function lockInputs(locked){
  for(const el of form.querySelectorAll('input,select')){
   if(locked){el.dataset.preJobDisabled=String(el.disabled);el.disabled=true;}
   else{el.disabled=el.dataset.preJobDisabled==='true';delete el.dataset.preJobDisabled;}
  }
  refresh();
 }
 const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!ready()||!form.reportValidity())return;
  const payload=new FormData(form);busy=true;lockInputs(true);setStep(1);
  const panel=$('progressPanel'),status=$('progressStatus'),step=$('progressStep'),download=$('downloadBtn');
  panel.classList.remove('hidden');$('errorDetails').classList.add('hidden');$('detailsBtn').classList.add('hidden');download.classList.add('hidden');$('resultSummary').classList.add('hidden');
  status.textContent='Starting harmonization…';step.textContent='Uploading files and preparing selected datasets';$('progressPct').textContent='0%';$('progressBar').style.width='0%';document.querySelector('.progress-track').setAttribute('aria-valuenow','0');$('elapsed').textContent='Elapsed: 00:00';
  panel.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});panel.focus({preventScroll:true});
  try{
   const data=await readJSON(await fetch('/process_start',{method:'POST',body:payload}),'Unable to start processing. Please retry.');
   if(!data.job_id)throw Error('The server did not provide a job ID. Please retry.');
   sessionStorage.setItem('harmonizer-active-job',data.job_id);
   let connectionErrors=0;
   for(;;){
    await pause(1000);
    let response;
    try{response=await fetch('/progress/'+encodeURIComponent(data.job_id),{cache:'no-store'});if(response.status>=500)throw Error('Temporary service interruption');connectionErrors=0;}catch(error){if(++connectionErrors>=8)throw Error('Progress connection interrupted. Reload this page to reconnect to your job.');step.textContent='Reconnecting to processing progress…';await pause(2000);continue;}
    const job=await readJSON(response,response.status===404?'This job is no longer available. The service may have restarted. Please run it again.':'Unable to read processing progress. Please retry.');
    const percent=job.status==='complete'?100:Math.max(0,Math.min(99,Number(job.percent)||0));
    $('progressBar').style.width=percent+'%';$('progressPct').textContent=percent+'%';document.querySelector('.progress-track').setAttribute('aria-valuenow',String(percent));
    status.textContent='Processing…';step.textContent=job.step||'Processing GIS layers';const seconds=Math.max(0,Math.floor(Number(job.elapsed)||0));$('elapsed').textContent='Elapsed: '+String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
    setStep(1);
    if(job.status==='error'){const error=Error(job.error||'Processing failed.');error.details=job.details;throw error;}
    if(job.status==='complete'){status.textContent='Processing complete';step.textContent='Your results are ready to download.';download.href='/download/'+encodeURIComponent(data.job_id);download.classList.remove('hidden');finishResults(job,data.job_id);break;}
   }
  }catch(error){status.textContent='Processing failed';step.textContent=error.message;$('errorDetails').textContent=error.details||error.message;$('detailsBtn').classList.remove('hidden');setStep(0);}
  finally{busy=false;lockInputs(false);refresh();}
 });
 $('retryInspect').addEventListener('click',inspectNow);
 $('detailsBtn').addEventListener('click',()=>{$('errorDetails').classList.toggle('hidden');$('detailsBtn').textContent=$('errorDetails').classList.contains('hidden')?'View Details':'Hide Details';});
 window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});

 function previewFilename(){
  const value=$('outputMunicipality').value.trim().replace(/[\x00-\x1f\x7f<>:"/\\|?*]+/g,'_').replace(/\s+/g,'_').replace(/^[._ ]+|[._ ]+$/g,'').slice(0,100)||'Selected_Area';
  $('filenamePreview').textContent=value+'_NPAAAD_SAFDZ_Harmonize_Agricultural_Zones.zip';
 }
 $('outputMunicipality').addEventListener('input',()=>{$('outputMunicipality').dataset.edited='true';previewFilename();});
 for(const input of form.querySelectorAll('input[type=file]')){
  const info=document.createElement('span');info.className='upload-selection';info.setAttribute('aria-live','polite');
  const name=document.createElement('span'),remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.className='link-btn';
  info.append(name,remove);input.after(info);info.hidden=true;
  const update=()=>{const selected=input.files?.[0];info.hidden=!selected;name.textContent=selected?selected.name+' · '+(selected.size/1048576).toFixed(2)+' MB':'';};
  input.addEventListener('change',update);
  remove.addEventListener('click',()=>{if(busy)return;input.value='';update();input.dispatchEvent(new Event('change',{bubbles:true}));});
 }
 function finishResults(job,id){
  sessionStorage.removeItem('harmonizer-active-job');
  $('progressStatus').textContent='Your results are ready';$('progressStep').textContent='Download your ZIP for the GIS layer, Excel summaries, styles, and quality report.';
  $('progressBar').style.width='100%';$('progressPct').textContent='100%';document.querySelector('.progress-track').setAttribute('aria-valuenow','100');
  const link=$('downloadBtn');link.href='/download/'+encodeURIComponent(id);link.dataset.filename=job.download_name||'';link.classList.remove('hidden');
  const summary=$('resultSummary'),details=job.result_summary||{};summary.replaceChildren();
  for(const text of [job.download_name||'Harmonization results ZIP',details.elu_features?details.elu_features.toLocaleString()+' cleaned ELU features':'GIS + Excel + QA/QC',details.land_cover_included?'Land Cover comparison included':'ELU + NPAAAD / SAFDZ', details.features?details.features.toLocaleString()+' output features · '+Number(details.area_ha||0).toLocaleString()+' ha':'GIS layer + area summaries', details.additional_color_features!=null?details.additional_color_features.toLocaleString()+' features use additional class colors · '+Number(details.gray_features||0).toLocaleString()+' missing classifications use gray · '+Number(details.fallback_decisions||0).toLocaleString()+' decisions retained from ELU_CLS':'Includes QGIS / SLD styles · Additional classes retain their ELU names']){const row=document.createElement('p');row.textContent=text;summary.append(row);}
  summary.classList.remove('hidden');setStep(2,true);
 }
 async function restoreJob(){
  const id=sessionStorage.getItem('harmonizer-active-job');if(!/^[a-f0-9]{32}$/.test(id||''))return;
  $('progressPanel').classList.remove('hidden');busy=true;lockInputs(true);setStep(1);
  try{
   let failures=0;
   for(;;){
    let response,job;
    try{response=await fetch('/progress/'+id,{cache:'no-store'});job=await readJSON(response,'Unable to reconnect to this job.');failures=0;}catch(error){if(response&&response.status<500)throw error;if(++failures>=8)throw error;$('progressStep').textContent='Reconnecting to your previous job…';await pause(2500);continue;}
    if(job.status==='complete'){finishResults(job,id);break;}
    if(job.status==='error')throw Error(job.error||'Processing failed.');
    const percent=Math.max(0,Math.min(99,Number(job.percent)||0));$('progressBar').style.width=percent+'%';$('progressPct').textContent=percent+'%';document.querySelector('.progress-track').setAttribute('aria-valuenow',String(percent));$('progressStatus').textContent='Processing resumed';$('progressStep').textContent=job.step||'Preparing files';
    const seconds=Math.max(0,Math.floor(Number(job.elapsed)||0));$('elapsed').textContent='Elapsed: '+String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');await pause(1500);
   }
  }catch(error){$('progressStatus').textContent='Job needs attention';$('progressStep').textContent=error.message;if(/expired|not found|no longer/i.test(error.message))sessionStorage.removeItem('harmonizer-active-job');}
  finally{busy=false;lockInputs(false);refresh();}
 }

 setStep(0);syncSources();restoreJob();
})();

