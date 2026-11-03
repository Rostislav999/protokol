'use strict';
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog=document.createElement('dialog');dialog.id='template-picker';dialog.className='modal template-picker';
  dialog.innerHTML=`<div class="modal-heading"><div><p class="eyebrow">БІБЛІОТЕКА WORD</p><h2 data-picker-title>Обрати шаблон</h2><p data-picker-subtitle></p></div><button type="button" class="icon-button" data-picker-close aria-label="Закрити вибір шаблону">×</button></div><div class="picker-toolbar"><label class="search-box"><svg class="icon"><use href="#i-search"/></svg><input type="search" data-picker-search placeholder="Назва, НД, синонім або ім’я файла" aria-label="Пошук у бібліотеці шаблонів"></label><label class="field"><span class="sr-only">Група шаблонів</span><select data-picker-group aria-label="Група шаблонів"><option value="">Усі групи</option></select></label><select class="picker-shortcut-filter" data-picker-shortcuts aria-label="Обрані й недавні шаблони"><option value="all">Уся бібліотека</option><option value="favorites">Обрані</option><option value="recent">Нещодавні</option></select><small data-picker-count></small></div><div class="picker-layout"><div class="picker-list" data-picker-list role="group" aria-label="Шаблони"></div><section class="picker-document"><div class="preview-heading"><strong data-picker-name>Оберіть шаблон зі списку</strong><span data-picker-state role="status"></span></div><div class="word-preview" data-picker-document></div><p class="preview-note">Попередній вигляд. Остаточне розташування сторінок визначає Word.</p></section></div><div class="modal-footer"><a class="button secondary" data-picker-download download hidden>Завантажити Word-зразок</a><button type="button" class="button secondary" data-picker-close>Скасувати</button><button type="button" class="button primary" data-picker-apply disabled>Обрати цей шаблон</button></div>`;
  document.body.append(dialog);
  const $=s=>dialog.querySelector(s);
  let options=null,selected='',controller=null,generation=0;
  const choices=new Map();
  const choice=id=>choices.get((options?.family||'protocols')+'/'+id)||{};
  async function saveChoice(family,template_id,values){
    const response=await fetch('/api/template-choices',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({family,template_id,...values})});
    if(!response.ok)throw new Error('Не вдалося зберегти обрані шаблони.');
    const value=await response.json();choices.set(family+'/'+template_id,value);if(dialog.open)rows();return value;
  }
  fetch('/api/template-choices').then(response=>response.ok?response.json():Promise.reject()).then(data=>{data.choices.forEach(value=>choices.set(value.family+'/'+value.template_id,value));if(dialog.open)rows();}).catch(()=>{});
  document.addEventListener('change',event=>{const select=event.target;if(!select.matches('select[data-field=template_id],select[data-dept-template]')||!select.value)return;saveChoice(select.matches('[data-dept-template]')?'assignments':'protocols',select.value,{used:true}).catch(error=>{if(dialog.open)$('[data-picker-state]').textContent=error.message;});});
  function rows() {
    const terms=$('[data-picker-search]').value.toLocaleLowerCase('uk').normalize('NFKC').trim().split(/\s+/).filter(Boolean);
    const group=$('[data-picker-group]').value,shortcuts=$('[data-picker-shortcuts]').value;
    const templates=options.templates.filter(t=>(shortcuts==='all'||shortcuts==='favorites'&&choice(t.id).favorite||shortcuts==='recent'&&choice(t.id).last_used_at)&&(!group || (t.group||t.department||t.category||'Інше')===group) &&
      terms.every(term=>[t.name,t.product_name,t.standard,t.filename,...(t.aliases||[])].join(' ').toLocaleLowerCase('uk').normalize('NFKC').includes(term)));
    const recommended=new Set(options.recommendedIds||[]);
    templates.sort((a,b)=>(shortcuts==='recent'?String(choice(b.id).last_used_at||'').localeCompare(String(choice(a.id).last_used_at||'')):Number(!!choice(b.id).favorite)-Number(!!choice(a.id).favorite)) || Number(recommended.has(b.id))-Number(recommended.has(a.id)) || String(a.name).localeCompare(String(b.name),'uk'));
    $('[data-picker-count]').textContent=`Знайдено: ${templates.length} з ${options.templates.length}`;
    $('[data-picker-list]').innerHTML=templates.length?templates.map(t=>`<div class="picker-row"><button type="button" class="picker-option ${t.id===selected?'selected':''}" data-picker-id="${esc(t.id)}" aria-pressed="${t.id===selected}"><span class="picker-option-title">${esc(t.name||t.product_name)}</span><span class="picker-option-meta">${esc(t.standard||t.group||t.category||'Word-шаблон')}</span><span class="picker-option-file">${esc(t.filename||'')}</span>${recommended.has(t.id)?'<span class="picker-recommended">Рекомендовано для зразка</span>':''}</button><button type="button" class="picker-star" data-picker-favorite="${esc(t.id)}" aria-pressed="${!!choice(t.id).favorite}" aria-label="${choice(t.id).favorite?'Прибрати з обраних':'Додати до обраних'}: ${esc(t.name||t.product_name)}">${choice(t.id).favorite?'★':'☆'}</button></div>`).join(''):'<div class="empty-state"><h3>Збігів немає</h3><p>Спробуйте частину назви, номер НД або ім’я файла.</p></div>';
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
    const target=$('[data-picker-document]');window.OZDocumentPreview.invalidate(target);target.replaceChildren();
    try {
      const response=await fetch(url,{signal:controller.signal});
      if(!response.ok)throw new Error('Не вдалося відкрити Word-зразок.');
      const data=await response.arrayBuffer();if(current!==generation)return;
      const count=await window.OZDocumentPreview.render(data,target,{className:'ozdoc-picker'});
      if(current===generation)$('[data-picker-state]').textContent=`${count||1} стор.`;
    }catch(error){if(error.name!=='AbortError' && current===generation){$('[data-picker-state]').textContent='Перегляд недоступний';target.textContent=error.message;}}
  }
  function close(){controller?.abort();generation++;window.OZDocumentPreview.invalidate($('[data-picker-document]'));dialog.close();}
  dialog.querySelectorAll('[data-picker-close]').forEach(button=>button.onclick=close);
  dialog.addEventListener('cancel',()=>{controller?.abort();generation++;window.OZDocumentPreview.invalidate($('[data-picker-document]'));});
  dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
  $('[data-picker-search]').oninput=rows;$('[data-picker-group]').onchange=rows;$('[data-picker-shortcuts]').onchange=rows;
  $('[data-picker-list]').onclick=async event=>{
    const star=event.target.closest('[data-picker-favorite]');
    if(star){const id=star.dataset.pickerFavorite;star.disabled=true;try{await saveChoice(options.family,id,{favorite:!choice(id).favorite});$(`[data-picker-favorite="${CSS.escape(id)}"]`)?.focus();}catch(error){$('[data-picker-state]').textContent=error.message;star.disabled=false;}return;}
    const row=event.target.closest('[data-picker-id]');if(row)showDocument(row.dataset.pickerId);
  };
  $('[data-picker-list]').addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;const buttons=[...dialog.querySelectorAll('[data-picker-id]')],index=buttons.indexOf(event.target);if(index<0)return;event.preventDefault();buttons[(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();});
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
    $('[data-picker-search]').value='';$('[data-picker-shortcuts]').value='all';
    const groups=[...new Set(config.templates.map(t=>t.group||t.department||t.category||'Інше'))].sort((a,b)=>a.localeCompare(b,'uk'));
    $('[data-picker-group]').innerHTML='<option value="">Усі групи</option>'+groups.map(group=>`<option value="${esc(group)}">${esc(group)}</option>`).join('');
    $('[data-picker-group]').closest('label').hidden=groups.length<2;
    $('[data-picker-document]').replaceChildren();$('[data-picker-download]').hidden=true;$('[data-picker-apply]').disabled=true;
    $('[data-picker-name]').textContent='Оберіть шаблон зі списку';$('[data-picker-state]').textContent='';
    rows();dialog.showModal();$('[data-picker-search]').focus();
    if(selected)showDocument(selected);
  }};
})();
