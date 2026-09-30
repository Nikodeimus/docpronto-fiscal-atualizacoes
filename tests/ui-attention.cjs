const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/quality.js','utf8');
const ctx={esc:x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),location:{},mountOrganizationSelectors(){assert.equal(ctx.orgScope,null)},action:f=>f(),render(){},historyMonth:'2026-08',historyMonthTo:'2026-09',historyPage:8,historyView:'files'};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function attentionHtml(')),ctx);
const rows=[{id:'a',name:'<script>',document:'123',can_manage:false,attention:[{title:'Vencido',detail:'Trocar',page:'certificates',admin:true,level:'error'},{title:'XML pendente',detail:'Aguardar',page:'history',label:'Ver pendências',pending:true,level:'info'}]}];
const html=ctx.attentionHtml(rows);assert(!html.includes('<script>'));assert(html.includes('&lt;script>'));assert(html.includes('Solicitar administrador'));assert(html.includes('data-dash-pending="1"'));assert(!html.includes('data-dash-page="certificates"'));
ctx.openDashboardAction({dataset:{dashId:'a',dashPage:'history',dashPending:'1'}});assert.equal(ctx.cid,'a');assert.equal(ctx.historyPending,true);assert.equal(ctx.historyMonth,'');assert.equal(ctx.historyMonthTo,'');assert.equal(ctx.historyPage,1);assert.equal(ctx.historyView,'notes');
ctx.openDashboardAction({dataset:{dashId:'b',dashPage:'history'}});assert.equal(ctx.historyPending,false);assert(ctx.attentionHtml([]).includes('Nenhuma pendência'));
console.log('PASS attention escaping, permissions, pending navigation and filter reset');
