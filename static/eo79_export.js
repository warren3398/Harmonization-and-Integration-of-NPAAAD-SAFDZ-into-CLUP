document.addEventListener('DOMContentLoaded',()=>{
const $=id=>document.getElementById(id), status=$('eo79-export-status'), island=$('eo79-island'),province=$('eo79-province');
// Place the export choices inside Step 5, immediately below the existing Export PDF button.
const panel=$('eo79-export-panel');
if(panel){
  panel.style.cssText='position:static;right:auto;bottom:auto;z-index:auto;width:100%;max-width:100%;box-sizing:border-box;margin:12px 0 0;padding:12px;border:1px solid #c8ded0;border-radius:10px;background:#f4faf6;color:#183a2b;box-shadow:none;font:13px Arial';
  const controls=[...document.querySelectorAll('button,input[type="button"],a')];
  const pdf=controls.find(el=>/^Export PDF$/i.test((el.textContent||el.value||'').trim()));
  if(pdf){
    // PDF and JPEG buttons share a row in the Step 5 Generate Result panel.
    const row=pdf.parentElement;
    row.insertAdjacentElement('afterend',panel);
  }else{
    const heading=[...document.querySelectorAll('h1,h2,h3,h4,h5,legend,strong,span,div')].find(el=>el.children.length===0&&/5\\.\\s*Generate Result/i.test((el.textContent||'').trim()));
    if(heading){const section=heading.closest('section,details,.card,.panel')||heading.parentElement;section.appendChild(panel);}
  }
}
let rows=[];
fetch('/api/r2/datasets').then(r=>r.json()).then(d=>{rows=(d.datasets||[]).filter(x=>x.layer==='safdz');[...new Set(rows.map(x=>x.island))].forEach(v=>island.add(new Option(v,v)));}).catch(()=>{status.textContent='Province inventory unavailable';});
island.addEventListener('change',()=>{province.replaceChildren(new Option('Province',''));rows.filter(x=>x.island===island.value).forEach(x=>province.add(new Option(x.province.replaceAll('-',' '),x.province)));});
document.querySelectorAll('.eo79-format').forEach(c=>c.addEventListener('change',()=>{$('eo79-parcel-settings').hidden=!document.querySelector('.eo79-format[value="shapefile"]').checked;}));
function save(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),12000);}
function library(url,test){return new Promise((ok,fail)=>{if(test())return ok();const s=document.createElement('script');s.src=url;s.onload=()=>test()?ok():fail(Error('Library unavailable'));s.onerror=()=>fail(Error('Could not load export library'));document.head.append(s);});}
$('eo79-generate').onclick=async()=>{
const formats=[...document.querySelectorAll('.eo79-format:checked')].map(x=>x.value), files=[];
if(!formats.length){status.textContent='Choose at least one format';return;}
const btn=$('eo79-generate');btn.disabled=true;status.textContent='Generating...';
try{
if(formats.includes('jpeg')||formats.includes('pdf')){
const map=$('printLayout');if(!map)throw Error('Generate the map layout first');
await library('https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js',()=>!!window.html2canvas);
const canvas=await html2canvas(map,{scale:2,backgroundColor:'#ffffff',useCORS:true});
const jpg=await new Promise(ok=>canvas.toBlob(ok,'image/jpeg',.94));
if(!jpg)throw Error('JPEG failed');
if(formats.includes('jpeg'))files.push(['EO79_Map.jpg',jpg]);
if(formats.includes('pdf')){
await library('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.2/jspdf.umd.min.js',()=>!!window.jspdf);
const pdf=new window.jspdf.jsPDF({orientation:canvas.width>canvas.height?'landscape':'portrait',unit:'mm',format:'a4'});
const w=pdf.internal.pageSize.getWidth(),h=pdf.internal.pageSize.getHeight(),ratio=Math.min(w/canvas.width,h/canvas.height),iw=canvas.width*ratio,ih=canvas.height*ratio;
pdf.addImage(canvas.toDataURL('image/jpeg',.94),'JPEG',(w-iw)/2,(h-ih)/2,iw,ih);
files.push(['EO79_Map.pdf',pdf.output('blob')]);
}}
if(formats.includes('shapefile')){
if(!island.value||!province.value||!$('eo79-parcel').files[0])throw Error('Choose island, province and parcel boundary');
const data=new FormData();data.append('island',island.value);data.append('province',province.value);data.append('parcel',$('eo79-parcel').files[0]);
const response=await fetch('/api/eo79/clip-shapefile',{method:'POST',body:data});
if(!response.ok){const msg=await response.json().catch(()=>({}));throw Error(msg.error||'Shapefile clipping failed');}
files.push(['EO79_SAFDZ_Clipped.zip',await response.blob()]);
}
if(files.length===1)save(files[0][1],files[0][0]);
else{
await library('https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js',()=>!!window.JSZip);
const zip=new JSZip();files.forEach(([name,blob])=>zip.file(name,blob));save(await zip.generateAsync({type:'blob'}),'EO79_Outputs.zip');
}
status.textContent='Generated: '+files.map(f=>f[0]).join(', ');
}catch(e){status.textContent='Error: '+e.message;}finally{btn.disabled=false;}
};
});