const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const scope={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../edge-extension/browser-compat.js'),'utf8'),scope);
function supported(){const f=()=>{};return {chrome:{runtime:{getManifest:()=>({manifest_version:3}),sendMessage:f},scripting:{executeScript:f},storage:{local:{get:f,set:f}},alarms:{create:f},tabs:{query:f,create:f,get:f,update:f,remove:f}},fetch:f,AbortSignal:{timeout:f},crypto:{randomUUID:f},Array};}
test('standard Chromium interfaces pass without guessing browser brand',()=>{
  const result=scope.workbenchBrowserCheck(supported());assert.equal(result.interfacesAvailable,true);assert.equal(result.missing.length,0);
});
test('MV2 or older chromium scripting is reported as unsupported',()=>{
  const env=supported();env.chrome.runtime.getManifest=()=>({manifest_version:2});delete env.chrome.scripting;
  const result=scope.workbenchBrowserCheck(env);assert.equal(result.interfacesAvailable,false);assert.ok(result.missing.includes('Manifest V3'));assert.ok(result.missing.includes('scripting.executeScript'));
});
test('missing modern runtime primitives fail even when MV3 exists',()=>{
  const env=supported();delete env.AbortSignal.timeout;delete env.crypto.randomUUID;
  const result=scope.workbenchBrowserCheck(env);assert.equal(result.interfacesAvailable,false);assert.equal(result.missing.length,2);
});
test('plain pages without extension API get a clear feature report',()=>{
  const result=scope.workbenchBrowserCheck({});assert.equal(result.interfacesAvailable,false);assert.ok(result.missing.length>0);
});
