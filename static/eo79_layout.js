/* Accessible controls and a scale-only print preview. GIS data stays in the existing map. */
document.addEventListener('DOMContentLoaded',()=>{
const $=id=>document.getElementById(id),sidebar=document.querySelector('.sidebar'),stage=document.querySelector('.stage');
if(!sidebar||!stage||!$('printLayout'))return;
document.body.dataset.eo79Layout='responsive-3';
const sections=[$('sec1'),$('sec2'),$('sec5')];
sections.forEach((sec,i)=>{const heading=sec.querySelector('h2');heading.textContent=heading.textContent.replace(/^\d+\.\s*/,'');heading.dataset.step=String(i+1);sec.querySelector('.detail-toggle')?.remove();});
$('sec5').querySelector('h2').textContent='Generate & Download';
const statusCard=document.createElement('section');statusCard.className='eo79-workflow-status';statusCard.id='eo79-workflow-status';statusCard.setAttribute('role','status');statusCard.setAttribute('aria-live','polite');
statusCard.innerHTML='<strong id="eo79-status-title">Ready to upload</strong><p id="eo79-status-message">Upload a mining parcel. SAFDZ data is matched automatically.</p><progress id="eo79-generation-progress" max="100" hidden aria-label="Processing progress"></progress><output id="eo79-generation-label" hidden></output>';
sidebar.prepend(statusCard);
const advanced=document.createElement('details');advanced.className='eo79-advanced';advanced.innerHTML='<summary>Advanced Options <span style="font-weight:400">(optional)</span></summary><div class="eo79-advanced-content"></div>';
const extras=advanced.lastElementChild;
const coordinateSection=document.createElement('section');coordinateSection.className='section';coordinateSection.innerHTML='<h2>Parcel coordinates</h2>';
const parcelDetails=$('sec1').querySelector('.section-details'),parcelStatus=$('parcelStatus');
while(parcelDetails.firstChild)coordinateSection.append(parcelDetails.firstChild);
$('sec1').append(parcelStatus);parcelDetails.remove();
coordinateSection.querySelector('button').textContent='Reload parcel and auto-fix';
extras.append(coordinateSection,$('sec3'));
const mapDetails=$('sec4');mapDetails.classList.add('eo79-map-details');mapDetails.querySelector('.detail-toggle')?.remove();
mapDetails.querySelector('h2').textContent='Map Details';
const fixedTitle=$('titleInput');fixedTitle.previousElementSibling?.remove();fixedTitle.type='hidden';fixedTitle.readOnly=true;fixedTitle.value='STRATEGIC AGRICULTURE AND FISHERIES DEVELOPMENT ZONE (SAFDZ) MAP';mapDetails.append(fixedTitle);
mapDetails.querySelector('.section-top').append($('applyText'));
const textFields=document.createElement('div');textFields.className='eo79-map-text-fields';
for(const id of ['permitInput','locationInput']){
 const input=$(id),label=input.previousElementSibling,field=document.createElement('div');field.className='eo79-map-text-field';
 if(label?.tagName==='LABEL')field.append(label);field.append(input);textFields.append(field);
}
mapDetails.querySelector('.section-details').replaceChildren(textFields);
extras.querySelectorAll('.detail-toggle').forEach(el=>el.remove());
extras.querySelectorAll('h2').forEach(el=>el.textContent=el.textContent.replace(/^\d+\.\s*/,''));
$('sec5').querySelector('.section-details').style.display='block';
sidebar.append($('sec5'),advanced);
const processingDetails=document.createElement('details');processingDetails.className='eo79-status-details';processingDetails.innerHTML='<summary>Processing details</summary>';
processingDetails.append($('safdzStatus'));$('sec2').append(processingDetails);
$('parcelStatus').setAttribute('role','status');$('parcelStatus').setAttribute('aria-live','polite');
const formatBox=$('eo79-export-panel').querySelector('div');formatBox.className='eo79-format-options';
formatBox.querySelectorAll('br').forEach(el=>el.remove());
$('exportStatus').hidden=true;
const hint=document.createElement('p');hint.id='eo79-export-hint';hint.className='eo79-export-hint';$('eo79-export-panel').append(hint);
$('eo79-export-status').setAttribute('role','status');$('eo79-export-status').setAttribute('aria-live','polite');
// Preserve existing IDs and handlers when moving the orientation and parcel controls.
const tools=document.createElement('div');tools.className='eo79-preview-tools';tools.setAttribute('role','toolbar');tools.setAttribute('aria-label','Map page preview');
tools.innerHTML='<span class="toolbar-label">Map layout preview</span><button type="button" id="eo79-fit-page">Fit Page</button><button type="button" id="eo79-fit-width">Fit Width</button><button type="button" id="eo79-zoom-out" aria-label="Zoom page out">−</button><output id="eo79-preview-zoom" aria-label="Preview zoom">100%</output><button type="button" id="eo79-zoom-in" aria-label="Zoom page in">+</button>';
const orientation=$('orientationSelect'),previousLabel=orientation.previousElementSibling;
if(previousLabel?.tagName==='LABEL')previousLabel.remove();
orientation.nextElementSibling?.tagName==='SMALL'&&orientation.nextElementSibling.remove();
const orientationLabel=document.createElement('label');orientationLabel.htmlFor='orientationSelect';orientationLabel.textContent='Orientation';orientationLabel.append(orientation);tools.append(orientationLabel,$('fitBtn'));
const previewHint=document.createElement('p');previewHint.className='eo79-preview-hint';previewHint.textContent='Fit Page shows the whole sheet. Use + or Fit Width to read details. Preview zoom does not change export quality.';
const viewport=document.createElement('div');viewport.id='eo79-preview-viewport';viewport.tabIndex=0;viewport.setAttribute('aria-label','Scrollable map page preview');
const paperSpace=document.createElement('div');paperSpace.id='eo79-paper-space';const wrap=$('layoutWrap');
viewport.append(paperSpace);paperSpace.append(wrap);stage.append(mapDetails,tools,previewHint,viewport);
$('sec5').querySelectorAll('.btnrow').forEach(row=>{if([...row.children].every(el=>el.hidden))row.hidden=true;});
document.querySelectorAll('.section label').forEach(label=>{if(label.htmlFor||label.querySelector('input,select'))return;const next=label.nextElementSibling;if(next?.id&&/INPUT|SELECT|TEXTAREA/.test(next.tagName))label.htmlFor=next.id;});
let mode='page',scale=1,exporting=false,activeActions=0,successfulFile=null,currentError=false,lastActionFailed=false;
function setStatus(message,{state='idle',progress=null}={}){
 statusCard.dataset.state=state;$('eo79-status-title').textContent=state==='busy'?'Processing':state==='error'?'Action needed':state==='success'?'Ready':'Ready to upload';
 $('eo79-status-message').textContent=message;
 const progressBar=$('eo79-generation-progress'),label=$('eo79-generation-label');
 progressBar.hidden=state!=='busy';label.hidden=state!=='busy';
 if(Number.isFinite(progress)){progressBar.value=progress;label.textContent=Math.round(progress)+'% · stage-based estimate';}
 else{progressBar.removeAttribute('value');label.textContent=state==='busy'?'Processing — time varies with file size and coverage.':'';}
}
function state(){return window.__eo79ExportState?.()||{};}
function canExport(){const data=state();return !activeActions&&!exporting&&!currentError&&successfulFile===$('parcelFile').files[0]&&!!data.parcelFC?.features?.length&&!!data.clippedFC;}
function refresh(){
 const data=state(),ready=canExport(),hasClip=!!data.clippedFC?.features?.length;
 const shape=document.querySelector('.eo79-format[value="shapefile"]');if(shape)shape.disabled=!ready||!hasClip;
 const selected=[...document.querySelectorAll('.eo79-format:checked')].some(el=>!el.disabled);
 $('eo79-generate').disabled=!ready||!selected;
 $('fitBtn').disabled=!data.parcelFC?.features?.length;
 $('parcelFile').disabled=!!activeActions||exporting;$('backgroundFile').disabled=!!activeActions||exporting;
 hint.textContent=activeActions||exporting?'Please wait for processing to finish.':!ready?'Upload a valid parcel and wait for SAFDZ matching to finish.':!selected?'Select at least one output format.':!hasClip?'No clipped SAFDZ polygons are available for Shapefile export. JPEG and PDF remain available.':'Choose the output formats, then Generate & Download.';
}
const originalStatus=window.status;
if(typeof originalStatus==='function')window.status=function(id,message,warn=false){
 originalStatus(id,message,warn);
 if(warn||/^error:|failed|unavailable/i.test(String(message))){lastActionFailed=true;if(id==='parcelStatus'||id==='safdzStatus')currentError=true;setStatus(String(message),{state:'error'});}
 else if(activeActions||exporting)setStatus(String(message),{state:'busy'});
};
function wrapAction(id,label,isParcel=false){const button=$(id),original=button?.onclick;if(!original)return;
 button.onclick=async function(event){
  if(exporting||activeActions)return;
  activeActions++;if(isParcel){currentError=false;successfulFile=null;}lastActionFailed=false;setStatus(label,{state:'busy'});refresh();
  const file=$('parcelFile').files[0];
  try{
   await original.call(this,event);
   if(isParcel&&!currentError&&state().parcelFC?.features?.length&&state().clippedFC)successfulFile=file;
   if(!lastActionFailed&&!currentError)setStatus(isParcel?'Parcel loaded and SAFDZ matching completed. Choose your output formats.':'Settings applied.',{state:'success'});
  }catch(error){currentError=true;setStatus(error.message||'Processing failed. Please retry.',{state:'error'});}
  finally{activeActions--;refresh();}
 };
}
wrapAction('loadParcel','Reading parcel and matching SAFDZ…',true);wrapAction('loadSafdz','Matching SAFDZ to the parcel…',true);wrapAction('loadBackground','Loading background layers…');wrapAction('clearBackground','Clearing background layers…');wrapAction('applyText','Applying map title…');
$('parcelFile').addEventListener('change',()=>{if(!$('parcelFile').files.length){successfulFile=null;setStatus('Choose a mining parcel file.');refresh();}});
document.querySelectorAll('.eo79-format').forEach(el=>el.addEventListener('change',refresh));
window.eo79UI={setStatus,refresh,canExport,startExport(){exporting=true;refresh();},finishExport(message,error=false){exporting=false;setStatus(message,{state:error?'error':'success'});refresh();}};
function resizePreview(){
 if(document.body.classList.contains('eo79-exporting'))return;
 if(innerWidth<=900){if(mapDetails.parentElement!==sidebar)sidebar.insertBefore(mapDetails,$('sec5'));}
 else if(mapDetails.parentElement!==stage)stage.insertBefore(mapDetails,tools);
 const headerHeight=document.querySelector('header').getBoundingClientRect().height;document.documentElement.style.setProperty('--eo79-header-height',headerHeight+'px');
 document.documentElement.style.setProperty('--eo79-editor-height',(mapDetails.parentElement===stage?mapDetails.getBoundingClientRect().height:0)+'px');
 const paper=$('printLayout'),width=paper.offsetWidth,height=paper.offsetHeight;
 const padding=parseFloat(getComputedStyle(viewport).paddingLeft)*2;
 const availableHeight=innerWidth<=900?innerHeight*(innerWidth<=540?.70:.75):viewport.clientHeight;
 const fitWidth=Math.max(.05,(viewport.clientWidth-padding)/width),fitHeight=Math.max(.05,(availableHeight-padding)/height);
 if(mode==='page')scale=Math.min(1,fitWidth,fitHeight);else if(mode==='width')scale=Math.min(1,fitWidth);
 paperSpace.style.width=Math.ceil(width*scale)+'px';paperSpace.style.height=Math.ceil(height*scale)+'px';wrap.style.transform=`scale(${scale})`;
 $('eo79-preview-zoom').textContent=Math.round(scale*100)+'%';
}
function fitPage(){mode='page';resizePreview();viewport.scrollTo(0,0);}
$('eo79-fit-page').onclick=fitPage;$('eo79-fit-width').onclick=()=>{mode='width';resizePreview();viewport.scrollTo(0,0);};
$('eo79-zoom-in').onclick=()=>{mode='manual';scale=Math.min(2,scale*1.25);resizePreview();};$('eo79-zoom-out').onclick=()=>{mode='manual';scale=Math.max(.05,scale/1.25);resizePreview();};
orientation.addEventListener('change',()=>{mode='page';requestAnimationFrame(resizePreview);setTimeout(resizePreview,220);});
const observer=new ResizeObserver(()=>requestAnimationFrame(resizePreview));observer.observe(stage);observer.observe(document.querySelector('header'));observer.observe(mapDetails);
window.addEventListener('resize',()=>requestAnimationFrame(resizePreview));
window.eo79Preview={fitPage,resize:resizePreview,beginExport(){const scroll={x:viewport.scrollLeft,y:viewport.scrollTop,page:window.scrollY};document.body.classList.add('eo79-exporting');return ()=>{document.body.classList.remove('eo79-exporting');resizePreview();viewport.scrollTo(scroll.x,scroll.y);window.scrollTo(0,scroll.page);};}};
// Render at the original print dimensions, independent of preview zoom.
const originalRasterize=window.rasterize;
if(typeof originalRasterize==='function')window.rasterize=async function(){const restore=window.eo79Preview.beginExport();try{return await originalRasterize();}finally{restore();}};
refresh();requestAnimationFrame(fitPage);
});
