// Feature detection only. It does not inspect account state, cookies or profiles.
(function(root) {
  root.workbenchBrowserCheck=function(env=root) {
    const c=env.chrome||{};
    const checks={
      'Manifest V3':c.runtime?.getManifest?.().manifest_version===3,
      'runtime.sendMessage':typeof c.runtime?.sendMessage==='function',
      'scripting.executeScript':typeof c.scripting?.executeScript==='function',
      'storage.local.get/set':typeof c.storage?.local?.get==='function'&&typeof c.storage?.local?.set==='function',
      'alarms.create':typeof c.alarms?.create==='function',
      'tabs.query/create/get/update/remove':['query','create','get','update','remove'].every(k=>typeof c.tabs?.[k]==='function'),
      'fetch':typeof env.fetch==='function',
      'AbortSignal.timeout':typeof env.AbortSignal?.timeout==='function',
      'crypto.randomUUID':typeof env.crypto?.randomUUID==='function',
      'Array.at':typeof env.Array?.prototype?.at==='function',
    };
    const missing=Object.keys(checks).filter(key=>!checks[key]);
    return {interfacesAvailable:missing.length===0,missing,checks,scope:'接口检查；尚未验证实际抖音读取和后台同步'};
  };
})(globalThis);
