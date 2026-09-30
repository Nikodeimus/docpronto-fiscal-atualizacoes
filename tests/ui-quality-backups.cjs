const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/quality.js','utf8');
function harness(){
 const requests=[],nodes={},timers=new Map();let task,seq=0,current=true,modalOpen=true,pending;
 let status={supported:true,state:'completed',attempted_at:1,items:[{id:'safe-copy',created:1,bytes:20,kind:'manual'}]};
 const submit={disabled:false},form={elements:{password:{value:'Synthetic password only'},confirmed:{checked:true}},querySelector:()=>submit};
 const restore={dataset:{restoreBackup:'safe-copy'}};
 const host={isConnected:true,innerHTML:'',querySelector:s=>nodes[s]??={},querySelectorAll:()=>[restore]};
 nodes['#backups-fiscal']=host;nodes['#restore-backup-form']=form;nodes['#modal']={close(){modalOpen=false}};
 const ctx={cid:'company-A',page:'integrations',user:{role:'superadmin'},esc:String,date:String,window:{addEventListener(){}},
  $:s=>nodes[s]??={},setTimeout:fn=>{timers.set(++seq,fn);return seq},clearTimeout:id=>timers.delete(id),
  modal:()=>()=>modalOpen,action:fn=>task=fn(),api:async(path,opt)=>{requests.push({path,opt});if(opt)return await new Promise((resolve,reject)=>pending={resolve,reject});return {...status}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 return {ctx,host,nodes,form,submit,restore,requests,timers,start:()=>ctx.mountFiscalBackups(()=>current),wait:()=>task,
  resolve:()=>pending.resolve({ok:true}),reject:()=>pending.reject(Error('Synthetic failure')),setState:v=>status={...status,...v},leave:()=>current=false,
  tick:async()=>{const [id,fn]=timers.entries().next().value;timers.delete(id);await fn()}};
}
(async()=>{
 for(const failure of [false,true]){
  const h=harness();await h.start();h.restore.onclick();const event={preventDefault(){}};
  h.form.onsubmit(event);h.form.onsubmit(event);
  assert.equal(h.requests.filter(r=>r.path==='/backups/restore').length,1,'duplicate restore must be blocked');assert(h.submit.disabled);
  const body=h.requests.at(-1).opt.body;assert.equal(body.backup_id,'safe-copy');assert.equal(body.confirm,'safe-copy');assert.equal(body.login_password,'Synthetic password only');
  failure?h.reject():h.resolve();await h.wait();assert.equal(h.form.elements.password.value,'','password must clear on success and failure');assert.equal(h.submit.disabled,false);
  if(failure)assert.match(h.nodes['#restore-backup-message'].textContent,/Synthetic failure/);
  else {await h.tick();assert.equal(h.timers.size,1,'previous completed state must not end polling for the new restore');h.setState({state:'restored',attempted_at:Date.now()/1000+1});await h.tick();assert.equal(h.timers.size,0,'current restore completion ends polling')}
 }
 let h=harness();await h.start();h.nodes['#backup-create'].onclick();h.nodes['#backup-create'].onclick();assert.equal(h.requests.filter(r=>r.path==='/backups/create').length,1);h.resolve();await h.wait();h.leave();await h.tick();assert.equal(h.timers.size,0,'obsolete view cannot keep polling');
 h=harness();let reply;h.ctx.api=()=>new Promise(resolve=>reply=resolve);const diagnostic={isConnected:true,innerHTML:'unchanged',querySelector:()=>null};
 const result={checked_at:1,connector:{online:false},certificate:{selected:false},sefaz:{last_response:null,last_attempt:null,note:'Sem teste ao vivo.'},actions:[]};
 const old=h.ctx.runDiagnostics(diagnostic,()=>true);h.ctx.cid='company-B';reply(result);await old;assert.equal(diagnostic.innerHTML,'unchanged','company switch rejects stale diagnostic');
 h.ctx.api=async()=>result;await h.ctx.runDiagnostics(diagnostic,()=>true);assert.match(diagnostic.innerHTML,/Nenhuma resposta da SEFAZ registrada para confirmar a comunicação/);assert.match(diagnostic.innerHTML,/Uma nova consulta confirma a comunicação atual/);assert(!diagnostic.innerHTML.includes('SEFAZ conectada'));
 const responses=[];h.ctx.api=()=>new Promise(resolve=>responses.push(resolve));
 const first=h.ctx.runDiagnostics(diagnostic,()=>true),second=h.ctx.runDiagnostics(diagnostic,()=>true);
 responses[1]({...result,checked_at:222});await second;responses[0]({...result,checked_at:111});await first;
 assert.match(diagnostic.innerHTML,/Verificado em 222/,'older diagnostic response must not overwrite the newer verification in the same company');
 console.log('PASS backup restore payload, duplicate guard, password cleanup, completion correlation, stale polling, diagnostic isolation and unconfirmed SEFAZ');
})().catch(error=>{console.error(error);process.exitCode=1});
