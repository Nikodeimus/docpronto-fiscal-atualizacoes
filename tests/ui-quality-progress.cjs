const fs=require('fs'),vm=require('vm'),assert=require('assert');
const quality=fs.readFileSync('app/static/quality.js','utf8');
function harness(){const timers=[];const host={innerHTML:'original',isConnected:true,querySelectorAll:()=>[],querySelector:()=>null};const ctx={window:{addEventListener(){}},Map,Date,Number,Math,Array,encodeURIComponent,esc:x=>String(x??''),date:String,cid:'a',page:'capture',clearTimeout(){},setTimeout(fn){timers.push(fn);return timers.length},$:()=>host,api:async()=>({items:[]}),action:fn=>fn(),toast(){},confirm:()=>true};vm.createContext(ctx);vm.runInContext(quality,ctx);return {ctx,host,timers}}
(async()=>{
 let h=harness();const base={id:'1',mode:'keys',state:'active',total:10,counts:{completed:3,failed:1,pending:6},xmls:2,progress:{determinate:true,percent:40,total:10,done:4,online:true,eta_seconds:120,elapsed_seconds:60}};
 let html=h.ctx.captureBatchesHtml([base]);assert.match(html,/value="40"/);assert.match(html,/4 de 10/);assert.match(html,/Falhas: 1/);assert.match(html,/Tempo restante estimado: 2 min/);
 assert.equal(h.ctx.durationText(7199),'2 h');
 assert.equal(h.ctx.durationText(3601),'1 h 1 min');
 for(const status of ['offline','waiting_sefaz','needs_attention','queued']){
  html=h.ctx.captureBatchesHtml([{...base,mode:'history',progress:{determinate:false,status,online:status!=='offline'}}]);
  assert(!html.includes('<progress'),'no animated progress while '+status);
 }
 html=h.ctx.captureBatchesHtml([{...base,mode:'history',progress:{determinate:false,status:'running',online:true}}]);assert(html.includes('<progress'));
 html=h.ctx.captureBatchesHtml([base]);assert(!html.includes('data-control="resume"'));
 html=h.ctx.captureBatchesHtml([{...base,state:'paused'}]);assert(!html.includes('data-control="pause"'));assert(html.includes('data-control="resume"'));
 html=h.ctx.captureBatchesHtml([{...base,state:'completed',counts:{failed:0}}]);assert(!html.includes('data-control='));
 html=h.ctx.captureBatchesHtml([{...base,state:'completed'}]);assert(html.includes('data-control="retry"'));assert(!html.includes('data-control="cancel"'));
 html=h.ctx.captureBatchesHtml([{...base,mode:'history',progress:{determinate:false,percent:null,eta_seconds:null,eta_reason:'unknown_total',online:true}}]);assert(!html.includes('value="'));assert.match(html,/total.*desconhecido|término ainda desconhecido/i);assert(!html.includes('100%'));
 html=h.ctx.captureBatchesHtml([{...base,next_allowed:Date.now()/1000+120,progress:{...base.progress,eta_seconds:null}}]);assert.match(html,/Próxima tentativa em/);assert(!html.includes('Tempo restante estimado:'));
 html=h.ctx.captureBatchesHtml([{...base,progress:{...base.progress,eta_seconds:null,eta_reason:'future_sefaz_limit'}}]);assert.match(html,/dependem dos limites/);assert(!html.includes('future_sefaz_limit'));
 let finish;h.ctx.api=()=>new Promise(resolve=>finish=resolve);h.ctx.mountCaptureProgress({items:[]},()=>true,'a');assert.equal(h.timers.length,1);const ticking=h.timers[0]();h.ctx.cid='b';finish({items:[base]});await ticking;assert.equal(h.host.innerHTML,'original');assert.equal(h.timers.length,1,'obsolete response never schedules another poll');
 h=harness();h.ctx.api=async()=>({items:[base]});h.ctx.mountCaptureProgress({items:[]},()=>true,'a');await h.timers[0]();assert.match(h.host.innerHTML,/40.0%/);assert.equal(h.timers.length,2,'poll continues without replacing capture form');
 console.log('PASS capture percentages/failures, unknown total, waits/ETA wording and stale polling');
})().catch(error=>{console.error(error);process.exit(1)});
