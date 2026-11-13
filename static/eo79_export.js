document.addEventListener('DOMContentLoaded',()=>{
const $=id=>document.getElementById(id),status=$('eo79-export-status');
function notify(message,state='busy',progress=null){status.textContent=message;window.eo79UI?.setStatus(message,{state,progress});}
function save(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),12000);}
function library(url,test){return new Promise((ok,fail)=>{if(test())return ok();const s=document.createElement('script');s.src=url;s.onload=()=>test()?ok():fail(Error('Library unavailable'));s.onerror=()=>fail(Error('Could not load export library'));document.head.append(s);});}
$('eo79-generate').onclick=async()=>{
const formats=[...document.querySelectorAll('.eo79-format:checked')].filter(x=>!x.disabled).map(x=>x.value),files=[];
if(window.eo79UI&&!window.eo79UI.canExport()){notify('Upload a valid parcel and wait for SAFDZ matching to finish.','error');return;}
if(!formats.length){notify('Choose at least one output format.','error');return;}
const btn=$('eo79-generate');btn.disabled=true;window.eo79UI?.startExport();
let completed=false,message='';
try{
 const state=window.__eo79ExportState?.();
 if(!state?.parcelFC?.features?.length)throw Error('Upload a valid parcel first.');
 if(formats.includes('jpeg')||formats.includes('pdf')){
  notify('Rendering the complete map layout…','busy',10);
  const canvas=await state.rasterize();
  notify('Creating high-resolution map image…','busy',55);
  const jpg=await new Promise(ok=>canvas.toBlob(ok,'image/jpeg',.94));
  if(!jpg)throw Error('JPEG generation failed.');
  if(formats.includes('jpeg'))files.push(['EO79_Map.jpg',jpg]);
  if(formats.includes('pdf')){
   notify('Preparing the A4 PDF…','busy',65);
   await library('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.2/jspdf.umd.min.js',()=>!!window.jspdf);
   const pdf=new window.jspdf.jsPDF({orientation:canvas.width>canvas.height?'landscape':'portrait',unit:'mm',format:'a4'});
   const w=pdf.internal.pageSize.getWidth(),h=pdf.internal.pageSize.getHeight(),ratio=Math.min(w/canvas.width,h/canvas.height),iw=canvas.width*ratio,ih=canvas.height*ratio;
   pdf.addImage(canvas.toDataURL('image/jpeg',.94),'JPEG',(w-iw)/2,(h-ih)/2,iw,ih);
   files.push(['EO79_Map.pdf',pdf.output('blob')]);
  }
 }
 if(formats.includes('shapefile')){
  if(!state.clippedFC?.features?.length)throw Error('No clipped SAFDZ polygons are available for Shapefile export.');
  notify('Preparing clipped SAFDZ Shapefile…','busy',75);
  const response=await fetch('/api/eo79/export-current-shapefile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state.clippedFC)});
  if(!response.ok){const msg=await response.json().catch(()=>({}));throw Error(msg.error||'Shapefile export failed.');}
  files.push(['EO79_SAFDZ_Parcel_Clip.zip',await response.blob()]);
 }
 if(files.length===1)save(files[0][1],files[0][0]);
 else{
  notify('Packaging selected outputs…','busy',85);
  await library('https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js',()=>!!window.JSZip);
  const zip=new JSZip();files.forEach(([name,blob])=>zip.file(name,blob));
  save(await zip.generateAsync({type:'blob'},meta=>notify('Packaging selected outputs…','busy',85+meta.percent*.14)),'EO79_Outputs.zip');
 }
 message='Generated: '+files.map(f=>f[0]).join(', ')+'. Check your browser downloads.';completed=true;notify(message,'success',100);
}catch(e){message='Export failed: '+e.message;notify(message,'error');}
finally{if(window.eo79UI)window.eo79UI.finishExport(message,!completed);else btn.disabled=false;}
};
});
