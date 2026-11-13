'use strict';
(() => {
 const link=document.getElementById('downloadBtn');
 if(!link)return;
 let downloading=false;
 const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 link.addEventListener('click',async event=>{
  event.preventDefault();
  if(downloading||!link.hasAttribute('href'))return;
  downloading=true;
  const address=link.href;
  const step=document.getElementById('progressStep');
  const update=text=>{if(link.href===address&&step)step.textContent=text;};
  link.setAttribute('aria-disabled','true');
  try{
   let file;
   for(let attempt=1;attempt<=3;attempt++){
    link.textContent=attempt===1?'Downloading results…':`Retrying download… (${attempt}/3)`;
    update(attempt===1?'Receiving your results. Keep this page open.':'Connection interrupted. Retrying the download automatically…');
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),90000);
    try{
     const response=await fetch(address,{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:'application/zip'}});
     if(!response.ok){
      let message='Unable to download results.';
      try{const body=await response.json();message=body.error||message;}catch{}
      if(response.status===404)message='These results are no longer available. The service may have restarted. Generate the results again.';
      const error=Error(message);error.permanent=response.status<500&&response.status!==429;throw error;
     }
     if(response.redirected||!(response.headers.get('content-type')||'').match(/application\/(zip|octet-stream|x-zip-compressed)/i)){
      const error=Error('The server did not return a ZIP file. Refresh this page and sign in again if requested.');error.permanent=true;throw error;
     }
     const blob=await response.blob();
     const expected=Number(response.headers.get('content-length'));
     if(expected>0&&!response.headers.get('content-encoding')&&blob.size!==expected)throw Error('The file transfer was incomplete.');
     const signature=new Uint8Array(await blob.slice(0,4).arrayBuffer());
     if(signature[0]!==80||signature[1]!==75||!((signature[2]===3&&signature[3]===4)||(signature[2]===5&&signature[3]===6)))throw Error('The received file is incomplete or is not a valid ZIP.');
     file=blob;break;
    }catch(error){
     if(error.permanent||attempt===3)throw error;
     await sleep(attempt*1000);
    }finally{clearTimeout(timeout);}
   }
   const objectUrl=URL.createObjectURL(file);
   const save=document.createElement('a');save.href=objectUrl;save.download='HARMONIZATION_RESULTS.zip';save.hidden=true;
   document.body.append(save);save.click();save.remove();setTimeout(()=>URL.revokeObjectURL(objectUrl),60000);
   update('Download started. Check your browser downloads.');
  }catch(error){
   update(error.permanent?error.message:'The connection was interrupted while downloading. Your results may still be available; click Download Results to retry.');
  }finally{
   downloading=false;link.removeAttribute('aria-disabled');link.textContent='Download Results';
  }
 });
})();
