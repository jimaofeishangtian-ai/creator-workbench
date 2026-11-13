"""General-domain onboarding and visible-following imports for the shareable edition."""
from datetime import datetime
from urllib.parse import urlsplit
import re
import hashlib,json

from .db import encode, now
from .providers import TaskError


def words(value, name, maximum=30, required=False):
    if not isinstance(value, list) or len(value)>maximum or (required and not value):
        raise TaskError('invalid_input', f'{name}需为1—{maximum}项文本' if required else f'{name}最多{maximum}项')
    if not all(isinstance(w,str) and w.strip() and len(w)<=100 and not any(ord(c)<32 for c in w) for w in value):
        raise TaskError('invalid_input', f'{name}包含空白、过长或无效文本')
    return list(dict.fromkeys(w.strip() for w in value))


def validate_domain(domain):
    expected={'configured','name','include_keywords','exclude_keywords','prefer_low_followers'}
    if not isinstance(domain,dict) or set(domain)!=expected:
        raise TaskError('invalid_input','领域配置字段不完整')
    if not isinstance(domain['configured'],bool) or not isinstance(domain['prefer_low_followers'],bool):
        raise TaskError('invalid_input','领域开关无效')
    if not isinstance(domain['name'],str) or not domain['name'].strip() or len(domain['name'])>100:
        raise TaskError('invalid_input','请填写领域名称')
    return {**domain,'name':domain['name'].strip(),'include_keywords':words(domain['include_keywords'],'匹配词',required=True),'exclude_keywords':words(domain['exclude_keywords'],'排除词')}


def match_domain(row,domain):
    text=' '.join(str(row.get(k) or '') for k in ['title','tools']).casefold()
    if any(word.casefold() in text for word in domain.get('exclude_keywords',[])):
        return []
    return [word for word in domain.get('include_keywords',[]) if word.casefold() in text]


def validate_profile(profile):
    required={'domain','audience','goal','formats','include_keywords','search_keywords','exclude_keywords','prefer_low_followers'}
    optional={'examples','categories','traffic_tags'}
    if not isinstance(profile,dict) or not required<=set(profile) or set(profile)-required-optional:
        raise TaskError('invalid_input','访谈配置缺字段或包含未知字段')
    for key in ['domain','audience','goal']:
        if not isinstance(profile[key],str) or not profile[key].strip() or len(profile[key])>2000:
            raise TaskError('invalid_input',f'{key}需为非空文本')
    domain=validate_domain({'configured':True,'name':profile['domain'],'include_keywords':profile['include_keywords'],'exclude_keywords':profile['exclude_keywords'],'prefer_low_followers':profile['prefer_low_followers']})
    formats=words(profile['formats'],'内容形式',10,True)
    keywords=words(profile['search_keywords'],'搜索关键词',20,True)
    categories=words(profile.get('categories',['教程方法','案例效果','工具产品','经验观点','趋势资讯','其他']),'主题分类',30,True)
    examples=words(profile.get('examples',[]),'参考描述',10)
    description=f"内容领域：{profile['domain']}\n目标受众：{profile['audience']}\n创作目标：{profile['goal']}\n形式：{'、'.join(formats)}\n偏好示例：{'；'.join(examples) or '尚未提供'}\n排除：{'、'.join(domain['exclude_keywords']) or '尚未提供'}"
    changes={'creator_domain':domain,'profile':description,'keywords':keywords,'categories':categories}
    if 'traffic_tags' in profile:changes['traffic_tags']=validate_tags(profile['traffic_tags'])
    return changes


def validate_tags(value):
    """Publishing topics are references, not implicit paid search queries."""
    if not isinstance(value,list) or len(value)>30:raise TaskError('invalid_input','发布标签最多30项')
    result=[];seen=set()
    for item in value:
        expected={'tag','platform','purpose','status','source_url','checked_at','valid_until'}
        if not isinstance(item,dict) or set(item)!=expected:raise TaskError('invalid_input','标签缺少来源、状态或日期')
        if not all(isinstance(v,str) and len(v)<=2000 and not any(ord(c)<32 for c in v) for v in item.values()):raise TaskError('invalid_input','标签文本无效')
        tag=item['tag'].strip().lstrip('#＃')
        if not tag or len(tag)>100 or any(c.isspace() for c in tag):raise TaskError('invalid_input','标签名称无效')
        if item['platform'] not in {'抖音','小红书','其他'} or item['purpose'] not in {'主题','活动'} or item['status'] not in {'待核实','已核实','已失效'}:raise TaskError('invalid_input','标签分类无效')
        u=urlsplit(item['source_url'])
        if u.scheme!='https' or not u.netloc or u.username or u.password:raise TaskError('invalid_input','标签必须标注无凭据的HTTPS来源')
        try:
            assert datetime.fromisoformat(item['checked_at']).tzinfo is not None
            if item['valid_until']:datetime.fromisoformat(item['valid_until'])
        except (ValueError,AssertionError):raise TaskError('invalid_input','标签检查日期无效')
        key=(item['platform'],tag.casefold())
        if key not in seen:seen.add(key);result.append({**item,'tag':tag})
    return result


