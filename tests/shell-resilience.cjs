const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const part=source.slice(source.indexOf('let shellRequest=0;'),source.indexOf("window.addEventListener('pagehide'",source.indexOf('let shellRequest=0;')));
(async()=>{
 let warnings=[],fallback=0,profile=0,hidden=[];
 const ctx={Promise,loadAppearance:async()=>{throw Error('offline')},mountProfile:async()=>{throw Error('offline')},appearanceRequest:0,appearanceDefault:{},applyAppearance:()=>fallback++,showBackdrop:()=>{},backdropUrl:'old',paintProfile:()=>profile++,toast:t=>warnings.push(t),$:s=>({classList:{add:()=>hidden.push(s),remove:()=>{}}})};
 vm.createContext(ctx);vm.runInContext(part,ctx);
 assert.equal(await ctx.refreshShell(),true);assert.equal(fallback,1);assert.equal(profile,1);assert.equal(warnings.length,1);
 ctx.loadAppearance=async()=>{throw Object.assign(Error('expired'),{status:401})};
 await assert.rejects(ctx.refreshShell(),e=>e.status===401);assert(hidden.includes('#shell'));
 let resolve;
 ctx.loadAppearance=()=>new Promise(r=>resolve=r);ctx.mountProfile=async()=>{};
 const old=ctx.refreshShell();ctx.loadAppearance=async()=>{};assert.equal(await ctx.refreshShell(),true);resolve();assert.equal(await old,false);
 console.log('PASS: fallback, authentication, obsolete navigation');
})().catch(e=>{console.error(e);process.exit(1)});
