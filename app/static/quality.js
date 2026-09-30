'use strict';
let captureProgressTimer=null,backupsPollTimer=null;
const certificateFilters=new Map();
const diagnosticGenerations=new WeakMap();
function stopCaptureProgress(){clearTimeout(captureProgressTimer);captureProgressTimer=null}
function stopBackupsPoll(){clearTimeout(backupsPollTimer);backupsPollTimer=null}
window.addEventListener('pagehide',()=>{stopCaptureProgress();stopBackupsPoll()});
function durationText(seconds){
 if(seconds==null||!Number.isFinite(Number(seconds)))return 'Ainda sem estimativa';
 seconds=Math.max(0,Math.ceil(Number(seconds)));
 if(seconds<60)return seconds+' s';
 if(seconds<3600)return Math.ceil(seconds/60)+' min';
 const minutes=Math.ceil(seconds/60),remainder=minutes%60;
 return Math.floor(minutes/60)+' h'+(remainder?' '+remainder+' min':'');
}
function captureBatchesHtml(items){return (items||[]).map(b=>{
 const p=b.progress||{},determinate=p.determinate&&Number.isFinite(p.percent),percent=determinate?Math.min(100,Math.max(0,p.percent)):null;
 const state=({active:'Ativo',paused:'Pausado',completed:'Concluído',cancelled:'Cancelado'})[b.state]||b.state;
 const wait=Math.max(0,Math.max(Number(b.next_allowed||0),Number(b.retry_at||0))-Date.now()/1000);
 const work=({waiting_sefaz:'Aguardando intervalo da SEFAZ',needs_attention:'A consulta precisa de atenção',offline:'Conector desconectado',running:'Consultando a SEFAZ',queued:'Na fila',paused:'Pausado',completed:'Concluído',cancelled:'Cancelado'})[p.status]||'';
 const bar=determinate?`<progress max="100" value="${percent}" aria-label="Progresso da lista de chaves"></progress><strong>${percent.toFixed(1)}% · ${p.done||0} de ${p.total||b.total||0} chaves processadas</strong>`:b.state==='active'&&p.status==='running'&&p.online!==false&&wait===0?'<progress aria-label="Consulta em andamento; total ainda desconhecido"></progress><span>Total de documentos ainda desconhecido</span>':'<span>Histórico sem total esperado informado pela SEFAZ</span>';
 const controls=b.state==='cancelled'?[]:[...(b.state==='active'?['pause']:b.state==='paused'?['resume']:[]),...((b.counts?.failed>0||p.status==='needs_attention')?['retry']:[]),...(['active','paused'].includes(b.state)?['cancel']:[])];
 const dailyWait=b.schedule==='daily'&&Number(b.next_run)>Date.now()/1000;
 const estimate=b.state==='completed'?'Processamento encerrado':b.state==='cancelled'?'Captura cancelada':b.state==='paused'?'Estimativa suspensa durante a pausa':dailyWait?'Busca diária prevista para '+date(Math.max(b.next_run,b.next_allowed||0,b.retry_at||0))+'. Acompanhamento do dia anterior.':wait>0?(b.retry_at>Date.now()/1000?'Recuperação automática '+(b.retry_count||0)+'/3 em ':'Próxima tentativa em ')+durationText(wait):p.online===false?'Estimativa indisponível enquanto o conector estiver desconectado':p.eta_seconds!=null?'Tempo restante estimado: '+durationText(p.eta_seconds):({unknown_total:'A SEFAZ não informa o total: término ainda desconhecido',insufficient_samples:'Calculando estimativa após as primeiras respostas',future_sefaz_limit:'Sem estimativa: consultas restantes dependem dos limites da SEFAZ',needs_attention:'Prazo da consulta encerrado; confira o conector',not_active:'Estimativa suspensa',waiting_sefaz:'Aguardando intervalo da SEFAZ',offline:'Conector desconectado'})[p.eta_reason]||'Ainda sem dados suficientes para estimar o término';
 return `<article class="info capture-progress-card"><strong>${esc(b.mode==='keys'?'Lista de '+b.total+' chaves':'Histórico NF-e')} · ${esc(state)}</strong><p>${esc(b.message)}</p><div class="capture-progress">${bar}</div><p>${esc(work)}${work?' · ':''}${esc(estimate)}</p>${p.elapsed_seconds!=null?`<small>Tempo desde o início: ${esc(durationText(p.elapsed_seconds))}. Estimativas podem mudar com as respostas e esperas da SEFAZ.</small>`:''}${b.date_from?`<p>Período solicitado: ${esc(b.date_from)} a ${esc(b.date_to)}. <button data-period-from="${esc(b.date_from.slice(0,7))}" data-period-to="${esc((b.date_to||b.date_from).slice(0,7))}">Ver notas deste período</button></p>`:''}<p>Na fila: ${b.counts?.pending||0} · Em consulta: ${b.counts?.running||0} · Processadas: ${b.counts?.completed||0} · Falhas: ${b.counts?.failed||0} · Arquivos XML recebidos: ${b.xmls||0} · Resumos recebidos: ${b.summaries||0} (podem ser da mesma nota)</p>${controls.map(a=>`<button data-batch="${esc(b.id)}" data-control="${a}">${({pause:'Pausar',resume:'Retomar',retry:'Repetir falhas',cancel:'Cancelar'})[a]}</button>`).join('')}</article>`;
}).join('')||'<p>Nenhum lote criado.</p>'}
function mountCaptureProgress(initial,current,target){
 stopCaptureProgress();const host=$('#capture-live');if(!host)return;
 const alive=()=>current()&&cid===target&&page==='capture'&&host.isConnected;
 function bind(){
  host.querySelectorAll('[data-batch]').forEach(button=>button.onclick=()=>action(async()=>{
   if(!alive()||button.disabled)return;button.disabled=true;
   try{await api('/capture/batches/'+button.dataset.batch+'/control',{method:'POST',body:{company:target,action:button.dataset.control}});if(alive())await update(false)}finally{if(button.isConnected)button.disabled=false}
  }));
  host.querySelectorAll('[data-period-from]').forEach(button=>button.onclick=()=>{if(!alive())return;historyMonth=button.dataset.periodFrom;historyMonthTo=button.dataset.periodTo;historyPage=1;page='history';action(render)});
 }
 let updating=false;
 async function update(schedule=true){
  if(!alive())return;if(updating){if(schedule)captureProgressTimer=setTimeout(()=>update(),5000);return}updating=true;
  try{const result=await api('/capture/batches?company='+encodeURIComponent(target));if(!alive())return;host.innerHTML=captureBatchesHtml(result.items);bind()}
  catch(error){if(alive()){let warning=host.querySelector('.capture-refresh-error');if(!warning){host.insertAdjacentHTML('beforeend','<p class="capture-refresh-error" role="status"></p>');warning=host.querySelector('.capture-refresh-error')}warning.textContent='Não foi possível atualizar o andamento: '+error.message}}
  finally{updating=false;if(schedule&&alive())captureProgressTimer=setTimeout(()=>update(),5000)}
 }
 bind();captureProgressTimer=setTimeout(()=>update(),5000);
}
function mountCertificateFilters(){
 $$('.cert-search').forEach(input=>{
  const container=input.closest('details'),select=container.querySelector('.cert-validity-filter');
  const key=cid+'|'+container.dataset.certificateDevice,previous=certificateFilters.get(key)||{text:'',validity:'all'};
  input.value=previous.text;select.value=previous.validity;
  let feedback=container.querySelector('.cert-filter-feedback');if(!feedback){feedback=document.createElement('p');feedback.className='cert-filter-feedback muted';feedback.setAttribute('role','status');container.querySelector('.cert-items').before(feedback)}
  const filter=()=>{certificateFilters.set(key,{text:input.value,validity:select.value});container.querySelectorAll('.cert-item').forEach(item=>{const expired=item.classList.contains('certificate-expired'),soon=item.classList.contains('certificate-expiring');item.hidden=!item.textContent.toLowerCase().includes(input.value.trim().toLowerCase())||(select.value==='expired'&&!expired)||(select.value==='soon'&&!soon)});const items=Array.from(container.querySelectorAll('.cert-item')),shown=items.filter(item=>!item.hidden).length;feedback.textContent=shown?shown+' de '+items.length+' certificados exibidos':items.length?'Nenhum certificado corresponde aos filtros. Altere o nome ou a validade.':'Nenhum certificado disponível neste computador.'};
  input.oninput=filter;select.onchange=filter;filter();
 });
}
async function mountMonthlyCoverage(target,current,periodQuery){
 const host=$('#history-coverage');if(!host)return;
 host.innerHTML='<p>Conferindo arquivos por mês…</p>';
 try{const r=await api('/history/coverage?company='+encodeURIComponent(target)+periodQuery);if(!current()||!host.isConnected||cid!==target)return;
 host.innerHTML=`<h3>Notas por mês — sem contar a mesma chave duas vezes</h3><p>${esc(r.message)}</p><p>Última consulta deste cadastro: ${r.last_query?esc(date(r.last_query.created))+' · '+esc(r.last_query.message):'Nenhuma consulta registrada'}</p><div class="table-wrap"><table><thead><tr><th>Mês</th><th>Notas</th><th>Com XML completo</th><th>Aguardando XML</th><th>Arquivos de eventos / outros</th><th>Último recebimento</th><th>Cobertura</th></tr></thead><tbody>${r.items.map(m=>`<tr><td>${esc(m.month)}</td><td>${m.notes??'—'}</td><td>${m.complete_notes??'—'}</td><td>${m.pending_notes??'—'}</td><td>${m.events}</td><td>${m.last_received?esc(date(m.last_received)):'Nenhum arquivo'}</td><td>${m.xmls||m.summaries||m.events?'Há arquivos; total esperado desconhecido':'Sem arquivos recebidos'} · completude não confirmada</td></tr>`).join('')}</tbody></table></div>`;
 }catch(error){if(current()&&host.isConnected)host.textContent='Resumo mensal indisponível: '+error.message}
}
function mountTeamMemberActions(host,id,current){
 const path=uid=>'/teams/'+encodeURIComponent(id)+'/members/'+encodeURIComponent(uid);
 host.querySelectorAll('[data-save-member]').forEach(button=>button.onclick=()=>action(async()=>{
  if(!current()||button.disabled)return;const select=Array.from(host.querySelectorAll('[data-member-role]')).find(x=>x.dataset.memberRole===button.dataset.saveMember);if(!select)return;
  button.disabled=true;try{await api(path(button.dataset.saveMember),{method:'PATCH',body:{role:select.value}});if(current()&&host.isConnected){toast('Perfil atualizado.');await enter()}}finally{if(button.isConnected)button.disabled=false}
 }));
 host.querySelectorAll('[data-remove-member]').forEach(button=>button.onclick=()=>action(async()=>{
  if(!current()||button.disabled||!confirm('Remover esta pessoa da equipe? Ela perderá o acesso aos dados desta organização.'))return;
  button.disabled=true;try{await api(path(button.dataset.removeMember),{method:'DELETE',body:{}});if(current()&&host.isConnected){toast('Pessoa removida da organização.');await enter()}}finally{if(button.isConnected)button.disabled=false}
 }));
}
async function runDiagnostics(host,current){
 const generation=(diagnosticGenerations.get(host)||0)+1;diagnosticGenerations.set(host,generation);
 const originalCurrent=current;current=()=>originalCurrent()&&diagnosticGenerations.get(host)===generation;
 const target=cid;if(!target){host.textContent='Selecione um cadastro fiscal para verificar.';return}
 host.textContent='Verificando site, conector e certificado…';
 const r=await api('/diagnostics?company='+encodeURIComponent(target));if(!current()||!host.isConnected||cid!==target)return;
 const c=r.connector,k=r.certificate,z=r.sefaz;
 const validity=({valid:'Dentro da validade',expired:'Vencido',unknown:'Validade não informada'})[k.validity];
 const states={completed:'Concluído',failed:'Falhou',expired:'Prazo encerrado',pending:'Aguardando',running:'Em andamento',cancelled:'Cancelado'};
 host.innerHTML=`<div class="diagnostic-check"><strong>Site e banco: respondendo</strong><small>Verificado em ${esc(date(r.checked_at))}</small></div><div class="diagnostic-check"><strong>Conector: ${c.online?'conectado':'desconectado ou não configurado'}</strong><p>${esc(c.name||'Nenhum computador vinculado')} · Último contato: ${c.last_seen?esc(date(c.last_seen)):'Não informado'}</p></div><div class="diagnostic-check"><strong>Certificado: ${k.selected?esc(validity):'nenhum selecionado'}</strong><p>${k.valid_until?'Validade: '+esc(date(k.valid_until))+' · ':''}Chave privada: ${k.has_private_key===true?'identificada':k.has_private_key===false?'indisponível':'não confirmada'}</p>${k.last_test?`<p>Último teste da chave: ${esc(states[k.last_test.state]||k.last_test.state)} · ${esc(date(k.last_test.requested_at))}</p>`:''}${c.online&&k.thumbprint&&k.has_private_key?'<button class="diagnostic-test-key">Testar chave neste computador</button><p class="diagnostic-test-result" role="status"></p>':''}</div><div class="diagnostic-check"><strong>Comunicação com a SEFAZ</strong><p>${z.last_response?'Última resposta registrada: '+esc(date(z.last_response.received_at))+' · código '+esc(z.last_response.status):'Nenhuma resposta da SEFAZ registrada para confirmar a comunicação.'}</p>${z.last_attempt?'<p>Última tentativa: '+esc(states[z.last_attempt.state]||z.last_attempt.state)+' · '+esc(z.last_attempt.message)+'</p>':''}${z.wait_seconds?'<p>Espera obrigatória: '+esc(durationText(z.wait_seconds))+'</p>':''}<p>${esc(z.note)}</p></div>${r.actions.length?'<h4>O que fazer agora</h4><ul>'+r.actions.map(text=>'<li>'+esc(text)+'</li>').join('')+'</ul>':'<p>Nenhum impedimento identificado nos dados disponíveis. Uma nova consulta confirma a comunicação atual.</p>'}`;
 const test=host.querySelector('.diagnostic-test-key');
 if(test)test.onclick=()=>action(async()=>{if(!current()||test.disabled)return;test.disabled=true;try{await api('/certificates/agents/'+encodeURIComponent(c.id)+'/test',{method:'POST',body:{company:target,thumbprint:k.thumbprint,store:k.store}});if(current()&&host.isConnected)host.querySelector('.diagnostic-test-result').textContent='Teste solicitado. Informe o PIN na janela do Windows, se solicitado. Use Verificar funcionamento novamente para consultar o resultado. Este teste verifica a chave, não faz uma consulta à SEFAZ.'}finally{if(test.isConnected)test.disabled=false}});
}
function mountDiagnosticsButton(){
 const current=beginViewRead('certificate-diagnostics');
 $('#main').insertAdjacentHTML('afterbegin','<section class="panel panel-pad"><h2>Diagnóstico</h2><button id="diagnose-certificate">Verificar funcionamento</button><div id="certificate-diagnostics" aria-live="polite"></div></section>');
 $('#diagnose-certificate').onclick=()=>action(()=>runDiagnostics($('#certificate-diagnostics'),current));
}
async function mountFiscalBackups(current){
 stopBackupsPoll();const host=$('#backups-fiscal');if(!host)return;
 if(user?.role!=='superadmin'){host.innerHTML='<h2>Backup completo</h2><p>Gerenciado pelo administrador da instalação.</p>';return}
 const alive=()=>current()&&host.isConnected&&page==='integrations';
 let status,requested=false,seenWorking=false,busy=false,requestedAt=0;
 function paint(){
  const running=requested||['creating','restoring'].includes(status.state);
  host.innerHTML=`<h2>Backup completo e restauração</h2><p>Inclui banco, documentos e arquivos fiscais de todas as organizações desta instalação. A cópia fica neste computador; copie os backups para outro local para se proteger contra perda do disco.</p>${!status.supported?'<p>Disponível na instalação Windows local. A hospedagem requer seu próprio backup do servidor.</p>':`<p>Último backup: ${status.last_backup?esc(date(status.last_backup.created)):'Ainda não realizado'}</p><p role="status">${esc(status.message||'')}</p><p>O backup e a restauração interrompem brevemente o site e a fila para manter os dados consistentes.</p><form id="backup-settings"><label><input name="automatic" type="checkbox" ${status.automatic?'checked':''} ${running?'disabled':''}> Fazer backup automaticamente enquanto o DocPronto estiver aberto</label><label>Intervalo em horas<input name="interval" type="number" min="6" max="168" value="${status.interval_hours||24}" ${running?'disabled':''}></label><button ${running?'disabled':''}>Salvar preferência</button></form><div class="actions"><button id="backup-create" ${running?'disabled':''}>Fazer backup agora</button><button id="backup-refresh">Atualizar situação</button></div><div class="table-wrap"><table><thead><tr><th>Data</th><th>Tamanho</th><th>Tipo</th><th>Ação</th></tr></thead><tbody>${(status.items||[]).map(item=>`<tr><td>${esc(date(item.created))}</td><td>${((item.bytes||0)/1048576).toFixed(1)} MB</td><td>${esc(({manual:'Manual',automatic:'Automático','before-restore':'Antes da restauração'})[item.kind]||item.kind)}</td><td><button data-restore-backup="${esc(item.id)}" ${running?'disabled':''}>Restaurar esta cópia</button></td></tr>`).join('')||'<tr><td colspan="4">Nenhuma cópia completa disponível.</td></tr>'}</tbody></table></div>`}`;
  if(!status.supported)return;
  if(typeof mountBackupVerification==='function')mountBackupVerification(host,status,alive,refresh);
  host.querySelector('#backup-refresh').onclick=()=>action(refresh);
  host.querySelector('#backup-settings').onsubmit=event=>{event.preventDefault();const form=event.target;if(busy)return;busy=true;action(async()=>{try{await api('/backups/settings',{method:'POST',body:{automatic:form.elements.automatic.checked,interval_hours:Number(form.elements.interval.value)}});if(alive())await refresh()}finally{busy=false}})};
  host.querySelector('#backup-create').onclick=()=>action(async()=>{if(busy||requested)return;busy=true;requestedAt=Date.now()/1000;try{await api('/backups/create',{method:'POST',body:{}});if(alive()){requested=true;status.message='Backup solicitado. Aguardando o site reiniciar…';paint();schedule()}}finally{busy=false}});
  host.querySelectorAll('[data-restore-backup]').forEach(button=>button.onclick=()=>{
   if(!alive()||busy||requested)return;const id=button.dataset.restoreBackup;
   const selected=(status.items||[]).find(item=>item.id===id);
   const open=modal('Restaurar backup completo',`<p>Restaurar a cópia de <strong>${esc(date(selected.created))}</strong> substituirá os dados de todas as organizações pelo estado daquela data. Será criada uma cópia preventiva antes da restauração.</p><p>O fiscal reiniciará e poderá solicitar seu login novamente.</p><form id="restore-backup-form" class="form-stack"><label><input name="confirmed" type="checkbox" required> Confirmo a restauração desta instalação</label><label>Sua senha de acesso<input name="password" type="password" autocomplete="current-password" required></label><button class="danger">Confirmar restauração</button><p role="status" id="restore-backup-message"></p></form>`);
   const form=$('#restore-backup-form');form.onsubmit=event=>{event.preventDefault();if(busy||!open())return;busy=true;const submit=form.querySelector('button');submit.disabled=true;action(async()=>{try{requestedAt=Date.now()/1000;await api('/backups/restore',{method:'POST',body:{backup_id:id,confirm:id,login_password:form.elements.password.value}});if(open())$('#modal').close();if(alive()){requested=true;status.message='Restauração solicitada. Aguarde o reinício e entre novamente.';paint();schedule()}}catch(error){if(open())$('#restore-backup-message').textContent=error.message;else throw error}finally{form.elements.password.value='';busy=false;submit.disabled=false}})};
  });
 }
 function schedule(){stopBackupsPoll();if(alive())backupsPollTimer=setTimeout(()=>action(refresh),3000)}
 async function refresh(){
  try{const next=await api('/backups');if(!alive())return;
   if(['creating','restoring'].includes(next.state))seenWorking=true;
   if(['completed','restored','error'].includes(next.state)&&(!requested||Number(next.attempted_at)>=requestedAt)){requested=false;seenWorking=false}
   status=next;paint();if(requested||['creating','restoring'].includes(next.state))schedule();
  }catch(error){if(!alive())return;if(requested||seenWorking){host.innerHTML='<h2>Backup completo</h2><p role="status">Aguardando o fiscal reiniciar. Os dados estão sendo conferidos…</p>';schedule()}else host.innerHTML='<h2>Backup completo</h2><p>'+esc(error.message)+'</p>'}
 }
 await refresh();
}
async function mountFiscalNetwork(current){
 const host=$('#network-fiscal');if(!host)return;
 if(user?.role!=='superadmin'){host.innerHTML='<h2>Acesso da equipe pela rede</h2><p>Gerenciado pelo administrador da instalação.</p>';return}
 try{const r=await api('/network');if(!current()||!host.isConnected)return;
 host.innerHTML=`<h2>Acesso da equipe pela rede</h2><p>Este computador pode servir o DocPronto aos demais computadores da empresa. Todos acessam a mesma instalação pelo navegador; a central precisa ficar ligada.</p>${!r.supported?'<p>Configuração disponível na instalação Windows local.</p>':`<p>Rede interna: ${r.active_enabled?'ativa':'desativada'}${r.restart_required?' · reinício necessário para aplicar a configuração salva':''}</p><form id="network-settings" class="form-stack"><label><input name="enabled" type="checkbox" ${r.enabled?'checked':''}> Permitir acesso pela rede privada da empresa</label><label>Nome ou IP privado deste computador<input name="hosts" value="${esc(r.hosts.join(', '))}" placeholder="Exemplo: CENTRAL, 192.168.1.10"></label><small>Separe vários endereços por vírgula. O banco permanece no disco da central.</small><button>Salvar configuração da rede</button><p id="network-message" role="status"></p></form>${(r.urls||[]).length?'<h3>Endereços para os outros computadores</h3>'+r.urls.map(url=>'<p><a href="'+esc(url)+'">'+esc(url)+'</a></p>').join(''):''}<p>Após alterar, use o atalho Encerrar DocPronto Local e reabra o DocPronto Fiscal na central. O Firewall do Windows deve permitir a porta 8080 no perfil Privado para a rede local.</p>`}`;
 const form=host.querySelector('#network-settings');if(form)form.onsubmit=event=>{event.preventDefault();if(form.dataset.busy)return;form.dataset.busy='1';const button=form.querySelector('button');button.disabled=true;action(async()=>{try{const saved=await api('/network/settings',{method:'POST',body:{enabled:form.elements.enabled.checked,hosts:form.elements.hosts.value.split(',').map(v=>v.trim()).filter(Boolean)}});if(current()&&form.isConnected){host.querySelector('#network-message').textContent=saved.message;toast('Configuração salva. Reinicie a central para aplicar.')}}finally{delete form.dataset.busy;button.disabled=false}})};
 }catch(error){if(current()&&host.isConnected)host.innerHTML='<h2>Acesso da equipe pela rede</h2><p>'+esc(error.message)+'</p>'}
}

