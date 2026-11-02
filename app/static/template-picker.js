'use strict';
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog=document.createElement('dialog');dialog.id='template-picker';dialog.className='modal template-picker';
  dialog.innerHTML=`<div class="modal-heading"><div><p class="eyebrow">БІБЛІОТЕКА WORD</p><h2 data-picker-title>Обрати шаблон</h2><p data-picker-subtitle></p></div><button type="button" class="icon-button" data-picker-close aria-label="Закрити вибір шаблону">×</button></div><div class="picker-toolbar"><label class="search-box"><svg class="icon"><use href="#i-search"/></svg><input type="search" data-picker-search placeholder="Назва, НД, синонім або ім’я файла" aria-label="Пошук у бібліотеці шаблонів"></label><label class="field"><span class="sr-only">Група шаблонів</span><select data-picker-group aria-label="Група шаблонів"><option value="">Усі групи</option></select></label><small data-picker-count></small></div><div class="picker-layout"><div class="picker-list" data-picker-list role="group" aria-label="Шаблони"></div><section class="picker-document"><div class="preview-heading"><strong data-picker-name>Оберіть шаблон зі списку</strong><span data-picker-state role="status"></span></div><div class="word-preview" data-picker-document></div><p class="preview-note">Попередній вигляд. Остаточне розташування сторінок визначає Word.</p></section></div><div class="modal-footer"><a class="button secondary" data-picker-download download hidden>Завантажити Word-зразок</a><button type="button" class="button secondary" data-picker-close>Скасувати</button><button type="button" class="button primary" data-picker-apply disabled>Обрати цей шаблон</button></div>`;
  document.body.append(dialog);
  const $=s=>dialog.querySelector(s);
  let options=null,selected='',controller=null,generation=0;
  function rows() {
    const terms=$('[data-picker-search]').value.toLocaleLowerCase('uk').normalize('NFKC').trim().split(/\s+/).filter(Boolean);
    const group=$('[data-picker-group]').value;
    const templates=options.templates.filter(t=>(!group || (t.group||t.department||t.category||'Інше')===group) &&
      terms.every(term=>[t.name,t.product_name,t.standard,t.filename,...(t.aliases||[])].join(' ').toLocaleLowerCase('uk').normalize('NFKC').includes(term)));
    const recommended=new Set(options.recommendedIds||[]);
    templates.sort((a,b)=>Number(recommended.has(b.id))-Number(recommended.has(a.id)) || String(a.name).localeCompare(String(b.name),'uk'));
    $('[data-picker-count]').textContent=`Знайдено: ${templates.length} з ${options.templates.length}`;
    $('[data-picker-list]').innerHTML=templates.length?templates.map(t=>`<button type="button" class="picker-option ${t.id===selected?'selected':''}" data-picker-id="${esc(t.id)}" aria-pressed="${t.id===selected}"><span class="picker-option-title">${esc(t.name||t.product_name)}</span><span class="picker-option-meta">${esc(t.standard||t.group||t.category||'Word-шаблон')}</span><span class="picker-option-file">${esc(t.filename||'')}</span>${recommended.has(t.id)?'<span class="picker-recommended">Рекомендовано для зразка</span>':''}</button>`).join(''):'<div class="empty-state"><h3>Збігів немає</h3><p>Спробуйте частину назви, номер НД або ім’я файла.</p></div>';
  }
  async function showDocument(id) {
    selected=id;rows();
    const template=options.templates.find(t=>t.id===id);
    $('[data-picker-apply]').disabled=!template;
    if(!template)return;
    $('[data-picker-name]').textContent=template.name||template.product_name;
    const url=options.downloadUrl(template);
    $('[data-picker-download]').href=url;$('[data-picker-download]').hidden=false;
    controller?.abort();controller=new AbortController();const current=++generation;
    $('[data-picker-state]').textContent='Відкриваємо…';
    const target=$('[data-picker-document]');target.replaceChildren();
    try {
      const response=await fetch(url,{signal:controller.signal});
      if(!response.ok)throw new Error('Не вдалося відкрити Word-зразок.');
      const data=await response.arrayBuffer();if(current!==generation)return;
      const count=await window.OZDocumentPreview.render(data,target,{className:'ozdoc-picker'});
      if(current===generation)$('[data-picker-state]').textContent=`${count||1} стор.`;
    }catch(error){if(error.name!=='AbortError' && current===generation){$('[data-picker-state]').textContent='Перегляд недоступний';target.textContent=error.message;}}
  }
  function close(){controller?.abort();generation++;dialog.close();}
  dialog.querySelectorAll('[data-picker-close]').forEach(button=>button.onclick=close);
  dialog.addEventListener('cancel',()=>{controller?.abort();generation++;});
  dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
  $('[data-picker-search]').oninput=rows;$('[data-picker-group]').onchange=rows;
  $('[data-picker-list]').onclick=event=>{const row=event.target.closest('[data-picker-id]');if(row)showDocument(row.dataset.pickerId);};
  $('[data-picker-apply]').onclick=()=>{
    const id=selected,callback=options.onSelect,focus=options.focusSelector;
    close();callback(id);
    requestAnimationFrame(()=>control(document.querySelector(focus))?.focus());
  };
  function sync(select) {
    const button=select?.previousElementSibling;
    if(!button?.matches('[data-template-picker-control]'))return;
    const option=select.selectedOptions[0];
    const text=select.value?(option?.dataset.templateName?[option.dataset.templateName,option.dataset.templateStandard].filter(Boolean).join(' · '):option?.textContent):'Обрати шаблон з бібліотеки';
    if(button.textContent!==text)button.textContent=text;
    button.classList.toggle('has-selection',!!select.value);
    if(button.disabled!==select.disabled)button.disabled=select.disabled;
  }
  function control(select){return select?.matches('.template-native-select')?select.previousElementSibling:select;}
  function enhance() {
    for(const select of document.querySelectorAll('select[data-field="template_id"],select[data-dept-template]')) {
      if(!select.classList.contains('template-native-select')){
        select.classList.add('template-native-select');select.setAttribute('tabindex','-1');select.setAttribute('aria-hidden','true');
        const button=document.createElement('button');button.type='button';button.className='template-choice';button.dataset.templatePickerControl='';
        button.setAttribute('aria-haspopup','dialog');select.before(button);
        button.onclick=()=>select.dispatchEvent(new CustomEvent('protokol:choose-template',{bubbles:true}));
      }
      sync(select);
    }
  }
  let queued=false;
  new MutationObserver(()=>{if(!queued){queued=true;queueMicrotask(()=>{queued=false;enhance();});}})
    .observe(document.querySelector('.main-content'),{subtree:true,childList:true,attributes:true,attributeFilter:['disabled']});
  window.OZTemplatePicker={sync,control,open(config){
    options=config;selected=config.selectedId||'';
    $('[data-picker-title]').textContent=config.title||'Обрати шаблон';
    $('[data-picker-subtitle]').textContent=config.subtitle||'';
    $('[data-picker-search]').value='';
    const groups=[...new Set(config.templates.map(t=>t.group||t.department||t.category||'Інше'))].sort((a,b)=>a.localeCompare(b,'uk'));
    $('[data-picker-group]').innerHTML='<option value="">Усі групи</option>'+groups.map(group=>`<option value="${esc(group)}">${esc(group)}</option>`).join('');
    $('[data-picker-group]').closest('label').hidden=groups.length<2;
    $('[data-picker-document]').replaceChildren();$('[data-picker-download]').hidden=true;$('[data-picker-apply]').disabled=true;
    $('[data-picker-name]').textContent='Оберіть шаблон зі списку';$('[data-picker-state]').textContent='';
    rows();dialog.showModal();$('[data-picker-search]').focus();
    if(selected)showDocument(selected);
  }};
})();
