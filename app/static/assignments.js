'use strict';

(function assignmentsModule() {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const uid = () => globalThis.crypto?.randomUUID?.() || `assignment-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const DEPTS = [
    {id: 'vet', name: 'Ветеринарно-санітарне', short: 'Ветсан'},
    {id: 'bacteriology', name: 'Бактеріологічне', short: 'Бактеріологія'},
    {id: 'chemistry', name: 'Хіміко-токсикологічне', short: 'Хімія та токсикологія'}
  ];
  const state = {
    templates: [], items: [], loaded: false, loading: false, busy: false, source: '', sourceUrl: '',
    text: '', today: '', nextNumber: null, initialNumber: '', jobId: uid(), result: null, editing: null,
    issuers: [], issuerId: '', warnings: []
  };
  const clone = value => JSON.parse(JSON.stringify(value));
  const changed = () => window.OZWorkspace?.changed('assignments');
  const originText = source => source?.label || ({act:'З акта',manual:'Введено вручну',directory:'Із довідника',template:'Із шаблону',derived:'Обчислено програмою'})[source?.kind] || 'Джерело не збережено';
  const sourceHint = (item,key) => {const text=originText(item.sources?.[key]);return `<small class="field-source" data-assignment-origin="${key}">${text.startsWith('Джерело')?'':'Джерело: '}${esc(text)}</small>`;};
  const itemSelector = item => `[data-assignment-item="${CSS.escape(item.id)}"]`;
  const view = document.createElement('section');
  view.id = 'assignments-view';
  view.hidden = true;
  view.setAttribute('aria-labelledby', 'assignments-title');
  view.innerHTML = `
    <div class="page-heading"><div><p class="eyebrow">ЗРАЗКИ → ВІДДІЛЕННЯ → ЗАВДАННЯ</p><h1 id="assignments-title">Завдання на дослідження</h1><p class="subtitle">Назва зразка та нормативний документ визначають потрібні шаблони для кожного відділення.</p></div><button id="assignment-new" class="button secondary">${icon('plus')}Новий акт</button></div>
    <div class="assignment-tabs" role="tablist" aria-label="Розділи завдань"><button id="assignment-tab-work" role="tab" aria-selected="true" class="active">Підготовка завдань</button><button id="assignment-tab-library" role="tab" aria-selected="false">Шаблони завдань <span id="assignment-template-count" class="count-pill">—</span></button></div>
    <div id="assignment-load-error" class="notice warning" hidden><p>Не вдалося завантажити налаштування завдань.</p><button id="assignment-retry" class="text-button">Спробувати знову</button></div>
    <div id="assignment-loading" class="panel empty-state" hidden><div class="spinner small"></div><p>Завантажуємо шаблони та лічильник номерів…</p></div>
    <section id="assignment-work" role="tabpanel" aria-labelledby="assignment-tab-work">
      <div id="assignment-upload-panel" class="panel assignment-upload-panel">
        <div><span class="mini-icon">${icon('file')}</span><h2>Почніть з акта відбору</h2><p>Завантажте акт або передайте зразки з вкладки «Акти відбору». Для кожного зразка перевірте НД та виберіть шаблони відділень.</p></div>
        <div class="assignment-upload-actions"><button id="assignment-upload" class="button primary">${icon('upload')}Завантажити акт</button><button id="assignment-manual" class="button secondary">Ввести вручну</button></div>
        <input id="assignment-act-file" type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.docx,.txt" hidden>
      </div>
      <section id="assignment-processing" class="panel processing" hidden aria-live="polite"><div class="spinner"></div><h2>Читаємо акт відбору</h2><p id="assignment-processing-label">Розпізнаємо назви та нормативні документи…</p><small>Багатосторінковий скан може потребувати кількох хвилин.</small></section>
      <fieldset id="assignment-editor" class="assignment-editor" hidden>
        <legend class="sr-only">Дані для завдань</legend>
        <div id="assignment-source" class="source-banner"></div>
        <div id="assignment-warnings" class="notice warning" hidden></div>
        <details class="panel source-details"><summary>Розпізнаний текст акта <span>Переглянути або виправити</span></summary><div class="details-body"><label for="assignment-raw-text">Текст для повторного розбору</label><textarea id="assignment-raw-text" rows="8" spellcheck="false"></textarea><div class="source-actions"><small>Повторний розбір замінить поля та вибір шаблонів.</small><button id="assignment-reparse" type="button" class="button secondary">Повторно розібрати текст</button></div></div></details>
        <section class="panel assignment-number-panel">
          <div class="panel-heading"><div><h2>Номери та дата видачі</h2><p>Один номер зразка використовується в усіх його завданнях.</p></div><span class="subtle-tag">ЛІЧИЛЬНИК ЗБЕРІГАЄТЬСЯ</span></div>
          <div class="form-grid">
            <label class="field"><span id="assignment-start-label">Перший номер зразка *</span><input id="assignment-start-number" type="number" min="1" max="2147483647" step="1" inputmode="numeric" placeholder="Наприклад, 1401"><small id="assignment-number-hint">Вкажіть початковий номер один раз. Після створення завдань програма продовжить нумерацію.</small></label>
            <div class="field"><span>Дата видачі завдань</span><output id="assignment-today" class="assignment-readonly">—</output><small>Поточна дата за київським часом. Встановлюється програмою під час створення.</small></div>
          </div>
          <div class="assignment-number-footer"><p id="assignment-next-label"></p><button id="assignment-change-next" type="button" class="text-button" hidden>Задати початковий номер</button></div>
          <div class="assignment-number-footer"><p id="assignment-issuer-summary">Особа, яка сформулювала та видала завдання: з обраного шаблону.</p><button id="assignment-open-issuer" type="button" class="button secondary">Хто видає завдання</button></div>
        </section>
        <div class="section-heading"><div><h2>Зразки <span id="assignment-item-count" class="count-pill">0</span></h2><p>Підбір виконується за назвою продукту та НД на відповідність.</p></div><div class="assignment-heading-actions"><button id="assignment-select-unambiguous" type="button" class="button secondary">${icon('check')}Обрати однозначні шаблони</button><button id="assignment-add-item" type="button" class="button secondary">${icon('plus')}Додати зразок</button></div></div>
        <div id="assignment-items"></div>
        <div id="assignment-empty" class="panel empty-state" hidden><h3>Зразки ще не визначено</h3><p>Додайте зразок вручну або виправте текст акта.</p></div>
        <div class="notice soft">${icon('info')}<div>Перевірте нормативний документ для кожного продукту. Вага та кількість автоматично підставляються з обраного шаблону кожного відділення. За потреби відредагуйте їх; очищене вручну поле буде позначено «[НЕ ЗАПОВНЕНО]».</div></div>
        <section class="panel generation-panel"><div><label class="confirmation"><input id="assignment-confirmed" type="checkbox"><span>Я перевірив(-ла) зразки, НД, відділення та відповідність кожного шаблону.</span></label><p id="assignment-generation-help" class="generation-help"></p></div><button id="assignment-generate" type="button" class="button primary" disabled>${icon('download')}Створити завдання</button></section>
        <div id="assignment-success" class="notice success" role="status" hidden></div>
      </fieldset>
    </section>
    <section id="assignment-library" role="tabpanel" aria-labelledby="assignment-tab-library" hidden>
      <div class="section-heading"><div><h2>Бібліотека завдань</h2><p>Відділення й НД визначаються з вмісту Word-шаблону.</p></div><button id="assignment-open-upload" class="button primary">${icon('plus')}Додати шаблон</button></div>
      <section class="panel library-panel"><div class="library-toolbar"><label class="search-box">${icon('search')}<input id="assignment-library-search" type="search" placeholder="Назва продукту, НД або синонім" aria-label="Пошук шаблонів завдань"></label><label class="category-filter"><span class="sr-only">Відділення</span><select id="assignment-library-dept"><option value="">Усі відділення</option>${DEPTS.map(d => `<option value="${d.id}">${d.name}</option>`).join('')}</select></label></div><div id="assignment-template-list"></div><div id="assignment-library-empty" class="empty-state" hidden><h3>Шаблонів не знайдено</h3><p>Змініть пошук або додайте Word-шаблон.</p></div></section>
      <div class="notice soft">${icon('info')}<div>Щоб змінити показники, методи випробувань, НД або відділення, відредагуйте сам Word-файл і додайте його як новий шаблон. Назва та синоніми для пошуку редагуються тут.</div></div>
    </section>`;
  $('.main-content').append(view);
  const dialog = document.createElement('dialog');
  dialog.id = 'assignment-template-dialog';
  dialog.className = 'modal';
  dialog.innerHTML = `<form id="assignment-template-form"><div class="modal-heading"><div><p class="eyebrow">ЗАВДАННЯ</p><h2 id="assignment-dialog-title">Додати шаблон завдання</h2></div><button type="button" id="assignment-dialog-close" class="icon-button" aria-label="Закрити">${icon('close')}</button></div><div class="modal-body"><label id="assignment-template-file-label" class="field"><span>Word-файл *</span><input id="assignment-template-file" type="file" accept=".docx"><small>Формат .docx, до 25 МБ.</small></label><label class="field"><span>Назва шаблону</span><input id="assignment-template-name" maxlength="250" placeholder="Наприклад, Свинина — бактеріологія"></label><label class="field"><span>Синоніми назви продукту</span><textarea id="assignment-template-aliases" rows="3" placeholder="Кожен варіант з нового рядка"></textarea><small>Розділяйте комами або новими рядками.</small></label><label class="field"><span>Область застосування</span><select id="assignment-template-scope"><option value="">Точне найменування</option><option value="first_course">Готові перші страви</option><option value="jam">Джеми</option><option value="ready_second_cereals_potatoes">Другі страви: крупи та картопля</option><option value="ready_second_all">Усі готові другі страви</option></select><small>Розширюйте область лише для шаблону, показники та методи якого придатні для цієї групи продуктів.</small></label><label id="assignment-template-department-label" class="field"><span>Відділення (якщо не визначене в документі)</span><select id="assignment-template-department"><option value="">Автоматично з документа</option>${DEPTS.map(d => `<option value="${d.id}">${d.name}</option>`).join('')}</select></label><div id="assignment-template-metadata" class="notice soft" hidden></div><div class="notice soft compact">Відділення та нормативний документ читаються з Word. Таблиці показників і методів зберігаються з обраного шаблону.</div><p id="assignment-template-error" class="field-error" role="alert" hidden></p></div><div class="modal-footer"><button type="button" id="assignment-dialog-cancel" class="button secondary">Скасувати</button><button type="submit" id="assignment-template-save" class="button primary">Зберегти шаблон</button></div></form>`;
  document.body.append(dialog);
  const issuerDialog = document.createElement('dialog');
  issuerDialog.id = 'assignment-issuer-dialog';
  issuerDialog.className = 'modal';
  issuerDialog.innerHTML = `<form id="assignment-issuer-form"><div class="modal-heading"><div><p class="eyebrow">ОСОБА, ЯКА ВИДАЄ ЗАВДАННЯ</p><h2>Сформулював та видав завдання</h2></div><button type="button" id="assignment-issuer-close" class="icon-button" aria-label="Закрити">${icon('close')}</button></div><div class="modal-body"><label class="field"><span>Збережена особа</span><select id="assignment-issuer-select"><option value="">Залишити особу з Word-шаблону</option></select></label><div class="source-actions"><button type="button" id="assignment-issuer-new" class="text-button">Додати нову особу</button><button type="button" id="assignment-issuer-delete" class="text-button danger" hidden>Видалити особу</button></div><label class="field"><span>Ім’я та прізвище</span><input id="assignment-issuer-name" maxlength="300" placeholder="Наприклад, Олександр ПРІЗВИЩЕ"></label><label class="field"><span>Звання / посада</span><input id="assignment-issuer-position" maxlength="300" placeholder="Наприклад, старший лейтенант"></label><button type="button" id="assignment-issuer-save" class="button secondary">Зберегти дані особи</button><div class="notice soft compact">Обрана особа підставляється в рядок «Сформулював та видав завдання» або «Видав» у завданнях усіх відділень. Вибір зберігається для наступних актів.</div><p id="assignment-issuer-error" class="field-error" role="alert" hidden></p></div><div class="modal-footer"><button type="button" id="assignment-issuer-cancel" class="button secondary">Скасувати</button><button type="submit" id="assignment-issuer-apply" class="button primary">Використовувати</button></div></form>`;
  document.body.append(issuerDialog);

  function notice(message, error = false) {
    const node = document.createElement('div');
    node.className = `toast${error ? ' error' : ''}`;
    const label = document.createElement('span');
    label.textContent = message;
    node.append(label);
    const close = document.createElement('button');
    close.innerHTML = icon('close');
    close.setAttribute('aria-label', 'Закрити повідомлення');
    close.addEventListener('click', () => node.remove());
    node.append(close);
    $('#toast-region').append(node);
    setTimeout(() => node.remove(), error ? 18000 : 7000);
  }
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = `Помилка запиту (${response.status}).`;
      try {
        const body = await response.json();
        message = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map(v => v.msg).join('; ') : body.message || message;
      } catch {}
      throw new Error(message);
    }
    return response;
  }
  const post = (url, body) => api(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const deptName = id => DEPTS.find(d => d.id === id)?.name || id;
  const displayDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value.split('-').reverse().join('.') : value || '—';
  const navigate = () => document.dispatchEvent(new CustomEvent('protokol:navigate', {detail: {name: 'assignments'}}));
  function switchTab(library) {
    $('#assignment-library').hidden = !library;
    $('#assignment-work').hidden = library;
    $('#assignment-tab-library').classList.toggle('active', library);
    $('#assignment-tab-work').classList.toggle('active', !library);
    $('#assignment-tab-library').setAttribute('aria-selected', String(library));
    $('#assignment-tab-work').setAttribute('aria-selected', String(!library));
  }
  $('#assignment-tab-work').addEventListener('click', () => switchTab(false));
  $('#assignment-tab-library').addEventListener('click', () => switchTab(true));

  async function load() {
    if (state.loading) return state.loading;
    state.loading = (async () => {
    $('#assignment-loading').hidden = false;
    $('#assignment-load-error').hidden = true;
    try {
      const [templatesResponse, numberResponse, settingsResponse] = await Promise.all([api('/api/assignments/templates'), api('/api/assignments/numbering'), api('/api/settings')]);
      const [data, number, settings] = await Promise.all([templatesResponse.json(), numberResponse.json(), settingsResponse.json()]);
      state.issuers = settings.issuers || [];
      const issuerId = state.issuers.some(issuer => issuer.id === settings.default_issuer_id) ? settings.default_issuer_id : '';
      if (state.loaded && state.issuerId !== issuerId) invalidate();
      state.issuerId = issuerId;
      renderIssuerSummary();
      state.templates = data.templates || [];
      state.items.forEach(item => DEPTS.forEach(department => {
        const selection = item.departments[department.id];
        if (selection.template_id && !state.templates.some(template => template.id === selection.template_id)) {
          selectDepartmentTemplate(item, department.id, '');
        }
        syncDepartmentWeight(item, department.id);
      }));
      state.today = data.today || '';
      state.nextNumber = number.next_number ?? null;
      state.loaded = true;
      $('#assignment-template-count').textContent = state.templates.length;
      $('#nav-assignment-count').textContent = state.templates.length;
      renderLibrary();
      await hydrateNumbers();
      renderNumbering();
      renderItems();
      updateReadiness();
    } catch (error) {
      $('#assignment-load-error p').textContent = error.message;
      $('#assignment-load-error').hidden = false;
    } finally {
      state.loading = false;
      $('#assignment-loading').hidden = true;
    }
    })();
    return state.loading;
  }
  $('#assignment-retry').addEventListener('click', load);
  document.addEventListener('protokol:view', event => {
    if (event.detail.name === 'assignments' && !state.loaded && !state.loading) load();
  });

  function renderIssuerSummary() {
    const issuer = state.issuers.find(value => value.id === state.issuerId);
    $('#assignment-issuer-summary').textContent = issuer
      ? `Сформулював та видав завдання: ${[issuer.position,issuer.name].filter(Boolean).join(' ')}. Джерело: довідник осіб.`
      : 'Особа, яка сформулювала та видала завдання: з обраного Word-шаблону.';
  }
  function renderIssuerOptions(selected = state.issuerId) {
    $('#assignment-issuer-select').innerHTML = '<option value="">Залишити особу з Word-шаблону</option>' +
      state.issuers.map(issuer => `<option value="${esc(issuer.id)}">${esc([issuer.position,issuer.name].filter(Boolean).join(' '))}</option>`).join('');
    $('#assignment-issuer-select').value = selected;
    fillIssuerForm();
  }
  function fillIssuerForm() {
    const issuer = state.issuers.find(value => value.id === $('#assignment-issuer-select').value);
    $('#assignment-issuer-name').value = issuer?.name || '';
    $('#assignment-issuer-position').value = issuer?.position || '';
    $('#assignment-issuer-delete').hidden = !issuer;
    $('#assignment-issuer-error').hidden = true;
  }
  async function refreshIssuers() {
    const settings = await (await api('/api/settings')).json();
    state.issuers = settings.issuers || [];
    return settings;
  }
  function issuerError(error) {
    $('#assignment-issuer-error').textContent = error.message;
    $('#assignment-issuer-error').hidden = false;
  }
  function issuerBusy(value) {
    ['apply','save','delete','new'].forEach(name => { $(`#assignment-issuer-${name}`).disabled = value; });
  }
  async function saveIssuerProfile() {
    const id = $('#assignment-issuer-select').value;
    const values = {name:$('#assignment-issuer-name').value.trim(),position:$('#assignment-issuer-position').value.trim()};
    if (!values.name) throw new Error('Укажіть ім’я та прізвище особи.');
    const response = await api(`/api/settings/issuers${id ? `/${encodeURIComponent(id)}` : ''}`, {
      method:id?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)
    });
    const saved = await response.json();
    await refreshIssuers();
    if (saved.id === state.issuerId) invalidate();
    renderIssuerOptions(saved.id);
    renderIssuerSummary();
    updateReadiness();
    return saved.id;
  }
  $('#assignment-open-issuer').addEventListener('click', async () => {
    if (state.busy) return;
    try {
      await refreshIssuers();
      renderIssuerOptions();
      issuerDialog.showModal();
    } catch (error) { notice(error.message,true); }
  });
  ['#assignment-issuer-close','#assignment-issuer-cancel'].forEach(id => $(id).addEventListener('click', () => issuerDialog.close()));
  issuerDialog.addEventListener('click',event => { if(event.target === issuerDialog) issuerDialog.close(); });
  $('#assignment-issuer-select').addEventListener('change',fillIssuerForm);
  $('#assignment-issuer-new').addEventListener('click', () => {
    $('#assignment-issuer-select').value = '';
    fillIssuerForm();
    $('#assignment-issuer-name').focus();
  });
  $('#assignment-issuer-save').addEventListener('click',async () => {
    issuerBusy(true);
    try { await saveIssuerProfile(); notice('Дані особи збережено. Натисніть «Використовувати», щоб обрати її для завдань.'); }
    catch(error) { issuerError(error); }
    finally { issuerBusy(false); }
  });
  $('#assignment-issuer-delete').addEventListener('click',async () => {
    const id = $('#assignment-issuer-select').value;
    if (!id || !window.confirm('Видалити цю особу з довідника? Уже створені файли збережуть попередні дані.')) return;
    issuerBusy(true);
    try {
      await api(`/api/settings/issuers/${encodeURIComponent(id)}`,{method:'DELETE'});
      await refreshIssuers();
      if (state.issuerId === id) { state.issuerId=''; invalidate(); }
      renderIssuerOptions('');
      renderIssuerSummary();
      updateReadiness();
    } catch(error) { issuerError(error); }
    finally { issuerBusy(false); }
  });
  $('#assignment-issuer-form').addEventListener('submit',async event => {
    event.preventDefault();
    issuerBusy(true);
    try {
      let id = $('#assignment-issuer-select').value;
      const issuer = state.issuers.find(value => value.id === id);
      const name = $('#assignment-issuer-name').value.trim(), position = $('#assignment-issuer-position').value.trim();
      if (id || name || position) {
        if (!issuer || issuer.name !== name || issuer.position !== position) id = await saveIssuerProfile();
      }
      await api('/api/settings',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({default_issuer_id:id})});
      state.issuerId=id;
      invalidate(); renderIssuerSummary(); updateReadiness();
      issuerDialog.close();
      notice(id ? 'Особу для завдань обрано.' : 'У завданнях залишиться особа з відповідного Word-шаблону.');
    } catch(error) { issuerError(error); }
    finally { issuerBusy(false); }
  });

  function blankItem(data = {}) {
    const departments = Object.fromEntries(DEPTS.map(d => [d.id, {enabled:false, template_id:'', sample_weight:'', weight_edited:false, weight_from_template:false}]));
    return {id: uid(), product_name:'', standard:'', sample_weight:'', assigned_number:null, matches:{}, reviewMatches:{}, matched:false,
      sources:Object.fromEntries(['product_name','standard','sample_weight'].map(key => [key,{kind:'manual',label:'Введено вручну'}])), ...data, departments};
  }
  function defaultDepartmentWeight(item, departmentId) {
    const common = item.departments[departmentId].weight_from_template ? '' : item.sample_weight.trim();
    if (common) return {value:common, source:'common'};
    const template = state.templates.find(value => value.id === item.departments[departmentId].template_id);
    return {value:String(template?.sample_weight || ''), source:template ? 'template' : ''};
  }
  function syncDepartmentWeight(item, departmentId) {
    const selection = item.departments[departmentId];
    if (!selection.weight_edited) {
      const defaults = defaultDepartmentWeight(item, departmentId);
      selection.sample_weight = defaults.value;
      const template = state.templates.find(value => value.id === selection.template_id);
      selection.weight_source = defaults.source === 'common'
        ? {kind:'derived',label:`Зі спільного поля: ${originText(item.sources?.sample_weight)}`}
        : defaults.source === 'template' ? {kind:'template',label:`Із шаблону: ${template?.name || 'обраний шаблон'}`} : null;
    }
  }
  function selectDepartmentTemplate(item, departmentId, templateId) {
    const selection = item.departments[departmentId];
    selection.template_id = templateId;
    selection.allow_standard_override = false;
    syncDepartmentWeight(item, departmentId);
  }
  function departmentWeightHint(item, departmentId) {
    const selection = item.departments[departmentId];
    if (selection.weight_edited) return selection.sample_weight.trim()
      ? 'Змінено вручну. Це значення буде використано в завданні.'
      : 'Поле очищено вручну. У завданні буде «[НЕ ЗАПОВНЕНО]».';
    const defaults = defaultDepartmentWeight(item, departmentId);
    if (defaults.source === 'common') return `Підставлено зі спільного поля зразка. Джерело: ${originText(item.sources?.sample_weight)}.`;
    if (!defaults.source) return 'Оберіть шаблон, щоб підставити вагу для цього відділення.';
    return defaults.value.trim()
      ? `${originText(selection.weight_source)}. За потреби відредагуйте.`
      : 'У шаблоні вагу не вказано. Заповніть за потреби.';
  }
  function updateDepartmentWeightControl(item, departmentId, card) {
    const selection = item.departments[departmentId];
    const input = $(`[data-dept-weight="${departmentId}"]`,card);
    if (input && input.value !== selection.sample_weight) input.value = selection.sample_weight;
    const hint = $(`[data-dept-weight-hint="${departmentId}"]`,card);
    if (hint) hint.textContent = departmentWeightHint(item, departmentId);
    const reset = $(`[data-dept-weight-reset="${departmentId}"]`,card);
    if (reset) {
      const defaults = defaultDepartmentWeight(item, departmentId);
      reset.hidden = !selection.weight_edited && defaults.source !== 'common';
      reset.disabled = !state.templates.some(template => template.id === selection.template_id);
    }
  }
  function invalidate() {
    state.result = null;
    state.jobId = uid();
    $('#assignment-confirmed').checked = false;
    $('#assignment-success').hidden = true;
    document.dispatchEvent(new CustomEvent('protokol:assignments-invalidated'));
    $$('.assignment-number-preview').forEach((node, index) => node.textContent = previewNumber(index));
    changed();
  }
  function startingNumber() {
    if (state.nextNumber !== null) return Number(state.nextNumber);
    const value = Number(state.initialNumber);
    return Number.isInteger(value) && value >= 1 && value <= 2147483647 ? value : null;
  }
  function previewNumber(index) {
    if (state.items[index]?.assigned_number) return String(state.items[index].assigned_number);
    const value = startingNumber();
    const offset = state.items.slice(0,index).filter(item => !item.assigned_number).length;
    return value === null ? '—' : String(value + offset);
  }
  async function hydrateNumbers() {
    if (!state.items.length) return;
    const ids = state.items.map(item => item.id);
    try {
      const response = await post('/api/assignments/numbering/preview', {sample_ids:ids});
      const result = await response.json();
      if (result.next_number !== undefined) state.nextNumber = result.next_number;
      state.items.forEach(item => {
        if (result.numbers?.[item.id] !== undefined) item.assigned_number = String(result.numbers[item.id]);
      });
    } catch (error) { notice(`Не вдалося перевірити номери зразків: ${error.message}`, true); }
  }
  function renderNumbering() {
    const input = $('#assignment-start-number');
    const existing = state.nextNumber !== null;
    input.readOnly = existing;
    input.value = existing ? state.nextNumber : state.initialNumber;
    $('#assignment-start-label').textContent = existing ? 'Початковий номер для нових зразків' : 'Перший номер зразка *';
    $('#assignment-number-hint').textContent = existing ? 'Кнопкою «Задати початковий номер» можна вказати будь-яке додатне число, зокрема менше. Уже створені зразки збережуть свої номери; ручний вибір може повторити раніше використаний номер.' : 'Вкажіть будь-який додатний початковий номер. Лічильник збережеться лише після успішного створення завдань.';
    $('#assignment-today').textContent = displayDate(state.today);
    $('#assignment-change-next').hidden = !existing;
    $('#assignment-next-label').textContent = existing ? `У збереженому лічильнику наступний номер: ${state.nextNumber}.` : 'Лічильник ще не налаштовано.';
  }
  $('#assignment-start-number').addEventListener('input', event => {
    state.initialNumber = event.target.value;
    invalidate();
    updateReadiness();
  });
  $('#assignment-change-next').addEventListener('click', async () => {
    if (state.busy) return;
    const value = window.prompt('Початковий номер для нових зразків: будь-яке додатне ціле число, зокрема менше за поточне. Уже створені завдання збережуть свої номери.', state.nextNumber);
    if (value === null) return;
    const number = Number(value);
    if (!Number.isInteger(number) || number < 1 || number > 2147483647) return notice('Вкажіть цілий додатний номер.', true);
    try {
      const response = await post('/api/assignments/numbering', {next_number:number});
      const result = await response.json();
      state.nextNumber = result.next_number;
      invalidate();
      renderNumbering();
      renderItems();
      notice('Початковий номер для нових зразків збережено.');
    } catch (error) { notice(error.message, true); }
  });

  function renderSource() {
    $('#assignment-source').innerHTML = `${icon('file')}<div><strong>${esc(state.source || 'Ручне введення зразків')}</strong><small>Позицій: ${state.items.length} · НД та шаблони потребують перевірки</small></div>${state.sourceUrl ? `<a class="source-original-link" href="${esc(state.sourceUrl)}" target="_blank" rel="noopener noreferrer">Відкрити оригінал акта ${icon('arrow')}</a>` : ''}`;
  }
  async function importData(data, source) {
    const manual = !source || source === 'Ручне введення';
    state.items = (data.items || []).map(item => blankItem({id:item.id || uid(), product_name:item.product_name || '', standard:item.standard || '', sample_weight:'',
      sources:Object.fromEntries(['product_name','standard','sample_weight'].map(key => [key,
        key === 'sample_weight' ? {kind:'manual',label:'Не заповнено'} : data.provenance?.items?.[item.id]?.[key] || {kind:manual?'manual':'act',label:manual?'Введено вручну':`З акта: ${source || 'акт відбору'}`,source:source || ''}]))}));
    state.text = data.text || '';
    state.source = source || 'Акт відбору';
    $('#assignment-raw-text').value = state.text;
    $('#assignment-editor').hidden = false;
    $('#assignment-upload-panel').hidden = true;
    $('#assignment-processing').hidden = true;
    invalidate();
    await hydrateNumbers();
    renderSource();
    const warnings = [...(data.warnings || [])];
    if (state.text && state.items.some(item => !item.standard.trim())) warnings.push('НД на відповідність не прочитано для частини зразків. Вкажіть його за актом перед підбором завдань.');
    state.warnings = warnings;
    $('#assignment-warnings').hidden = warnings.length === 0;
    $('#assignment-warnings').innerHTML = `<strong>Перевірте перед видачею завдань</strong><ul>${warnings.map(v => `<li>${esc(v)}</li>`).join('')}</ul>`;
    renderNumbering();
    renderItems();
    changed();
    for (const item of state.items) {
      if (item.product_name.trim() && item.standard.trim()) await findMatches(item, false);
    }
    renderItems();
  }
  document.addEventListener('protokol:assignments-import', async event => {
    if (state.busy) return notice('Дочекайтеся завершення поточної операції.', true);
    if (state.items.length && !window.confirm('Передати зразки з акта? Поточний список завдань буде замінено.')) return;
    navigate();
    switchTab(false);
    if (!state.loaded) await load();
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.sourceUrl = '';
    await importData(event.detail, event.detail.source || 'Зразки з акта');
  });
  $('#assignment-upload').addEventListener('click', () => $('#assignment-act-file').click());
  $('#assignment-act-file').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file || state.busy) return;
    if (file.size > 25 * 1024 * 1024) return notice('Максимальний розмір файла — 25 МБ.', true);
    if (!/\.(pdf|jpe?g|png|tiff?|docx|txt)$/i.test(file.name)) return notice('Підтримуються PDF, JPG, PNG, TIFF, DOCX і TXT.', true);
    state.busy = true;
    $('#assignment-upload-panel').hidden = true;
    $('#assignment-processing').hidden = false;
    $('#assignment-processing-label').textContent = `Опрацьовуємо «${file.name}»…`;
    try {
      const form = new FormData();
      form.append('file', file);
      const response = await api('/api/extract', {method:'POST', body:form});
      const data = await response.json();
      if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
      state.sourceUrl = URL.createObjectURL(file);
      await importData(data, file.name);
    } catch (error) {
      $('#assignment-processing').hidden = true;
      $('#assignment-upload-panel').hidden = false;
      notice(error.message, true);
    } finally {
      state.busy = false;
      event.target.value = '';
      updateReadiness();
    }
  });
  $('#assignment-manual').addEventListener('click', () => importData({items:[{}]}, 'Ручне введення'));
  $('#assignment-new').addEventListener('click', () => {
    if (state.busy) return;
    if (state.items.length && !window.confirm('Почати новий список? Поточні незбережені дані буде очищено.')) return;
    state.items = []; state.text = ''; state.source = ''; state.warnings = [];
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.sourceUrl = '';
    invalidate();
    $('#assignment-editor').hidden = true;
    $('#assignment-upload-panel').hidden = false;
    $('#assignment-processing').hidden = true;
    switchTab(false);
    renderItems(); renderSource();
  });
  $('#assignment-raw-text').addEventListener('input',event => { state.text=event.target.value; changed(); });
  $('#assignment-reparse').addEventListener('click', async () => {
    const text = $('#assignment-raw-text').value.trim();
    if (!text) return notice('Вкажіть текст акта.', true);
    const message = state.items.some(item => item.assigned_number) ? 'Повторний розбір створить новий список зразків із новими номерами. Щоб виправити вже зареєстрований зразок зі збереженням номера, змініть поля нижче. Продовжити повторний розбір?' : 'Повторно розібрати текст? Поля зразків і вибір шаблонів буде замінено.';
    if (!window.confirm(message)) return;
    const button = $('#assignment-reparse');
    button.disabled = true;
    try {
      const response = await post('/api/parse', {text});
      await importData({...await response.json(), text}, state.source);
    } catch (error) { notice(error.message, true); }
    finally { button.disabled = false; }
  });

  function templateOptions(item, departmentId, query = '') {
    const matches = new Set((item.matches[departmentId] || []).map(t => t.id));
    const reviews = new Set((item.reviewMatches[departmentId] || []).map(t => t.id));
    const selected = item.departments[departmentId].template_id;
    const terms = query.toLocaleLowerCase('uk').trim().split(/\s+/).filter(Boolean);
    const templates = state.templates.filter(t => t.department === departmentId && (t.id === selected ||
      terms.every(term => [t.name,t.product_name,t.standard,t.filename,...(t.aliases || [])].join(' ').toLocaleLowerCase('uk').includes(term))));
    const groups = [
      ['Назва та НД збігаються', templates.filter(t => matches.has(t.id))],
      ['Рекомендації з іншим НД', templates.filter(t => !matches.has(t.id) && reviews.has(t.id))],
      ['Вся база відділення — ручний вибір', templates.filter(t => !matches.has(t.id) && !reviews.has(t.id))]
    ];
    return `<option value="">Оберіть шаблон з усієї бази відділення</option>` + groups.map(([label,entries]) => entries.length
      ? `<optgroup label="${label}">${entries.map(t => `<option value="${esc(t.id)}" data-template-name="${esc(t.name)}" data-template-standard="${esc(t.standard)}" ${t.id === selected ? 'selected' : ''}>${esc(t.name)} · ${esc(t.standard)} · ${esc(t.filename)}</option>`).join('')}</optgroup>` : '').join('');
  }
  function needsManualReview(item, departmentId) {
    const id = item.departments[departmentId].template_id;
    return !!id && !(item.matches[departmentId] || []).some(t => t.id === id);
  }
  function renderDepartment(item, department) {
    const selection = item.departments[department.id];
    const matches = (item.matches[department.id] || []).filter(m => state.templates.some(t => t.id === m.id));
    const selectedTemplate = state.templates.find(t => t.id === selection.template_id);
    const needsOverride = needsManualReview(item, department.id);
    return `<section class="assignment-department ${selection.enabled ? 'enabled' : ''}" data-department="${department.id}">
      <label class="assignment-dept-toggle"><input type="checkbox" data-dept-enabled="${department.id}" ${selection.enabled ? 'checked' : ''}><strong>${department.name}</strong></label>
      <div class="assignment-dept-fields" ${selection.enabled ? '' : 'hidden'}><label class="field"><span>Пошук у всій базі відділення</span><input type="search" data-dept-search="${department.id}" placeholder="Назва, НД, синонім або ім’я файла"></label><label class="field"><span>Шаблон завдання *</span><select data-dept-template="${department.id}">${templateOptions(item,department.id)}</select></label>
      ${selectedTemplate ? `<a class="text-button" href="/api/assignments/templates/${encodeURIComponent(selectedTemplate.id)}/download" download>Переглянути Word-шаблон</a>` : ''}
      ${needsOverride ? `<div class="assignment-standard-warning"><strong>Шаблон обрано вручну з усієї бази</strong><br>Зразок: ${esc(item.product_name)}<br>В акті: ${esc(item.standard)}<br>У шаблоні: ${esc(selectedTemplate?.standard || 'не зазначено')}<br>У завданні залишаться НД та методи саме цього шаблону.<label><input type="checkbox" data-dept-override="${department.id}" ${selection.allow_standard_override ? 'checked' : ''}><span>Підтверджую ручний вибір і застосування цього шаблону</span></label></div>` : ''}
      <label class="field"><span>Вага та кількість для відділення</span><input data-dept-weight="${department.id}" value="${esc(selection.sample_weight)}" placeholder="Вага з шаблону або власне значення"><small data-dept-weight-hint="${department.id}">${esc(departmentWeightHint(item, department.id))}</small></label><button type="button" class="text-button" data-dept-weight-reset="${department.id}" ${selection.weight_edited || defaultDepartmentWeight(item, department.id).source === 'common' ? '' : 'hidden'} ${selectedTemplate ? '' : 'disabled'}>Взяти з шаблону</button></div>
      <p class="assignment-dept-hint ${!matches.length ? 'no-match' : ''}">${!item.product_name.trim() || !item.standard.trim() ? 'Для автоматичного підбору вкажіть назву та НД зразка. Шаблон можна обрати вручну з усієї бази.' : !matches.length ? 'Автоматичного збігу немає. Увімкніть відділення та оберіть потрібний шаблон з усієї бази вручну.' : `Відповідних шаблонів: ${matches.length}. Також доступна вся база відділення для ручного вибору.`}</p>
      ${selection.enabled && matches.length ? `<div class="match-actions">${matches.slice(0,2).map(m => `<button type="button" class="match-chip" data-assignment-match="${esc(m.id)}" data-match-department="${department.id}" title="${esc(m.reason || 'Перевірте відповідність шаблону')}"><span>${esc(m.name)}</span><small>Схожість ${Math.round(Number(m.score) * 100)}%</small></button>`).join('')}</div>` : ''}
      </section>`;
  }
  function renderItems() {
    state.items.forEach(item => DEPTS.forEach(department => syncDepartmentWeight(item, department.id)));
    $('#assignment-item-count').textContent = state.items.length;
    $('#assignment-empty').hidden = state.items.length > 0;
    $('#assignment-items').innerHTML = state.items.map((item, index) => `<article class="panel item-card assignment-item" data-assignment-item="${esc(item.id)}">
      <div class="item-heading"><div class="item-label"><span class="item-number">${index + 1}</span><div><h3 class="item-title">${esc(item.product_name || 'Новий зразок')}</h3><p class="item-subtitle">Номер зразка: <strong class="assignment-number-preview">${esc(previewNumber(index))}</strong> · той самий для всіх відділень</p></div></div><button type="button" class="icon-button danger assignment-remove" aria-label="Видалити зразок ${index + 1}">${icon('trash')}</button></div>
      <div class="form-grid"><label class="field wide"><span>Назва зразка *</span><input data-assignment-field="product_name" value="${esc(item.product_name)}" placeholder="Повне найменування з акта" required>${sourceHint(item,'product_name')}</label><label class="field"><span>НД на відповідність *</span><input data-assignment-field="standard" value="${esc(item.standard)}" placeholder="Наприклад, ДСТУ 4450:2005" required>${sourceHint(item,'standard')}</label><label class="field"><span>Спільна вага та кількість (за потреби)</span><input data-assignment-field="sample_weight" value="${esc(item.sample_weight)}" placeholder="Залиште порожнім для ваги з шаблонів"><small>Замінює вагу з шаблонів для відділень без ручних змін.</small>${sourceHint(item,'sample_weight')}</label></div>
      <div class="assignment-match-toolbar"><button type="button" class="text-button assignment-find">Підібрати за назвою та НД</button><small>Дата видачі: ${esc(displayDate(state.today))}</small></div>
      <div class="assignment-departments">${DEPTS.map(d => renderDepartment(item,d)).join('')}</div>
    </article>`).join('');
    updateReadiness();
  }
  async function findMatches(item, render = true) {
    if (!item.product_name.trim() || !item.standard.trim()) return notice('Спочатку вкажіть назву та НД зразка.', true);
    const name = item.product_name, standard = item.standard;
    try {
      const response = await post('/api/assignments/match', {product_name:name, standard});
      const data = await response.json();
      if (item.product_name !== name || item.standard !== standard) return;
      item.matches = data.departments || {};
      item.reviewMatches = data.review_departments || {};
      item.matched = true;
      for (const department of DEPTS) {
        const selection = item.departments[department.id];
        const matches = item.matches[department.id] || [];
        const stillAvailable = state.templates.some(template => template.id === selection.template_id && template.department === department.id);
        if (!stillAvailable) selectDepartmentTemplate(item, department.id, '');
        syncDepartmentWeight(item, department.id);
        selection.enabled = matches.length > 0 || !!selection.template_id;
        selection.allow_standard_override = false;
      }
      if (render) {
        invalidate();
        renderItems();
        if (data.warnings?.length) notice(data.warnings.join(' '));
      }
    } catch (error) { notice(error.message, true); }
  }
  $('#assignment-items').addEventListener('input', event => {
    const input = event.target;
    const card = input.closest('[data-assignment-item]');
    if (!card) return;
    const item = state.items.find(v => v.id === card.dataset.assignmentItem);
    if (!item) return;
    if (input.dataset.deptSearch) {
      $(`[data-dept-template="${input.dataset.deptSearch}"]`,card).innerHTML = templateOptions(item,input.dataset.deptSearch,input.value);
      return;
    }
    if (input.dataset.assignmentField) {
      const key = input.dataset.assignmentField;
      item[key] = input.value;
      item.sources ||= {};
      item.sources[key] = {kind:'manual',label:'Введено вручну'};
      const originNode = $(`[data-assignment-origin="${key}"]`,card);
      if (originNode) originNode.textContent = 'Джерело: введено вручну';
      if (key === 'product_name' || key === 'standard') {
        item.matches = {}; item.reviewMatches = {}; item.matched = false;
        DEPTS.forEach(d => { item.departments[d.id].allow_standard_override = false; });
        $('.item-title',card).textContent = item.product_name || 'Новий зразок';
        $('.assignment-departments',card).innerHTML = DEPTS.map(d => renderDepartment(item,d)).join('');
      } else if (key === 'sample_weight') {
        DEPTS.forEach(department => {
          syncDepartmentWeight(item, department.id);
          updateDepartmentWeightControl(item, department.id, card);
        });
      }
    } else if (input.dataset.deptWeight) {
      const selection = item.departments[input.dataset.deptWeight];
      selection.sample_weight = input.value;
      selection.weight_edited = true;
      selection.weight_source = {kind:'manual',label:'Введено вручну'};
      updateDepartmentWeightControl(item, input.dataset.deptWeight, card);
    }
    else return;
    invalidate();
    updateReadiness();
  });
  $('#assignment-items').addEventListener('change', event => {
    const input = event.target;
    const card = input.closest('[data-assignment-item]');
    if (!card) return;
    const item = state.items.find(v => v.id === card.dataset.assignmentItem);
    if (!item) return;
    if (input.dataset.deptEnabled) {
      item.departments[input.dataset.deptEnabled].enabled = input.checked;
      invalidate(); renderItems();
    } else if (input.dataset.deptTemplate) {
      selectDepartmentTemplate(item, input.dataset.deptTemplate, input.value);
      invalidate(); renderItems();
    } else if (input.dataset.deptOverride) {
      item.departments[input.dataset.deptOverride].allow_standard_override = input.checked;
      invalidate(); updateReadiness();
    }
  });
  $('#assignment-items').addEventListener('protokol:choose-template',event=>{
    const select=event.target,card=select.closest('[data-assignment-item]');
    const item=state.items.find(value=>value.id===card?.dataset.assignmentItem),department=select.dataset.deptTemplate;
    if(!item)return;
    window.OZTemplatePicker.open({title:`Шаблони: ${deptName(department)}`,subtitle:item.product_name||'Ручний вибір із бази відділення',
      templates:state.templates.filter(t=>t.department===department).map(t=>({...t,group:deptName(t.department)})),
      selectedId:item.departments[department].template_id,recommendedIds:(item.matches[department]||[]).map(t=>t.id),
      downloadUrl:t=>`/api/assignments/templates/${encodeURIComponent(t.id)}/download`,
      focusSelector:`${itemSelector(item)} [data-dept-template="${department}"]`,
      onSelect:id=>{select.value=id;select.dispatchEvent(new Event('change',{bubbles:true}));}
    });
  });
  $('#assignment-items').addEventListener('click', async event => {
    const card = event.target.closest('[data-assignment-item]');
    if (!card) return;
    const item = state.items.find(v => v.id === card.dataset.assignmentItem);
    if (!item) return;
    const weightReset = event.target.closest('[data-dept-weight-reset]');
    if (weightReset) {
      const departmentId = weightReset.dataset.deptWeightReset;
      item.departments[departmentId].weight_edited = false;
      item.departments[departmentId].weight_from_template = true;
      syncDepartmentWeight(item, departmentId);
      updateDepartmentWeightControl(item, departmentId, card);
      invalidate(); updateReadiness();
      return;
    }
    if (event.target.closest('.assignment-remove')) {
      if (item.product_name && !window.confirm(`Видалити зразок «${item.product_name}»?`)) return;
      state.items = state.items.filter(v => v !== item);
      invalidate(); renderItems(); renderSource();
    }
    if (event.target.closest('.assignment-find')) {
      const button = event.target.closest('.assignment-find');
      button.disabled = true;
      button.textContent = 'Шукаємо…';
      await findMatches(item);
      button.disabled = false;
      button.textContent = 'Підібрати за назвою та НД';
    }
    const match = event.target.closest('[data-assignment-match]');
    if (match) {
      selectDepartmentTemplate(item, match.dataset.matchDepartment, match.dataset.assignmentMatch);
      invalidate(); renderItems();
    }
  });
  $('#assignment-add-item').addEventListener('click', async () => {
    state.items.push(blankItem());
    invalidate(); await hydrateNumbers(); renderNumbering(); renderItems(); renderSource();
    const card = $$('.assignment-item').at(-1);
    card.scrollIntoView({behavior:'smooth',block:'center'});
    $('input[data-assignment-field="product_name"]',card).focus({preventScroll:true});
  });
  $('#assignment-select-unambiguous').addEventListener('click', () => {
    if (state.busy || !state.loaded || !state.items.length) return;
    invalidate();
    let selected = 0;
    state.items.forEach(item => DEPTS.forEach(department => {
      const choice = item.departments[department.id];
      if (!choice.enabled || choice.template_id) return;
      const candidates = (item.matches[department.id] || []).filter(match => state.templates.some(template => template.id === match.id && template.department === department.id));
      if (candidates.length !== 1 || !Number.isFinite(Number(candidates[0].score)) || Number(candidates[0].score) < 0.5) return;
      selectDepartmentTemplate(item, department.id, candidates[0].id);
      selected++;
    }));
    renderItems();
    const unresolved = state.items.reduce((count,item) => count + DEPTS.filter(department => item.departments[department.id].enabled && !item.departments[department.id].template_id).length, 0);
    notice(`Обрано однозначних шаблонів: ${selected}. ${unresolved ? `Ще ${unresolved} потребують ручного вибору.` : 'Перевірте дані та підтвердьте їх перед створенням завдань.'}`);
  });

  function missing() {
    const issues = [];
    if (!state.loaded) issues.push('завантаження налаштувань');
    if (!state.items.length) issues.push('принаймні один зразок');
    if (state.items.some(item => !item.assigned_number) && startingNumber() === null) issues.push('початковий номер зразка');
    let count = 0;
    state.items.forEach((item,index) => {
      if (!item.product_name.trim()) issues.push(`зразок ${index + 1}: назва`);
      if (!item.standard.trim()) issues.push(`зразок ${index + 1}: НД`);
      const selected = DEPTS.filter(d => item.departments[d.id].enabled);
      if (!selected.length) issues.push(`зразок ${index + 1}: відділення`);
      selected.forEach(d => {
        const id = item.departments[d.id].template_id;
        if (!id || !state.templates.some(t => t.id === id && t.department === d.id)) issues.push(`зразок ${index + 1}: шаблон (${d.short})`);
        if (needsManualReview(item,d.id) && !item.departments[d.id].allow_standard_override) issues.push(`зразок ${index + 1}: підтвердження ручного вибору (${d.short})`);
        count++;
      });
    });
    return {issues,count};
  }
  function updateReadiness() {
    const {issues,count} = missing();
    const confirmed = $('#assignment-confirmed').checked;
    const ready = !issues.length && confirmed && !state.busy;
    $('#assignment-generate').disabled = !ready;
    $('#assignment-select-unambiguous').disabled = state.busy || !state.loaded || !state.items.length;
    $('#assignment-generation-help').classList.toggle('ready',ready);
    $('#assignment-generation-help').textContent = issues.length ? `Потрібно: ${issues.slice(0,3).join('; ')}${issues.length > 3 ? `; ще ${issues.length - 3}` : ''}.` : !confirmed ? 'Підтвердьте перевірку даних і шаблонів.' : state.result ? 'Архів можна завантажити повторно з тими самими номерами.' : `Буде створено ${count} завдань для ${state.items.length} зразків.`;
    $('#assignment-generate').innerHTML = state.busy ? 'Створюємо завдання…' : `${icon('download')}${state.result ? 'Завантажити повторно' : 'Створити завдання'}`;
  }
  function reviewIssues() {
    const issues = [];
    const add = (message,selector,level = 'error') => issues.push({message,selector,level,section:'assignments'});
    if (!state.loaded) add('Не завантажено шаблони та налаштування завдань.','#assignment-retry');
    if (!state.items.length) add('Додайте принаймні один зразок.','#assignment-manual');
    if (state.items.some(item => !item.assigned_number) && startingNumber() === null) add('Укажіть початковий номер нового зразка.','#assignment-start-number');
    state.items.forEach((item,index) => {
      const base = itemSelector(item);
      const field = key => `${base} [data-assignment-field="${key}"]`;
      if (!item.product_name.trim()) add(`Зразок ${index+1}: не заповнено назву.`,field('product_name'));
      if (!item.standard.trim()) add(`Зразок ${index+1}: не заповнено НД на відповідність.`,field('standard'));
      const selected = DEPTS.filter(department => item.departments[department.id].enabled);
      if (!selected.length) add(`Зразок ${index+1}: оберіть відділення.`,`${base} [data-dept-enabled="vet"]`);
      selected.forEach(department => {
        const selection = item.departments[department.id];
        const selector = `${base} [data-dept-template="${department.id}"]`;
        if (!selection.template_id || !state.templates.some(template => template.id === selection.template_id && template.department === department.id)) add(`Зразок ${index+1}: оберіть наявний шаблон (${department.short}).`,selector);
        if (needsManualReview(item,department.id) && !selection.allow_standard_override) add(`Зразок ${index+1}: підтвердьте ручний вибір шаблону (${department.short}).`,`${base} [data-dept-override="${department.id}"]`);
        if (!selection.sample_weight.trim()) add(`Зразок ${index+1}: не заповнено вагу й кількість (${department.short}); буде позначка «[НЕ ЗАПОВНЕНО]».`,`${base} [data-dept-weight="${department.id}"]`,'warning');
      });
    });
    state.warnings.forEach(message => add(message,'#assignment-warnings','warning'));
    return issues;
  }
  $('#assignment-confirmed').addEventListener('change', updateReadiness);
  $('#assignment-generate').addEventListener('click', async () => {
    if (missing().issues.length || !$('#assignment-confirmed').checked || state.busy) return;
    const base = startingNumber();
    const payload = state.result?.payload || {
      job_id:state.jobId, confirmed:true, start_number:base, issuer_id:state.issuerId,
      items:state.items.map(item => ({id:item.id,product_name:item.product_name.trim(),standard:item.standard.trim(),sample_weight:item.sample_weight.trim(),
        departments:Object.fromEntries(DEPTS.filter(d => item.departments[d.id].enabled).map(d => [d.id,{template_id:item.departments[d.id].template_id,sample_weight:item.departments[d.id].sample_weight.trim(),allow_standard_override:!!item.departments[d.id].allow_standard_override}]))}))
    };
    state.busy = true;
    $('#assignment-editor').disabled = true;
    updateReadiness();
    try {
      const response = await post('/api/assignments/generate',payload);
      const blob = await response.blob();
      const issued = response.headers.get('X-Assignment-Date') || state.today;
      const next = Number(response.headers.get('X-Next-Sample-Number'));
      let issuedNumbers;
      try { issuedNumbers = JSON.parse(response.headers.get('X-Sample-Numbers') || 'null'); } catch {}
      if (!Array.isArray(issuedNumbers) || issuedNumbers.length !== state.items.length) issuedNumbers = state.items.map((_,i)=>previewNumber(i));
      state.items.forEach((item,index) => { item.assigned_number = String(issuedNumbers[index]); });
      if (!state.result) state.result = {payload,numbers:issuedNumbers.map(String),date:issued};
      if (Number.isInteger(next) && next > 0) state.nextNumber=next;
      state.today=issued;
      renderNumbering();
      renderItems();
      $('#assignment-success').hidden=false;
      const numbers=state.result.numbers;
      $('#assignment-success').innerHTML=`${icon('check')}<div><strong>Завдання сформовано. Номери зразків: ${esc(numbers.slice(0,8).join(', '))}${numbers.length > 8 ? ` та ще ${numbers.length - 8}` : ''}.</strong><br>Дата видачі: ${esc(displayDate(issued))}. Повторне завантаження — у меню «Ще». Уже присвоєні номери зберігаються при виправленні даних і додаванні відділень.</div>`;
      document.dispatchEvent(new CustomEvent('protokol:assignments-ready',{detail:{payload:state.result.payload}}));
      changed();
      await window.OZOffice.deliver(blob,'zavdannia.zip','assignments');
      notice('Архів завдань готовий.');
    } catch(error) {
      notice(error.message,true);
      try {
        const response = await api('/api/assignments/numbering');
        const number = await response.json();
        if (number.next_number !== null && number.next_number !== state.nextNumber) {
          state.nextNumber = number.next_number;
          invalidate(); renderNumbering(); renderItems();
          notice('Лічильник оновлено з іншого вікна. Перевірте нові номери та підтвердьте дані знову.');
        }
      } catch {}
    } finally {
      state.busy=false;
      $('#assignment-editor').disabled=false;
      updateReadiness();
    }
  });

  function renderLibrary() {
    const search=$('#assignment-library-search').value.toLocaleLowerCase('uk').trim();
    const dept=$('#assignment-library-dept').value;
    const entries=state.templates.filter(t => (!dept||t.department===dept)&&[t.name,t.product_name,t.standard,...(t.aliases||[])].join(' ').toLocaleLowerCase('uk').includes(search));
    $('#assignment-template-list').innerHTML=entries.map(t=>`<article class="template-row" data-assignment-template="${esc(t.id)}"><div class="word-icon">${icon('file')}<small>DOCX</small></div><div class="template-info"><h3>${esc(t.name)}</h3><p>${esc(t.product_name)}</p>${t.review_note ? `<p class="assignment-template-note">${esc(t.review_note)}</p>` : ''}<div class="template-aliases"><span class="alias-tag assignment-standard-tag">${esc(t.standard)}</span><span class="alias-tag">${esc(deptName(t.department))}</span>${(t.aliases||[]).slice(0,4).map(a=>`<span class="alias-tag">${esc(a)}</span>`).join('')}</div></div><div class="row-actions"><a class="icon-button" href="/api/assignments/templates/${encodeURIComponent(t.id)}/download" title="Завантажити шаблон" aria-label="Завантажити ${esc(t.name)}">${icon('download')}</a><button type="button" class="icon-button assignment-edit-template" title="Редагувати" aria-label="Редагувати ${esc(t.name)}">${icon('edit')}</button><button type="button" class="icon-button danger assignment-delete-template" title="Видалити" aria-label="Видалити ${esc(t.name)}">${icon('trash')}</button></div></article>`).join('');
    $('#assignment-library-empty').hidden=entries.length>0;
  }
  $('#assignment-library-search').addEventListener('input',renderLibrary);
  $('#assignment-library-dept').addEventListener('change',renderLibrary);
  function openTemplate(template=null) {
    if (state.busy) return notice('Дочекайтеся завершення поточної операції.', true);
    state.editing=template;
    $('#assignment-template-form').reset();
    $('#assignment-dialog-title').textContent=template?'Редагувати шаблон':'Додати шаблон завдання';
    $('#assignment-template-file-label').hidden=!!template;
    $('#assignment-template-file').required=!template;
    $('#assignment-template-name').value=template?.name||'';
    $('#assignment-template-aliases').value=(template?.aliases||[]).join('\n');
    $('#assignment-template-scope').value=template?.product_scope||'';
    $('#assignment-template-department-label').hidden=!!template;
    $('#assignment-template-metadata').hidden=!template;
    $('#assignment-template-metadata').textContent=template?`${deptName(template.department)} · ${template.standard}`:'';
    $('#assignment-template-error').hidden=true;
    dialog.showModal();
  }
  $('#assignment-open-upload').addEventListener('click',()=>openTemplate());
  ['#assignment-dialog-close','#assignment-dialog-cancel'].forEach(id=>$(id).addEventListener('click',()=>dialog.close()));
  dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
  $('#assignment-template-list').addEventListener('click',async event=>{
    const row=event.target.closest('[data-assignment-template]');
    if(!row)return;
    const template=state.templates.find(t=>t.id===row.dataset.assignmentTemplate);
    if(!template)return;
    if(event.target.closest('.assignment-edit-template'))openTemplate(template);
    if(event.target.closest('.assignment-delete-template')){
      if(state.busy)return notice('Дочекайтеся завершення поточної операції.',true);
      if(!window.confirm(`Видалити шаблон «${template.name}»?`))return;
      try{
        await api(`/api/assignments/templates/${encodeURIComponent(template.id)}`,{method:'DELETE'});
        state.items.forEach(item=>DEPTS.forEach(d=>{if(item.departments[d.id].template_id===template.id)selectDepartmentTemplate(item,d.id,'');}));
        invalidate();
        await load();
        notice('Шаблон завдання видалено.');
      }catch(error){notice(error.message,true);}
    }
  });
  $('#assignment-template-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const name=$('#assignment-template-name').value.trim();
    const aliases=[...new Set($('#assignment-template-aliases').value.split(/[,\n]/).map(s=>s.trim()).filter(Boolean))];
    const file=$('#assignment-template-file').files[0];
    const product_scope=$('#assignment-template-scope').value;
    const department=$('#assignment-template-department').value;
    const errorNode=$('#assignment-template-error');
    if(!state.editing&&(!file||!file.name.toLowerCase().endsWith('.docx'))){errorNode.textContent='Виберіть Word-файл .docx.';errorNode.hidden=false;return;}
    if(file?.size>25*1024*1024){errorNode.textContent='Максимальний розмір — 25 МБ.';errorNode.hidden=false;return;}
    $('#assignment-template-save').disabled=true;
    errorNode.hidden=true;
    try{
      if(state.editing)await api(`/api/assignments/templates/${encodeURIComponent(state.editing.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,aliases,product_scope})});
      else{
        const form=new FormData();form.append('file',file);form.append('name',name);form.append('aliases',JSON.stringify(aliases));form.append('product_scope',product_scope);if(department)form.append('department',department);
        await api('/api/assignments/templates',{method:'POST',body:form});
      }
      dialog.close();
      await load();
      notice('Шаблон завдання збережено.');
    }catch(error){errorNode.textContent=error.message;errorNode.hidden=false;}
    finally{$('#assignment-template-save').disabled=false;}
  });
  window.OZWorkspace?.register('assignments',{
    title:'Завдання на дослідження',
    hasData:() => state.items.length > 0 || !!state.text || !!state.source,
    snapshot:() => state.items.length || state.text || state.source ? ({source:state.source,text:state.text,sourceHadFile:!!state.sourceUrl,warnings:clone(state.warnings),
      initialNumber:state.initialNumber,issuerId:state.issuerId,
      items:state.items.map(item => ({id:item.id,product_name:item.product_name,standard:item.standard,sample_weight:item.sample_weight,
        sources:clone(item.sources || {}),departments:Object.fromEntries(DEPTS.map(department => {
          const selection = item.departments[department.id];
          return [department.id,{enabled:!!selection.enabled,template_id:selection.template_id,sample_weight:selection.sample_weight,
            weight_edited:!!selection.weight_edited,weight_from_template:!!selection.weight_from_template}];
        }))}))}) : null,
    restore:async data => {
      if (state.busy) throw new Error('Дочекайтеся завершення поточної операції завдань.');
      await load();
      if (!state.loaded) throw new Error('Не вдалося завантажити актуальні шаблони та налаштування завдань.');
      const warnings = Array.isArray(data.warnings) ? data.warnings.map(String).slice(0,200) : [];
      const seen = new Set();
      const choices = new Map();
      state.items = (Array.isArray(data.items) ? data.items : []).slice(0,100).map(saved => {
        const id = typeof saved.id === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(saved.id) && !seen.has(saved.id) ? saved.id : uid();
        seen.add(id);
        const item = blankItem({id,product_name:String(saved.product_name || '').slice(0,12000),standard:String(saved.standard || '').slice(0,12000),
          sample_weight:String(saved.sample_weight || '').slice(0,12000),sources:saved.sources && typeof saved.sources === 'object' ? clone(saved.sources) : {}});
        choices.set(id,clone(saved.departments || {}));
        return item;
      });
      state.source=String(data.source || '').slice(0,1000);
      state.text=String(data.text || '').slice(0,250000);
      state.initialNumber=String(data.initialNumber || '').slice(0,30);
      state.issuerId=state.issuers.some(issuer => issuer.id === data.issuerId) ? data.issuerId : '';
      if (data.issuerId && !state.issuerId) warnings.push('Особу з чернетки вже видалено з довідника. Оберіть її повторно або залиште особу з шаблону.');
      if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
      state.sourceUrl='';
      for (const item of state.items) {
        if (item.product_name.trim() && item.standard.trim()) await findMatches(item,false);
        const savedDepartments=choices.get(item.id);
        DEPTS.forEach(department => {
          const saved=savedDepartments[department.id] || {};
          const selection=item.departments[department.id];
          selection.enabled=!!saved.enabled;
          const valid=state.templates.some(template => template.id === saved.template_id && template.department === department.id);
          selectDepartmentTemplate(item,department.id,valid ? saved.template_id : '');
          if (saved.template_id && !valid) warnings.push(`Зразок «${item.product_name || 'без назви'}»: збережений шаблон (${department.short}) відсутній або належить іншому відділенню.`);
          selection.weight_edited=!!saved.weight_edited;
          selection.weight_from_template=!!saved.weight_from_template;
          if (selection.weight_edited) {
            selection.sample_weight=String(saved.sample_weight ?? '').slice(0,12000);
            selection.weight_source={kind:'manual',label:'Введено вручну'};
          } else syncDepartmentWeight(item,department.id);
          // Review confirmations are deliberately never restored.
          selection.allow_standard_override=false;
        });
      }
      state.warnings=warnings;
      $('#assignment-raw-text').value=state.text;
      $('#assignment-warnings').hidden=!warnings.length;
      $('#assignment-warnings').innerHTML=`<strong>Перевірте перед видачею завдань</strong><ul>${warnings.map(value => `<li>${esc(value)}</li>`).join('')}</ul>`;
      $('#assignment-editor').hidden=!state.items.length;
      $('#assignment-upload-panel').hidden=!!state.items.length;
      $('#assignment-processing').hidden=true;
      switchTab(false);
      invalidate();
      await hydrateNumbers();
      renderSource(); renderNumbering(); renderIssuerSummary(); renderItems();
      if (data.sourceHadFile) notice('Поля та розпізнаний текст відновлено. Для перегляду оригіналу акта потрібно знову обрати файл.');
    },
    issues:reviewIssues,
    reveal:issue => {
      navigate(); switchTab(false);
      requestAnimationFrame(() => {
        const field=$(issue.selector);
        const node=window.OZTemplatePicker?.control(field)||field;
        const details=node?.closest('details'); if (details) details.open=true;
        node?.scrollIntoView({block:'center',behavior:'smooth'}); node?.focus({preventScroll:true});
      });
    }
  });

})();
