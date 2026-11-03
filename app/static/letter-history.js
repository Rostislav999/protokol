'use strict';
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const labels={recipients:'Адресат',body:'Текст листа',attachments:'Додатки',position:'Посада',signer:'Підпис',contact:'Контакт'};
  const text=value=>Array.isArray(value)?value.join('\n'):String(value||'');
  const dialog=document.createElement('dialog');dialog.id='letter-history';dialog.className='modal history-dialog';dialog.setAttribute('aria-labelledby','history-title');
  dialog.innerHTML=`<div class="history-frame"><div class="modal-heading"><div><h2 id="history-title">Історія листа</h2><p data-history-letter class="section-description"></p></div><button type="button" class="icon-button" data-history-close aria-label="Закрити історію">×</button></div><div class="history-layout"><aside class="history-aside"><label class="field"><span>Назва контрольної версії</span><input data-history-label maxlength="150" placeholder="Наприклад, погоджений текст"></label><button type="button" class="button secondary" data-history-save>Зберегти версію</button><p class="error-text" data-history-error role="alert" hidden></p><div class="history-list" data-history-list></div></aside><section class="history-preview" data-history-preview><p>Оберіть версію для порівняння з поточним текстом.</p></section></div><div class="modal-footer"><p>Відновлення замінить текст поточного листа. Вихідний номер, дата й підтвердження перевірки будуть очищені.</p><button type="button" class="button secondary" data-history-close>Закрити</button><button type="button" class="button primary" data-history-restore disabled>Відновити текст</button></div></div>`;
  document.body.append(dialog);const $=selector=>dialog.querySelector(selector);
  let scope='',letter=null,chosen=null,requestGeneration=0;
  async function api(url,options){const response=await fetch(url,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Історія листа недоступна.');return data;}
  function error(exc){$('[data-history-error]').textContent=exc.message;$('[data-history-error]').hidden=false;}
  async function refresh(){
    const data=await api(`/api/workbench/versions?scope=${encodeURIComponent(scope)}&letter_id=${encodeURIComponent(letter.id)}`);
    $('[data-history-list]').innerHTML=data.versions.map(version=>`<button type="button" class="history-version ${chosen?.id===version.id?'selected':''}" data-history-version="${version.id}"><strong>${esc(version.label||'Автозбереження')}</strong><small>${esc(new Date(version.created_at).toLocaleString('uk-UA',{timeZone:'Europe/Kyiv',dateStyle:'short',timeStyle:'medium'}))}</small></button>`).join('')||'<p class="empty-note">Історія з’явиться після першого збереження листа.</p>';
  }
  async function open(){
    if(window.OZWorkspace.busy)throw new Error('Дочекайтеся завершення поточної операції.');
    await window.OZWorkspace.flushSection('cover_letters');
    scope=window.OZWorkspace.scope;letter=window.OZCoverLetters.snapshot()?.letters?.find(value=>value.id===window.OZCoverLetters.selectedLetter);
    if(!letter)return;chosen=null;requestGeneration++;
    $('[data-history-restore]').disabled=true;$('[data-history-error]').hidden=true;$('[data-history-label]').value='';
    $('[data-history-letter]').textContent=letter.title;$('[data-history-preview]').innerHTML='<p>Оберіть версію для порівняння з поточним текстом.</p>';
    dialog.showModal();await refresh();
  }
  async function checkpoint(label){
    const snapshot=window.OZCoverLetters.snapshot(),current=snapshot.letters.find(value=>value.id===letter.id);
    await api('/api/workbench/versions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope,label,data:{letters:[current]}})});
  }
  $('[data-history-list]').onclick=async event=>{
    const button=event.target.closest('[data-history-version]');if(!button)return;
    const generation=++requestGeneration;$('[data-history-restore]').disabled=true;
    try{
      const version=await api(`/api/workbench/versions/${button.dataset.historyVersion}?scope=${encodeURIComponent(scope)}`);
      if(generation!==requestGeneration||!dialog.open)return;chosen=version;
      const current=window.OZCoverLetters.snapshot()?.letters?.find(value=>value.id===letter.id)||letter;
      $('[data-history-preview]').innerHTML=Object.entries(labels).map(([key,label])=>`<section class="history-field ${text(current.content[key])!==text(version.data.content[key])?'history-changed':''}"><h3>${label}</h3><div class="history-compare"><section><h4>Поточний текст</h4><pre>${esc(text(current.content[key])||'—')}</pre></section><section><h4>Обрана версія</h4><pre>${esc(text(version.data.content[key])||'—')}</pre></section></div></section>`).join('');
      $('[data-history-restore]').disabled=false;await refresh();
    }catch(exc){error(exc);}
  };
  $('[data-history-save]').onclick=async()=>{
    const button=$('[data-history-save]');button.disabled=true;
    try{await checkpoint($('[data-history-label]').value.trim()||'Контрольна версія');$('[data-history-label]').value='';await refresh();}catch(exc){error(exc);}finally{button.disabled=false;}
  };
  $('[data-history-restore]').onclick=async()=>{
    if(!chosen)return;const button=$('[data-history-restore]');button.disabled=true;
    try{
      if(scope!==window.OZWorkspace.scope)throw new Error('Пакет змінився. Відкрийте історію цього листа повторно.');
      await checkpoint('Перед відновленням версії');
      await window.OZCoverLetters.restoreVersion(chosen.data);dialog.close();
    }catch(exc){error(exc);button.disabled=false;}
  };
  dialog.querySelectorAll('[data-history-close]').forEach(button=>button.onclick=()=>dialog.close());
  dialog.addEventListener('close',()=>requestGeneration++);
  document.addEventListener('protokol:letters-rendered',()=>{
    const toolbar=document.querySelector('.letter-toolbar');if(!toolbar||toolbar.querySelector('[data-letter-history]'))return;
    const button=document.createElement('button');button.type='button';button.className='button quiet';button.dataset.letterHistory='';button.textContent='Історія';
    button.onclick=()=>open().catch(exc=>{if(dialog.open){error(exc);return;}document.querySelector('#cover_letters-view [data-error]').textContent=exc.message;document.querySelector('#cover_letters-view [data-error]').hidden=false;});
    toolbar.append(button);
  });
})();
