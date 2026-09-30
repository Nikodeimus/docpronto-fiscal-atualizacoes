"use strict";
let historyPreferenceOwner='';
function preferenceKey(company=''){return 'docpronto.ui.v1:'+encodeURIComponent(user?.id||user?.email||'')+':'+encodeURIComponent(company)}
function readPreference(company=''){try{return JSON.parse(localStorage.getItem(preferenceKey(company))||'{}')||{}}catch{return {}}}
function writePreference(value,company=''){try{localStorage.setItem(preferenceKey(company),JSON.stringify(value))}catch{}}
function mountWorkspacePreferences(){
 const compact=readPreference().compact===true;document.body.classList.toggle('compact',compact);
 let button=document.getElementById('compact-view');
 if(!button){button=document.createElement('button');button.id='compact-view';button.type='button';document.querySelector('header').append(button)}
 function label(){button.textContent=document.body.classList.contains('compact')?'Exibição confortável':'Exibição compacta';button.setAttribute('aria-pressed',String(document.body.classList.contains('compact')))}
 button.onclick=()=>{document.body.classList.toggle('compact');writePreference({compact:document.body.classList.contains('compact')});label()};label();historyPreferenceOwner='';restoreHistoryPreferences();
}
function restoreHistoryPreferences(){
 const p=readPreference(cid),month=v=>typeof v==='string'&&/^\d{4}-(0[1-9]|1[0-2])$/.test(v)?v:'';
 historyMonth=month(p.from);historyMonthTo=month(p.to);if(historyMonthTo&&(!historyMonth||historyMonthTo<historyMonth))historyMonthTo='';
 const day=v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&!isNaN(Date.parse(v))?v:'';historyDateFrom=day(p.dayFrom);historyDateTo=day(p.dayTo);if(historyDateTo&&(!historyDateFrom||historyDateTo<historyDateFrom))historyDateTo='';if(historyDateFrom){historyMonth='';historyMonthTo=''}
 historyPending=p.pending===true;historySearch=typeof p.search==='string'?p.search.slice(0,120):'';historyPage=1;historyPreferenceOwner=preferenceKey(cid);
}
function syncHistoryPreferences(){
 if(historyPreferenceOwner!==preferenceKey(cid)){restoreHistoryPreferences();return}
 writePreference({from:historyMonth,to:historyMonthTo,pending:historyPending,search:historySearch,dayFrom:historyDateFrom,dayTo:historyDateTo},cid);
}
function applyHistoryMonth(offset,now=new Date()){
 const month=new Date(now.getFullYear(),now.getMonth()+offset,1);
 historyDateFrom='';historyDateTo='';historyMonth=String(month.getFullYear())+'-'+String(month.getMonth()+1).padStart(2,'0');historyMonthTo='';historyPage=1;action(renderHistory);
}
function bindNoteWorkspace(target,periodQuery,isCurrent){
 $('#history-yesterday').onclick=()=>applyHistoryDays(1,1);$('#history-week').onclick=()=>applyHistoryDays(6,0);
 bindHistoryDayFields();
 $('#history-current-month').onclick=()=>applyHistoryMonth(0);$('#history-previous-month').onclick=()=>applyHistoryMonth(-1);
 $('#history-compare').onclick=()=>{if(isCurrent()&&cid===target)action(()=>openNoteComparison(target,periodQuery))};
 document.querySelectorAll('[data-note-key]').forEach(button=>button.onclick=()=>{if(isCurrent()&&cid===target)action(()=>openNoteDetails(button.dataset.noteKey,target))});
 $('#history-search').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('#history-refresh').click()}};
}

function localDay(date){return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0')}
function applyHistoryDays(fromOffset,toOffset,now=new Date()){
 const from=new Date(now.getFullYear(),now.getMonth(),now.getDate()-fromOffset),to=new Date(now.getFullYear(),now.getMonth(),now.getDate()-toOffset);
 historyDateFrom=localDay(from);historyDateTo=localDay(to);historyMonth='';historyMonthTo='';historyPage=1;action(renderHistory);
}
function applyHistoryDayDraft(){
 const from=$('#history-day-from').value,to=$('#history-day-to').value;
 if(to&&(!from||to<from)){toast('Informe o dia inicial e um final igual ou posterior.');return false}
 historyDateFrom=from;historyDateTo=to;return true;
}
function historyDayDraftMatches(){return (!$('#history-day-from')||$('#history-day-from').value===historyDateFrom)&&(!$('#history-day-to')||$('#history-day-to').value===historyDateTo)}

function bindHistoryDayFields(){
 for(const id of ['#history-day-from','#history-day-to'])$(id).oninput=()=>{$('#history-month').value='';$('#history-month-to').value=''};
 for(const id of ['#history-month','#history-month-to'])$(id).oninput=()=>{$('#history-day-from').value='';$('#history-day-to').value=''};
}

function certificateNoticeKey(item){return JSON.stringify([String(item.company),String(item.valid_until)])}
function unseenCertificateAlerts(items){
 const saved=readPreference('__certificate_alerts__'),seen=Array.isArray(saved.seen)?new Set(saved.seen):new Set();
 return items.filter(item=>!seen.has(certificateNoticeKey(item)));
}
function markCertificateAlertsSeen(items){
 const saved=readPreference('__certificate_alerts__'),seen=new Set(Array.isArray(saved.seen)?saved.seen:[]);
 items.forEach(item=>seen.add(certificateNoticeKey(item)));writePreference({seen:[...seen].slice(-2000)},'__certificate_alerts__');
}
