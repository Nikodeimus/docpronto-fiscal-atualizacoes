const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/app.js','utf8');
function part(start,end){return source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)))}
function harness(){
 const nodes={},requests=[],messages=[];let task=Promise.resolve();
 const link={dataset:{periodFrom:'2026-08',periodTo:'2026-09'}};
 const ctx={cid:'company-A',page:'capture',historyMonth:'',historyMonthTo:'',historyPage:5,historyView:'files',historyPending:false,captureKey:'',window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},esc:String,date:String,heading:()=>'',certificateLabel:()=>'',mountCaptureCertificatePicker(){},beginViewRead:()=>()=>true,toast:m=>messages.push(m),render:async()=>{},
 $:id=>nodes[id]??=( {value:'',checked:false,isConnected:true,querySelectorAll:selector=>selector.includes('data-period-from')?[link]:[],addEventListener(){}} ),$$:selector=>selector.includes('data-period-from')?[link]:[],
 action:fn=>task=fn(),api:async(path,opt)=>{requests.push({path,opt});if(opt)return {};if(path.startsWith('/certificates/agents'))return {items:[{id:'dev',online:true,certificates:[{has_private_key:true,thumbprint:'synthetic',store:'CurrentUser'}]}]};if(path.startsWith('/capture/batches'))return {items:[{id:'batch',mode:'history',state:'completed',date_from:'2026-08-01',date_to:'2026-09-30',counts:{},xmls:0,summaries:0}]};if(path.startsWith('/certificates/selection'))return {};if(path.startsWith('/history'))return {items:[],total:0};return {items:[],nsu:'0'};}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('app/static/quality.js','utf8'),ctx);ctx.mountMonthlyCoverage=async()=>{};vm.runInContext(part('async function renderCapture()', 'let dashboardScope='),ctx);vm.runInContext(part('async function renderHistory()', 'async function renderCaptureMonitor()'),ctx);
 return {ctx,nodes,requests,messages,link,wait:()=>task};
}
(async()=>{
 for(const to of ['', '2026-09']){
  const h=harness();await h.ctx.renderCapture();h.ctx.$('#capture-cert').value='0';h.ctx.$('#capture-period').value='months';h.ctx.$('#capture-period').onchange();assert.equal(h.ctx.$('#capture-month-from').required,true);
  h.ctx.$('#capture-month-from').value='2026-08';h.ctx.$('#capture-month-to').value=to;
  h.ctx.renderCapture=async()=>{};h.ctx.$('#capture-form').onsubmit({preventDefault(){}});await h.wait();
  const payload=h.requests.find(r=>r.path==='/distribution'&&r.opt).opt.body;
  assert.equal(payload.period,'months');assert.equal(payload.month_from,'2026-08');assert.equal(payload.month_to,to);assert.equal(payload.company,'company-A');
 }
 const h=harness();await h.ctx.renderCapture();h.ctx.$('#capture-cert').value='0';h.ctx.$('#capture-period').value='months';h.ctx.$('#capture-month-from').value='2026-09';h.ctx.$('#capture-month-to').value='2026-08';h.ctx.$('#capture-form').onsubmit({preventDefault(){}});assert(!h.requests.some(r=>r.opt),'reversed interval never reaches API');
 h.ctx.$('#capture-period').value='all';h.ctx.$('#capture-period').onchange();assert.equal(h.ctx.$('#capture-months').hidden,true);assert.equal(h.ctx.$('#capture-month-from').disabled,true);
 assert.match(h.ctx.$('#main').innerHTML,/data-period-from="2026-08" data-period-to="2026-09"/);h.link.onclick();assert.equal(h.ctx.page,'history');assert.equal(h.ctx.historyPage,1);assert.equal(h.ctx.historyMonth,'2026-08');assert.equal(h.ctx.historyMonthTo,'2026-09');
 await h.ctx.renderHistory();const query=h.requests.find(r=>r.path.startsWith('/history?')).path;assert.match(query,/month_from=2026-08&month_to=2026-09/);
 for(const type of ['export','keys.txt'])assert(h.ctx.$('#main').innerHTML.includes('/api/history/'+type+'?company=company-A&month_from=2026-08&month_to=2026-09'));
 h.ctx.$('#history-month').value='2026-08';h.ctx.$('#history-month-to').value='';h.ctx.$('#history-refresh').onclick();await h.wait();assert.equal(h.ctx.historyMonthTo,'');assert(h.requests.some(r=>r.path.includes('month_from=2026-08&month_to=')&&!r.path.includes('month_to=2026')));
 h.ctx.$('#history-clear').onclick();await h.wait();assert.equal(h.ctx.historyMonth,'');assert.equal(h.ctx.historyMonthTo,'');assert.equal(h.ctx.historyPage,1);
 console.log('PASS single/range capture payload, reversed validation, period visibility, batch navigation, history/export filters, clear');
})().catch(e=>{console.error(e);process.exitCode=1});
