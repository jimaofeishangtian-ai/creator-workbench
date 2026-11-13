const message=document.getElementById('message');
function compatibility() {
  const result=globalThis.workbenchBrowserCheck();
  document.getElementById('compatibility').textContent=result.interfacesAvailable
    ? '必要接口齐全；连接后仍需验证一次实际收藏同步。'
    : '当前浏览器缺少：'+result.missing.join('、')+'。请让Agent使用本浏览器的可见页面读取，不迁移登录。';
  document.getElementById('pair').disabled=!result.interfacesAvailable;
  document.getElementById('sync').disabled=!result.interfacesAvailable;
  return result.interfacesAvailable;
}
async function action(type) {
  if(type!=='forget'&&!compatibility())return;
  const buttons=Array.from(document.querySelectorAll('button'));buttons.forEach(b=>b.disabled=true);
  message.textContent=type==='sync'?'正在等待收藏读取，完成后可在工作台查看任务。':'正在连接本机工作台…';
  try {
    const response=await chrome.runtime.sendMessage({type,code:document.getElementById('code').value});
    message.textContent=response.message||(response.ok?'已完成':'操作未完成');
    if(response.ok&&type==='pair')document.getElementById('code').value='';
  } catch {message.textContent='本机连接组件未响应，请确认扩展和电脑工作台正在运行。';}
  finally {buttons.forEach(b=>b.disabled=false);compatibility();}
}
document.getElementById('pair').addEventListener('click',()=>void action('pair'));
document.getElementById('sync').addEventListener('click',()=>void action('sync'));
document.getElementById('forget').addEventListener('click',()=>void action('forget'));
document.getElementById('check').addEventListener('click',compatibility);
compatibility();
if(chrome.storage?.local?.get) chrome.storage.local.get('message').then(data=>{if(data.message)message.textContent=data.message;});