let certificateAlertChecked=0,certificateAlertBusy=false;
async function refreshCertificateAlerts(force=false){
 const host=document.getElementById('certificate-alerts'),owner=user;
 if(!host||!owner||certificateAlertBusy||(!force&&Date.now()-certificateAlertChecked<60000))return;
 certificateAlertBusy=true;certificateAlertChecked=Date.now();
 try{
  const r=await api('/certificates/alerts');if(user!==owner)return;
  const items=typeof unseenCertificateAlerts==='function'?unseenCertificateAlerts(r.items):r.items;
  host.hidden=!items.length;
  host.innerHTML=items.length?'<strong>'+items.length+' aviso(s) de certificado vencido</strong><p>Providencie a renovação. Marcar como visto dispensa este aviso; o vencimento continua indicado em Certificados e Visão geral.</p><div class="actions"><button id="certificate-notices-seen">Marcar avisos como vistos</button></div><details><summary>Ver cadastros afetados ('+items.length+')</summary>'+items.map(x=>`<button data-certificate-alert="${esc(x.company)}">${esc(x.name)} · venceu em ${esc(date(x.valid_until))} · Ver certificado</button>`).join(' ')+'</details>':'';
  const seen=host.querySelector('#certificate-notices-seen');if(seen)seen.onclick=()=>{if(user!==owner)return;if(typeof markCertificateAlertsSeen==='function')markCertificateAlertsSeen(items);host.hidden=true;host.innerHTML='';};
  host.querySelectorAll('[data-certificate-alert]').forEach(button=>button.onclick=()=>action(async()=>{if(user!==owner)return;cid=button.dataset.certificateAlert;orgScope=null;mountOrganizationSelectors();page='certificates';await render()}));
 }catch(error){/* Retry on the next interval; never hide a known expiry on network failure. */}
 finally{certificateAlertBusy=false}
}
function renderNoteHistory(r,h,target,periodQuery,isCurrent){
 $('#main').innerHTML=heading('Histórico fiscal','Uma nota por chave. O XML completo substitui o resumo nesta lista assim que chegar.')+`<section class="panel panel-pad"><div class="actions"><label>Mês inicial de emissão<input id="history-month" type="month" value="${esc(historyMonth)}"></label><label>Mês final (opcional)<input id="history-month-to" type="month" value="${esc(historyMonthTo)}"></label><label>Dia inicial<input id="history-day-from" type="date" value="${esc(typeof historyDateFrom==='string'?historyDateFrom:'')}"></label><label>Dia final<input id="history-day-to" type="date" value="${esc(typeof historyDateTo==='string'?historyDateTo:'')}"></label><label>Buscar nota<input id="history-search" maxlength="120" placeholder="Chave, número, nome ou documento" value="${esc(typeof historySearch==='string'?historySearch:'')}"></label><button id="history-refresh">Aplicar filtros</button><button id="history-current-month">Mês atual</button><button id="history-previous-month">Mês anterior</button><button id="history-yesterday">Ontem</button><button id="history-week">Últimos 7 dias</button><a class="action-link" href="/api/history/export?company=${encodeURIComponent(target)}${periodQuery}&organized=1">Exportar ZIP por mês</a><button id="history-compare">Conferir lista de chaves</button><button id="history-clear">Todos os meses</button><button id="history-pending" aria-pressed="${historyPending}">${historyPending?'Mostrar todas as notas':'Somente aguardando XML'}</button><button id="history-files">Arquivos originais e eventos</button></div><p><strong>${r.complete_xml+r.pending_xml} notas distintas · ${r.complete_xml} com XML completo · ${r.pending_xml} aguardando XML</strong></p><p>As pendências são acompanhadas pelas próximas consultas da captura automática configurada em Cadastros. Um resumo não garante que o XML já esteja liberado. Nenhuma Ciência da Emissão é enviada automaticamente.</p><p class="muted">A lista atualiza automaticamente enquanto você não estiver preenchendo os filtros. Os arquivos originais permanecem preservados.</p><p>Última consulta: ${esc(h.items[0]?.message||'Nenhuma consulta registrada')} · Espera da SEFAZ: ${esc(date(h.next_allowed))}</p><div class="table-wrap"><table><thead><tr><th>Chave da nota</th><th>Emissão</th><th>Situação</th><th>Recebido em</th><th>Arquivo disponível</th></tr></thead><tbody>${r.items.map(x=>`<tr><td class="mono">${esc(x.key)}<br><button data-note-key="${esc(x.key)}">Abrir ficha da nota</button></td><td>${esc(date(x.data.issued_at))}</td><td><strong>${x.kind==='nfeProc'?'XML completo':'Aguardando XML'}</strong>${x.warning?'<p>'+esc(x.warning)+'</p>':''}</td><td>${esc(date(x.created))}</td><td><a class="action-link" href="/api/history/${encodeURIComponent(x.id)}/xml">${x.kind==='nfeProc'?'Baixar XML completo':'Baixar resumo'}</a></td></tr>`).join('')||'<tr><td colspan="5">Nenhuma nota neste filtro.</td></tr>'}</tbody></table></div><div class="actions"><button id="history-prev" ${historyPage===1?'disabled':''}>Anterior</button><span>Página ${historyPage} · ${r.total} notas neste filtro</span><button id="history-next" ${historyPage*50>=r.total?'disabled':''}>Próxima</button></div><section id="history-coverage" aria-live="polite"></section><section id="history-sources"></section></section>`;
 $('#history-refresh').onclick=()=>{if(typeof applyHistoryDayDraft==='function'&&!applyHistoryDayDraft())return;const from=$('#history-month').value,to=$('#history-month-to').value;if(to&&(!from||to<from)){toast('Informe o mês inicial e um final igual ou posterior.');return;}historyMonth=from;historyMonthTo=to;historySearch=$('#history-search').value.trim();historyPage=1;action(renderHistory)};
 $('#history-clear').onclick=()=>{historyMonth='';historyMonthTo='';historySearch='';historyPending=false;historyDateFrom='';historyDateTo='';historyPage=1;action(renderHistory)};
 $('#history-pending').onclick=()=>{historyPending=!historyPending;historyPage=1;action(renderHistory)};
 $('#history-files').onclick=()=>{historyView='files';historyPage=1;action(renderHistory)};
 $('#history-prev').onclick=()=>{historyPage--;action(renderHistory)};
 $('#history-next').onclick=()=>{historyPage++;action(renderHistory)};
 if(typeof bindNoteWorkspace==='function')bindNoteWorkspace(target,periodQuery,isCurrent);
 if(typeof mountFiscalSources==='function')action(()=>mountFiscalSources($('#history-sources'),target,isCurrent));
 action(()=>mountMonthlyCoverage(target,isCurrent,periodQuery));
}
let historyAutoChecked=0;
function refreshHistoryAutomatically(){
 if(page!=='history'||historyView!=='notes'||$('#modal').open||Date.now()-historyAutoChecked<30000)return;
 if(!historyCanRefresh())return;
 historyAutoChecked=Date.now();action(()=>renderHistory(true));
}
function historyCanRefresh(){
 if(typeof fiscalSourcesCanRefresh==='function'&&!fiscalSourcesCanRefresh())return false;
 const from=$('#history-month'),to=$('#history-month-to');
 return page==='history'&&historyView==='notes'&&!$('#modal').open&&from&&to&&from.value===historyMonth&&to.value===historyMonthTo&&(!$('#history-search')||$('#history-search').value===(typeof historySearch==='string'?historySearch:''))&&(typeof historyDayDraftMatches!=='function'||historyDayDraftMatches())&&!document.activeElement?.matches('input,select,textarea,[contenteditable="true"]');
}

