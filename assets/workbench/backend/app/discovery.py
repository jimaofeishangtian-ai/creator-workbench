"""A small actionable reference queue, with explicit and reversible feedback."""
import re,unicodedata
from collections import Counter
from urllib.parse import quote

from .db import now
from .providers import TaskError

RETIRED_JOBS={'knowledge_sync','knowledge_deep','hooks','script','commercial_analyze','brief_extract'}
GENERIC_TOPICS={'其他','AI教程','AIGC','资讯','工具与新功能','Agent工作流','AI视频与剪辑'}


def feedback(store,identity,action,reason=''):
    if action not in {'viewed','dismiss','restore'} or not isinstance(reason,str) or len(reason)>200:
        raise TaskError('invalid_input','反馈格式无效')
    if not store.one('SELECT id FROM videos WHERE id=?',(identity,)):
        raise TaskError('not_found','素材不存在')
    with store.connect() as c:
        c.execute('INSERT OR IGNORE INTO discovery_feedback(video_id,updated_at) VALUES (?,?)',(identity,now()))
        if action=='viewed':c.execute('UPDATE discovery_feedback SET viewed_at=?,updated_at=? WHERE video_id=?',(now(),now(),identity))
        elif action=='dismiss':c.execute('UPDATE discovery_feedback SET dismissed_at=?,reason=?,updated_at=? WHERE video_id=?',(now(),reason,now(),identity))
        else:c.execute("UPDATE discovery_feedback SET dismissed_at=NULL,reason='',updated_at=? WHERE video_id=?",(now(),identity))
    return store.one('SELECT * FROM discovery_feedback WHERE video_id=?',(identity,))


def topic_identity(row):
    topic=(row.get('topic_key') or '').strip()
    title=re.split(r'[#＃]',row['title'],maxsplit=1)[0]
    text=topic if len(topic)>=8 and topic not in GENERIC_TOPICS else title
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def decorate(store,row):
    from .services import follower_observation
    reasons=[]
    domain=store.settings().get('creator_domain')
    if domain and domain.get('configured'):
        from .onboarding import match_domain
        matches=match_domain(row,domain)
        row['domain_matches']=matches
    watched=store.one('SELECT name FROM creators WHERE id=? AND enabled=1',(row.get('author_id'),))
    if watched:reasons.append('来自你跟踪的对标：'+watched['name'])
    for source in row.get('sources',[]):
        if source['kind']=='hotspot':
            reason='赛道检索：'+source['source_key'].split(':',1)[-1]
            if reason not in reasons:reasons.append(reason)
            if len(reasons)>=3:break
    matches=[w for w in store.settings()['keywords'] if w.casefold() in row['title'].casefold()]
    if matches:reasons.append('标题匹配：'+'、'.join(matches[:3]))
    if row.get('domain_matches'):reasons.append('领域匹配：'+'、'.join(row['domain_matches'][:3]))
    if row.get('likes') is not None and row.get('followers'):
        reasons.append(f"点赞/当前粉丝 {row['likes']/row['followers']:.0%}，用于筛选而非观看转化率")
    if not reasons:reasons.append('通过当前时效与互动筛选；相关性需看原片确认')
    row['reference_reasons']=reasons
    headline=re.split(r'[#＃]',row['title'],maxsplit=1)[0]
    row['reference_use']='参考实测条件与结果呈现' if re.search('实测|评测|对比',headline) else '参考步骤与演示方式' if re.search('教程|如何|怎么|实操|工作流',headline) else '参考前5秒与画面效果' if 'AIGC' in row.get('content_tracks',[]) else '判断题材、开头或表达方式是否值得借鉴'
    row['monitored_author']=bool(watched)
    row['is_favorite']=any(source['kind']=='favorites' for source in row.get('sources',[]))
    row['follower_observation']=follower_observation(store,row)
    return row


