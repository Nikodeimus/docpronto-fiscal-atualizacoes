const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const nodes={'#modal':{open:false,showModal(){this.open=true},close(){this.open=false}},'#modal-title':{},'#modal-body':{}};
const ctx={Map,cid:'a',page:'documents',$:s=>nodes[s]};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('let modalGeneration='),source.indexOf("$('#close-modal')")),ctx);
vm.runInContext(source.slice(source.indexOf('let viewEpoch=0;')),ctx);
const first=ctx.modal('First','body');assert(first());
ctx.cid='b';assert(!first());ctx.cid='a';ctx.page='history';assert(!first());ctx.page='documents';
const second=ctx.modal('Second','body');assert(!first());assert(second());nodes['#modal'].close();assert(!second());
// Exercise the actual asynchronous document-open boundary, without rendering a fake DOM.
const start=source.indexOf('async function openDocument(id)');const end=source.indexOf('const x=d.data;',start);
vm.runInContext(source.slice(start,end)+'rendered++;}',ctx);
ctx.rendered=0;let release;ctx.api=()=>new Promise(r=>release=r);
(async()=>{
 let task=ctx.openDocument('old');ctx.modal('Import','file');release({data:{}});await task;assert.equal(ctx.rendered,0);
 task=ctx.openDocument('old');ctx.cid='b';release({data:{}});await task;assert.equal(ctx.rendered,0);
 ctx.cid='a';task=ctx.openDocument('current');release({data:{}});await task;assert.equal(ctx.rendered,1);
 console.log('PASS: modal ownership, close, company/page switch, late details do not replace another dialog');
})().catch(e=>{console.error(e);process.exit(1)});