function attentionHtml(rows){
 const items=rows.flatMap(r=>(r.attention||[]).map(a=>({r,a})));
 items.sort((x,y)=>({error:0,warning:1,info:2}[x.a.level]??2)-({error:0,warning:1,info:2}[y.a.level]??2));
 return '<section class="panel panel-pad"><h2>Pendências e próximos passos</h2>'+(!items.length?'<p>Nenhuma pendência identificada nos cadastros deste filtro.</p>':'<p>'+items.length+' situações para acompanhar. A lista reflete os dados recebidos pelo DocPronto.</p><div class="table-wrap"><table><thead><tr><th>Cadastro</th><th>Situação</th><th>Próximo passo</th></tr></thead><tbody>'+items.map(({r,a})=>'<tr><td><strong>'+esc(r.name)+'</strong><span class="sub">'+esc(r.document)+'</span></td><td><strong>'+esc(a.title)+'</strong><p>'+esc(a.detail)+'</p></td><td>'+(a.admin&&!r.can_manage?'<span>Solicitar administrador</span>':'<button data-dash-id="'+esc(r.id)+'" data-dash-page="'+esc(a.page)+'" '+(a.pending?'data-dash-pending="1"':'')+'>'+esc(a.label)+'</button>')+'</td></tr>').join('')+'</tbody></table></div>')+'</section>';
}
function openDashboardAction(button){
 cid=button.dataset.dashId;orgScope=null;mountOrganizationSelectors();if(typeof restoreHistoryPreferences==='function')restoreHistoryPreferences();
 page=button.dataset.dashPage;
 if(page==='history'){historyView='notes';historyPending=button.dataset.dashPending==='1';historyMonth='';historyMonthTo='';historySearch='';historyDateFrom='';historyDateTo='';historyPage=1;}
 location.hash=page;action(render);
}
