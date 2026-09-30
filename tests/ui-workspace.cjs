const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/workspace-ui.js','utf8');
const storage=new Map(),ctx={user:{id:'user-a'},cid:'company-a',historyMonth:'',historyMonthTo:'',historyPending:false,historySearch:'',historyDateFrom:'',historyDateTo:'',historyPage:1,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},action:f=>f(),renderHistory(){},Date};
vm.createContext(ctx);vm.runInContext(source,ctx);
ctx.restoreHistoryPreferences();ctx.historyMonth='2026-09';ctx.historySearch='123';ctx.historyPending=true;ctx.syncHistoryPreferences();
ctx.cid='company-b';ctx.syncHistoryPreferences();assert.equal(ctx.historyMonth,'');assert.equal(ctx.historySearch,'');assert.equal(ctx.historyPending,false);
ctx.cid='company-a';ctx.syncHistoryPreferences();assert.equal(ctx.historyMonth,'2026-09');assert.equal(ctx.historySearch,'123');
ctx.user={id:'user-b'};ctx.syncHistoryPreferences();assert.equal(ctx.historyMonth,'');assert.equal(ctx.historyPending,false);
storage.set(ctx.preferenceKey(ctx.cid),JSON.stringify({from:'2026-99',to:'<script>',pending:'yes',search:'a'.repeat(300)}));ctx.restoreHistoryPreferences();assert.equal(ctx.historyMonth,'');assert.equal(ctx.historyMonthTo,'');assert.equal(ctx.historySearch.length,120);assert.equal(ctx.historyPending,false);
ctx.applyHistoryMonth(-1,new Date(2026,0,29));assert.equal(ctx.historyMonth,'2025-12');assert.equal(ctx.historyMonthTo,'');assert.equal(ctx.historyPage,1);
ctx.localStorage.getItem=()=>{throw Error('storage blocked')};assert.doesNotThrow(()=>ctx.restoreHistoryPreferences());
const nodes={},calls=[];ctx.$=id=>nodes[id]??={};ctx.document={querySelectorAll:()=>[{dataset:{noteKey:'1'.repeat(44)},set onclick(f){calls.push(f)}}]};ctx.openNoteDetails=(key,cid)=>calls.push([key,cid]);ctx.openNoteComparison=(cid,query)=>calls.push([cid,query]);let current=true;ctx.bindNoteWorkspace('company-a','&month_from=2026-09',()=>current);
current=false;nodes['#history-compare'].onclick();assert.equal(calls.length,1,'stale comparison blocked');current=true;nodes['#history-compare'].onclick();assert.deepEqual(calls[1],['company-a','&month_from=2026-09']);ctx.cid='company-b';calls[0]();assert.equal(calls.length,2,'cross-company detail blocked');
console.log('PASS user/company preferences, invalid storage, January rollover, stale note actions');

ctx.applyHistoryDays(1,1,new Date(2026,0,1));assert.equal(ctx.historyDateFrom,'2025-12-31');assert.equal(ctx.historyDateTo,'2025-12-31');assert.equal(ctx.historyMonth,'');ctx.applyHistoryDays(6,0,new Date(2026,2,2));assert.equal(ctx.historyDateFrom,'2026-02-24');assert.equal(ctx.historyDateTo,'2026-03-02');console.log('PASS daily presets cross month and year without UTC drift');

ctx.localStorage.getItem=k=>storage.get(k);ctx.user={id:'alerts-a'};
const oldAlert={company:'one',valid_until:'2026-01-01'},newAlert={company:'one',valid_until:'2026-09-01'};
assert.equal(ctx.unseenCertificateAlerts([oldAlert]).length,1);ctx.markCertificateAlertsSeen([oldAlert]);assert.equal(ctx.unseenCertificateAlerts([oldAlert]).length,0);
assert.equal(ctx.unseenCertificateAlerts([oldAlert,newAlert]).length,1,'changed certificate expiry reappears');assert.equal(ctx.unseenCertificateAlerts([{...oldAlert,company:'two'}]).length,1,'same expiry in another company is not dismissed');
ctx.user={id:'alerts-b'};assert.equal(ctx.unseenCertificateAlerts([oldAlert]).length,1,'another user sees their own alerts');ctx.user={id:'alerts-a'};assert.equal(ctx.unseenCertificateAlerts([oldAlert]).length,0,'seen state persists for original user');
console.log('PASS certificate notices persist per user and company, changed expiry returns');
