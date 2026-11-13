const pairingMessage=document.getElementById('pairing-message');
const pairingButton=document.getElementById('pairing-button');
window.addEventListener('message',event=>{
  if(event.source!==window||event.origin!==location.origin||event.data?.source!=='AIWorkbenchEdge')return;
  if(event.data.type==='ready'){pairingButton.disabled=false;pairingMessage.textContent='已检测到当前Edge的本地连接组件。';}
  if(event.data.type==='paired'){
    pairingMessage.textContent=event.data.ok?'配对成功，可回到工作台验证收藏同步。':event.data.message||'配对未完成';
    if(event.data.ok)document.getElementById('pairing-code').value='';
  }
});
pairingButton.addEventListener('click',()=>{
  const code=document.getElementById('pairing-code').value.trim();
  if(code.length<20){pairingMessage.textContent='请先使用工作台生成的一次性配对码。';return;}
  window.postMessage({source:'AIWorkbenchApp',type:'pair',code},location.origin);
  pairingMessage.textContent='正在配对当前Edge组件…';
});
window.postMessage({source:'AIWorkbenchApp',type:'probe'},location.origin);
