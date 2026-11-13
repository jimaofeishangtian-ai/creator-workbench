const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../edge-extension/reader.js'),'utf8');

async function run({grow=true,limit=300,stop_ids=[],known_ids=[],mode='sync',maxCount=350}={}) {
  let count=50,scrolls=0;
  const list={clientHeight:900,scrollHeight:10000,scrollTop:0,parentElement:null,
    scrollTo({top}){scrolls++;this.scrollTop=top;if(grow&&count<maxCount){count+=50;this.scrollHeight+=10000;}}};
  const cards=()=>Array.from({length:count},(_,i)=>{
    const id=String(100000+i),img={alt:'作者：AI工具 '+id,src:'https://p.douyinpic.com/'+id};
    return {href:'https://www.douyin.com/video/'+id,parentElement:list,
      getClientRects:()=>[{}],closest:()=>null,getAttribute:()=>'/video/'+id,
      querySelector:s=>s==='img[alt]'?img:s==='p'?{innerText:'AI工具 '+id}:null};
  });
  const favorite={getAttribute:()=> 'true'};
  const document={getElementById:()=>favorite,querySelector:()=>({}),scrollingElement:list,
    querySelectorAll:s=>s==='a[href]'?cards():[]};
  const context={document,location:{hostname:'www.douyin.com',pathname:'/user/self',href:'https://www.douyin.com/user/self'},URL,
    getComputedStyle:()=>({overflowY:'auto'}),setTimeout:fn=>{fn();return 0;}};
  vm.runInNewContext(source,context);
  return {result:await context.AIWorkbenchReadFavorites({mode,limit,stop_ids,known_ids}),scrolls};
}

test('long grids reach the true bottom and import the initial 300 item batch',async()=>{
  const {result,scrolls}=await run();
  assert.equal(result.items.length,300);
  assert.equal(result.stop_reason,'batch_limit');
  assert.equal(result.truncated,false);
  assert.equal(scrolls,5);
});
test('unchanging page is partial rather than exhausted or checkpointed',async()=>{
  const {result,scrolls}=await run({grow:false});
  assert.equal(result.items.length,50);
  assert.equal(result.stop_reason,'page_stalled');
  assert.equal(result.exhausted,false);
  assert.equal(result.truncated,true);
  assert.ok(scrolls>=5&&scrolls<24);
});
test('incremental sync skips known items and stops at an existing checkpoint',async()=>{
  const {result,scrolls}=await run({stop_ids:['100047','100048','100049'],known_ids:['100047','100048','100049']});
  assert.equal(result.items.length,47);
  assert.equal(result.stop_reason,'checkpoint');
  assert.equal(result.reached_checkpoint,true);
  assert.equal(scrolls,0);
});
test('deep known history keeps its scroll position for another bounded pass',async()=>{
  const {result}=await run({mode:'backfill',limit:100,maxCount:3000,known_ids:Array.from({length:3000},(_,i)=>String(100000+i))});
  assert.equal(result.outcome,'ok');
  assert.equal(result.items.length,0);
  assert.equal(result.continuation,true);
  assert.equal(result.stop_reason,'scan_limit');
  assert.equal(result.exhausted,false);
});
