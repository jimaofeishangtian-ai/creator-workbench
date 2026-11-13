import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Bookmark, ChevronDown, RefreshCw, Users, RotateCcw, Search } from 'lucide-react';
import { api, fmtDate, fmtFullDate, fmtNumber } from './api';
import { CoverImage, CoverPlayHint, CoverDuration } from './CoverImage';
type Row=Record<string,any>;
const BATCH_SIZE=30;
type Props={initialMode?:string;view?:string;onViewChange?:(view:string)=>void;mobileTasks?:ReactNode;showJobs:()=>void;refresh:number;reload:()=>void;notify:(m:string,error?:boolean,action?:"jobs")=>void;open:(id:string,tab?:string)=>void;queue:(kind:string,payload?:Row)=>Promise<Row|null>;goCreators:()=>void;goRules:()=>void;settings:Row|null;jobs:Row[]};
export default function Discovery({initialMode='hot',view,onViewChange,mobileTasks,showJobs,refresh,reload,notify,open,queue,goCreators,goRules,settings,jobs}:Props) {
  const [localMode,setLocalMode]=useState(initialMode);
  const mode=view??localMode;
  const [hours,setHours]=useState(72);
  const [hotThreshold,setHotThreshold]=useState<number|null>(null);
  const [data,setData]=useState<Row>({items:[],total:0,tracks:[]});
  const [busy,setBusy]=useState(true),[loadedKey,setLoadedKey]=useState(''),[error,setError]=useState('');
  const [visibleCount,setVisibleCount]=useState(BATCH_SIZE);
  useEffect(()=>setVisibleCount(BATCH_SIZE),[mode]);
  const cache=useRef<{key:string;data:Row;offset:number}|null>(null);
  const [query,setQuery]=useState(''),[search,setSearch]=useState(''),[track,setTrack]=useState(''),[sort,setSort]=useState('saved');
  const [saving,setSaving]=useState<string|null>(null),[tick,setTick]=useState(0);
  const [submitting,setSubmitting]=useState<string|null>(null),[cancelling,setCancelling]=useState<string|null>(null);
  const saved=mode==='saved';
  const hot=mode==='hot';
  const hotMinimum=hotThreshold ?? settings?.hotspots?.min_likes ?? 10000;
  const rangeLabel=hours===168?'7天':`${hours}小时`;
  const searchJob=jobs.find(j=>j.kind==='search');
  const latestSearch=searchJob?{...(data.search_status?.id===searchJob.id?data.search_status:{}),...searchJob}:data.search_status;
  const lastSearch=data.last_sync?.search||settings?.last_sync?.search;
  const hotJob=jobs.find(j=>j.kind==='hotspot_sync');
  const latestHotspot=hotJob?{...(data.hotspot_status?.id===hotJob.id?data.hotspot_status:{}),...hotJob}:data.hotspot_status;
  const lastHotspot=data.last_sync?.hotspots||settings?.last_sync?.hotspots;
  const hotspotIssue=latestHotspot&&['failed','attention'].includes(latestHotspot.status);
  const requestKey=JSON.stringify([mode,search,track,sort,hours,hot?hotMinimum:null]),stale=loadedKey!==requestKey;
  useEffect(()=>{const t=setTimeout(()=>{setSearch(query);setVisibleCount(BATCH_SIZE);},250);return()=>clearTimeout(t);},[query]);
  useEffect(()=>{const t=setInterval(()=>{if(document.visibilityState==='visible')setTick(n=>n+1);},60000);return()=>clearInterval(t);},[]);
  useEffect(()=>{
    let current=true;setBusy(true);setError('');
    const cacheKey=JSON.stringify([requestKey,refresh,tick]);
    async function load(){
      const previous=cache.current?.key===cacheKey?cache.current:null;
      const items:Row[]=previous?[...previous.data.items]:[];
      const known=new Set(items.map(v=>v.id)),newIds:string[]=[];
      let fetched=previous?.offset||0,result=previous?.data;
      while(current&&items.length<visibleCount&&(!result||fetched<result.total)){
        const p=new URLSearchParams({mode,limit:String(BATCH_SIZE),offset:String(fetched),q:search,track,sort});
        if(mode==='focus'||hot)p.set('hours',String(hours));
        if(hot)p.set('hot_min_likes',String(hotMinimum));
        const next=await api('/discovery?'+p);
        if(!current)return;
        result=next;fetched+=next.items.length;
        for(const v of next.items){if(!known.has(v.id)){known.add(v.id);items.push(v);newIds.push(v.id);}}
        if(!next.items.length)break;
      }
      if(!current||!result)return;
      const combined={...result,items};
      cache.current={key:cacheKey,data:combined,offset:fetched};
      setData(combined);setLoadedKey(requestKey);
      if(['focus','hot','creators'].includes(mode))for(let i=0;i<newIds.length;i+=200)void api('/recommendations/record-local','POST',{ids:newIds.slice(i,i+200),refresh_profiles:false}).catch(()=>{});
    }
    void load().catch(e=>{if(current)setError(e.message);}).finally(()=>{if(current)setBusy(false);});
    return()=>{current=false;};
  },[requestKey,visibleCount,refresh,tick]);
  async function act(id:string,path:string,body?:Row){
    if(saving)return;setSaving(id);
    try{await api('/discovery/'+id+'/'+path,'POST',body);reload();}
    catch(e){notify((e as Error).message,true);}finally{setSaving(null);}
  }
  function changeMode(next:string){if(next===mode)return;if(onViewChange)onViewChange(next);else setLocalMode(next);setVisibleCount(BATCH_SIZE);}
  const runningUpdates=[latestHotspot,latestSearch].filter((job:Row)=>job&&['queued','running'].includes(job.status));
  const isUpdating=(kind:string)=>submitting===kind||runningUpdates.some((job:Row)=>job.kind===kind);
  async function update(kind:string){
    if(submitting||isUpdating(kind))return;
    changeMode(kind==='hotspot_sync'?'hot':'focus');setSubmitting(kind);
    try{await queue(kind);}finally{setSubmitting(null);}
  }
  async function cancelUpdate(job:Row){
    setCancelling(job.id);
    try{const response=await api('/jobs/'+job.id+'/cancel','POST',{});notify(response.message,false,"jobs");reload();}
    catch(e){notify((e as Error).message,true,"jobs");}finally{setCancelling(null);}
  }
  const updateButtons = <>
    <button className="button" onClick={goRules}>编辑发现关键词</button>
    <button className="button" onClick={()=>queue('creators_sync')}><RefreshCw size={15}/>更新对标作品</button>
  </>;
  const updateStatus = <span>对标更新 {fmtDate(data.last_sync?.creators)}</span>;
  function refreshAction(kind:string,label:string,job:Row|undefined,last:string|undefined){
    const active=isUpdating(kind),completed=job?.status==='succeeded';
    const stamp=completed?(job.updated_at||last):last;
    const sameKeywords=kind!=='search'||!job?.searched_keywords?.length||JSON.stringify(job.searched_keywords.map((word:string)=>word.toLowerCase()))===JSON.stringify([...new Set<string>((settings?.keywords||[]).map((word:string)=>word.trim().toLowerCase()))].slice(0,20));
    const cooldown=completed&&sameKeywords&&stamp?Math.max(0,Math.ceil((Date.parse(stamp)+600000-Date.now())/60000)):0;
    const phase=submitting===kind?'正在提交…':active?(job?.status==='queued'?'排队中…':`更新中 ${job?.progress||0}%`):cooldown>0?'已更新 ✓':label;
    return <div className="discovery-refresh-item" key={kind}>
      <button aria-label={label} className={'button '+((kind==='hotspot_sync'&&hot||kind==='search'&&mode==='focus')?'primary':'')} disabled={busy&&stale||!!submitting||active||cooldown>0} title={cooldown>0?`刚更新过，约${cooldown}分钟后可再更新`:label+'，采集最新数据'} onClick={()=>void update(kind)}><RefreshCw size={15} className={active?'spin':''}/>{phase}</button>
      <div className="discovery-refresh-status" role="status">
        {!active&&job&&['failed','attention','cancelled'].includes(job.status)?<details key={job.id} className="discovery-update-result">
          <summary>本轮已结束 · {job.status==='attention'?'部分成功':job.status==='failed'?'更新失败':'已取消'}<ChevronDown size={12} aria-hidden="true"/></summary>
          <p>结束于 {fmtFullDate(job.updated_at)}<br/>{job.message}</p>
          <p>本轮请求已结束，不会自动继续重试。{stamp?`已有数据更新于 ${fmtDate(stamp)}。`:'已有内容仍可查看。'}</p>
          <button type="button" className="button" onClick={showJobs}>查看任务中心</button>
        </details>:<small>{stamp?`最近更新 ${fmtDate(stamp)}`:'尚未取得更新数据'}{cooldown>0&&<><br/>{cooldown}分钟后可再更新</>}</small>}
        {!active&&stamp&&Date.now()-Date.parse(stamp)>24*3600000&&<small>数据已超过24小时，可手动更新</small>}
      </div>
      {active&&job?.id&&<button className="cancel-discovery-update" disabled={cancelling===job.id} onClick={()=>void cancelUpdate(job)}>{cancelling===job.id?'正在取消…':'取消更新'}</button>}
    </div>;
  }
  return <>
    <section className="page-heading discovery-heading"><div><div className="eyebrow">{saved?'YOUR HANDPICKED TOPICS':'LESS SCROLLING, BETTER REFERENCES'}</div><h1><span className="discovery-heading-desktop-title">{saved?'备选选题':'今日推荐'}</span><span className="discovery-heading-phone-title">{saved?'备选选题':mode==='hot'?'热点推荐':mode==='focus'?'低粉爆款':mode==='creators'?'对标新作':'不感兴趣'}</span><span className="heading-dot"/></h1><p>{saved?'你亲自选过的题材，留到有空时再做。长期保留；已看过、超过推荐时限都不会消失。':'每次显示30条候选，点击继续加载更多。看过、备选或已收藏的作品仍可入选；点击「不感兴趣」才隐藏。'}</p></div>
      {!saved&&<div className="discovery-update-actions discovery-desktop-tools">{updateButtons}</div>}
      <div className="discovery-phone-actions">{mobileTasks}{!saved&&<details className="discovery-mobile-tools"><summary><RefreshCw size={14}/>更多</summary><div className="discovery-tools-panel"><div className="discovery-update-actions">{updateButtons}<button className="button" onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');changeMode(mode==='dismissed'?'hot':'dismissed');}}>{mode==='dismissed'?'返回热点推荐':'不感兴趣的作品'}</button></div><div className="discovery-status">{updateStatus}</div></div></details>}</div>
    </section>
    {!saved&&<div className="discovery-controls">
      <div className="discovery-status discovery-desktop-tools">{updateStatus}</div>
      <div className="discovery-modes" role="group" aria-label="发现视图">{[['hot','热点推荐'],['focus','低粉爆款'],['creators','对标新作'],['dismissed','不感兴趣']].map(([key,label])=><button type="button" key={key} className="button" aria-pressed={mode===key} onClick={()=>changeMode(key)}>{label}</button>)}</div>
      <section className="discovery-refresh-bar" aria-label="采集最新数据"><div className="discovery-refresh-buttons">
        {refreshAction('hotspot_sync','更新热点推荐',latestHotspot,lastHotspot)}
        {refreshAction('search','更新低粉爆款',latestSearch,lastSearch)}
      </div><small>采集最新数据，计入API预算；成功更新后保护10分钟，避免重复点击。</small></section>
    </div>}
    {mode==='focus'&&<>
      <div className="discovery-range"><span>发布时间</span><div role="group" aria-label="低粉爆款发布时间">{[[24,'24小时'],[72,'72小时'],[168,'7天']].map(([value,label])=><button type="button" key={value} className="button" aria-pressed={hours===value} onClick={()=>{if(hours===Number(value))return;setHours(Number(value));setVisibleCount(BATCH_SIZE);}}>{label}</button>)}</div><small>筛选已采集内容</small></div>
      <details className="discovery-filter-help"><summary>筛选条件与数据范围<ChevronDown size={13}/></summary>{settings?<p>近{rangeLabel}发布，当前粉丝不超过{Number(settings.low_followers).toLocaleString('zh-CN')}，点赞至少{Number(settings.low_min_likes).toLocaleString('zh-CN')}，点赞/粉丝至少{Math.round(settings.low_ratio*10000)/100}%。粉丝采样须在48小时内，未知时不入选。</p>:<p>正在读取已保存条件…</p>}<p>从已采集的相关作品中筛选，已收藏也可入选。没有对标或关注列表，也可以通过关键词发现；这是搜索样本，不是全站榜单。</p><button className="button" onClick={goRules}>调整筛选条件</button></details>
    </>}
    {hot&&<>
      {!lastHotspot&&!hotspotIssue&&<p className="discovery-freshness">尚未运行热点发现，以下为本机已有样本；点「更新热点推荐」补充热榜与搜索结果。</p>}
      <div className="discovery-range"><span>发布时间</span><div role="group" aria-label="热点推荐发布时间">{[[24,'24小时'],[72,'72小时'],[168,'7天']].map(([value,label])=><button type="button" key={value} className="button" aria-pressed={hours===value} onClick={()=>{if(hours===Number(value))return;setHours(Number(value));setVisibleCount(BATCH_SIZE);}}>{label}</button>)}</div></div>
      <div className="discovery-range"><span>最低点赞</span><div role="group" aria-label="热点推荐点赞门槛">{[...new Set<number>([hotMinimum,settings?.hotspots?.min_likes ?? 10000,10000,20000,30000,50000,100000])].sort((a,b)=>a-b).map(value=><button key={value} type="button" className="button" aria-pressed={hotMinimum===value} onClick={()=>{setHotThreshold(value);setVisibleCount(BATCH_SIZE);}}>{value===1000?'千':value>=10000&&value%10000===0?`${value/10000}万`:fmtNumber(value)}赞＋</button>)}</div><small>不限制粉丝数</small></div>
      <details className="discovery-filter-help"><summary>发现范围与费用<ChevronDown size={13}/></summary><p>近{rangeLabel}、点赞至少{Number(hotMinimum).toLocaleString('zh-CN')}，不限制作者粉丝数；互动采样须在48小时内。结果来自本机相关样本，不是全站排行榜。</p><p>每轮最多1次热榜＋5个词的两种排序，各查1页，最多11次数据请求；热榜选词最多1次模型调用。自动更新每天08:40一次，需在电脑设置中启用；手动更新有10分钟保护间隔，全部受现有预算限制。</p>{data.hotspot_state?.terms?.length>0&&<><strong>本轮搜索词</strong>{data.hotspot_state.terms.map((item:Row)=><p key={item.keyword}><b>{item.keyword}</b> · {item.filtered?'已排除 · ':''}{item.origin}<br/>{item.reason}</p>)}<p>请求尝试 {data.hotspot_state.request_attempts} 次 · 完成 {data.hotspot_state.completed_queries}/{data.hotspot_state.queries?.length||0} 个查询 · 样本 {data.hotspot_state.observed_videos} 条</p></>}{data.hotspot_state?.issues?.map((issue:Row,i:number)=><p key={'issue'+i}>{issue.stage}：{issue.message}</p>)}{data.hotspot_state?.queries?.filter((q:Row)=>q.status==='failed').map((q:Row,i:number)=><p key={'query'+i}>{q.keyword} · {q.sort}：{q.message}</p>)}<button className="button" onClick={goRules}>设置热点推荐</button></details>
    </>}
    {saved&&<div className="candidate-filters"><label className="candidate-search"><Search size={16}/><input aria-label="搜索备选选题" placeholder="搜标题、作者，或你留下的备选备注" value={query} maxLength={200} onChange={e=>setQuery(e.target.value)}/></label><select aria-label="备选方向" value={track} onChange={e=>{setTrack(e.target.value);setVisibleCount(BATCH_SIZE);}}><option value="">全部方向</option>{data.tracks.map((t:string)=><option key={t}>{t}</option>)}</select><select aria-label="备选排序" value={sort} onChange={e=>{setSort(e.target.value);setVisibleCount(BATCH_SIZE);}}><option value="saved">最近加入优先</option><option value="oldest">最早加入优先</option><option value="published">最近发布优先</option></select>{(query||track)&&<button className="button" onClick={()=>{setQuery('');setTrack('');setVisibleCount(BATCH_SIZE);}}>清空筛选</button>}</div>}
    <p className="discovery-summary">{stale?'正在更新…':`${saved?'找到':'当前本地筛选后'} ${data.total || 0} 条 · 已显示 ${data.items.length} 条${busy?' · 正在加载…':''}${data.merged?` · 归并 ${data.merged} 条同题参考`:''}`}<small>{saved?'收藏与发现中的「加入备选」统一出现在这里。不自动过期，也不因刷新重新按热度淘汰；旧记录未保存加入时间的按首次发现排序。':mode==='creators'?'最近7天已跟踪账号的相关新作；不要求每条达到低粉爆款门槛。':hot?'近期高赞，按当前点赞数排列；粉丝未知或超过5万仍可入选，选题相关性需回原视频确认。':mode==='focus'?`低粉爆款按近${rangeLabel}发布时间与互动条件筛选，已收藏也可入选；看过或加入备选不会移走。`:'你主动隐藏的作品，可点击恢复推荐；收藏与备选仍然保留。'}</small></p>
    {error&&<div className="empty"><p role="alert">{error}</p><button className="button" onClick={()=>setTick(n=>n+1)}>重试</button></div>}
    {!error&&!busy&&!stale&&!data.items.length&&<div className="empty"><h3>{saved?(query||track?'没有匹配的备选':'还没有留下备选选题'):hot?'当前样本没有达到门槛的热点推荐':mode==='focus'?'这一批没有合适候选':mode==='creators'?'暂时没有符合条件的对标新作':'这里暂时没有记录'}</h3><p>{saved?'发现好题时点「加入备选选题」，可以补一句为什么想做，之后按备注找回来。':hot?'可以调整发布时间或点赞门槛，或更新热点。空列表只代表当前样本，不代表赛道没有热点。':mode==='focus'?'没有对标账号也能用。配置TikHub与发现关键词后，点「更新低粉爆款」；也可切换时间范围查看已有样本。':'记录不会删除原素材。先完成同步，再回来看。'}</p>{hot&&data.hotspot_state?.observed_videos>0&&<><p>最近一轮采集 {data.hotspot_state.observed_videos} 条样本{data.hotspot_state.highest_likes!=null?`，最高 ${Number(data.hotspot_state.highest_likes).toLocaleString('zh-CN')} 赞`:''}；仍需符合当前时间与相关性条件。</p>{hotMinimum>1000&&<button className="button" onClick={()=>{setHotThreshold(1000);setVisibleCount(BATCH_SIZE);}}>查看千赞候选（不新增调用）</button>}</>}{!saved&&<button className="button" onClick={mode==='creators'?goCreators:goRules}>{mode==='creators'?'管理对标账号':'设置内容方向与关键词'}</button>}</div>}
    <div className="discovery-list" aria-busy={busy}>{!stale&&data.items.map((v:Row)=> {
      const reasons = <><p>{v.reference_use}</p><ul>{v.reference_reasons?.map((reason:string)=><li key={reason}>{reason}</li>)}</ul><p className="reference-data-time">互动数据采集：{fmtFullDate(v.metrics_at)}<br/>粉丝采样：{fmtFullDate(v.followers_at)}</p></>;
      const shortTitle = v.title.split(/[#＃]/, 1)[0].trim() || v.title;
      return <article className="discovery-card" key={v.id}>
      <a className="discovery-cover" href={v.url} target="_blank" rel="noopener noreferrer" aria-label="点击封面打开原视频" title="打开原视频"><CoverImage id={v.id} cover={v.cover}/><CoverPlayHint image={v.media_kind==='image'}/><CoverDuration duration={v.duration} image={v.media_kind==='image'}/></a>
      <div className="discovery-body"><div className="discovery-author"><strong>{v.author||'作者待核对'}</strong><span>{v.published_at?`发布 ${fmtDate(v.published_at)}`:'发布时间未知'}</span>{v.monitored_author?<span className="tag green">已跟踪</span>:<button className="discovery-follow" aria-label="跟踪作者" disabled={!!saving} onClick={()=>void act(v.id,'follow-author')}><Users size={11}/>跟踪</button>}{v.is_favorite&&<span className="tag green">已收藏</span>}{hot&&v.likes>=100000&&<span className="tag green">10万赞＋</span>}</div>
        <button type="button" className="title-link" onClick={()=>open(v.id)}><h3><span className="discovery-title-full">{v.title}</span><span className="discovery-title-short">{shortTitle}</span></h3></button>
        <p className="discovery-metrics">点赞 {fmtNumber(v.likes)} · 收藏 {fmtNumber(v.favorites)} · 当前粉丝 {fmtNumber(v.followers)}</p>
        <FollowerObservation video={v} open={()=>open(v.id,'data')}/>
        {saved?<><p className="candidate-saved-at">{v.saved_at?`加入备选 ${fmtFullDate(v.saved_at)}`:`历史备选 · 首次发现 ${fmtFullDate(v.discovered_at)}`}</p><CandidateNote video={v} notify={notify} reload={reload}/></>:<>
          <div className="reference-reason reference-reason-desktop"><strong>值得参考什么</strong>{reasons}</div>
          <details className="reference-reason reference-reason-mobile"><summary>筛选依据 · 展开查看<ChevronDown size={12} aria-hidden="true"/></summary>{reasons}</details>
        </>}
        <div className="discovery-actions">
          <SaveCandidate video={v} notify={notify} reload={reload}/>
          <button className="button discovery-read" onClick={()=>open(v.id)}><span className="discovery-title-full">看文稿 / 拆开头</span><span className="discovery-title-short">看文稿</span></button>
          {!saved&&(mode==='dismissed'?<button className="button discovery-dismiss" disabled={!!saving} onClick={()=>void act(v.id,'feedback',{action:'restore'})}><RotateCcw size={14}/>恢复推荐</button>:<button className="button subtle discovery-dismiss" disabled={!!saving} onClick={()=>void act(v.id,'feedback',{action:'dismiss',reason:'不感兴趣'})}>不感兴趣</button>)}
        </div>
      </div>
    </article>})}</div>
    {!stale&&data.items.length>0&&<div className="discovery-next">{data.items.length<data.total?<button className="button" disabled={busy} onClick={()=>setVisibleCount(n=>n+BATCH_SIZE)}>{busy?'正在加载…':'再显示30条'}</button>:<p>已显示全部 {data.items.length} 条</p>}</div>}
  </>;
}

function SaveCandidate({video,notify,reload}:{video:Row;notify:(m:string,error?:boolean)=>void;reload:()=>void}){
  const [busy,setBusy]=useState(false);
  return <button type="button" className="button candidate-save" aria-label={video.state==='备选'?'移出备选':'加入备选选题'} aria-pressed={video.state==='备选'} disabled={busy} onClick={async()=>{setBusy(true);try{await api('/videos/'+video.id,'PATCH',{state:video.state==='备选'?'待看':'备选'});notify(video.state==='备选'?'已移出备选，素材仍保留':'已加入备选选题，之后可从「备选选题」找回');reload();}catch(e){notify((e as Error).message,true);}finally{setBusy(false);}}}><Bookmark size={14}/><span className="discovery-title-full">{video.state==='备选'?'移出备选':'加入备选选题'}</span><span className="discovery-title-short">{video.state==='备选'?'移出备选':'加入备选'}</span></button>;
}
function CandidateNote({video,notify,reload}:{video:Row;notify:(m:string,error?:boolean)=>void;reload:()=>void}){
  const [editing,setEditing]=useState(false),[text,setText]=useState(video.notes||''),[busy,setBusy]=useState(false);
  return <div className="candidate-note">{editing?<><label>备选备注<textarea aria-label="备选备注" value={text} maxLength={20000} rows={3} onChange={e=>setText(e.target.value)} placeholder="为什么想做？打算参考哪个开头，或补什么案例？"/></label><button className="button" disabled={busy} onClick={async()=>{setBusy(true);try{await api('/videos/'+video.id,'PATCH',{notes:text});setEditing(false);reload();notify('备选备注已保存');}catch(e){notify((e as Error).message,true);}finally{setBusy(false);}}}>保存备注</button><button className="button" disabled={busy} onClick={()=>setEditing(false)}>取消</button></>:<>{video.notes&&<p>{video.notes}</p>}<button type="button" onClick={()=>{setText(video.notes||'');setEditing(true);}}>{video.notes?'修改备注':'记一句为什么想做'}</button></>}</div>;
}


function FollowerObservation({video,open}:{video:Row;open:()=>void}){
  const observation=video.follower_observation;
  const delta=observation?.delta;
  const hasComparison=delta!=null;
  const value=hasComparison?(delta===0?'持平':`${delta>0?'+':''}${Number(delta).toLocaleString('zh-CN')} 粉`):video.followers==null||!video.followers_at?'粉丝待采样':'待下次采样对比';
  const basis=observation?.basis==='recommendation'?'较上次推荐':'较上次采样';
  const period=hasComparison?(observation.days>=1?`相隔约${observation.days}天`:'间隔不足1天'):'';
  return <button type="button" className={'discovery-follower-change '+(hasComparison&&delta<0?'decreased':'')} onClick={open} title={hasComparison?`${fmtFullDate(observation.previous.observed_at)}：${observation.previous.followers}粉 → ${fmtFullDate(observation.current.observed_at)}：${observation.current.followers}粉`:'查看真实粉丝采样与推荐历史；不会自动调用数据接口'}>{hasComparison?`${basis} ${value}`:value}<span>{period&&`${period} · `}查看粉丝记录 ›</span></button>;
}
