const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../edge-extension/relay.js'),'utf8');
function fixture(origin='http://127.0.0.1:8765'){
  const events=[],calls=[];let handler;
  const window={postMessage:(message,target)=>events.push({message,target}),addEventListener:(_event,fn)=>handler=fn};
  const chrome={runtime:{sendMessage:async message=>{calls.push(message);return {ok:true,token:'must-never-reach-page',message:'已配对'};}}};
  vm.runInNewContext(source,{window,location:{origin},chrome});
  return {window,events,calls,get handler(){return handler;}};
}
test('relay runs only on the exact workbench port and rejects foreign page messages',()=>{
  assert.equal(fixture('http://127.0.0.1:8766').handler,undefined);
  const f=fixture();
  f.handler({source:f.window,origin:'https://www.douyin.com',data:{source:'AIWorkbenchApp',type:'pair',code:'a'.repeat(32)}});
  f.handler({source:{},origin:'http://127.0.0.1:8765',data:{source:'AIWorkbenchApp',type:'pair',code:'a'.repeat(32)}});
  assert.equal(f.calls.length,0);
});
test('pairing reply never transfers the persistent credential to the webpage',async()=>{
  const f=fixture();
  f.handler({source:f.window,origin:'http://127.0.0.1:8765',data:{source:'AIWorkbenchApp',type:'pair',code:'a'.repeat(32)}});
  await Promise.resolve();await Promise.resolve();
  assert.equal(f.calls.length,1);
  const reply=f.events.find(x=>x.message.type==='paired').message;
  assert.equal(reply.ok,true);
  assert.equal('token' in reply,false);
  assert.equal(JSON.stringify(f.events).includes('must-never-reach-page'),false);
});
