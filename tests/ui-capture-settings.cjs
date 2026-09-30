const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/app.js','utf8');
const start=source.indexOf(" form.onsubmit=e=>{e.preventDefault();action(async()=>{const values=Object.fromEntries(new FormData(form));values.enabled=form.elements.enabled.checked;");
assert(start>=0);const handler=source.slice(start,source.indexOf('\n',start));
(async()=>{
 const calls=[],ctx={form:{elements:{enabled:{checked:true},schedule_hour:{value:'9',disabled:true},interval_hours:{value:'3'},priority:{value:'1'}}},target:'company-a',action:fn=>ctx.pending=fn(),api:async(path,options)=>calls.push({path,body:options.body}),toast(){},FormData:class{*[Symbol.iterator](){yield ['schedule','hourly'];yield ['interval_hours','3'];yield ['priority','1'];}}};
 vm.createContext(ctx);vm.runInContext(handler,ctx);ctx.form.onsubmit({preventDefault(){}});await ctx.pending;
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/registrations/company-a/capture');assert.equal(calls[0].body.schedule_hour,9,'disabled field preference is retained');assert.equal(calls[0].body.interval_hours,3);assert.equal(calls[0].body.priority,1);assert.equal(calls[0].body.enabled,true);
 console.log('PASS capture settings submit numeric JSON and preserve inactive schedule preference');
})().catch(e=>{console.error(e);process.exitCode=1});
