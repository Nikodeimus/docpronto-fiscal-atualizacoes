async function mountHistoryTrash(targetCompany,isCurrent){
 const host=document.getElementById('history-trash');if(!host)return;
 const alive=()=>isCurrent()&&cid===targetCompany&&host.isConnected;
 let currentPage=1;
 async function paint(){
  const r=await api('/history/trash?company='+encodeURIComponent(targetCompany)+'&page='+currentPage);if(!alive())return;
  host.innerHTML='<h3>Lixeira do histórico</h3><p>'+esc(r.message)+'</p>'+r.items.map(batch=>`<div class="info"><strong>Exclusão de ${esc(date(batch.created))}</strong><p>${Number(batch.total)} arquivos · ${Number(batch.pending)} pendentes de restauração · ${Number(batch.restored)} restaurados · ${Number(batch.duplicates)} já existentes</p><button data-restore-history="${esc(batch.id)}" ${batch.pending?'':'disabled'}>Restaurar este lote</button></div>`).join('')+(r.items.length?'':'<p>Nenhuma exclusão registrada na lixeira.</p>')+`<div class="actions"><button id="trash-prev" ${currentPage<=1?'disabled':''}>Anterior</button><span>Página ${currentPage} · ${Number(r.total)} lotes</span><button id="trash-next" ${currentPage*20>=r.total?'disabled':''}>Próxima</button></div>`;
  host.querySelector('#trash-prev').onclick=()=>{if(!alive()||currentPage<=1)return;currentPage--;action(paint)};
  host.querySelector('#trash-next').onclick=()=>{if(!alive()||currentPage*20>=r.total)return;currentPage++;action(paint)};
  host.querySelectorAll('[data-restore-history]').forEach(button=>button.onclick=()=>{
   if(!alive())return;
   const batch=r.items.find(item=>item.id===button.dataset.restoreHistory);if(!batch||!batch.pending)return;
   const open=modal('Restaurar arquivos excluídos',`<p>Restaurar até ${Number(batch.pending)} arquivos deste lote para a empresa selecionada. Arquivos ausentes ou alterados serão informados; registros existentes serão preservados.</p><p>Esta ação não consulta a SEFAZ nem altera o último NSU.</p><button id="confirm-history-restore">Restaurar lote</button><p id="history-restore-result" role="status"></p>`);
   const confirmButton=document.getElementById('confirm-history-restore');let busy=false;
   confirmButton.onclick=()=>{if(busy||!open()||cid!==targetCompany)return;busy=true;confirmButton.disabled=true;action(async()=>{
    try{
     const result=await api('/history/trash/'+encodeURIComponent(batch.id)+'/restore',{method:'POST',body:{company:targetCompany}});
     if(!open()||cid!==targetCompany)return;
     document.getElementById('history-restore-result').textContent=`Restaurados: ${result.restored}. Já restaurados: ${result.already_restored}. Já existentes: ${result.duplicates}. Ausentes ou alterados: ${result.unavailable}. Conflitos: ${result.conflicts}.`;
     if(result.issues.length){const list=document.createElement('pre');list.textContent=result.issues.map(issue=>issue.key+': '+issue.reason).join('\n');document.getElementById('history-restore-result').after(list)}
     confirmButton.textContent='Concluído';
     if(alive())await renderHistory();
    }catch(error){if(open())document.getElementById('history-restore-result').textContent=error.message;else throw error;busy=false;confirmButton.disabled=false}
   })};
  });
 }
 try{await paint()}catch(error){if(alive())host.innerHTML='<h3>Lixeira do histórico</h3><p>'+esc(error.message)+'</p>'}
}
