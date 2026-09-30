const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app/static/app.js','utf8');
const ctx={Map,page:'documents',cid:'a'};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('let viewEpoch=0;')),ctx);
let a=ctx.beginViewRead('documents');assert(a());ctx.cid='b';assert(!a());ctx.cid='a';
a=ctx.beginViewRead('documents');const newer=ctx.beginViewRead('documents');assert(!a());assert(newer());
ctx.page='history';assert(!newer());ctx.page='documents';
a=ctx.beginViewRead('documents');vm.runInContext('++viewEpoch',ctx);assert(!a());
// Run the actual documents renderer through its first asynchronous boundary.
const start=source.indexOf('async function renderDocuments()');const end=source.indexOf('docs=list.items;',start)+'docs=list.items;'.length;
let resolve;let mutations=0;
Object.assign(ctx,{Promise,URLSearchParams,pageNum:1,query:'',status:'',api:()=>new Promise(r=>resolve=r)});
Object.defineProperty(ctx,'docs',{set:()=>mutations++});
// Resolve both reads together, after switching registrations.
let pending=[];ctx.api=()=>new Promise(r=>pending.push(r));
vm.runInContext(source.slice(start,end)+'}',ctx);
(async()=>{ctx.cid='a';const task=ctx.renderDocuments();ctx.cid='b';pending[0]({});pending[1]({items:['old']});await task;assert.equal(mutations,0);console.log('PASS: company switch, page switch, pagination supersession, old document response discarded')})().catch(e=>{console.error(e);process.exit(1)});
