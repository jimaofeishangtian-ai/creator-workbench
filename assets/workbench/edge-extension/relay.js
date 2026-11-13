// Pairing relay on the exact local workbench origin. Never reads page cookies.
if (location.origin === 'http://127.0.0.1:8765') {
  const announce=()=>window.postMessage({source:'AIWorkbenchEdge',type:'ready'},location.origin);
  window.addEventListener('message',event=>{
    if(event.source!==window||event.origin!==location.origin||event.data?.source!=='AIWorkbenchApp')return;
    if(event.data.type==='probe')announce();
    if(event.data.type==='pair'&&typeof event.data.code==='string'&&event.data.code.length>=20&&event.data.code.length<=128) {
      chrome.runtime.sendMessage({type:'pair',code:event.data.code}).then(result=>{
        window.postMessage({source:'AIWorkbenchEdge',type:'paired',ok:!!result.ok,message:result.message||''},location.origin);
      }).catch(()=>window.postMessage({source:'AIWorkbenchEdge',type:'paired',ok:false,message:'本地组件暂时未响应'},location.origin));
    }
  });
  announce();
}
