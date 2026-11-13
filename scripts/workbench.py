"""Local assistant helper. No account cookies, API secrets, or remote requests."""
import argparse,hashlib,json,os,shutil,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'assets/workbench'
RUNTIME=APP/'runtime'

def read_json(path):
    p=Path(path)
    if p.stat().st_size>2_000_000:raise ValueError('Input exceeds 2MB')
    return json.loads(p.read_text(encoding='utf-8-sig'))

def modules():
    sys.path.insert(0,str(APP/'backend'))
    from app.db import Store
    from app.onboarding import validate_profile,apply_profile,validate_following,select_following
    return Store,validate_profile,apply_profile,validate_following,select_following

def current_digest(store):
    values={k:v for k,v in store.settings().items() if k in {'profile','keywords','categories','creator_domain','traffic_tags'}}
    return hashlib.sha256(json.dumps(values,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    for name in ['plan-profile','apply-profile','save-following']:
        sp=sub.add_parser(name);sp.add_argument('--input',required=True)
        if name=='apply-profile':sp.add_argument('--expected-revision',required=True)
    sp=sub.add_parser('select-following');sp.add_argument('--ids',nargs='+',required=True)
    sub.add_parser('status')
    args=p.parse_args()
    if args.command=='doctor':
        return {'system':sys.platform,'python':sys.version.split()[0],'node':bool(shutil.which('node')),'npm':bool(shutil.which('npm.cmd' if os.name=='nt' else 'npm')),'ffmpeg_optional':bool(shutil.which('ffmpeg')),'venv_installed':(APP/'.venv').exists(),'data_exists':(RUNTIME/'workbench.sqlite3').exists(),'data_directory':str(RUNTIME),'browser_control':'由宿主Agent另行核实；脚本不能授予本机浏览器访问权','release':'0.3.0-beta'}
    Store,validate_profile,apply_profile,validate_following,select_following=modules()
    # This helper is scoped to the distribution only, regardless of process environment.
    store=Store(RUNTIME)
    if args.command=='plan-profile':
        return {'changes':validate_profile(read_json(args.input)),'expected_revision':current_digest(store),'paid_requests':0,'automation_changed':False}
    if args.command=='apply-profile':
        if current_digest(store)!=args.expected_revision:raise ValueError('偏好已变更，请重新plan-profile，避免覆盖其他端修改')
        changes=apply_profile(store,read_json(args.input),expected_revision=args.expected_revision)
        return {'saved':True,'domain':changes['creator_domain']['name'],'keywords':changes['keywords'],'paid_requests':0,'automation_changed':False}
    if args.command=='save-following':
        result=validate_following(read_json(args.input));RUNTIME.mkdir(parents=True,exist_ok=True)
        # validated output gets ids; persist the input schema for deterministic revalidation.
        saved={**result,'items':[{k:v for k,v in item.items() if k!='id'} for item in result['items']]}
        (RUNTIME/'following-candidates.json').write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        return {'candidates':result['items'],'status':result['status'],'monitored_added':0}
    if args.command=='select-following':
        return select_following(store,read_json(RUNTIME/'following-candidates.json'),args.ids)
    return {'domain':store.settings().get('creator_domain'),'keywords':store.settings()['keywords'],'automation':store.settings()['automation'],'materials':store.one('SELECT COUNT(*) n FROM videos')['n'],'monitored_creators':store.one('SELECT COUNT(*) n FROM creators WHERE enabled=1')['n'],'pending_jobs':store.one("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")['n']}

if __name__=='__main__':
    try:print(json.dumps(main(),ensure_ascii=False,indent=2))
    except Exception as e:
        # No request credentials are accepted by this helper; don't dump raw stack/context.
        print(json.dumps({'error':getattr(e,'message',str(e))},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
