const BASE = 'http://127.0.0.1:8765/api/edge/bridge';
const ALARM = 'workbench-local-poll';
let busy = false;

async function local(path, method = 'GET', body, token) {
  const response = await fetch(BASE + path, {
    method, credentials: 'omit', signal: AbortSignal.timeout(15000),
    headers: {'Content-Type':'application/json', ...(token ? {Authorization:'Bearer '+token} : {})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})
  });
  const data = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(data.message || '本机工作台请求未完成');
  return data;
}

async function record(message) {
  await chrome.storage.local.set({message, updatedAt:Date.now()});
}

async function waitForPage(id) {
  for (let i=0;i<50;i++) {
    const tab = await chrome.tabs.get(id);
    if (tab.status === 'complete') return;
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error('抖音页面暂时没有加载完成');
}

async function readRequest(request) {
  const opened = await chrome.tabs.query({url:'https://www.douyin.com/*'});
  if (!opened.length) return {outcome:'page_unavailable',items:[]};
  const saved = await chrome.storage.local.get('historyTab');
  let tab;
  if (request.mode === 'backfill' && saved.historyTab?.id) {
    tab = await chrome.tabs.get(saved.historyTab.id).catch(()=>null);
    if (!tab?.url?.startsWith('https://www.douyin.com/user/self') || new URL(tab.url).searchParams.get('ai_workbench_tab')!==saved.historyTab.nonce) tab=null;
  }
  let ownership=saved.historyTab?.nonce;
  if (!tab) {
    // This is a tab in the existing Edge profile, never another browser profile.
    ownership=crypto.randomUUID();
    tab = await chrome.tabs.create({url:'https://www.douyin.com/user/self?showTab=favorite_collection&ai_workbench_tab='+ownership,active:request.foreground===true});
  }
  let keep = false;
  try {
    if(request.foreground===true) await chrome.tabs.update(tab.id,{active:true});
    await waitForPage(tab.id);
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['reader.js']});
    const result = await chrome.scripting.executeScript({target:{tabId:tab.id},func:async request=>await globalThis.AIWorkbenchReadFavorites(request),args:[request]});
    const data = result[0]?.result || {outcome:'page_unavailable',items:[]};
    keep = request.mode === 'backfill' && data.outcome==='ok' && !data.exhausted;
    if (keep) await chrome.storage.local.set({historyTab:{id:tab.id,nonce:ownership}});
    return data;
  } catch {
    return {outcome:'browser_closed',items:[]};
  } finally {
    if (!keep) {
      await chrome.tabs.remove(tab.id).catch(()=>{});
      if (saved.historyTab?.id === tab.id) await chrome.storage.local.remove('historyTab');
    }
  }
}

async function poll() {
  if (busy) return;
  const config=await chrome.storage.local.get(['token','busyUntil']);
  if (!config.token || config.busyUntil > Date.now()) return;
  busy=true;
  await chrome.storage.local.set({busyUntil:Date.now()+240000});
  try {
    const work=await local('/work','GET',undefined,config.token);
    if (!work.request) return;
    await record('正在使用当前浏览器读取收藏；不会执行登录。');
    const result=await readRequest(work.request);
    const saved=await local('/result','POST',{job_id:work.request.job_id,nonce:work.request.nonce,...result},config.token);
    await record(saved.saved ? `已读取${saved.scanned}条，新增${saved.added}条。` : '收藏未完成，请查看工作台任务提示。');
  } catch (e) {
    await record(e.message || '暂时连接不到本机工作台');
  } finally {
    busy=false;
    await chrome.storage.local.set({busyUntil:0});
  }
}

async function ensureAlarm() {
  if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM,{periodInMinutes:1});
}
chrome.runtime.onInstalled.addListener(()=>{void ensureAlarm();});
chrome.runtime.onStartup.addListener(()=>{void ensureAlarm();void poll();});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===ALARM) void poll();});
void ensureAlarm();
chrome.runtime.onMessage.addListener((message,_sender,reply)=>{
  (async()=>{
    if(message.type==='pair') {
      const data=await local('/pair','POST',{code:String(message.code||'').trim(),extension_id:chrome.runtime.id});
      await chrome.storage.local.set({token:data.token,busyUntil:0});
      await record('已连接本机工作台。打开平时使用的抖音即可同步，无需重新扫码。');
      void poll();
    } else if(message.type==='sync') {
      const {token}=await chrome.storage.local.get('token');
      if (!token) throw new Error('请先填写工作台的一次性配对码');
      await local('/sync','POST',{},token);
      await poll();
    } else if(message.type==='forget') {
      const {historyTab}=await chrome.storage.local.get('historyTab');
      if(historyTab?.id) {
        const owned=await chrome.tabs.get(historyTab.id).catch(()=>null);
        if(owned?.url?.startsWith('https://www.douyin.com/user/self') && new URL(owned.url).searchParams.get('ai_workbench_tab')===historyTab.nonce) await chrome.tabs.remove(historyTab.id).catch(()=>{});
      }
      await chrome.storage.local.clear();
      await record('已移除本机连接。也可在工作台解除配对以撤销旧凭据。');
    }
    return {ok:true,...await chrome.storage.local.get(['message','updatedAt'])};
  })().then(reply).catch(e=>reply({ok:false,message:e.message||'操作未完成'}));
  return true;
});
