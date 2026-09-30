const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function scenario(fail=false,leave=false){
 let pending,job,current=true,refreshes=0;const requests=[];
 const button={},select={value:'copy-id'},message={};
 const host={isConnected:true,html:'',insertAdjacentHTML(_,html){this.html+=html},querySelector(s){return {'#backup-test':button,'#backup-test-id':select,'#backup-test-message':message}[s]}};
 const ctx={user:{role:'superadmin'},Date,esc:s=>String(s).replaceAll('<','&lt;'),date:String,action:fn=>job=fn(),api:(path,opt)=>{requests.push({path,opt});return new Promise((resolve,reject)=>pending={resolve,reject})}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/static/backup-check.js','utf8'),ctx);
 ctx.mountBackupVerification(host,{supported:true,automatic:true,items:[{id:'copy-id',created:1}],verification:{message:'<script>',finished_at:2}},()=>current,async()=>refreshes++);
 assert(!host.html.includes('<script>'));assert(host.html.includes('Não substitui seus dados'));
 button.onclick();const first=job;button.onclick();assert.equal(requests.length,1);assert.equal(button.disabled,true);
 assert.equal(requests[0].path,'/backups/verify');assert.equal(requests[0].opt.body.backup_id,'copy-id');
 if(leave)current=false;
 fail?pending.reject(Error('Synthetic failure')):pending.resolve({ok:true,message:'Verified'});
 await first;
 assert.equal(refreshes,!leave&&!fail?1:0);
 if(!leave){assert.equal(button.disabled,false);assert.equal(message.textContent,fail?'Synthetic failure':'Verified')}
}
(async()=>{await scenario();await scenario(true);await scenario(false,true);console.log('PASS backup rehearsal: escaped report, duplicate guard, failure and stale view handling')})().catch(error=>{console.error(error);process.exitCode=1});
