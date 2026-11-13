// Isolated extension world; only DOM content and normal UI controls are used.
globalThis.AIWorkbenchReadFavorites = async function(request) {
  const pause = ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const visible = el=>el && el.getClientRects().length>0;
  const problem = ()=>{
    const dialogs=Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"]')).filter(visible).map(el=>el.innerText).join('\n');
    if(/安全验证|滑块|请完成验证|验证码/.test(dialogs)) return 'verification_required';
    if(/扫码登录|手机号登录|请先登录/.test(dialogs)) return 'login_required';
    return null;
  };
  if(location.hostname!=='www.douyin.com'||location.pathname!=='/user/self') return {outcome:'login_required',items:[]};
  let favorite;
  for(let i=0;i<24;i++) {
    const issue=problem();if(issue)return {outcome:issue,items:[]};
    favorite=document.getElementById('semiTabfavorite_collection');
    if(favorite && document.querySelector('h1')) break;
    await pause(500);
  }
  if(!favorite||!document.querySelector('h1')) return {outcome:'login_required',items:[]};
  if(favorite.getAttribute('aria-selected')!=='true') {favorite.click();await pause(1800);}
  if(favorite.getAttribute('aria-selected')!=='true') return {outcome:'page_unavailable',items:[]};
  const known=new Set(request.known_ids||[]),stop=new Set(request.stop_ids||[]);
  const matched=new Set(),all=new Map(),head=[];
  const initial=request.mode!=='backfill'&&!stop.size;
  const readerState={visibility:document.visibilityState,rounds:0,scroll_top:0,scroll_height:0,viewport_height:0};
  let limitHit=false,exhausted=false,reached=false,stagnant=0,previous='';
  let stopReason='time_limit';
  const started=Date.now();
  function cards() {
    return Array.from(document.querySelectorAll('a[href]')).filter(a=>{
      const u=new URL(a.href,location.href);
      return u.hostname==='www.douyin.com' && /^\/(video|note)\/\d+$/.test(u.pathname) && a.querySelector('img[alt]') && visible(a) && !a.closest('footer,[role="contentinfo"]');
    });
  }
  for(let round=0;round<24 && Date.now()-started<100000;round++) {
    readerState.rounds=round+1;
    const issue=problem();if(issue)return {outcome:issue,items:[]};
    const current=cards();
    for(const a of current) {
      const u=new URL(a.href,location.href),id=u.pathname.split('/').pop();
      if(head.length<40 && !head.includes(id))head.push(id);
      if(stop.has(id))matched.add(id);
      if(!initial && known.has(id))continue;
      const img=a.querySelector('img[alt]'),alt=img?.alt||'',cut=alt.indexOf('：');
      const title=a.querySelector('p')?.innerText?.trim()||(cut>=0?alt.slice(cut+1):'标题未在页面显示');
      const cover=img?.currentSrc||img?.src||'';
      if(!all.has(id))all.set(id,{id,title:title.slice(0,4000),author:cut>=0?alt.slice(0,cut).slice(0,200):'',cover:/^https:\/\//.test(cover)?cover.slice(0,3000):null,media_kind:u.pathname.startsWith('/note/')?'image':'video'});
      if(all.size>=request.limit){limitHit=true;break;}
    }
    reached=stop.size>0&&matched.size>=Math.min(3,stop.size);
    const endLabels=new Set(['暂时没有收藏','暂无收藏','还没有收藏','没有更多了','已经到底了']);
    exhausted=Array.from(document.querySelectorAll('p,span,div')).some(el=>visible(el)&&el.children.length===0&&!el.closest('a')&&endLabels.has(el.textContent.trim()));
    if(limitHit||reached||exhausted){stopReason=limitHit?'batch_limit':reached?'checkpoint':'end';break;}
    let scrollable=current.at(-1)?.parentElement;
    while(scrollable) {
      const style=getComputedStyle(scrollable);
      if(scrollable.scrollHeight>scrollable.clientHeight+100 && /(auto|scroll)/.test(style.overflowY))break;
      scrollable=scrollable.parentElement;
    }
    scrollable=scrollable||document.scrollingElement;
    Object.assign(readerState,{visibility:document.visibilityState,scroll_top:Math.round(scrollable?.scrollTop||0),scroll_height:Math.round(scrollable?.scrollHeight||0),viewport_height:Math.round(scrollable?.clientHeight||0)});
    // A long grid can need many viewport steps before another page loads.
    // Reach its real bottom; unchanged cards alone do not prove a stalled list.
    const signature=current.map(a=>a.getAttribute('href')).join('|')+';'+(scrollable?.scrollTop||0)+';'+(scrollable?.scrollHeight||0);
    stagnant=signature===previous?stagnant+1:0;previous=signature;
    if(stagnant>=5){stopReason='page_stalled';break;}
    scrollable?.scrollTo({top:scrollable.scrollHeight,behavior:'instant'});
    await pause(1800);
    stopReason='scan_limit';
  }
  const continuation=request.mode==='backfill'&&!all.size&&head.length>0&&readerState.scroll_top>0&&['scan_limit','time_limit'].includes(stopReason);
  if(!all.size&&!exhausted&&!reached&&!continuation)return {outcome:'no_progress',items:[]};
  const truncated = !exhausted&&!reached && (!initial || !limitHit);
  return {outcome:'ok',items:Array.from(all.values()),head_ids:head,reached_checkpoint:reached,exhausted,truncated,reader_revision:'dom-bottom-v3',stop_reason:stopReason,reader_state:readerState,continuation};
};
