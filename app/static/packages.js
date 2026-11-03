'use strict';
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const names={acts:'Акт і протоколи',assignments:'Завдання',cover_letters:'Супровідні',excel:'Excel'};
  const states={draft:'У роботі',ready:'Перевірено',archived:'В архіві'};
  const kinds={source:'Оригінал',excel_template:'Вихідна книга',attachment:'Додаток',output_word:'Word',output_pdf:'PDF',output_excel:'Excel'};
  const date=value=>new Date(value).toLocaleString('uk-UA',{timeZone:'Europe/Kyiv',dateStyle:'short',timeStyle:'short'});
  const size=value=>value<1048576?Math.max(1,Math.round(value/1024))+' КБ':(value/1048576).toFixed(1)+' МБ';
  const view=document.createElement('section');view.id='packages-view';view.hidden=true;
  view.innerHTML=`<div class="page-heading"><div><h1>Пакети документів</h1><p class="subtitle">Акт, завдання, протоколи й супровідні — у спільній справі з оригіналами та створеними файлами.</p></div><button type="button" class="button primary" data-package-new>Новий пакет</button></div><p class="notice warning" data-package-error role="alert" hidden></p><div data-package-list-view><div class="package-search"><label class="search-box"><svg class="icon"><use href="#i-search"/></svg><input type="search" data-package-search aria-label="Пошук пакетів" placeholder="Назва, замовник, номер акта або примітка"></label><label class="field"><span class="sr-only">Стан пакета</span><select data-package-filter aria-label="Стан пакета"><option value="">Усі стани</option><option value="draft">У роботі</option><option value="ready">Перевірені</option><option value="archived">Архів</option></select></label></div><div data-packages-list></div></div><section data-package-detail hidden></section>`;
  document.querySelector('.main-content').append(view);const $=selector=>view.querySelector(selector);
  const bar=document.createElement('section');bar.className='package-context';bar.hidden=true;
  bar.innerHTML='<div><small>Поточний пакет</small><strong data-context-title></strong></div><button type="button" class="button secondary" data-context-open>Документи пакета</button><button type="button" class="text-button" data-context-leave>Поза пакетом</button>';
  document.querySelector('.main-content').prepend(bar);
  let selected='',detail=null,report=null,listRequest=0,detailRequest=0,queryTimer=null,working=false;
  async function api(url,options){const response=await fetch(url,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Не вдалося відкрити пакети документів.');return data;}
  const send=(url,data,method='POST')=>api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  function error(value){$('[data-package-error]').textContent=value?.message||String(value);$('[data-package-error]').hidden=false;}
  function navigate(){document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'packages'}}));}
  function context(){const value=window.OZWorkspace.package;bar.hidden=!value;if(value)bar.querySelector('[data-context-title]').textContent=value.title;}
  async function listing(){
    const generation=++listRequest;
    try{
      const params=new URLSearchParams({q:$('[data-package-search]').value,status:$('[data-package-filter]').value});
      const data=await api('/api/packages?'+params);if(generation!==listRequest)return;
      $('[data-packages-list]').innerHTML=data.packages.length?`<div class="package-table-wrap"><table class="package-table"><thead><tr><th>Пакет</th><th>Замовник / акт</th><th>Документи</th><th>Стан</th><th>Оновлено</th><th><span class="sr-only">Дії</span></th></tr></thead><tbody>${data.packages.map(value=>`<tr><td><button type="button" class="text-button package-title-link" data-package-select="${esc(value.id)}">${esc(value.title)}</button>${value.id===window.OZWorkspace.scope?'<small>Відкритий у редакторі</small>':''}</td><td>${esc(value.customer||'—')}<small>${esc(value.reference)}</small></td><td>${value.files} файлів<small>${value.sections.map(name=>esc(names[name])).join(' · ')}</small></td><td><span class="package-state ${value.status}">${states[value.status]}</span></td><td>${esc(date(value.updated_at))}</td><td><button type="button" class="button secondary" data-package-select="${esc(value.id)}">Відкрити</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="panel package-empty"><h2>Пакетів поки немає</h2><p>Створіть пакет із поточної роботи або почніть порожню справу. Оригінали та результати зберігатимуться на цьому комп’ютері.</p><button type="button" class="button primary" data-package-new>Створити пакет</button></div>';
    }catch(exc){error(exc);}
  }
  function renderFiles(){
    const showOld=$('[data-package-show-old]')?.checked;
    const files=detail.files.filter(file=>file.active||showOld);
    $('[data-package-files]').innerHTML=files.length?files.map(file=>`<div class="package-file"><svg class="icon"><use href="#i-file"/></svg><div><strong>${esc(file.filename)}</strong><small>${esc(names[file.section])} · ${esc(kinds[file.kind])} · ${size(file.size)} · ${esc(date(file.created_at))}${file.active?'':' · Попередній файл'}</small></div><a class="button secondary" href="/api/workbench/files/${file.id}?scope=${selected}" download>Завантажити</a></div>`).join(''):'<p class="empty-note">Файли з’являться після читання оригіналів або створення документів у редакторі. Можна додати їх і вручну.</p>';
  }
  function renderDetail(){
    const value=detail.package;report=null;
    $('[data-package-detail]').innerHTML=`<button type="button" class="text-button" data-package-back>← Усі пакети</button><div class="package-heading"><div><h2>${esc(value.title)}</h2><p>${esc(value.customer||'Замовника не зазначено')}${value.reference?' · '+esc(value.reference):''}</p></div><span class="package-state ${value.status}">${states[value.status]}</span></div>${value.notes?`<p class="package-notes">${esc(value.notes)}</p>`:''}<div class="package-actions"><button type="button" class="button primary" data-package-work>${value.status==='archived'?'Повернути в роботу':'Продовжити роботу'}</button><button type="button" class="button secondary" data-package-check>Перевірити комплект</button><button type="button" class="button secondary" data-package-download>Завантажити пакет ZIP</button><button type="button" class="button quiet" data-package-edit>Відомості</button><button type="button" class="text-button" data-package-archive ${value.status==='archived'?'hidden':''}>До архіву</button></div><section class="panel package-check" data-package-check-result hidden></section><section class="panel package-documents"><div class="panel-heading"><h2>Файли пакета</h2><label class="confirmation"><input type="checkbox" data-package-show-old><span>Попередні файли</span></label></div><div data-package-files></div><details class="package-add-files"><summary>Додати файли вручну</summary><form data-package-upload><label class="field"><span>Документи</span><input type="file" multiple data-package-upload-files accept=".docx,.pdf,.zip,.xlsx,.png,.jpg,.jpeg,.tif,.tiff,.txt,.csv"></label><label class="field"><span>Розділ</span><select data-package-upload-section>${Object.entries(names).map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></label><label class="field"><span>Призначення</span><select data-package-upload-kind><option value="source">Вихідні файли</option><option value="attachment">Додаткові документи</option></select></label><button type="submit" class="button secondary" ${value.status==='archived'?'disabled':''}>Додати до пакета</button></form></details></section>`;
    renderFiles();
  }
  async function showDetail(id){
    const generation=++detailRequest,value=await api('/api/packages/'+id);if(generation!==detailRequest)return;selected=id;detail=value;
    window.OZWorkspace.updatePackage(value.package);
    $('[data-package-list-view]').hidden=true;$('[data-package-detail]').hidden=false;$('[data-package-error]').hidden=true;renderDetail();
  }
  async function openWork(id){
    if(working)return;working=true;
    const buttons=[...view.querySelectorAll('[data-package-work]')];buttons.forEach(button=>{button.disabled=true;button.dataset.caption=button.textContent;button.textContent='Відкриваємо…';});
    try{
      const value=await api('/api/packages/'+id);
      if(value.package.status==='archived')value.package=await send('/api/packages/'+id,{...metadata(value.package),status:'draft',expected_revision:value.package.revision},'PATCH');
      await window.OZWorkspace.activateScope(id);context();
      const section=['acts','assignments','cover_letters','excel'].find(name=>value.drafts.sections[name])||'acts';
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:section}}));
    }finally{working=false;buttons.forEach(button=>{button.disabled=false;button.textContent=button.dataset.caption;});}
  }
  function metadata(value){return Object.fromEntries(['title','customer','reference','notes'].map(key=>[key,value[key]]));}
  function issueGroups(issues){
    return [['error','Помилки'],['warning','Зауваження']].map(([level,title])=>{
      const rows=issues.filter(issue=>issue.level===level);if(!rows.length)return '';
      const open=level==='error'||rows.length<=5&&!issues.some(issue=>issue.level==='error');
      return `<details class="package-issue-group" ${open?'open':''}><summary>${title}: ${rows.length}</summary><ul class="package-issues">${rows.map(issue=>`<li class="${level==='error'?'danger-text':''}"><span>${esc(issue.message)}</span><button type="button" class="text-button" data-package-go="${issue.section}">До розділу</button></li>`).join('')}</ul></details>`;
    }).join('');
  }
  async function check(){
    const id=selected,generation=detailRequest;
    if(id===window.OZWorkspace.scope)await window.OZWorkspace.flush();
    if(id!==selected||generation!==detailRequest)return;
    const button=$('[data-package-check]');button.disabled=true;button.textContent='Зіставляємо документи…';
    try{
      const result=await api('/api/packages/'+id+'/check');if(id!==selected||generation!==detailRequest)return;report=result;
      const blocked=report.errors||report.issues.some(issue=>['no-output','missing-output','outdated-output'].includes(issue.code));
      const box=$('[data-package-check-result]');box.hidden=false;
      box.innerHTML=`<h2>Узгодженість комплекту</h2><p>${report.errors} помилок · ${report.warnings} зауважень · Протоколів: ${report.protocols} · Листів: ${report.letters}</p>${report.issues.length?issueGroups(report.issues):'<p class="notice success">Розбіжностей у реквізитах не знайдено.</p>'}<div class="package-verify"><label class="confirmation"><input type="checkbox" data-package-reviewed ${blocked?'disabled':''}><span>Звірено комплект документів та наведені зауваження</span></label><button type="button" class="button secondary" data-package-ready disabled>Позначити перевіреним</button></div>`;
    }finally{button.disabled=false;button.textContent='Перевірити комплект';}
  }
  const dialog=document.createElement('dialog');dialog.className='modal package-dialog';dialog.id='package-form';dialog.setAttribute('aria-labelledby','package-form-title');
  dialog.innerHTML=`<form><div class="modal-heading"><h2 id="package-form-title">Новий пакет</h2><button type="button" class="icon-button" data-package-form-close aria-label="Закрити">×</button></div><div class="modal-body"><label class="field"><span>Назва пакета *</span><input name="title" required maxlength="200" placeholder="Наприклад, акт №125 від 03.11.2026"></label><label class="field"><span>Замовник</span><input name="customer" maxlength="1000"></label><label class="field"><span>Номер акта або справи</span><input name="reference" maxlength="200"></label><label class="field"><span>Примітка</span><textarea name="notes" rows="3" maxlength="5000"></textarea></label><label class="confirmation" data-package-copy-label><input type="checkbox" name="copy" checked><span>Додати поточну роботу та вихідні файли</span></label><p class="error-text" data-package-form-error role="alert" hidden></p></div><div class="modal-footer"><button type="button" class="button secondary" data-package-form-close>Скасувати</button><button type="submit" class="button primary" data-package-form-save>Створити й відкрити</button></div></form>`;
  document.body.append(dialog);const form=dialog.querySelector('form');let editing=false,editingId='',editingPackage=null,formBusy=false;
  function openForm(edit=false){
    if(formBusy)throw new Error('Дочекайтеся завершення збереження пакета.');
    if(!edit&&(window.OZWorkspace.busy||working))throw new Error('Дочекайтеся завершення або скасуйте поточну обробку.');
    editing=edit;editingId=edit?selected:'';editingPackage=edit?{...detail.package}:null;form.reset();form.querySelector('[data-package-form-error]').hidden=true;
    dialog.querySelector('#package-form-title').textContent=edit?'Відомості про пакет':'Новий пакет';
    form.querySelector('[data-package-form-save]').textContent=edit?'Зберегти':'Створити й відкрити';
    form.querySelector('[data-package-copy-label]').hidden=edit||window.OZWorkspace.scope!=='workspace';
    if(edit)for(const [key,value] of Object.entries(metadata(detail.package)))form.elements.namedItem(key).value=value;
    else{
      const current=window.OZWorkspace.snapshot('acts');
      form.elements.customer.value=current.common?.customer||window.OZWorkspace.package?.customer||'';
      form.elements.reference.value=current.common?.act_number||'';
      form.elements.copy.checked=window.OZWorkspace.scope==='workspace'&&window.OZWorkspace.hasData;
    }
    dialog.showModal();form.elements.title.focus();
  }
  form.querySelectorAll('[data-package-form-close]').forEach(button=>button.onclick=()=>{if(!formBusy)dialog.close();});
  dialog.addEventListener('cancel',event=>{if(formBusy)event.preventDefault();});
  form.onsubmit=async event=>{
    event.preventDefault();if(formBusy)return;formBusy=true;const button=form.querySelector('[type=submit]');form.querySelectorAll('button,input,textarea').forEach(field=>field.disabled=true);
    const values=Object.fromEntries(['title','customer','reference','notes'].map(key=>[key,form.elements.namedItem(key).value]));
    try{
      if(editing){
        const current=editingPackage,id=editingId;
        const checked=current.status==='ready'?await api('/api/packages/'+id+'/check'):null;
        await send('/api/packages/'+id,{...values,status:current.status,expected_revision:current.revision,check_fingerprint:checked?.fingerprint||''},'PATCH');
        dialog.close();if(selected===id)await showDetail(id);
      }else{
        const copy=form.elements.copy.checked;
        const drafts=copy?await window.OZWorkspace.bundle():{};
        const value=await send('/api/packages',{...values,drafts,copy_scope:copy?window.OZWorkspace.scope:null});
        dialog.close();await openWork(value.id);
      }
    }catch(exc){if(dialog.open){const message=form.querySelector('[data-package-form-error]');message.textContent=exc.message;message.hidden=false;}else error(exc);}
    finally{formBusy=false;form.querySelectorAll('button,input,textarea').forEach(field=>field.disabled=false);}
  };
  view.addEventListener('click',async event=>{
    try{
      const target=event.target.closest('button');if(!target)return;
      const id=selected,packageValue=detail?.package,checkedReport=report;
      if(target.matches('[data-package-new]'))openForm();
      if(target.matches('[data-package-select]'))await showDetail(target.dataset.packageSelect);
      if(target.matches('[data-package-back]')){detailRequest++;selected='';detail=null;$('[data-package-list-view]').hidden=false;$('[data-package-detail]').hidden=true;await listing();}
      if(target.matches('[data-package-work]'))await openWork(id);
      if(target.matches('[data-package-edit]'))openForm(true);
      if(target.matches('[data-package-check]'))await check();
      if(target.matches('[data-package-go]')){const section=target.dataset.packageGo;await openWork(id);document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:section}}));}
      if(target.matches('[data-package-ready]')){
        if(!checkedReport)throw new Error('Повторіть перевірку комплекту.');
        const current=(await api('/api/packages/'+id)).package;
        await send('/api/packages/'+id,{...metadata(current),status:'ready',expected_revision:current.revision,check_fingerprint:checkedReport.fingerprint},'PATCH');if(selected===id)await showDetail(id);
      }
      if(target.matches('[data-package-archive]')){
        if(window.OZWorkspace.scope===id)await window.OZWorkspace.activateScope('workspace');
        const current=(await api('/api/packages/'+id)).package;
        await send('/api/packages/'+id,{...metadata(current),status:'archived',expected_revision:current.revision},'PATCH');if(selected===id)await showDetail(id);context();
      }
      if(target.matches('[data-package-download]')){
        target.disabled=true;try{
          if(window.OZWorkspace.scope===id)await window.OZWorkspace.flush();
          const response=await fetch('/api/packages/'+id+'/download');if(!response.ok)throw new Error('Не вдалося зібрати ZIP пакета. Збережіть технічний звіт.');
          window.OZOffice.download(await response.blob(),packageValue.title.replace(/[\\/:*?"<>|]/g,'_')+'.zip');
        }finally{target.disabled=false;}
      }
    }catch(exc){error(exc);}
  });
  view.addEventListener('change',event=>{
    if(event.target.matches('[data-package-filter]'))listing();
    if(event.target.matches('[data-package-show-old]'))renderFiles();
    if(event.target.matches('[data-package-reviewed]'))$('[data-package-ready]').disabled=!event.target.checked;
  });
  view.addEventListener('submit',async event=>{
    if(!event.target.matches('[data-package-upload]'))return;event.preventDefault();
    const files=[...$('[data-package-upload-files]').files],button=event.target.querySelector('[type=submit]');if(!files.length)return;
    button.disabled=true;
    try{
      if(files.some(file=>file.size>25*1024*1024)||files.reduce((sum,file)=>sum+file.size,0)>100*1024*1024)throw new Error('До 25 МБ на файл і 100 МБ на комплект.');
      await window.OZWorkspace.retainFiles(files,$('[data-package-upload-section]').value,$('[data-package-upload-kind]').value,{scope:selected});await showDetail(selected);
    }catch(exc){error(exc);}finally{button.disabled=false;}
  });
  $('[data-package-search]').oninput=()=>{clearTimeout(queryTimer);queryTimer=setTimeout(listing,250);};
  bar.querySelector('[data-context-open]').onclick=async()=>{navigate();try{await showDetail(window.OZWorkspace.scope);}catch(exc){error(exc);}};
  bar.querySelector('[data-context-leave]').onclick=async()=>{try{await window.OZWorkspace.activateScope('workspace');context();}catch(exc){navigate();error(exc);}};
  document.addEventListener('protokol:package-context',context);
  document.addEventListener('protokol:view',event=>{if(event.detail.name==='packages'){if(selected)showDetail(selected).catch(error);else listing();}});
  document.addEventListener('protokol:workspace-saved',event=>{if(event.detail.scope===selected&&report){report=null;const box=$('[data-package-check-result]');if(box){box.innerHTML='<p>Дані змінилися. Повторіть перевірку комплекту.</p>';box.hidden=false;}}});
  window.OZPackages={async open(){navigate();if(window.OZWorkspace.scope!=='workspace')await showDetail(window.OZWorkspace.scope);else await listing();},create:()=>openForm(),check:async()=>{navigate();if(window.OZWorkspace.scope==='workspace')return openForm();await showDetail(window.OZWorkspace.scope);await check();}};
  window.OZWorkspace.ready.then(context);
})();
