async function checkCapturePreflight(body,target,isCurrent){
 const company=body.company;
 const current=()=>isCurrent()&&cid===company&&(!target||target.isConnected);
 if(!current())return false;
 if(target)target.innerHTML='<p role="status">Conferindo conector, certificado e intervalo da consulta…</p>';
 const params=new URLSearchParams();
 for(const key of ['company','device','thumbprint','store','uf'])params.set(key,body[key]??'');
 try{
  const result=await api('/capture/preflight?'+params);
  if(!current()||result.company!==company)return false;
  const messages=[...(result.blockers||[]),...(result.warnings||[])];
  if(target)target.innerHTML=`<div class="info ${result.ready?'':'warning'}" role="status"><strong>${result.ready?'Verificação local concluída.':'A consulta precisa de atenção.'}</strong><ul>${messages.map(item=>`<li>${esc(item.message)}</li>`).join('')}</ul></div>`;
  else if(result.ready!==true)toast((result.blockers||[])[0]?.message||'Não foi possível iniciar a consulta.');
  else if(result.warnings?.length)toast(result.warnings.map(item=>item.message).join(' '));
  return result.ready===true;
 }catch(error){
  if(current()){
   if(target)target.innerHTML='<div class="info warning" role="alert">'+esc(error.message)+'</div>';
   else toast(error.message);
  }
  return false;
 }
}
