const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/quality.js','utf8');
assert(source.includes('function renderNoteHistory('),'grouped note view must exist');
const nodes={},ctx={historyPage:1,historyMonth:'2026-09',historyMonthTo:'',historyPending:false,historyView:'notes',cid:'one',page:'history',
 $:id=>nodes[id]??={innerHTML:'',value:'',open:false},esc:String,date:String,heading:()=>'',mountMonthlyCoverage(){},action:f=>f(),renderHistory(){},toast(){},encodeURIComponent};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function renderNoteHistory(')),ctx);
ctx.renderNoteHistory({total:2,complete_xml:1,pending_xml:1,items:[{id:'full',key:'1'.repeat(44),kind:'nfeProc',data:{issued_at:'2026-09-01'},created:1},{id:'summary',key:'2'.repeat(44),kind:'resNFe',data:{issued_at:'2026-09-02'},created:2}]},{items:[]},'one','',()=>true);
assert(nodes['#main'].innerHTML.includes('XML completo'));
assert(nodes['#main'].innerHTML.includes('Aguardando XML'));
assert(nodes['#main'].innerHTML.includes('Baixar resumo'));
assert(!nodes['#main'].innerHTML.includes('history-delete-selected'),'note view must not expose ambiguous file deletion');
nodes['#history-pending'].onclick();assert.equal(ctx.historyPending,true);assert.equal(ctx.historyPage,1);
nodes['#history-files'].onclick();assert.equal(ctx.historyView,'files');
console.log('PASS grouped note labels, pending filter and separate original-file view');

ctx.historyView='notes';let refreshes=0;ctx.renderHistory=()=>refreshes++;ctx.document={activeElement:{matches:()=>false}};ctx.$('#history-month').value='2026-08';ctx.refreshHistoryAutomatically();assert.equal(refreshes,0,'unsaved filters survive polling');ctx.$('#history-month').value='2026-09';ctx.$('#history-month-to').value='';ctx.refreshHistoryAutomatically();assert.equal(refreshes,1);ctx.refreshHistoryAutomatically();assert.equal(refreshes,1,'refresh is throttled');

vm.runInContext('historyAutoChecked=0',ctx);ctx.document.activeElement.matches=()=>true;ctx.refreshHistoryAutomatically();assert.equal(refreshes,1,'active filter editing blocks refresh');

(async()=>{
 const app=fs.readFileSync('app/static/app.js','utf8');ctx.beginViewRead=()=>()=>true;
 vm.runInContext(app.slice(app.indexOf('async function renderHistory()'),app.indexOf('async function renderCaptureMonitor()')),ctx);
 ctx.document.activeElement.matches=()=>false;ctx.historyPage=1;ctx.historyPending=true;
 ctx.$('#history-month').value=ctx.historyMonth;ctx.$('#main').innerHTML='keep-draft';
 let resolve;ctx.api=()=>new Promise(r=>resolve=r);
 const loading=ctx.renderHistory(true);
 ctx.$('#history-month').value='2026-08';
 resolve({notes_view:true,total:1,items:[],complete_xml:0,pending_xml:1});await loading;
 assert.equal(ctx.$('#main').innerHTML,'keep-draft','typing during request must preserve draft');
 ctx.$('#history-month').value=ctx.historyMonth;ctx.historyPage=2;const calls=[];
 ctx.api=async path=>{calls.push(path);return path.startsWith('/history?')?{notes_view:true,total:50,items:[],complete_xml:1,pending_xml:50}:{items:[]}};
 await ctx.renderHistory(true);assert.equal(ctx.historyPage,1);assert(calls.some(p=>p.includes('page=1')),'shrinking pending list must fetch last available page');
 console.log('PASS late polling preserves filters and pending pagination follows shrinking result');
})().catch(e=>{console.error(e);process.exitCode=1});
