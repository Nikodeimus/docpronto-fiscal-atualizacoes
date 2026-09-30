const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 let pending,current=true;const messages=[];
 const ctx={cid:'company',URLSearchParams,esc:s=>String(s).replaceAll('<','&lt;'),toast:s=>messages.push(s),api:()=>new Promise((resolve,reject)=>pending={resolve,reject})};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/static/capture-preflight.js','utf8'),ctx);
 const body={company:'company',device:'device',thumbprint:'A',store:'CurrentUser',uf:'35'},target={isConnected:true,innerHTML:''};
 let promise=ctx.checkCapturePreflight(body,target,()=>current);
 pending.resolve({company:'company',ready:false,blockers:[{message:'<expired>'}],warnings:[]});
 assert.equal(await promise,false);assert(target.innerHTML.includes('&lt;expired>'));
 promise=ctx.checkCapturePreflight(body,null,()=>current);
 pending.resolve({company:'company',ready:true,blockers:[],warnings:[]});assert.equal(await promise,true);
 promise=ctx.checkCapturePreflight(body,target,()=>current);ctx.cid='other';pending.resolve({company:'company',ready:true});assert.equal(await promise,false);
 ctx.cid='company';promise=ctx.checkCapturePreflight(body,null,()=>current);pending.reject(Error('offline'));assert.equal(await promise,false);assert.equal(messages.at(-1),'offline');
 promise=ctx.checkCapturePreflight(body,target,()=>current);current=false;pending.resolve({company:'company',ready:true});assert.equal(await promise,false);
 console.log('PASS capture preflight: blockers, readiness, escaping, company/view isolation and connection error');
})().catch(error=>{console.error(error);process.exitCode=1});
