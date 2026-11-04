'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const uid = () => globalThis.crypto?.randomUUID?.() || `excel-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const state = {fields:[], records:[], warnings:[], loaded:false, loading:null, busy:false, result:null, fileUrl:'', reportUrl:'', legacyTemplateName:''};
  const changed = () => window.OZWorkspace?.changed('excel');
  const clone = value => JSON.parse(JSON.stringify(value));
  const originText = source => source?.label || ({act:'З акта',protocol:'Із протоколу',manual:'Введено вручну',directory:'Із довідника',template:'Із шаблону',derived:'Обчислено з даних джерела'})[source?.kind] || 'Джерело не збережено';
  const origin = (kind,label,source = '') => ({kind,label,source});
  const recordSelector = record => `[data-excel-record="${CSS.escape(record.id)}"]`;
  const section = document.createElement('section');
  section.id = 'excel-view';
  section.hidden = true;
  section.setAttribute('aria-labelledby','excel-title');
  section.innerHTML = `
    <div class="page-heading"><div><p class="eyebrow">АКТ АБО ПРОТОКОЛИ → ПЕРЕВІРКА → ТАБЛИЦЯ</p><h1 id="excel-title">Excel за актом і протоколами</h1><p class="subtitle">Перенесіть дані поточного акта або завантажте готові протоколи. Знайома таблиця D–N; два стовпці про відповідність не заповнюються.</p></div><button id="excel-clear" type="button" class="button secondary">Новий список</button></div>
    <div id="excel-load-error" class="notice warning" hidden><p></p><button id="excel-retry" type="button" class="text-button">Спробувати знову</button></div>
    <section class="panel common-panel"><div class="panel-heading"><div><h2>Протоколи випробувань</h2><p>Виберіть один або кілька DOCX, PDF чи ZIP із протоколами.</p></div></div><div class="source-actions"><button id="excel-upload" type="button" class="button primary">${icon('upload')}Додати протоколи</button><small>До 100 файлів, по 25 МБ. Загалом до 100 МБ.</small></div><input id="excel-files" type="file" accept=".docx,.pdf,.zip" multiple hidden></section>
    <section id="excel-processing" class="panel processing" hidden aria-live="polite"><div class="spinner"></div><h2>Читаємо протоколи</h2><p>Розпізнаємо дані для таблиці. Скан PDF може потребувати кількох хвилин.</p></section>
    <div id="excel-warnings" class="notice warning" hidden></div>
    <fieldset id="excel-editor" style="border:0;padding:0;min-width:0;margin:0">
      <section class="panel common-panel"><div class="panel-heading"><div><h2>Зведена таблиця</h2><p>Excel створюється за формою УБХП: з готовою шапкою, оформленням рядків і форматами клітинок. Достатньо перевірити дані та натиснути «Створити Excel».</p></div></div><div id="excel-legacy-template" class="notice warning" hidden><p></p></div></section>
      <div class="section-heading"><div><h2>Протоколи <span id="excel-record-count" class="count-pill">0</span></h2><p>Перевірте назви, дати, масу в кг та коди організацій перед збереженням.</p></div></div>
      <div id="excel-records"></div>
      <div id="excel-empty" class="panel empty-state"><h3>Дані ще не додані</h3><p>Перенесіть дані кнопкою «Підготувати Excel за цим актом» або завантажте готові протоколи.</p></div>
      <div class="notice soft">${icon('info')}<div>Партія (позначення) заповнюється датою виготовлення. Маса нетто одиниці — вага однієї упаковки, а не загальна вага зразка. Постачальник і виробник містять лише цифри коду ЄДРПОУ; початкові нулі зберігаються.</div></div>
      <section class="panel generation-panel"><div><label class="confirmation"><input id="excel-confirmed" type="checkbox"><span>Я перевірив(-ла) витягнуті дані та попередження.</span></label><p id="excel-generation-help" class="generation-help"></p></div><button id="excel-generate" type="button" class="button primary" disabled>${icon('download')}Створити Excel</button></section>
    </fieldset>
    <div id="excel-error" class="notice warning" role="alert" hidden></div>
    <section id="excel-success" class="notice success" hidden role="status"><div><strong id="excel-success-message"></strong><p>Збережіть таблицю та за потреби звіт перевірки.</p><div class="source-actions"><a id="excel-download" class="button secondary" download="Додаток_зведена_таблиця.xlsx">Завантажити Excel повторно</a><a id="excel-report" class="text-button" download="Звіт_Excel.txt">Завантажити звіт</a></div></div></section>`;
  $('.main-content').append(section);

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = `Помилка запиту (${response.status}).`;
      try {
        const body = await response.json();
        message = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('; ') : body.message || message;
      } catch {}
      throw new Error(message);
    }
    return response;
  }
  function showError(error) {
    $('#excel-error').textContent = error.message || String(error);
    $('#excel-error').hidden = false;
  }
  function invalidate() {
    $('#excel-confirmed').checked = false;
    $('#excel-success').hidden = true;
    $('#excel-error').hidden = true;
    state.result = null;
    for (const name of ['fileUrl','reportUrl']) {
      if (state[name]) URL.revokeObjectURL(state[name]);
      state[name] = '';
    }
    changed();
  }
  async function load() {
    if (state.loaded) return true;
    if (state.loading) return state.loading;
    state.loading = (async () => {
      $('#excel-load-error').hidden = true;
      try {
        const data = await (await api('/api/excel/fields')).json();
        state.fields = data.fields || [];
        if (!state.fields.length) throw new Error('Не вдалося отримати структуру таблиці Excel.');
        state.loaded = true;
        render();
        return true;
      } catch (error) {
        $('#excel-load-error p').textContent = error.message;
        $('#excel-load-error').hidden = false;
        return false;
      } finally {
        state.loading = null;
        updateReadiness();
      }
    })();
    updateReadiness();
    return state.loading;
  }
  function numberKey(value) { return String(value || '').replace(/[\s№]/g,'').replace(/[–—]/g,'-'); }
  function reviewIssues() {
    const result = [];
    const add = (message,selector,level = 'error') => result.push({message,selector,level,section:'excel'});
    if (!state.loaded) add('Не завантажено структуру таблиці Excel.','#excel-retry');
    if (!state.records.length) add('Додайте принаймні один протокол або перенесіть дані акта.','#excel-upload');
    const seen = new Set();
    state.records.forEach((record,index) => {
      const base = recordSelector(record);
      const field = column => `${base} [data-excel-column="${column}"]`;
      const number = numberKey(record.values.D);
      if (!number) add(`Протокол ${index+1}: не вказано номер.`,field('D'));
      else if (seen.has(number)) add(`Протокол ${index+1}: повторний номер ${record.values.D}.`,field('D'));
      seen.add(number);
      state.fields.forEach(({column,label,type}) => {
        const value = String(record.values[column] || '').trim();
        if (!value) {
          if (column !== 'D') add(`Протокол ${index+1}: не заповнено «${label}».`,field(column),'warning');
          return;
        }
        if (type === 'date') {
          const match = value.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
          const date = match && new Date(Date.UTC(Number(match[3]),Number(match[2])-1,Number(match[1])));
          if (!match || date.getUTCFullYear() !== Number(match[3]) || date.getUTCMonth() !== Number(match[2])-1 || date.getUTCDate() !== Number(match[1])) add(`Протокол ${index+1}: «${label}» має бути справжньою датою ДД.ММ.РРРР.`,field(column));
        } else if (type === 'number') {
          const numberValue = Number(value.replace(/\s/g,'').replace(',','.'));
          if (!Number.isFinite(numberValue) || numberValue < 0) add(`Протокол ${index+1}: «${label}» має бути невід’ємним числом.`,field(column));
        } else if (column === 'M' && !/^\d{8}(?:\d{2})?$/.test(value) || column === 'N' && !/^\d{8}$/.test(value)) {
          add(`Протокол ${index+1}: перевірте кількість цифр коду в полі «${label}».`,field(column));
        }
      });
      (record.warnings || []).forEach(message => add(`Протокол ${index+1}, повідомлення джерела: ${message}`,base,'warning'));
    });
    state.warnings.forEach(message => add(message,'#excel-warnings','warning'));
    return result;
  }
  function issues() { return reviewIssues().filter(issue => issue.level === 'error').map(issue => issue.message); }
  function updateReadiness() {
    const missing = issues();
    $('#excel-upload').disabled = state.busy || !state.loaded;
    $('#excel-clear').disabled = state.busy;
    $('#excel-editor').disabled = state.busy;
    $('#excel-generate').disabled = state.busy || missing.length > 0 || !$('#excel-confirmed').checked;
    $('#excel-generate').innerHTML = state.busy ? 'Опрацьовуємо…' : `${icon('download')}Створити Excel`;
    $('#excel-generation-help').textContent = state.busy ? 'Дочекайтеся завершення операції.' : missing.length ? `Потрібно: ${missing.slice(0,3).join('; ')}.` : !$('#excel-confirmed').checked ? 'Підтвердьте перевірку даних.' : `До таблиці буде передано ${state.records.length} протоколів.`;
    $('#excel-generation-help').classList.toggle('ready', !state.busy && !missing.length && $('#excel-confirmed').checked);
    $('#excel-legacy-template').hidden = !state.legacyTemplateName;
    $('#excel-legacy-template p').textContent = `Ця чернетка раніше доповнювала книгу «${state.legacyTemplateName}». Тепер буде створено нову зведену таблицю лише з протоколами поточного списку. Попередня книга не змінюється.`;
  }
  function render() {
    $('#excel-record-count').textContent = state.records.length;
    $('#nav-excel-count').textContent = state.records.length;
    $('#excel-empty').hidden = state.records.length > 0;
    $('#excel-warnings').hidden = state.warnings.length === 0;
    $('#excel-warnings').innerHTML = `<strong>Повідомлення імпорту</strong><ul>${state.warnings.map(value => `<li>${esc(value)}</li>`).join('')}</ul>`;
    $('#excel-records').innerHTML = state.records.map((record,index) => {
      const missing = state.fields.filter(field => !String(record.values[field.column] || '').trim()).map(field => field.label);
      return `<article class="panel item-card" data-excel-record="${esc(record.id)}"><div class="item-heading"><div class="item-label"><span class="item-number">${index+1}</span><div><h3 class="item-title">${esc(record.values.G || 'Протокол без назви')}</h3><p class="item-subtitle" style="overflow-wrap:anywhere">${esc(record.source || 'Протокол')} · ${esc(record.values.D || 'номер не визначено')}</p></div></div><button type="button" class="icon-button danger excel-remove" aria-label="Видалити протокол ${index+1}">${icon('trash')}</button></div>${record.warnings?.length ? `<details class="notice warning"><summary>Попередження джерела: ${record.warnings.length}</summary><ul>${record.warnings.map(value => `<li>${esc(value)}</li>`).join('')}</ul></details>` : ''}<div class="item-grid">${state.fields.map(field => `<label class="field ${['G','M'].includes(field.column) ? 'full' : ''}"><span>${esc(field.column)} · ${esc(field.label)}${field.column === 'D' ? ' *' : ''}</span><input data-excel-column="${esc(field.column)}" value="${esc(record.values[field.column] || '')}" ${field.type === 'number' ? 'inputmode="decimal"' : ''} placeholder="${field.type === 'date' ? 'ДД.ММ.РРРР' : ['M','N'].includes(field.column) ? 'Код ЄДРПОУ' : ''}" ${field.column === 'D' ? 'required' : ''}><small class="field-source" data-excel-origin="${esc(field.column)}">Джерело: ${esc(originText(record.sources?.[field.column]))}</small></label>`).join('')}</div><p class="item-footnote" data-excel-missing>${missing.length ? `Не заповнено: ${esc(missing.join('; '))}.` : 'Усі поля заповнені.'}</p></article>`;
    }).join('');
    updateReadiness();
  }
  function importedRecord(record, kind = 'protocol', source = '') {
    const filename = record.source || source || (kind === 'act' ? 'Акт відбору' : 'Протокол');
    const sources = Object.fromEntries(state.fields.map(field => [field.column,
      record.sources?.[field.column] || origin(kind,`${kind === 'act' ? 'З акта' : 'Із протоколу'}: ${filename}`,filename)]));
    // Older servers cannot distinguish directory enrichment from document data.
    if (record.values?.M && !record.sources?.M) sources.M = origin('derived',`Код із джерела або довідника: ${filename}`,filename);
    return {id:uid(),source:filename,values:{...(record.values || {})},warnings:Array.isArray(record.warnings) ? record.warnings : [],sources};
  }
  function actOrigins(record, item, detail) {
    const mapping = {D:'protocol_number',E:'protocol_date',F:'catalog_number',G:'product_name',H:'manufacture_date',I:'manufacture_date',J:'manufacture_date',K:'batch_size',L:'sample_weight',N:'manufacturer'};
    Object.entries(mapping).forEach(([column,key]) => {
      const source = detail.provenance?.items?.[item?.id]?.[key];
      if (source) record.sources[column] = clone(source);
    });
    const supplier = detail.provenance?.common?.supplier;
    if (supplier && record.sources.M?.kind !== 'directory') record.sources.M = clone(supplier);
  }
  async function importFiles(files) {
    if (state.busy || !files.length || !await load()) return;
    if (files.length > 100) return showError(new Error('За один раз можна додати до 100 файлів.'));
    if (files.some(file => !/\.(docx|pdf|zip)$/i.test(file.name))) return showError(new Error('Підтримуються файли DOCX, PDF і ZIP із протоколами.'));
    if (files.some(file => !file.size || file.size > 25 * 1024 * 1024)) return showError(new Error('Кожен файл має бути непорожнім і не більшим за 25 МБ.'));
    if (files.reduce((sum,file) => sum+file.size,0) > 100 * 1024 * 1024) return showError(new Error('Загальний розмір файлів — до 100 МБ.'));
    invalidate();
    state.busy = true;
    $('#excel-processing h2').textContent = 'Читаємо протоколи';
    $('#excel-processing p').textContent = 'Розпізнаємо дані для таблиці. Скан PDF може потребувати кількох хвилин.';
    $('#excel-processing').hidden = false;
    updateReadiness();
    try {
      const form = new FormData();
      files.forEach(file => form.append('files',file));
      const data = await window.OZTasks.run('excel',files,{section:'excel'});
      const imported = (data.records || []).map(record => importedRecord(record));
      if (state.records.length + imported.length > 100) throw new Error('Список містить більше 100 протоколів. Створіть окремий комплект.');
      state.records.push(...imported);
      state.warnings.push(...(data.warnings || []));
      if (!imported.length && !(data.warnings || []).length) throw new Error('У файлах не знайдено протоколів для імпорту.');
      render();
      changed();
    } catch (error) { showError(error); }
    finally { state.busy=false; $('#excel-processing').hidden=true; updateReadiness(); }
  }
  async function importAct(detail) {
    if (state.busy) {
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'excel'}}));
      showError(new Error('Дочекайтеся завершення поточної операції Excel.'));
      return;
    }
    if (!detail?.items?.length) return;
    if (state.records.length && !window.confirm('Замінити поточний список Excel даними цього акта? Незбережені зміни списку буде замінено.')) return;
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'excel'}}));
    if (!await load()) return;
    if (state.busy) { showError(new Error('Дочекайтеся завершення поточної операції Excel.')); return; }
    state.busy = true;
    $('#excel-error').hidden = true;
    $('#excel-processing h2').textContent = 'Готуємо Excel за актом';
    $('#excel-processing p').textContent = 'Переносимо поточні дані та ваші виправлення. Створювати Word-протоколи для цього не потрібно.';
    $('#excel-processing').hidden = false;
    updateReadiness();
    try {
      const data = await (await api('/api/excel/from-act', {
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(detail)
      })).json();
      const imported = (data.records || []).map((value,index) => {
        const record = importedRecord(value,'act',detail.source);
        actOrigins(record,detail.items[index],detail);
        return record;
      });
      if (!imported.length) throw new Error('В акті не знайдено позицій для таблиці Excel.');
      if (imported.length > 100) throw new Error('Список містить більше 100 позицій. Створіть окремі таблиці.');
      // Keep the existing preview and its downloads until the replacement succeeds.
      invalidate();
      state.records = imported;
      state.warnings = data.warnings || [];
      state.legacyTemplateName = '';
      render();
      changed();
    } catch (error) { showError(error); }
    finally { state.busy=false; $('#excel-processing').hidden=true; updateReadiness(); }
  }
  $('#excel-upload').addEventListener('click',() => $('#excel-files').click());
  $('#excel-files').addEventListener('change',async event => { const files=[...event.target.files]; event.target.value=''; await importFiles(files); });
  $('#excel-clear').addEventListener('click',() => {
    if (state.busy || state.records.length && !window.confirm('Очистити список протоколів для Excel?')) return;
    state.records=[]; state.warnings=[]; state.legacyTemplateName=''; invalidate(); render();
  });
  $('#excel-records').addEventListener('input',event => {
    const column=event.target.dataset.excelColumn;
    if (!column) return;
    const card=event.target.closest('[data-excel-record]');
    const record=state.records.find(value => value.id === card?.dataset.excelRecord);
    if (!record) return;
    record.values[column]=event.target.value;
    record.sources ||= {};
    record.sources[column] = origin('manual','Введено вручну');
    const originNode = $(`[data-excel-origin="${column}"]`,card);
    if (originNode) originNode.textContent = 'Джерело: введено вручну';
    if (column === 'G') $('.item-title',card).textContent=event.target.value || 'Протокол без назви';
    const missing=state.fields.filter(field => !String(record.values[field.column] || '').trim()).map(field => field.label);
    $('[data-excel-missing]',card).textContent=missing.length ? `Не заповнено: ${missing.join('; ')}.` : 'Усі поля заповнені.';
    invalidate(); updateReadiness();
  });
  $('#excel-records').addEventListener('click',event => {
    const remove=event.target.closest('.excel-remove');
    if (!remove || state.busy) return;
    const card=remove.closest('[data-excel-record]');
    state.records=state.records.filter(value => value.id !== card.dataset.excelRecord);
    invalidate(); render();
  });
  $('#excel-confirmed').addEventListener('change',updateReadiness);
  $('#excel-retry').addEventListener('click',load);
  $('#excel-generate').addEventListener('click',async () => {
    if (state.busy || issues().length || !$('#excel-confirmed').checked) return;
    $('#excel-error').hidden=true;
    state.busy=true; updateReadiness();
    const exportContext=window.OZWorkspace.context('excel');
    try {
      const form=new FormData();
      form.append('payload',JSON.stringify({records:state.records.map(({source,values,warnings})=>({source,values,warnings})),confirmed:true}));
      await window.OZWorkspace.flushSection('excel');
      const data=await (await api('/api/excel/generate',{method:'POST',body:form})).json();
      const decoded=atob(data.workbook_base64 || '');
      const bytes=new Uint8Array(decoded.length);
      for(let index=0;index<decoded.length;index++) bytes[index]=decoded.charCodeAt(index);
      if (!bytes.length) throw new Error('Отримано порожній файл Excel.');
      for (const name of ['fileUrl','reportUrl']) if (state[name]) URL.revokeObjectURL(state[name]);
      state.fileUrl=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
      state.reportUrl=URL.createObjectURL(new Blob([data.report_text || ''],{type:'text/plain;charset=utf-8'}));
      state.result=data;
      const counts=data.counts || {};
      $('#excel-success-message').textContent=`Зведена таблиця готова. Протоколів: ${counts.added || 0}.`;
      $('#excel-download').href=state.fileUrl;
      $('#excel-download').download=data.filename || 'Додаток_зведена_таблиця.xlsx';
      $('#excel-report').href=state.reportUrl;
      $('#excel-success').hidden=false;
      await window.OZOffice.deliver(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),data.filename||'Додаток_зведена_таблиця.xlsx','excel',exportContext);
    } catch (error) { showError(error); }
    finally { state.busy=false; updateReadiness(); }
  });
  document.addEventListener('protokol:view',event => { if (event.detail?.name === 'excel') load(); });
  document.addEventListener('protokol:excel-import',async event => {
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'excel'}}));
    await importFiles(event.detail?.files || []);
  });
  document.addEventListener('protokol:excel-from-act',event => importAct(event.detail));
  window.OZWorkspace?.register('excel',{
    title:'Таблиця Excel',isBusy:()=>state.busy,
    hasData:() => state.records.length > 0 || !!state.legacyTemplateName,
    snapshot:() => state.records.length || state.legacyTemplateName ? ({records:clone(state.records),warnings:clone(state.warnings),legacyTemplateName:state.legacyTemplateName}) : null,
    restore:async data => {
      if (state.busy) throw new Error('Дочекайтеся завершення поточної операції Excel.');
      if (!await load()) throw new Error('Не вдалося завантажити структуру Excel для відновлення чернетки.');
      const fields = new Set(state.fields.map(field => field.column));
      state.records = (Array.isArray(data.records) ? data.records : []).slice(0,100).map(saved => ({
        id:typeof saved.id === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(saved.id) ? saved.id : uid(),
        source:String(saved.source || 'Джерело чернетки').slice(0,1000),
        values:Object.fromEntries(Object.entries(saved.values || {}).filter(([key]) => fields.has(key)).map(([key,value]) => [key,String(value ?? '').slice(0,4000)])),
        warnings:Array.isArray(saved.warnings) ? saved.warnings.map(value => String(value).slice(0,5000)).slice(0,200) : [],
        sources:saved.sources && typeof saved.sources === 'object' ? clone(saved.sources) : {}
      }));
      state.warnings = Array.isArray(data.warnings) ? data.warnings.map(String).slice(0,200) : [];
      state.legacyTemplateName=String(data.legacyTemplateName || (data.templateWasSelected ? data.templateName || 'Excel' : '')).slice(0,1000);
      invalidate(); render();
    },
    reset:async()=>{state.records=[];state.warnings=[];state.legacyTemplateName='';invalidate();render();},
    issues:reviewIssues,
    reveal:issue => {
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'excel'}}));
      requestAnimationFrame(() => { const node=$(issue.selector); node?.scrollIntoView({block:'center',behavior:'smooth'}); node?.focus({preventScroll:true}); });
    }
  });
  render();
})();