def feed(store,*,mode='focus',limit=10,offset=0,exclude_ids=(),query='',track='',sort='saved',hours=72,hot_min_likes=None):
    from . import services
    if mode not in {'focus','hot','creators','saved','seen','dismissed'}:raise TaskError('invalid_input','发现视图无效')
    if hours not in {24,72,168}:raise TaskError('invalid_input','精选时间范围仅支持24小时、72小时或7天')
    settings=store.settings()
    if hot_min_likes is not None and (type(hot_min_likes) is not int or not 1<=hot_min_likes<=10000000):raise TaskError('invalid_input','高赞门槛无效')
    if sort not in {'saved','oldest','published'}:raise TaskError('invalid_input','排序无效')
    if mode=='saved':
        # A human selection outlives discovery gates and later source changes.
        rows=services.ranked_videos(store,scope='all',state='备选',mode='recent',include_archived=True)
    elif mode in {'seen','dismissed'}:
        rows=services.ranked_videos(store,scope='all',mode='recent',include_archived=True)
    elif mode=='creators':
        rows=services.ranked_videos(store,scope='all',source='creator',mode='recent',days=7)
        active={r['id'] for r in store.rows('SELECT id FROM creators WHERE enabled=1')}
        rows=[r for r in rows if r.get('author_id') in active]
        domain=settings.get('creator_domain')
        if domain and domain.get('configured'):
            from .onboarding import match_domain
            rows=[r for r in rows if match_domain(r,domain)]
        elif not domain:rows=[r for r in rows if r.get('is_ai')]
        rows.sort(key=lambda r:r.get('published_at') or '',reverse=True)
    elif mode=='hot':
        from .hotspots import direct_topic_match
        minimum=hot_min_likes if hot_min_likes is not None else settings['hotspots']['min_likes']
        rows=services.ranked_videos(store,scope='all',mode='hot',days=hours/24)
        domain=settings.get('creator_domain')
        from .onboarding import match_domain
        rows=[r for r in rows if r.get('likes') is not None and r['likes']>=minimum and services.age_hours(r.get('metrics_at')) is not None and services.age_hours(r['metrics_at'])<=48 and (bool(domain.get('configured') and match_domain(r,domain)) if domain is not None else (r.get('is_ai') or (not r.get('manual_classification') and any(s['kind']=='hotspot' and direct_topic_match(s['source_key'].split(':',1)[-1],settings) for s in r.get('sources',[])))))]
        rows.sort(key=lambda r:(r['likes'],r.get('published_at') or ''),reverse=True)
    else:rows=services.ranked_videos(store,scope='recommendations',days=hours/24)
    feedback_by_id={r['video_id']:r for r in store.rows('SELECT * FROM discovery_feedback')}
    saved_by_id={r['video_id']:r['saved_at'] for r in store.rows('SELECT * FROM candidate_saves')}
    tracks=sorted({t for r in rows for t in r.get('content_tracks',[])}) if mode=='saved' else []
    pool=len(rows);filtered=[]
    for r in rows:
        f=feedback_by_id.get(r['id'],{})
        if mode=='seen' and not f.get('viewed_at'):continue
        if mode=='dismissed' and not f.get('dismissed_at'):continue
        if mode=='saved' and r['state']!='备选':continue
        # Viewing and shortlisting are annotations; only explicit disinterest hides a candidate.
        if mode in {'focus','hot','creators'} and f.get('dismissed_at'):continue
        if r['id'] in exclude_ids:continue
        if track and track not in r.get('content_tracks',[]):continue
        if query.strip().casefold() not in ' '.join(str(r.get(k) or '') for k in ('title','author','notes')).casefold():continue
        r['feedback']=f;r['saved_at']=saved_by_id.get(r['id']);filtered.append(r)
    if mode=='saved':
        filtered.sort(key=lambda r:(r.get('published_at') or '') if sort=='published' else (r.get('saved_at') or r.get('discovered_at') or ''),reverse=sort!='oldest')
    elif mode in {'seen','dismissed'}:
        filtered.sort(key=lambda r:r['feedback'].get('viewed_at' if mode=='seen' else 'dismissed_at') or '',reverse=True)
    # Exact normalized topic/title deduplication; no claim of semantic duplicate detection.
    seen=set();unique=[];merged=0
    for r in filtered:
        topic=topic_identity(r)
        if mode in {'focus','hot'} and topic and topic in seen:merged+=1;continue
        seen.add(topic);unique.append(r)
    total=len(unique)
    if mode=='focus':
        counts=Counter();balanced=[];deferred=[]
        for r in unique:
            key=r.get('author_id') or r.get('author') or r['id']
            (balanced if counts[key]<2 else deferred).append(r);counts[key]+=1
        unique=balanced+deferred
    items=[decorate(store,r) for r in unique[offset:offset+limit]]
    counts={'viewed':sum(bool(x.get('viewed_at')) for x in feedback_by_id.values()),'dismissed':sum(bool(x.get('dismissed_at')) for x in feedback_by_id.values())}
    search_status=store.one("SELECT id,kind,status,progress,error_code,message,created_at,updated_at,result FROM jobs WHERE kind='search' ORDER BY created_at DESC LIMIT 1")
    if search_status:
        import json
        search_status['searched_keywords']=json.loads(search_status.pop('result') or '{}').get('searched_keywords',[])
    hotspot_status=store.one("SELECT id,kind,status,progress,error_code,message,created_at,updated_at FROM jobs WHERE kind='hotspot_sync' ORDER BY created_at DESC LIMIT 1")
    return {'items':items,'total':total,'pool':pool,'merged':merged,'limit':limit,'offset':offset,'tracks':tracks,'feedback_counts':counts,'last_sync':settings['last_sync'],'search_status':search_status,'hotspot_status':hotspot_status,'hotspot_state':settings['hotspot_state'],'selection_method':'本地已采集候选初筛；同一题名/主题归并，未完整观看视频'}


def follow_author(store,identity):
    row=store.one('SELECT author_id,author FROM videos WHERE id=?',(identity,))
    if not row or not row['author_id'] or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',row['author_id']):
        raise TaskError('invalid_input','缺少可核对的作者主页ID，请打开原片确认作者后粘贴主页链接')
    old=store.one('SELECT enabled FROM creators WHERE id=?',(row['author_id'],))
    if not old or not old['enabled']:
        if store.one('SELECT COUNT(*) n FROM creators WHERE enabled=1')['n']>=100:raise TaskError('limit','最多跟踪100位，请先整理对标名单')
        store.run('INSERT INTO creators(id,name,url,enabled) VALUES (?,?,?,1) ON CONFLICT(id) DO UPDATE SET enabled=1',(row['author_id'],row['author'] or '待确认作者','https://www.douyin.com/user/'+quote(row['author_id'],safe='')))
    return {'followed':True,'already_followed':bool(old and old['enabled']),'network_requests':0}
