/* Human review of source invoice fields. Never supplies fiscal sample values. */
(() => {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const ICMS = ['ICMS00','ICMS10','ICMS20','ICMS30','ICMS40','ICMS51','ICMS60','ICMS70','ICMS90','ICMSPart','ICMSST','ICMSSN101','ICMSSN102','ICMSSN201','ICMSSN202','ICMSSN500','ICMSSN900'];
  const CST = {ICMS00:['00'],ICMS10:['10'],ICMS20:['20'],ICMS30:['30'],ICMS40:['40','41','50'],ICMS51:['51'],ICMS60:['60'],ICMS70:['70'],ICMS90:['90'],ICMSPart:['10','90'],ICMSST:['41','60'],ICMSSN101:['101'],ICMSSN102:['102','103','300','400'],ICMSSN201:['201'],ICMSSN202:['202','203'],ICMSSN500:['500'],ICMSSN900:['900']};
  const DECIMAL = /^(v|p|q)[A-Z]/;
  const labels = {
    key:'Chave de acesso', cUF:'Código da UF', cNF:'Código numérico', natOp:'Natureza da operação', mod:'Modelo', serie:'Série', nNF:'Número da nota', dhEmi:'Emissão com fuso horário', dhSaiEnt:'Entrada/saída com fuso horário', tpNF:'Operação da nota', idDest:'Destino da operação', cMunFG:'Município do fato gerador (IBGE)', tpImp:'Formato do DANFE', tpEmis:'Tipo de emissão', cDV:'Dígito verificador', tpAmb:'Ambiente', finNFe:'Finalidade', indFinal:'Consumidor final', indPres:'Presença do comprador', procEmi:'Processo de emissão', verProc:'Versão do emissor', CNPJ:'CNPJ', CPF:'CPF', xNome:'Nome / razão social', xFant:'Nome fantasia', IE:'Inscrição estadual', IEST:'IE do substituto tributário', IM:'Inscrição municipal', CNAE:'CNAE', CRT:'Regime tributário', indIEDest:'Indicador de IE do destinatário', email:'E-mail', xLgr:'Logradouro', nro:'Número', xCpl:'Complemento', xBairro:'Bairro', cMun:'Código do município (IBGE)', xMun:'Município', UF:'UF', CEP:'CEP', cPais:'Código do país', xPais:'País', fone:'Telefone', cProd:'Código do produto', cEAN:'EAN comercial', xProd:'Descrição do produto', NCM:'NCM', CEST:'CEST', CFOP:'CFOP', uCom:'Unidade comercial', qCom:'Quantidade comercial', vUnCom:'Valor unitário comercial', vProd:'Valor dos produtos', cEANTrib:'EAN tributável', uTrib:'Unidade tributável', qTrib:'Quantidade tributável', vUnTrib:'Valor unitário tributável', indTot:'Compõe total de produtos', vFrete:'Frete', vSeg:'Seguro', vDesc:'Desconto', vOutro:'Outras despesas', infAdProd:'Informações do item', orig:'Origem da mercadoria', CST:'CST', CSOSN:'CSOSN', modBC:'Modalidade da base de ICMS', vBC:'Base de cálculo', pICMS:'Alíquota ICMS (%)', vICMS:'Valor ICMS', pRedBC:'Redução da base (%)', vICMSDeson:'ICMS desonerado', motDesICMS:'Motivo da desoneração', indDeduzDeson:'Deduz desoneração do total', modBCST:'Modalidade da base ST', pMVAST:'Margem de valor agregado ST (%)', pRedBCST:'Redução da base ST (%)', vBCST:'Base de cálculo ST', pICMSST:'Alíquota ICMS ST (%)', vICMSST:'Valor ICMS ST', vBCSTRet:'Base ST retida', vICMSSTRet:'ICMS ST retido', pCredSN:'Crédito Simples Nacional (%)', vCredICMSSN:'Crédito de ICMS Simples Nacional', vFCP:'Valor FCP', vBCFCP:'Base FCP', pFCP:'Alíquota FCP (%)', vFCPST:'Valor FCP ST', vBCFCPST:'Base FCP ST', pFCPST:'Alíquota FCP ST (%)', vFCPSTRet:'FCP ST retido', vST:'ICMS ST total', vII:'Imposto de importação', vIPI:'Valor IPI', vIPIDevol:'IPI devolvido', vPIS:'Valor PIS', vCOFINS:'Valor COFINS', vNF:'Total da nota', vTotTrib:'Tributos aproximados', pPIS:'Alíquota PIS (%)', pCOFINS:'Alíquota COFINS (%)', qBCProd:'Quantidade base tributável', vAliqProd:'Alíquota por unidade', cEnq:'Enquadramento legal do IPI', pIPI:'Alíquota IPI (%)', qUnid:'Quantidade tributada IPI', vUnid:'Valor IPI por unidade', modFrete:'Responsabilidade pelo frete', xEnder:'Endereço', placa:'Placa', RNTC:'Registro transportador', indPag:'Indicador do pagamento', tPag:'Forma de pagamento', xPag:'Descrição do pagamento', vPag:'Valor do pagamento', vTroco:'Troco', infCpl:'Informações complementares', infAdFisco:'Informações ao fisco'
  };
  const fields = words => words.split(' ');
  const IDE = fields('cUF cNF natOp mod serie nNF dhEmi dhSaiEnt tpNF idDest cMunFG tpImp tpEmis cDV tpAmb finNFe indFinal indPres procEmi verProc');
  const ADDRESS = fields('xLgr nro xCpl xBairro cMun xMun UF CEP cPais xPais fone');
  const PRODUCT = fields('cProd cEAN xProd NCM CEST CFOP uCom qCom vUnCom vProd cEANTrib uTrib qTrib vUnTrib vFrete vSeg vDesc vOutro indTot');
  const TOTAL = fields('vBC vICMS vICMSDeson vFCP vBCST vST vFCPST vFCPSTRet vProd vFrete vSeg vDesc vII vIPI vIPIDevol vPIS vCOFINS vOutro vNF vTotTrib');
  const TAX = fields('orig modBC vBC pRedBC pICMS vICMS vICMSDeson motDesICMS indDeduzDeson modBCST pMVAST pRedBCST vBCST pICMSST vICMSST vBCSTRet vICMSSTRet pCredSN vCredICMSSN vBCFCP pFCP vFCP vBCFCPST pFCPST vFCPST');
  const required = new Set(['key',...fields('cUF cNF mod serie nNF dhEmi tpNF tpEmis cDV').map(k=>'groups.ide.'+k),'groups.emit.xNome','groups.dest.xNome',...fields('vNF vProd vBC vICMS').map(k=>'groups.total.ICMSTot.'+k)]);
  const normalizePath = value => String(value).replace(/\[(\d+)\]/g,'.$1').replaceAll('/','.').replace(/^invoice\./,'').replace(/^\./,'');
  function decimal(value) {
    const s = String(value).trim();
    if (!s) return '';
    // XML-style dots are decimal; a comma explicitly marks Brazilian notation.
    if (/^\d{1,3}(\.\d{3})+,\d+$/.test(s)) return s.replaceAll('.','').replace(',','.');
    if (/^\d+,\d+$/.test(s)) return s.replace(',','.');
    if (/^\d+(\.\d+)?$/.test(s)) return s;
    throw new Error('Use um número não negativo, como 0,00 ou 1234.56.');
  }
  const at = (obj,path) => path.split('.').reduce((x,k)=>x == null ? undefined : x[k],obj);
  function put(obj,path,value) {
    const parts=path.split('.'); if(parts.some(p=>['__proto__','prototype','constructor'].includes(p)))throw new Error('Caminho de campo inválido.'); const end=parts.pop(); let node=obj;
    for (let i=0;i<parts.length;i++) { const k=parts[i]; if (!node[k] || typeof node[k]!=='object') node[k]=/^\d+$/.test(parts[i+1] || end)?[]:{}; node=node[k]; }
    if (value==='') delete node[end]; else node[end]=value;
  }
  function el(tag,attrs={},text) {
    const n=document.createElement(tag);
    for(const [k,v] of Object.entries(attrs)) { if(k==='class') n.className=v; else n.setAttribute(k,String(v)); }
    if(text!==undefined)n.textContent=text;
    return n;
  }
  let sequence=0;
  function mount(container,payload,onSave,options={}) {
    if (!container || typeof onSave!=='function') throw new Error('Contêiner e função de salvamento são obrigatórios.');
    let invoice=clone(payload.invoice || {key:'',groups:{}}); invoice.groups ||= {};
    if (invoice.groups.det && !Array.isArray(invoice.groups.det)) invoice.groups.det=[invoice.groups.det];
    if (invoice.groups.pag?.detPag && !Array.isArray(invoice.groups.pag.detPag)) invoice.groups.pag.detPag=[invoice.groups.pag.detPag];
    const missing=new Set((payload.missing||[]).map(x=>normalizePath(typeof x==='string'?x:x.path||x.field||'')));
    const prefix='invoice-review-'+(++sequence), form=el('form',{class:'invoice-review',novalidate:''});
    let active=0, controls=[], panels=[], tabs=[], status, busy=false;
    const cache={};
    function lockEditor() {
      const elements=[...form.querySelectorAll('input,select,textarea,button')];
      const states=elements.map(element=>element.disabled);
      elements.forEach(element=>{element.disabled=true});
      form.setAttribute('aria-busy','true');
      return ()=>{elements.forEach((element,index)=>{element.disabled=states[index]});form.removeAttribute('aria-busy')};
    }
    function isMissing(path) {return missing.has(path)||missing.has(path.replace(/^groups\./,''));}
    function isRequired(path) {
      if(required.has(path))return true;
      if (/^groups\.ide\./.test(path) && !path.endsWith('.dhSaiEnt')) return IDE.includes(path.split('.').at(-1));
      if (/^groups\.(emit|dest)\.ender(Emit|Dest)\.(xLgr|nro|xBairro|cMun|xMun|UF)$/.test(path))return true;
      if (/^groups\.(emit\.(IE|CRT)|dest\.indIEDest)$/.test(path))return true;
      if (/^groups\.total\.ICMSTot\./.test(path))return !path.endsWith('.vTotTrib');
      if (path==='groups.transp.modFrete'||/^groups\.pag\.detPag\.\d+\.(tPag|vPag)$/.test(path))return true;
      if (/^groups\.det\.\d+\.prod\.(cEAN|cEANTrib|uTrib|qTrib|vUnTrib|indTot)$/.test(path))return true;
      if (/\.imposto\.ICMS\.[^.]+\.orig$/.test(path))return true;
      if (/\.imposto\.ICMS\.(ICMS00|ICMS10|ICMS20|ICMS51|ICMS70|ICMS90|ICMSSN900)\.(modBC|vBC|pICMS|vICMS)$/.test(path))return true;
      if (/\.imposto\.ICMS\.(ICMS10|ICMS30|ICMS70|ICMSSN201|ICMSSN202)\.(modBCST|vBCST|pICMSST|vICMSST)$/.test(path))return true;
      if (/\.imposto\.ICMS\.(ICMSSN101|ICMSSN201)\.(pCredSN|vCredICMSSN)$/.test(path))return true;
      if (/\.imposto\.(PIS|COFINS)\.[^.]+\.CST$/.test(path))return true;
      if (/\.imposto\.(PIS|COFINS)\.[^.]+Aliq\.(vBC|pPIS|vPIS|pCOFINS|vCOFINS)$/.test(path))return true;
      if (/\.imposto\.(PIS|COFINS)\.[^.]+Qtde\.(qBCProd|vAliqProd|vPIS|vCOFINS)$/.test(path))return true;
      if (/^groups\.det\.\d+\.prod\.(cProd|xProd|NCM|CFOP|uCom|qCom|vUnCom|vProd)$/.test(path))return true;
      if (/\.imposto\.ICMS\.[^.]+\.(CST|CSOSN)$/.test(path))return true;
      if (/\.imposto\.ICMS\.(ICMS00|ICMS10|ICMS20|ICMS70|ICMSPart)\.(vBC|pICMS|vICMS)$/.test(path))return true;
      return /\.imposto\.ICMS\.(ICMS20|ICMS70)\.pRedBC$/.test(path);
    }
    function field(parent,path,options={}) {
      const key=path.split('.').at(-1), id=prefix+'-field-'+controls.length;
      const value=at(invoice,path); const needed=isRequired(path);
      const wrapper=el('div',{class:'review-field'+(isMissing(path)?' review-source-missing':'')});
      const label=el('label',{for:id},(options.label||labels[key]||key)+(needed?' *':''));
      let input;
      if(options.choices) {
        input=el('select',{id,name:path}); input.append(el('option',{value:''},'Selecione conforme a nota'));
        const choices=options.choices.map(x=>Array.isArray(x)?x:[x,x]);
        if(value!==undefined && !choices.some(x=>String(x[0])===String(value)))choices.push([value,value+' — valor existente']);
        choices.forEach(([v,t])=>input.append(el('option',{value:v},t)));
      } else input=el(options.long?'textarea':'input',{id,name:path,...(options.long?{rows:3}:{type:'text'})});
      input.value=value??''; if(needed)input.required=true;
      if(DECIMAL.test(key)) input.inputMode='decimal';
      const help=el('small',{id:id+'-help'},isMissing(path)?'Não confirmado na extração. Confira no documento original.':needed?'Obrigatório para validar este perfil de importação. Copie da nota.':'Preencha somente se esta informação constar na nota.');
      input.setAttribute('aria-describedby',help.id); input.title=help.textContent;
      input.addEventListener('input',()=>{put(invoice,path,input.value);input.setCustomValidity('');input.removeAttribute('aria-invalid');});
      input.addEventListener('change',()=>{put(invoice,path,input.value);if(options.change)options.change(input.value);});
      wrapper.append(label,input,help);
      const evidence=payload.evidence?.[path]||payload.evidence?.[path.replace(/^groups\./,'')];
      if(evidence){const note=el('small',{class:'review-evidence'},'Origem: '+[evidence.source,evidence.line?'linha '+evidence.line:'',evidence.excerpt].filter(Boolean).join(' · '));wrapper.append(note);}
      parent.append(wrapper);controls.push({input,path,panel:panels.length-1,wrapper});return input;
    }
    function group(parent,title,path,names) {
      const box=el('fieldset',{class:'review-group'});box.append(el('legend',{},title));const grid=el('div',{class:'review-grid'});box.append(grid);parent.append(box);
      const actual=at(invoice,path); const all=[...new Set([...names,...Object.keys(actual||{}).filter(k=>typeof actual[k]!=='object'&&!k.startsWith('@')&&!['__proto__','prototype','constructor'].includes(k))])];
      all.filter(k=>!controls.some(c=>c.path===path+'.'+k)).forEach(k=>field(grid,path+'.'+k,{long:['infCpl','infAdFisco','infAdProd','xProd'].includes(k)}));return box;
    }
    function choose(parent,label,current,choices,onChange) {
      const id=prefix+'-selector-'+form.querySelectorAll('select').length;
      const box=el('div',{class:'review-field'}),select=el('select',{id});box.append(el('label',{for:id},label),select);
      select.append(el('option',{value:''},'Selecione conforme a nota'));
      [...new Set([...choices,...(current?[current]:[])])].forEach(v=>select.append(el('option',{value:v},v)));
      select.value=current||'';select.addEventListener('change',()=>onChange(select.value));parent.append(box);return select;
    }
    function taxItem(parent,item,index) {
      const base='groups.det.'+index+'.imposto',box=el('fieldset',{class:'review-item'});box.append(el('legend',{},'Tributos do item '+(index+1)+' — '+(item.prod?.xProd||'Descrição pendente')));parent.append(box);
      const icms=item.imposto?.ICMS||{},kind=Object.keys(icms)[0];
      choose(box,'Modalidade ICMS (obrigatória)',kind,ICMS,next=>{
        if(kind)cache[index+':'+kind]=clone(icms[kind]);
        if(next) put(invoice,base+'.ICMS',{[next]:clone(cache[index+':'+next]||{})}); else put(invoice,base+'.ICMS','');
        render();
      });
      if(kind) {
        const grid=el('div',{class:'review-grid'});box.append(grid);
        const code=kind.startsWith('ICMSSN')?'CSOSN':'CST';field(grid,base+'.ICMS.'+kind+'.'+code,{choices:CST[kind]||[]});
        group(box,'Valores ICMS',base+'.ICMS.'+kind,TAX);
      } else box.append(el('p',{class:'review-warning'},'Informe a modalidade existente na nota. Não será definida automaticamente.'));
      for (const [name,kinds,names] of [['PIS',['PISAliq','PISQtde','PISNT','PISOutr'],fields('CST vBC pPIS qBCProd vAliqProd vPIS')],['COFINS',['COFINSAliq','COFINSQtde','COFINSNT','COFINSOutr'],fields('CST vBC pCOFINS qBCProd vAliqProd vCOFINS')],['IPI',['IPITrib','IPINT'],fields('CST vBC pIPI qUnid vUnid vIPI')]]) {
        const detail=el('details',{class:'review-optional'}),summary=el('summary',{},name+' — conforme a nota');detail.append(summary);box.append(detail);
        const tax=at(invoice,base+'.'+name)||{},old=Object.keys(tax).find(k=>kinds.includes(k))||Object.keys(tax).find(k=>tax[k]&&typeof tax[k]==='object');
        if(old)detail.open=true;
        choose(detail,'Modalidade '+name,old,kinds,next=>{
          if(old)cache[index+':'+old]=clone(tax[old]);
          if(old)put(invoice,base+'.'+name+'.'+old,'');
          if(next)put(invoice,base+'.'+name+'.'+next,clone(cache[index+':'+next]||{}));
          if(!next&&Object.keys(at(invoice,base+'.'+name)||{}).length===0)put(invoice,base+'.'+name,'');
          render();
        });
        if(name==='IPI'&&old) group(detail,'Enquadramento IPI',base+'.IPI',['cEnq']);
        if(old)group(detail,name,base+'.'+name+'.'+old,names);
      }
    }
    function selectTab(index,focus=false) {
      active=index;panels.forEach((p,i)=>{p.hidden=i!==index;tabs[i].setAttribute('aria-selected',String(i===index));tabs[i].tabIndex=i===index?0:-1;});if(focus)tabs[index].focus();
    }
    function render() {
      form.replaceChildren();controls=[];panels=[];tabs=[];
      form.append(el('h3',{},'Conferência dos dados da nota'),el('p',{class:'review-intro'},'Confira os campos com o documento original. Campos vazios não viram zero. Salvar envia os dados à validação do servidor; não comprova autorização fiscal ou importação no sistema de destino.'));
      if(payload.conflicts?.length)form.append(el('p',{class:'review-warning'},'A extração detectou divergências. Confira no PDF antes de gerar o XML: '+JSON.stringify(payload.conflicts)));
      if(missing.size)form.append(el('p',{class:'review-warning'},missing.size+' campo(s) foram sinalizados na extração. A indicação permanece como referência da origem até nova análise.'));
      const nav=el('div',{class:'review-tabs',role:'tablist','aria-label':'Seções da nota'});form.append(nav);
      ['Identificação','Participantes','Itens','Tributos','Transporte e pagamento'].forEach((name,i)=>{
        const tab=el('button',{type:'button',role:'tab',id:prefix+'-tab-'+i,'aria-controls':prefix+'-panel-'+i},name);
        const panel=el('section',{class:'review-panel',role:'tabpanel',id:prefix+'-panel-'+i,'aria-labelledby':tab.id});
        tab.addEventListener('click',()=>selectTab(i));tab.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(i+1)%5;if(event.key==='ArrowLeft')next=(i+4)%5;if(event.key==='Home')next=0;if(event.key==='End')next=4;if(next!==undefined){event.preventDefault();selectTab(next,true);}});
        tabs.push(tab);panels.push(panel);nav.append(tab);form.append(panel);
      });
      field(panels[0],'key');group(panels[0],'Identificação fiscal','groups.ide',IDE);group(panels[0],'Informações adicionais','groups.infAdic',['infCpl','infAdFisco']);
      for(const [name,title] of [['emit','Emitente'],['dest','Destinatário']]) {
        group(panels[1],title,'groups.'+name,fields(name==='emit'?'CNPJ CPF xNome xFant IE IEST IM CNAE CRT':'CNPJ CPF xNome indIEDest IE email'));
        panels[1].append(el('p',{class:'review-hint'},title+': informe exatamente um documento (CNPJ ou CPF). Preserve a identidade real da nota.'));
        group(panels[1],'Endereço do '+title.toLowerCase(),'groups.'+name+'.ender'+(name==='emit'?'Emit':'Dest'),ADDRESS);
      }
      const items=invoice.groups.det||[];
      if(!items.length)panels[2].append(el('p',{class:'review-warning'},'Nenhum item confirmado. Adicione os produtos do documento para prosseguir.'));
      items.forEach((item,i)=>{
        const box=group(panels[2],'Item '+(i+1),'groups.det.'+i+'.prod',PRODUCT);field(box,'groups.det.'+i+'.infAdProd',{long:true});
        const remove=el('button',{type:'button',class:'review-remove'},'Remover item '+(i+1));remove.addEventListener('click',()=>{invoice.groups.det.splice(i,1);invoice.groups.det.forEach((x,n)=>x['@nItem']=String(n+1));for(const k of Object.keys(cache))delete cache[k];render();});box.append(remove);taxItem(panels[3],item,i);
      });
      const add=el('button',{type:'button',class:'review-add'},'Adicionar item da nota');add.addEventListener('click',()=>{invoice.groups.det ||= [];invoice.groups.det.push({'@nItem':String(invoice.groups.det.length+1),prod:{},imposto:{}});render();});panels[2].append(add);
      group(panels[3],'Totais da nota','groups.total.ICMSTot',TOTAL);
      group(panels[4],'Frete','groups.transp',['modFrete']);group(panels[4],'Transportador','groups.transp.transporta',fields('CNPJ CPF xNome IE xEnder xMun UF'));group(panels[4],'Veículo','groups.transp.veicTransp',fields('placa UF RNTC'));
      (invoice.groups.pag?.detPag||[]).forEach((payment,i)=>{
        const box=group(panels[4],'Pagamento '+(i+1),'groups.pag.detPag.'+i,fields('indPag tPag xPag vPag'));
        const remove=el('button',{type:'button',class:'review-remove'},'Remover pagamento');remove.addEventListener('click',()=>{invoice.groups.pag.detPag.splice(i,1);render();});box.append(remove);
      });
      const addPay=el('button',{type:'button',class:'review-add'},'Adicionar pagamento da nota');addPay.addEventListener('click',()=>{invoice.groups.pag ||= {};invoice.groups.pag.detPag ||= [];invoice.groups.pag.detPag.push({});render();});panels[4].append(addPay);group(panels[4],'Troco','groups.pag',['vTroco']);
      status=el('div',{class:'review-status',role:'status','aria-live':'polite'});form.append(status);
      if(typeof options.onDraft==='function') {
        const draft=el('button',{type:'button',class:'review-draft'},'Salvar rascunho para continuar depois');draft.disabled=busy;
        draft.addEventListener('click',async()=>{if(busy)return;busy=true;const unlock=lockEditor();try{await options.onDraft(clone(invoice));showMessage('Rascunho salvo. A validação fiscal ainda está pendente.');}catch(error){showMessage(error?.message||'Não foi possível salvar o rascunho.',true);}finally{busy=false;unlock();}});form.append(draft);
      }
      const confirmLabel=el('label',{class:'review-confirm'});const confirmBox=el('input',{type:'checkbox',required:true});confirmBox.name='confirm_review';confirmLabel.append(confirmBox,document.createTextNode('Conferi os dados com o PDF, inclusive os campos completados manualmente.'));form.append(confirmLabel);
      const submit=el('button',{type:'submit',class:'review-save'},'Validar e salvar dados conferidos');submit.disabled=busy;form.append(submit);selectTab(active);
      // Map each control to its actual panel after all sections have been mounted.
      controls.forEach(c=>{c.panel=panels.indexOf(c.input.closest('[role="tabpanel"]'));});
    }
    function showMessage(message,isError=false) {status.textContent=message;status.className='review-status'+(isError?' review-warning':'');}
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy)return;
      if(!form.elements.confirm_review.checked){showMessage('Confirme a conferência dos dados com o PDF.',true);form.elements.confirm_review.focus();return;}
      let first=null;const errors=[];
      for(const c of controls) {
        c.input.setCustomValidity('');let message='';const value=c.input.value.trim();
        if(c.input.required&&!value)message='Preencha este campo com a informação da nota.';
        else if(value&&DECIMAL.test(c.path.split('.').at(-1))) {try{const normalized=decimal(value);put(invoice,c.path,normalized);c.input.value=normalized;}catch(error){message=error.message;}}
        if(c.path==='key'&&value&&!/^\d{44}$/.test(value))message='A chave precisa ter 44 dígitos.';
        if(/\.dh(Emi|SaiEnt)$/.test(c.path)&&value&&!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)$/.test(value))message='Use a data da nota com horário e fuso, no formato AAAA-MM-DDTHH:MM:SS-03:00.';
        if(message){c.input.setCustomValidity(message);c.input.setAttribute('aria-invalid','true');first ||= c;errors.push((labels[c.path.split('.').at(-1)]||c.path)+': '+message);}else c.input.removeAttribute('aria-invalid');
      }
      for(const name of ['emit','dest']) {
        const party=invoice.groups[name]||{};if(Boolean(party.CNPJ)===Boolean(party.CPF)){errors.push((name==='emit'?'Emitente':'Destinatário')+': informe exatamente um CNPJ ou CPF.');first ||= controls.find(c=>c.path==='groups.'+name+'.CNPJ');}
      }
      if(!invoice.groups.pag?.detPag?.length){errors.push('Adicione o pagamento conforme a nota.');if(!first)selectTab(4);}
      for(const [i,item] of (invoice.groups.det||[]).entries())for(const name of ['PIS','COFINS']) {if(Object.keys(item.imposto?.[name]||{}).length!==1){errors.push('Item '+(i+1)+': informe a modalidade '+name+' da nota.');if(!first)selectTab(3);}}
      if(!invoice.groups.det?.length){errors.push('Adicione ao menos um item da nota.');if(!first)selectTab(2);}
      (invoice.groups.det||[]).forEach((item,i)=>{const kinds=Object.keys(item.imposto?.ICMS||{});if(kinds.length!==1||!ICMS.includes(kinds[0])){errors.push('Item '+(i+1)+': selecione uma modalidade ICMS suportada.');if(!first)selectTab(3);}else {const kind=kinds[0],code=item.imposto.ICMS[kind][kind.startsWith('ICMSSN')?'CSOSN':'CST'];if(!CST[kind].includes(code)){errors.push('Item '+(i+1)+': CST/CSOSN incompatível com a modalidade ICMS.');if(!first)selectTab(3);}}});
      if(errors.length){showMessage(errors.join('\n'),true);if(first){selectTab(first.panel);first.input.focus();}return;}
      busy=true;const unlock=lockEditor();showMessage('Validando no servidor…');
      try{await onSave(clone(invoice));showMessage('Dados enviados e processados. Confira o resultado da validação antes de exportar.');}
      catch(error){showMessage(error?.message||'Não foi possível salvar. Revise os dados e tente novamente.',true);}
      finally{busy=false;unlock();}
    });
    render();container.replaceChildren(form);form.getInvoice=()=>clone(invoice);form.showMessage=showMessage;return form;
  }
  window.DocProntoReview={mount,normalizeDecimal:decimal};
})();
