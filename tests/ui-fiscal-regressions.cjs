const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/app.js','utf8');
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
async function imports(){
 const button={disabled:false},form={dataset:{},querySelector:()=>button};
 const nodes={'#pdf-batch':form,'#batch-pdfs':{files:[{name:'first.pdf'},{name:'second.pdf'}]},'#pdf-key':{value:'synthetic-key'},'#pdf-result':{}};
 let current=true,task,resolve;const requests=[];
 class Form {constructor(){this.data={}}set(k,v){this.data[k]=v}append(k,v){this.data[k]=v}}
 const ctx={cid:'A',$:id=>nodes[id],modal:()=>()=>current,FormData:Form,esc:String,renderDocuments:async()=>{},action:fn=>task=fn(),api:(path,options)=>{requests.push(options.body.data);return new Promise(done=>resolve=done)}};
 vm.createContext(ctx);vm.runInContext(slice('function importPdfs()','async function pairAgent'),ctx);ctx.importPdfs();form.onsubmit({preventDefault(){},target:form});
 assert.equal(requests.length,1);assert.equal(requests[0].company,'A');
 form.onsubmit({preventDefault(){},target:form});assert.equal(requests.length,1,'duplicate submit blocked');
 ctx.cid='B';current=false;resolve({results:[]});await task;
 assert.equal(requests.length,1,'remaining uploads stopped after company switch');assert.equal(button.disabled,false);
 console.log('PASS PDF batch fixes origin company, rejects duplicate submit, stops on context change');
}
async function historyDelete(){
 const button={disabled:false},form={dataset:{},elements:{password:{value:'AB12'},count:{value:'1'}},querySelector:()=>button};
 const nodes={'#history-delete-form':form,'#modal':{close(){}}};
 let task,resolve,count=0,rendered=0;
 const ctx={cid:'A',page:'history',modalGeneration:0,historyPage:1,$:id=>nodes[id],esc:String,beginViewRead:()=>()=>true,modal:()=>()=>true,toast(){},renderHistory:async()=>rendered++,action:fn=>task=fn(),api:async(path)=>{if(path.endsWith('delete-preview'))return {count:1,token:'test',company_name:'Synthetic',company_document:'000'};count++;return new Promise(done=>resolve=done)}};
 vm.createContext(ctx);vm.runInContext(slice('async function deleteHistory(', 'async function renderHistory('),ctx);
 await ctx.deleteHistory('A','selected',['one']);form.onsubmit({preventDefault(){},target:form});const first=task;form.onsubmit({preventDefault(){},target:form});
 assert.equal(count,1);resolve({deleted:1});await first;assert.equal(rendered,1);assert.equal(form.elements.password.value,'');assert.equal(button.disabled,false);
 console.log('PASS history deletion submits once and clears password');
}
async function historyPreview(){
 let release,modals=0;const ctx={cid:'A',page:'history',modalGeneration:0,beginViewRead:()=>()=>true,api:()=>new Promise(r=>release=r),modal:()=>modals++};
 vm.createContext(ctx);vm.runInContext(slice('async function deleteHistory(', 'async function renderHistory('),ctx);
 const pending=ctx.deleteHistory('A','all',[]);ctx.modalGeneration++;release({});await pending;assert.equal(modals,0);
 console.log('PASS stale history preview does not replace a newer dialog');
}
(async()=>{await imports();await historyDelete();await historyPreview()})().catch(e=>{console.error(e);process.exitCode=1});
// The review editor freezes fields during a save; a late save cannot erase newer typing.
const review=fs.readFileSync('app/static/review.js','utf8');
const lockSource=review.slice(review.indexOf('    function lockEditor()'),review.indexOf('    function isMissing'));
const controls=[{disabled:false},{disabled:true}];const attributes={};
const lockContext={form:{querySelectorAll:()=>controls,setAttribute:(k,v)=>attributes[k]=v,removeAttribute:k=>delete attributes[k]}};
vm.createContext(lockContext);vm.runInContext(lockSource,lockContext);const unlock=lockContext.lockEditor();
assert(controls.every(x=>x.disabled));assert.equal(attributes['aria-busy'],'true');unlock();assert.equal(controls[0].disabled,false);assert.equal(controls[1].disabled,true);assert(!('aria-busy' in attributes));
console.log('PASS review save locks editing and restores previous states');
