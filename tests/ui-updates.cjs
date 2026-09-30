const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/app.js','utf8');
const code=source.slice(source.indexOf('let updatesPollTimer='),source.indexOf('async function renderCompanies()'));
function harness(initial){
 let current=true,state={supported:true,manageable:true,configured:true,source:'C:/channel',automatic:false,current_version:'1',state:'ready',available_version:'2',...initial},nextTimer,id=0,reloads=0,fail=false;
 const requests=[],raw=[];const controls=[{disabled:false},{disabled:false}];
 const form={elements:{source:{value:'C:/channel'},automatic:{checked:false}},querySelectorAll:()=>controls};
 const nodes={'#updates-settings':form,'#updates-state':{},'#updates-guide':{},'#updates-check':{},'#updates-apply':{}};
 const host={isConnected:true,querySelector:selector=>nodes[selector]};
 const ctx={page:'integrations',csrf:'test-csrf',$:()=>host,esc:String,date:String,window:{addEventListener(){}},location:{reload(){reloads++}},AbortSignal,
 setTimeout:fn=>{nextTimer=fn;return ++id},clearTimeout:()=>{nextTimer=null},api:async(path,opt)=>{requests.push({path,opt});if(fail)throw Error('Synthetic failure');return {...state}},
 fetch:async(path,opt)=>{raw.push({path,opt});if(path.endsWith('/apply'))return {ok:true};if(path.endsWith('/status'))return {ok:true};return {ok:true,json:async()=>({...state})}}};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return {ctx,nodes,form,controls,host,requests,raw,start:()=>ctx.mountFiscalUpdates(()=>current),tick:()=>{const fn=nextTimer;nextTimer=null;return fn?.()},setState:value=>state={...state,...value},leave:()=>{current=false;ctx.page='documents'},fail:()=>fail=true,reloads:()=>reloads,timer:()=>nextTimer};
}
(async()=>{
 let h=harness({supported:false});await h.start();assert(h.controls.every(c=>c.disabled));assert(h.nodes['#updates-check'].disabled);assert.match(h.nodes['#updates-guide'].textContent,/instalação local/);await h.nodes['#updates-apply'].onclick();assert.equal(h.raw.length,0);
 h=harness({manageable:false});await h.start();assert(h.controls.every(c=>c.disabled));assert.match(h.nodes['#updates-guide'].textContent,/administrador/);
 h=harness({configured:false,state:'not_configured',source:''});await h.start();assert(h.nodes['#updates-check'].disabled);assert.match(h.nodes['#updates-guide'].textContent,/nenhuma versão será buscada/);h.form.elements.source.value='https://example.test/latest.json';h.form.elements.automatic.checked=true;await h.form.onsubmit({preventDefault(){}});const settings=h.requests.find(r=>r.opt);assert.equal(settings.path,'/updates/settings');assert.equal(settings.opt.body.source,'https://example.test/latest.json');assert.equal(settings.opt.body.automatic,true);
 h=harness({state:'current'});await h.start();await h.nodes['#updates-check'].onclick();assert(h.requests.some(r=>r.path==='/updates/check'&&r.opt.method==='POST'));h.fail();await h.nodes['#updates-check'].onclick();assert.match(h.nodes['#updates-state'].textContent,/Synthetic failure/);assert.equal(h.nodes['#updates-check'].disabled,false);
 h=harness();await h.start();await h.nodes['#updates-apply'].onclick();assert.equal(h.raw[0].path,'/api/updates/apply');assert.equal(h.raw[0].opt.headers['X-CSRF-Token'],'test-csrf');assert(h.nodes['#updates-apply'].disabled);h.setState({state:'updated',current_version:'2'});await h.tick();assert.equal(h.reloads(),1);
 h=harness();await h.start();const before=h.requests.length;h.leave();await h.tick();assert.equal(h.requests.length,before,'obsolete view never polls');assert.equal(h.timer(),null);
 assert(!source.includes('docpronto://update'));const renderPrefix=source.slice(source.indexOf('async function render(){'),source.indexOf('++viewEpoch',source.indexOf('async function render(){')));for(const cleanup of ['stopCaptureProgress','stopBackupsPoll','stopUpdatesPoll'])assert(renderPrefix.includes(cleanup+'();'),cleanup+' must run before changing the view');
 console.log('PASS updates: unsupported/permissions, source settings, check/errors, apply/restart, polling context, legacy link removed');
})().catch(error=>{console.error(error);process.exitCode=1});