def apply_profile(store, profile, *, expected_revision=None):
    changes=validate_profile(profile)
    # One transaction: a malformed interview cannot half-update the account.
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if expected_revision is not None:
            values={r['key']:json.loads(r['value']) for r in c.execute('SELECT key,value FROM settings') if r['key'] in {'profile','keywords','categories','creator_domain','traffic_tags'}}
            actual=hashlib.sha256(json.dumps(values,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if actual!=expected_revision:raise TaskError('revision_conflict','偏好已改变，请重新预览，不覆盖其他端修改')
        for key,value in changes.items():
            c.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,encode(value)))
    return changes


def validate_following(payload):
    if not isinstance(payload,dict) or set(payload)!={'captured_at','page_url','status','items'}:
        raise TaskError('invalid_input','关注列表需包含captured_at/page_url/status/items')
    try:
        stamp=datetime.fromisoformat(payload['captured_at'])
        assert stamp.tzinfo is not None
    except (TypeError,ValueError,AssertionError):
        raise TaskError('invalid_input','关注采集时间必须是带时区的真实时间')
    if payload['page_url']!='https://www.douyin.com/user/self' or payload['status'] not in {'partial','complete'}:
        raise TaskError('invalid_input','只接受本人关注页的可见读取结果；未到底请标partial')
    if not isinstance(payload['items'],list) or not 1<=len(payload['items'])<=500:
        raise TaskError('invalid_input','每批关注候选需为1—500项，不能把异常空页当成功')
    result={}
    for item in payload['items']:
        if not isinstance(item,dict) or set(item)!={'name','url','evidence'}:
            raise TaskError('invalid_input','候选必须含页面昵称、主页链接和可见依据')
        if not all(isinstance(item[k],str) and item[k].strip() for k in item) or len(item['name'])>200 or len(item['evidence'])>1000:
            raise TaskError('invalid_input','关注候选文本无效')
        try:
            u=urlsplit(item['url'])
            match=re.fullmatch(r'/user/([A-Za-z0-9_-]{5,200})',u.path)
            if u.scheme!='https' or u.netloc!='www.douyin.com' or not match or u.username or u.query or u.fragment:
                raise ValueError()
        except (ValueError,TypeError):
            raise TaskError('invalid_input','候选主页必须是页面实际显示的标准抖音链接，不含参数或凭据')
        identity=match[1]
        if identity=='self':raise TaskError('invalid_input','不能把本人主页作为关注候选')
        result.setdefault(identity,{'id':identity,**item})
    return {**payload,'items':list(result.values())}


def select_following(store,candidates,identities):
    validated=validate_following(candidates)
    by_id={item['id']:item for item in validated['items']}
    if not isinstance(identities,list) or not 1<=len(identities)<=100 or not all(isinstance(i,str) and i in by_id for i in identities):
        raise TaskError('invalid_input','只可选择本次可见关注候选，每批1—100位')
    chosen=list(dict.fromkeys(identities))
    with store.connect() as c:
        enabled={row[0] for row in c.execute('SELECT id FROM creators WHERE enabled=1')}
        if len(enabled|set(chosen))>100:raise TaskError('limit','最多监控100位，不自动移除已选对标')
        for identity in chosen:
            item=by_id[identity]
            c.execute('INSERT INTO creators (id,name,url,enabled,updated_at) VALUES (?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,enabled=1,updated_at=excluded.updated_at',(identity,item['name'],item['url'],now()))
    return {'selected':len(chosen),'candidates':len(by_id),'read_status':validated['status'],'paid_requests':0}
