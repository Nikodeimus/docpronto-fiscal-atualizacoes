/* An isolated restore rehearsal never replaces the active installation. */
function mountBackupVerification(host,status,current,refresh){
 if(!status.supported||user?.role!=='superadmin')return;
 const working=['creating','restoring'].includes(status.state);
 const last=status.verification,passed=status.last_verified;
 host.insertAdjacentHTML('beforeend',`<section class="info" id="backup-verification"><h3>Conferir recuperação do backup</h3><p>O teste copia o backup para uma pasta temporária e confere arquivos e banco. Não substitui seus dados nem reinicia o sistema. Não testa a comunicação com a SEFAZ ou o uso de certificados em outro computador.</p><p>Último teste aprovado: ${passed?esc(date(passed.finished_at)):'Ainda não realizado'}</p>${last?`<p role="status">${esc(last.message)} · ${esc(date(last.finished_at))}</p>`:''}<p>Próximo backup automático: ${status.automatic?(status.next_due&&status.next_due>Date.now()/1000?esc(date(status.next_due)):'Na próxima verificação, com o DocPronto aberto'):'Desativado'}</p><div class="form-stack"><label>Cópia para testar<select id="backup-test-id" ${working?'disabled':''}>${(status.items||[]).map(item=>`<option value="${esc(item.id)}">${esc(date(item.created))}</option>`).join('')}</select></label><button id="backup-test" ${working||!status.items?.length?'disabled':''}>Testar recuperação desta cópia</button><p id="backup-test-message" role="status" aria-live="polite"></p></div></section>`);
 const button=host.querySelector('#backup-test'),select=host.querySelector('#backup-test-id'),message=host.querySelector('#backup-test-message');
 let busy=false;
 button.onclick=()=>action(async()=>{
  if(busy||working||!current()||!host.isConnected||!select.value)return;
  busy=true;button.disabled=true;select.disabled=true;
  message.textContent='Conferindo a cópia isolada. Você pode continuar usando o DocPronto.';
  try{
   const result=await api('/backups/verify',{method:'POST',body:{backup_id:select.value}});
   if(current()&&host.isConnected){message.textContent=result.message;await refresh()}
  }catch(error){if(current()&&host.isConnected)message.textContent=error.message}
  finally{busy=false;if(current()&&host.isConnected){button.disabled=false;select.disabled=false}}
 });
}
