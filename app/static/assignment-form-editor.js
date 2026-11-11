'use strict';
(() => {
  const departments = [
    ['vet','Ветеринарно-санітарне'],['bacteriology','Бактеріологічне'],['chemistry','Хіміко-токсикологічне']
  ];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const dialog = document.createElement('dialog');
  dialog.id = 'assignment-form-editor';
  dialog.className = 'modal assignment-form-editor';
  dialog.innerHTML = `<form id="assignment-blank-form">
    <div class="modal-heading"><div><p class="eyebrow">БЛАНК ЗАВДАННЯ</p><h2 id="assignment-form-title">Новий шаблон завдання</h2></div><button type="button" data-form-close class="icon-button" aria-label="Закрити">×</button></div>
    <div class="modal-body assignment-form-layout">
      <fieldset class="assignment-form-fields"><legend class="sr-only">Відомості та показники шаблону</legend>
        <p class="assignment-form-intro">Шапка, оформлення та таблиця — як у завданнях обраного відділення. Заповніть потрібні пункти або залиште їх порожніми для подальшого редагування.</p>
        <label class="field"><span>Назва шаблону *</span><input name="name" maxlength="250" required></label>
        <label class="field"><span>Відділення *</span><select name="department">${departments.map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></label>
        <label class="field"><span>Назва продукту</span><textarea name="product_name" maxlength="2000" rows="2" placeholder="Назва нового продукту"></textarea></label>
        <label class="field"><span>НД на відповідність</span><textarea name="standard" maxlength="2000" rows="2" placeholder="Наприклад, ДСТУ або ТУ У"></textarea><small>У готовому завданні програма автоматично підставить НД з акта.</small></label>
        <label class="field"><span>Вага та кількість за замовчуванням</span><input name="sample_weight" maxlength="2000" placeholder="Можна залишити порожнім"></label>
        <label class="field"><span>Синоніми для пошуку</span><textarea name="aliases" rows="2" placeholder="Кожен варіант з нового рядка"></textarea></label>
        <label class="field"><span>Область застосування</span><select name="product_scope"><option value="">Точне найменування</option><option value="family">Група продуктів</option><option value="first_course">Готові перші страви</option><option value="jam">Джеми</option><option value="ready_second_cereals_potatoes">Другі страви: крупи та картопля</option><option value="ready_second_all">Усі готові другі страви</option></select></label>
        <div class="section-heading"><h3>Показники та методи</h3><span id="assignment-form-row-count" class="count-pill"></span></div>
        <div id="assignment-form-rows"></div>
        <button id="assignment-form-add-row" type="button" class="button secondary">+ Додати показник</button>
      </fieldset>
      <section class="assignment-form-preview-panel" aria-label="Перегляд бланка"><div class="assignment-form-preview-toolbar"><h3>Вигляд у Word</h3><button id="assignment-form-preview-button" type="button" class="button secondary">Оновити перегляд</button></div><p id="assignment-form-preview-status" role="status">Завантажуємо бланк…</p><div id="assignment-form-preview" class="word-preview"></div></section>
    </div>
    <div class="modal-footer"><p id="assignment-form-error" class="field-error" role="alert" hidden></p><button type="button" data-form-close class="button secondary">Скасувати</button><button id="assignment-form-download" type="button" class="button secondary">Завантажити Word</button><button id="assignment-form-save" type="submit" class="button primary">Зберегти в бібліотеку</button></div>
  </form>`;
  document.body.append(dialog);
  const $ = selector => dialog.querySelector(selector);
  const form = $('#assignment-blank-form');
  const preview = $('#assignment-form-preview');
  let editing = null, context = {}, dirty = false, busy = false, revision = 0, previewSequence = 0;
  const blankRow = () => ({indicator:'',method:'',notes:''});
  function rows() {
    return [...dialog.querySelectorAll('[data-form-row]')].map(row => Object.fromEntries(
      ['indicator','method','notes'].map(key => [key,row.querySelector(`[data-row-field="${key}"]`).value])));
  }
  function renderRows(values) {
    $('#assignment-form-rows').innerHTML = values.map((value,index)=>`<section class="assignment-form-row" data-form-row>
      <div class="assignment-form-row-heading"><strong>Показник ${index+1}</strong><div><button type="button" data-row-action="up" class="icon-button" aria-label="Перемістити показник ${index+1} вище" ${index===0?'disabled':''}>↑</button><button type="button" data-row-action="down" class="icon-button" aria-label="Перемістити показник ${index+1} нижче" ${index===values.length-1?'disabled':''}>↓</button><button type="button" data-row-action="remove" class="icon-button danger" aria-label="Видалити показник ${index+1}">×</button></div></div>
      <label class="field"><span>Показник, що випробовується</span><textarea data-row-field="indicator" rows="2" maxlength="2000" placeholder="Назва показника та вимоги">${escape(value.indicator)}</textarea></label>
      <label class="field"><span>НД на метод випробувань</span><textarea data-row-field="method" rows="2" maxlength="1000" placeholder="Метод або нормативний документ">${escape(value.method)}</textarea></label>
      <label class="field"><span>Примітка</span><textarea data-row-field="notes" rows="1" maxlength="2000" placeholder="За потреби">${escape(value.notes)}</textarea></label>
    </section>`).join('');
    $('#assignment-form-row-count').textContent = values.length;
    $('#assignment-form-add-row').disabled = values.length >= 100;
  }
  function changed() {
    dirty = true;revision++;
    $('#assignment-form-preview-status').textContent = 'Є зміни. Оновіть перегляд, щоб побачити актуальний бланк.';
  }
  function payload() {
    return {
      name:form.elements.name.value.trim(), department:form.elements.department.value,
      product_name:form.elements.product_name.value.trim(), standard:form.elements.standard.value.trim(),
      sample_weight:form.elements.sample_weight.value.trim(), product_scope:form.elements.product_scope.value,
      aliases:[...new Set(form.elements.aliases.value.split(/[,\n]/).map(value=>value.trim()).filter(Boolean))],
      indicators:rows()
    };
  }
  function showError(message) {
    $('#assignment-form-error').textContent=message;
    $('#assignment-form-error').hidden=!message;
  }
  async function request(url, values, method='POST') {
    const response=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
    if(!response.ok) {
      const error=await response.json().catch(()=>({}));
      throw new Error(typeof error.detail==='string'?error.detail:Array.isArray(error.detail)?error.detail.map(value=>value.msg).join('; '):'Не вдалося зберегти бланк.');
    }
    return response;
  }
  async function previewForm() {
    const sequence=++previewSequence, currentRevision=revision;
    $('#assignment-form-preview-button').disabled=true;
    showError('');
    $('#assignment-form-preview-status').textContent='Готуємо перегляд…';
    try {
      const values=payload();values.name ||= 'Новий шаблон завдання';
      const response=await request('/api/assignments/templates/forms/preview',values);
      if(sequence!==previewSequence||!dialog.open)return;
      await window.OZDocumentPreview.render(await response.blob(),preview);
      if(sequence!==previewSequence||!dialog.open)return;
      $('#assignment-form-preview-status').textContent=revision===currentRevision?'Бланк оновлено. Номер зразка та дата заповняться під час створення завдання.':'Є нові зміни. Оновіть перегляд.';
    }catch(error){if(sequence===previewSequence)showError(error.message);}
    finally{if(sequence===previewSequence)$('#assignment-form-preview-button').disabled=false;}
  }
  function close() {
    if(busy)return;
    if(dirty&&!window.confirm('Закрити бланк без збереження змін?'))return;
    previewSequence++;
    window.OZDocumentPreview.invalidate(preview);
    dialog.close();
  }
  dialog.querySelectorAll('[data-form-close]').forEach(button=>button.addEventListener('click',close));
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
  form.addEventListener('input',changed);
  form.addEventListener('change',changed);
  $('#assignment-form-rows').addEventListener('click',event=>{
    const button=event.target.closest('[data-row-action]');if(!button||busy)return;
    const elements=[...dialog.querySelectorAll('[data-form-row]')],index=elements.indexOf(button.closest('[data-form-row]'));
    const values=rows(),action=button.dataset.rowAction;
    if(action==='remove')values.splice(index,1);
    if(action==='up'&&index>0)[values[index-1],values[index]]=[values[index],values[index-1]];
    if(action==='down'&&index<values.length-1)[values[index+1],values[index]]=[values[index],values[index+1]];
    renderRows(values.length?values:[blankRow()]);changed();
  });
  $('#assignment-form-add-row').addEventListener('click',()=>{
    if(busy)return;const values=rows();if(values.length>=100)return;
    renderRows([...values,blankRow()]);changed();
    dialog.querySelector('[data-form-row]:last-child textarea').focus();
  });
  $('#assignment-form-preview-button').addEventListener('click',previewForm);
  $('#assignment-form-download').addEventListener('click',async()=>{
    if(busy)return;
    const button=$('#assignment-form-download');button.disabled=true;showError('');
    try {
      const values=payload();values.name ||= 'Новий шаблон завдання';
      const response=await request('/api/assignments/templates/forms/preview',values);
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
      link.href=url;link.download=(values.name.replace(/[<>:"/\\|?*]/g,'_')||'Завдання')+'.docx';
      document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
    }catch(error){showError(error.message);}
    finally{button.disabled=false;}
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    busy=true;showError('');
    $('.assignment-form-fields').disabled=true;
    $('#assignment-form-save').disabled=true;
    try {
      const url=editing?`/api/assignments/templates/${encodeURIComponent(editing.id)}/form`:'/api/assignments/templates/forms';
      const response=await request(url,payload(),editing?'PUT':'POST'),template=await response.json();
      dirty=false;dialog.close();previewSequence++;window.OZDocumentPreview.invalidate(preview);
      document.dispatchEvent(new CustomEvent('protokol:assignment-templates-changed',{detail:{template,...context}}));
    }catch(error){showError(error.message);}
    finally{busy=false;$('.assignment-form-fields').disabled=false;$('#assignment-form-save').disabled=false;}
  });
  window.OZAssignmentFormEditor={open({template=null,defaults={},itemId='',department=''}={}){
    if(busy||dialog.open)return;
    editing=template;context={itemId,department};form.reset();showError('');
    const values=template?{...template.form,name:template.name,aliases:template.aliases,product_scope:template.product_scope}:defaults;
    for(const key of ['name','product_name','standard','sample_weight','product_scope'])form.elements[key].value=values[key]||'';
    form.elements.name.value ||= values.product_name?`${values.product_name.slice(0,200)} — завдання`:'Новий шаблон завдання';
    form.elements.department.value=values.department||department||'vet';
    form.elements.aliases.value=(values.aliases||[]).join('\n');
    renderRows(values.indicators?.length?values.indicators:[blankRow(),blankRow(),blankRow()]);
    $('#assignment-form-title').textContent=template?'Редагувати бланк завдання':'Новий бланк завдання';
    $('#assignment-form-preview-button').disabled=false;preview.replaceChildren();dirty=false;revision++;
    dialog.showModal();previewForm();
  }};
})();
