const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const code=fs.readFileSync('app/static/note-details.js','utf8');
function setup(){let html='',resolve;const context={cid:'a',page:'history',modalGeneration:1,beginViewRead:()=>()=>true,api:()=>new Promise(r=>resolve=r),esc:x=>String(x??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),date:String,modal:(title,body)=>{html=body;return()=>true},URLSearchParams};vm.createContext(context);vm.runInContext(code,context);return {context,resolve:r=>resolve(r),html:()=>html}}
const result={key:'1'.repeat(44),data:{issuer_name:'<script>bad</script>'},versions:[],events:[],availability:'pending_xml',fiscal_status:'unknown',message:'Read only'};
test('details escape fiscal text and distinguish pending from fiscal status',async()=>{const x=setup(),p=x.context.openNoteDetails(result.key,'a');x.resolve(result);await p;assert.match(x.html(),/&lt;script&gt;/);assert.match(x.html(),/Aguardando XML completo/);assert.match(x.html(),/Situação fiscal não confirmada/)});
test('late result cannot open details after company changed',async()=>{const x=setup(),p=x.context.openNoteDetails(result.key,'a');x.context.cid='b';x.resolve(result);await p;assert.equal(x.html(),'')});
test('late result cannot replace a newer modal',async()=>{const x=setup(),p=x.context.openNoteDetails(result.key,'a');x.context.modalGeneration++;x.resolve(result);await p;assert.equal(x.html(),'')});

test('corrupted complete XML is shown as unavailable',async()=>{const x=setup(),p=x.context.openNoteDetails(result.key,'a');x.resolve({...result,availability:'unavailable'});await p;assert.match(x.html(),/Arquivo ausente ou com integridade divergente/);assert.doesNotMatch(x.html(),/Aguardando XML completo/)});
