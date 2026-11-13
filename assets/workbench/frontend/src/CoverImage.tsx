import { useEffect, useState } from 'react';
import { ExternalLink, ImageOff, Play } from 'lucide-react';

export function CoverPlayHint({image=false}:{image?:boolean}) {
  return <span className="cover-play-hint" aria-hidden="true">{image?<ExternalLink size={14}/>:<Play size={15} fill="currentColor"/>}</span>;
}

export function CoverDuration({duration,image=false}:{duration?:number|null;image?:boolean}) {
  const seconds=typeof duration==='number'&&Number.isFinite(duration)&&duration>0?Math.floor(duration):null;
  const label=image?'图文':seconds==null?'时长未知':`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
  return <span className="cover-duration" title={image?'图文作品':seconds==null?'暂未取得视频时长':`视频时长 ${Math.floor(seconds/60)}分${seconds%60}秒`}>{label}</span>;
}

// The server converts HEIC and caches by source URL. Bump the request when metadata changes.
export function CoverImage({id,cover,alt='原视频封面'}:{id:string;cover?:string|null;alt?:string}) {
  const [failed,setFailed]=useState(false);
  useEffect(()=>setFailed(false),[id,cover]);
  let version=2166136261;
  for(const char of cover||'')version=Math.imul(version^char.charCodeAt(0),16777619);
  if(!cover||failed)return <div className="cover-placeholder" title="可打开原视频查看"><ImageOff size={20}/><span>{cover?'封面暂不可用':'暂无封面'}</span></div>;
  return <img src={`/api/videos/${encodeURIComponent(id)}/cover?v=${(version>>>0).toString(16)}`} alt={alt} loading="lazy" onError={()=>setFailed(true)}/>;
}
