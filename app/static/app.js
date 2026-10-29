'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const uid = () => globalThis.crypto?.randomUUID?.() || `item-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const subgroupLabels = {legumes: 'Бобові', cereals: 'Круп’яні', potatoes: 'Картопляні', other: 'Інші другі страви'};
  const subgroupOf = (template) => template.subcategory || template.inferred_subcategory || '';
  const subgroupLabel = (template) => subgroupLabels[subgroupOf(template)] || '';
  const state = {templates: [], common: {}, items: [], text: '', source: '', sourceUrl: '', busy: false, editingTemplate: null, libraryLoaded: false, sequenceEnabled:false};
  const commonFields = [
    ['customer', 'Замовник', true, 'Наприклад, ТОВ «Назва підприємства»'],
    ['supplier', 'Постачальник', false, 'Назва, код ЄДРПОУ'],
    ['sampling_description', 'Відомості про відбір зразків', true, 'Адреса, хто відібрав зразки, присутні представники'],
    ['contract', 'Договір', false, 'Номер і дата договору'],
    ['act_number', 'Номер акта відбору зразків', false, 'Наприклад, 87-010-1-247'],
    ['act_date', 'Дата акта відбору зразків', false, 'Дата з акта, наприклад 20.10.2026'],
    ['seal_number', 'Спільний номер пломби (якщо один для всіх зразків)', false, 'Або вкажіть окремо в кожній позиції']
  ];
  const itemFields = [
    ['product_name', 'Найменування зразка', true, 'Повна назва продукту з акта', 'full'],
    ['standard', 'НД дослідження (для завдань)', false, 'Наприклад, ДСТУ 4450:2005', 'full'],
    ['protocol_number', 'Номер протоколу / зразка', true, 'Наприклад, 87-015-3000-1305-2', ''],
    ['protocol_date', 'Дата протоколу', true, 'Наприклад, 29 жовтня 2026 року', ''],
    ['catalog_number', 'Номер за Каталогом', false, 'Якщо зазначений в акті', ''],
    ['seal_number', 'Номер пломби зразка', false, 'Номер пломби або сейф-пакета з акта', ''],
    ['manufacturer', 'Виробник', false, 'Назва, адреса, код ЄДРПОУ', 'full'],
    ['manufacture_date', 'Дата виготовлення / вжити до', false, 'Як зазначено в акті', ''],
    ['batch_size', 'Розмір партії', false, 'Наприклад, 500,0 кг', 'two']
  ];

  function toast(message, error = false) {
    const node = document.createElement('div');
    node.className = `toast${error ? ' error' : ''}`;
    const text = document.createElement('span');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', 'Закрити повідомлення');
    close.innerHTML = icon('close');
    close.addEventListener('click', () => node.remove());
    node.append(text, close);
    $('#toast-region').append(node);
    window.setTimeout(() => node.remove(), error ? 14000 : 7000);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = `Не вдалося виконати запит (${response.status}).`;
      try {
        const error = await response.json();
        const detail = error.detail ?? error.error ?? error.message;
        if (typeof detail === 'string') message = detail;
        else if (Array.isArray(detail)) message = detail.map(v => v.msg || JSON.stringify(v)).join('; ');
        else if (detail) message = JSON.stringify(detail);
      } catch { /* retain a useful HTTP error */ }
      throw new Error(message);
    }
    return response;
  }
  const jsonPost = (url, data) => api(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  const supplierDirectory = createSupplierDirectory({api,jsonPost,escape,icon,onSelect:supplier => {
    if (supplier) {
      state.common.supplier = supplier.name;
      const supplierInput = $('[data-common="supplier"]');
      if (supplierInput) supplierInput.value = supplier.name;
      state.items.forEach(item => {
        const parts = protocolParts(item.protocol_number,true);
        if (parts) { parts[1] = supplier.code; item.protocol_number = parts.join('-'); }
      });
    }
    applyProtocolDefaults();
    invalidateReview();
    updateGeneration();
  }});

  function switchView(name) {
    $('#acts-view').hidden = name !== 'acts';
    $('#library-view').hidden = name !== 'library';
    if ($('#assignments-view')) $('#assignments-view').hidden = name !== 'assignments';
    $('#breadcrumb-current').textContent = ({library: 'Бібліотека шаблонів', assignments: 'Завдання', acts: 'Акти відбору'})[name] || 'Робочий простір';
    $$('.nav-button').forEach(button => {
      const active = button.dataset.view === name;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (name === 'library' && !state.libraryLoaded) loadTemplates();
    document.dispatchEvent(new CustomEvent('protokol:view', {detail: {name}}));
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  $$('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
  document.addEventListener('protokol:navigate', event => switchView(event.detail.name));
  $('#prepare-assignments').addEventListener('click', () => {
    if (!state.items.length) { toast('Спочатку додайте позиції акта.', true); return; }
    document.dispatchEvent(new CustomEvent('protokol:assignments-import', {detail: {items: JSON.parse(JSON.stringify(state.items)), text: state.text, source: state.source}}));
  });

  async function loadTemplates() {
    $('#library-loading').hidden = false;
    try {
      const response = await api('/api/templates');
      const data = await response.json();
      state.templates = data.templates || [];
      state.libraryLoaded = true;
      ['#nav-template-count', '#aside-template-count', '#library-template-count'].forEach(id => $(id).textContent = state.templates.length);
      refreshCategories();
      renderLibrary();
      if (state.items.length) renderItems();
    } catch (error) {
      toast(`Бібліотека: ${error.message}`, true);
      $('#library-empty').hidden = false;
      $('#library-empty').innerHTML = `${icon('info')}<h3>Не вдалося завантажити бібліотеку</h3><p>Перевірте підключення до сервера та оновіть сторінку.</p>`;
    } finally { $('#library-loading').hidden = true; }
  }

  function refreshCategories() {
    const filter = $('#category-filter');
    const previous = filter.value;
    const categories = [...new Set(state.templates.map(t => t.category || '').filter(Boolean))].sort((a,b) => a.localeCompare(b, 'uk'));
    filter.innerHTML = '<option value="">Усі категорії</option>' + categories.map(c => `<option value="${escape(c)}">${escape(c)}</option>`).join('');
    if (categories.includes(previous)) filter.value = previous;
    $('#category-options').innerHTML = categories.map(c => `<option value="${escape(c)}"></option>`).join('');
  }

  function aliasesOf(template) {
    return Array.isArray(template.aliases) ? template.aliases : [];
  }

  function renderLibrary() {
    const query = $('#template-search').value.toLocaleLowerCase('uk').trim();
    const category = $('#category-filter').value;
    const subgroup = $('#subcategory-filter').value;
    const templates = state.templates.filter(t => (!category || t.category === category) && (!subgroup || subgroupOf(t) === subgroup) && [t.name, t.product_name, t.filename, subgroupLabel(t), ...aliasesOf(t)].join(' ').toLocaleLowerCase('uk').includes(query));
    $('#template-list').innerHTML = templates.map(t => `<article class="template-row" data-template="${escape(t.id)}">
      <div class="word-icon" aria-hidden="true">${icon('file')}<small>DOCX</small></div>
      <div class="template-info"><h3>${escape(t.name || t.product_name || 'Шаблон без назви')}</h3><p>${escape(t.product_name || t.filename || '')}</p><div class="template-aliases">${subgroupLabel(t) ? `<span class="alias-tag subgroup-tag">${escape(subgroupLabel(t))}</span>` : ''}${aliasesOf(t).slice(0, 6).map(alias => `<span class="alias-tag">${escape(alias)}</span>`).join('')}${aliasesOf(t).length > 6 ? `<span class="alias-tag">+${aliasesOf(t).length - 6}</span>` : ''}</div></div>
      ${t.category ? `<span class="category-tag">${escape(t.category)}</span>` : ''}
      <div class="row-actions"><a class="icon-button" href="/api/templates/${encodeURIComponent(t.id)}/download" aria-label="Завантажити ${escape(t.name)}" title="Завантажити Word-шаблон">${icon('download')}</a><button class="icon-button edit-template" type="button" aria-label="Редагувати ${escape(t.name)}" title="Редагувати">${icon('edit')}</button><button class="icon-button danger delete-template" type="button" aria-label="Видалити ${escape(t.name)}" title="Видалити">${icon('trash')}</button></div>
    </article>`).join('');
    $('#library-empty').hidden = templates.length > 0;
  }
  $('#template-search').addEventListener('input', renderLibrary);
  $('#category-filter').addEventListener('change', renderLibrary);
  $('#subcategory-filter').addEventListener('change', renderLibrary);
  $('#template-list').addEventListener('click', async (event) => {
    const row = event.target.closest('[data-template]');
    if (!row) return;
    const template = state.templates.find(t => String(t.id) === row.dataset.template);
    if (!template) return;
    if (event.target.closest('.edit-template')) openTemplateDialog(template);
    if (event.target.closest('.delete-template')) {
      if (!window.confirm(`Видалити шаблон «${template.name}» з бібліотеки?`)) return;
      try {
        await api(`/api/templates/${encodeURIComponent(template.id)}`, {method: 'DELETE'});
        state.items.forEach(item => { if (item.template_id === template.id) item.template_id = ''; });
        await loadTemplates();
        toast('Шаблон видалено.');
      } catch (error) { toast(error.message, true); }
    }
  });

  function openTemplateDialog(template = null) {
    state.editingTemplate = template;
    $('#template-form').reset();
    $('#template-dialog-title').textContent = template ? 'Редагувати шаблон' : 'Додати шаблон';
    $('#template-file-label').hidden = !!template;
    $('#template-file').required = !template;
    $('#template-name').value = template?.name || '';
    $('#template-category').value = template?.category || '';
    $('#template-subcategory').value = template?.subcategory || '';
    $('#template-aliases').value = template ? aliasesOf(template).join('\n') : '';
    $('#template-form-error').hidden = true;
    $('#template-dialog').showModal();
  }
  $('#open-template-upload').addEventListener('click', () => openTemplateDialog());
  ['#close-template-dialog', '#cancel-template-dialog'].forEach(id => $(id).addEventListener('click', () => $('#template-dialog').close()));
  $('#template-dialog').addEventListener('click', event => { if (event.target === $('#template-dialog')) $('#template-dialog').close(); });
  $('#template-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('#save-template');
    const errorNode = $('#template-form-error');
    const aliases = [...new Set($('#template-aliases').value.split(/[,\n]/).map(v => v.trim()).filter(Boolean))];
    const values = {name: $('#template-name').value.trim(), category: $('#template-category').value.trim(), subcategory: $('#template-subcategory').value, aliases};
    const file = $('#template-file').files[0];
    if (!state.editingTemplate && (!file || !file.name.toLowerCase().endsWith('.docx'))) {
      errorNode.textContent = 'Виберіть Word-файл у форматі .docx.';
      errorNode.hidden = false;
      return;
    }
    if (file && file.size > 25 * 1024 * 1024) {
      errorNode.textContent = 'Файл завеликий. Максимальний розмір — 25 МБ.';
      errorNode.hidden = false;
      return;
    }
    submit.disabled = true;
    submit.textContent = 'Зберігаємо…';
    errorNode.hidden = true;
    try {
      if (state.editingTemplate) {
        await api(`/api/templates/${encodeURIComponent(state.editingTemplate.id)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
      } else {
        const form = new FormData();
        form.append('file', file);
        form.append('name', values.name);
        form.append('category', values.category);
        form.append('subcategory', values.subcategory);
        form.append('aliases', JSON.stringify(aliases));
        await api('/api/templates', {method: 'POST', body: form});
      }
      $('#template-dialog').close();
      await loadTemplates();
      toast('Шаблон збережено в бібліотеці.');
    } catch (error) {
      errorNode.textContent = error.message;
      errorNode.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = 'Зберегти шаблон';
    }
  });

  const dropzone = $('#dropzone');
  const actFile = $('#act-file');
  dropzone.addEventListener('click', () => { if (!state.busy) actFile.click(); });
  dropzone.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); actFile.click(); } });
  ['dragenter','dragover'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add('dragging'); }));
  ['dragleave','drop'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('dragging'); }));
  dropzone.addEventListener('drop', event => {
    if (event.dataTransfer.files.length > 1) { toast('Завантажуйте по одному акту. Багатосторінковий акт можна об’єднати в PDF.', true); return; }
    if (event.dataTransfer.files[0]) extractFile(event.dataTransfer.files[0]);
  });
  actFile.addEventListener('change', () => { if (actFile.files[0]) extractFile(actFile.files[0]); });

  function showStage(stage) {
    $('#upload-section').hidden = stage !== 'upload';
    $('#processing-section').hidden = stage !== 'processing';
    $('#review-section').hidden = stage !== 'review';
    $('#new-act').hidden = stage === 'upload' || stage === 'processing';
    $('#step-upload').classList.toggle('current', stage === 'upload' || stage === 'processing');
    $('#step-upload').classList.toggle('complete', stage === 'review');
    $('#step-review').classList.toggle('current', stage === 'review');
    $('#step-review').classList.remove('complete');
    $('#step-download').classList.remove('current', 'complete');
  }

  function revokeSourceUrl() {
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.sourceUrl = '';
  }

  async function extractFile(file) {
    if (state.busy) return;
    if (file.size > 25 * 1024 * 1024) { toast('Файл завеликий. Максимальний розмір — 25 МБ.', true); return; }
    if (!/\.(pdf|jpe?g|png|tiff?|docx|txt)$/i.test(file.name)) { toast('Підтримуються PDF, JPG, PNG, TIFF, DOCX і TXT.', true); return; }
    revokeSourceUrl();
    state.sourceUrl = URL.createObjectURL(file);
    state.busy = true;
    showStage('processing');
    $('#processing-label').textContent = `Опрацьовуємо «${file.name}»…`;
    try {
      const form = new FormData();
      form.append('file', file);
      const response = await api('/api/extract', {method: 'POST', body: form});
      applyExtraction(await response.json(), file.name);
      toast('Акт опрацьовано. Перевірте всі поля та оберіть шаблони.');
    } catch (error) {
      showStage('upload');
      toast(error.message, true);
    } finally { state.busy = false; actFile.value = ''; }
  }

  function blankItem() {
    return {id: uid(), product_name: '', standard: '', protocol_number: '', protocol_date: '', catalog_number: '', seal_number: '', manufacturer: '', manufacture_date: '', batch_size: '', template_id: '', matches: [], _protocol_date_manual:false, _protocol_number_manual:false};
  }

  function formatProtocolDate(value) {
    const months = ['січня','лютого','березня','квітня','травня','червня','липня','серпня','вересня','жовтня','листопада','грудня'];
    const text = String(value || '').trim();
    let day, month, year;
    let match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) [,year,month,day] = match.map(Number);
    else if ((match = text.match(/^(\d{1,2})[./](\d{1,2})[./](\d{4})\.?$/))) [,day,month,year] = match.map(Number);
    else if ((match = text.toLocaleLowerCase('uk').match(/^«?(\d{1,2})»?\s+([а-яіїєґ]+)\s+(\d{4})(?:\s+року)?\.?$/))) {
      day = Number(match[1]); month = months.indexOf(match[2]) + 1; year = Number(match[3]);
    }
    if (!year || !month || !day) return text;
    const date = new Date(Date.UTC(year,month-1,day));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month-1 || date.getUTCDate() !== day) return text;
    return `${day} ${months[month-1]} ${year} року`;
  }

  function protocolParts(value, allowIncomplete = false) {
    const parts = String(value || '').trim().replace(/^№\s*/, '').replace(/[‐‑‒–—−]/g, '-').replace(/\s+/g, '').split('-');
    if (parts.length !== 5 || ![0,1,2,4].every(index => /^\d+$/.test(parts[index]))) return null;
    if (!(allowIncomplete ? /^\d{0,4}$/ : /^\d{4}$/).test(parts[3])) return null;
    return parts;
  }

  function protocolNumberHint(item, index) {
    if (!index) return 'Для решти позицій спільні частини копіюються, а чотиризначна частина збільшується на 1.';
    if (item._protocol_number_manual) return 'Індивідуальну частину змінено вручну. Спільні частини беруться з першої позиції.';
    if (item._protocol_number_overflow) return 'Черга перевищує 9999. Змініть початковий номер або вкажіть номер цієї позиції вручну.';
    return state.sequenceEnabled ? 'Номер підставлено за першою позицією: індивідуальна частина наступна за порядком.' : 'Введіть повний номер у першій позиції, щоб продовжити чергу автоматично.';
  }

  function updateProtocolControls() {
    $$('#items-list [data-item]').forEach((card,index) => {
      const item = state.items.find(value => value.id === card.dataset.item);
      if (!item) return;
      ['protocol_number','protocol_date'].forEach(key => {
        const input = $(`[data-field="${key}"]`,card);
        if (input && input.value !== item[key]) input.value = item[key];
      });
      const numberHint = $('[data-protocol-number-hint]',card);
      if (numberHint) numberHint.textContent = protocolNumberHint(item,index);
      const dateHint = $('[data-protocol-date-hint]',card);
      if (dateHint) dateHint.textContent = !index ? 'Дата застосовується до решти позицій, доки її не змінено вручну.' : item._protocol_date_manual ? 'Дата змінена вручну для цієї позиції.' : 'Дата автоматично береться з першої позиції.';
      const numberReset = $('[data-inherit-number]',card);
      if (numberReset) numberReset.hidden = !index || !item._protocol_number_manual;
      const dateReset = $('[data-inherit-date]',card);
      if (dateReset) dateReset.hidden = !index || !item._protocol_date_manual;
    });
  }

  function applyProtocolDefaults() {
    if (!state.items.length) return;
    const first = state.items[0];
    const parts = protocolParts(first.protocol_number);
    const supplierCode = supplierDirectory.selected()?.code || '';
    if (parts && supplierCode) {
      parts[1] = supplierCode;
      first.protocol_number = parts.join('-');
    }
    state.items.forEach((item,index) => {
      if (index && !item._protocol_date_manual) item.protocol_date = first.protocol_date;
      item._protocol_number_overflow = false;
      if (!index || !parts || !state.sequenceEnabled) return;
      const number = [...parts];
      if (item._protocol_number_manual) {
        const own = protocolParts(item.protocol_number);
        if (own) number[3] = own[3];
        else if (/^\d{4}$/.test(item.protocol_number.trim())) number[3] = item.protocol_number.trim();
        else return;
      } else {
        const individual = Number(parts[3]) + index;
        item._protocol_number_overflow = individual > 9999;
        number[3] = individual > 9999 ? '' : String(individual).padStart(4,'0');
      }
      item.protocol_number = number.join('-');
    });
    updateProtocolControls();
  }

  function applyExtraction(data, source) {
    state.common = Object.fromEntries(commonFields.map(([key]) => [key, data.common?.[key] || '']));
    state.items = (data.items || []).map(item => ({...blankItem(), ...item, id: item.id || uid(), template_id: item.template_id || '', matches: item.matches || []}));
    state.sequenceEnabled = false;
    state.items.forEach((item,index) => {
      item._protocol_date_manual = !!(index && item.protocol_date.trim() && item.protocol_date !== state.items[0].protocol_date);
      item._protocol_number_manual = false;
    });
    supplierDirectory.syncName(state.common.supplier);
    applyProtocolDefaults();
    state.text = data.text || '';
    state.source = source;
    $('#raw-text').value = state.text;
    $('#review-confirmed').checked = false;
    $('#download-success').hidden = true;
    const methodLabels = {ocr: 'Оптичне розпізнавання', tesseract: 'Оптичне розпізнавання', pdf_text: 'Текст із PDF', pdf: 'PDF-документ', docx: 'Word-документ', text: 'Текстовий документ', manual: 'Ручне введення'};
    const method = methodLabels[data.method] || data.method || 'Розбір тексту';
    $('#source-banner').innerHTML = `${icon('file')}<div><strong>${escape(source || 'Дані акта')}</strong><small>${escape(method)}${data.pages ? ` · Сторінок: ${escape(data.pages)}` : ''} · Позицій: ${state.items.length}</small></div>${state.sourceUrl ? `<a class="source-original-link" href="${escape(state.sourceUrl)}" target="_blank" rel="noopener noreferrer">Відкрити оригінал акта ${icon('arrow')}</a>` : ''}`;
    renderWarnings(data.warnings || []);
    renderCommon();
    renderItems();
    showStage('review');
  }

  function renderWarnings(warnings) {
    const node = $('#extraction-warnings');
    node.hidden = warnings.length === 0;
    node.innerHTML = `<strong>Зверніть увагу перед створенням протоколів</strong><ul>${warnings.map(v => `<li>${escape(v)}</li>`).join('')}</ul>`;
  }

  function renderCommon() {
    $('#common-fields').innerHTML = commonFields.map(([key, label, required, placeholder]) => `<label class="field ${key === 'sampling_description' ? 'wide' : ''}"><span>${escape(label)}${required ? ' *' : ''}</span>${key === 'sampling_description' || key === 'contract' ? `<textarea data-common="${key}" rows="${key === 'sampling_description' ? 3 : 2}" placeholder="${escape(placeholder)}" ${required ? 'required' : ''}>${escape(state.common[key])}</textarea>` : `<input data-common="${key}" value="${escape(state.common[key])}" placeholder="${escape(placeholder)}" ${required ? 'required' : ''}>`}</label>`).join('');
  }

  function templateOptions(selected) {
    return `<option value="">Оберіть відповідний шаблон *</option>` + state.templates.map(t => `<option value="${escape(t.id)}" ${String(t.id) === String(selected) ? 'selected' : ''}>${escape(t.name || t.product_name)}${subgroupLabel(t) ? ` · ${escape(subgroupLabel(t))}` : ''}</option>`).join('');
  }

  function scoreText(score) {
    const value = Number(score);
    if (!Number.isFinite(value)) return '';
    return `${Math.round(value <= 1 ? value * 100 : value)}%`;
  }

  function renderMatchChip(match) {
    const template = state.templates.find(t => String(t.id) === String(match.id));
    const group = subgroupLabel(template || match);
    return `<button type="button" class="match-chip" data-match="${escape(match.id)}" title="Обрати шаблон ${escape(match.name)}"><span>${escape(match.name)}${group ? ` · ${escape(group)}` : ''}</span><small title="Схожість найменувань, а не ймовірність правильного розпізнавання">Схожість ${scoreText(match.score)}</small></button>`;
  }

  function renderItemField(item,index,[key,label,required,placeholder,width]) {
    if (key === 'protocol_number' || key === 'protocol_date') {
      const number = key === 'protocol_number';
      const manual = number ? item._protocol_number_manual : item._protocol_date_manual;
      const hint = number ? protocolNumberHint(item,index) : !index ? 'Дата застосовується до решти позицій, доки її не змінено вручну.' : manual ? 'Дата змінена вручну для цієї позиції.' : 'Дата автоматично береться з першої позиції.';
      return `<div class="field ${width}"><label for="protocol-row-${index}-${key}">${escape(label)}${required ? ' *' : ''}</label><input id="protocol-row-${index}-${key}" data-field="${key}" value="${escape(item[key])}" placeholder="${escape(placeholder)}" ${required ? 'required' : ''}><small ${number ? 'data-protocol-number-hint' : 'data-protocol-date-hint'}>${escape(hint)}</small><button type="button" class="text-button" ${number ? 'data-inherit-number' : 'data-inherit-date'} ${index && manual ? '' : 'hidden'}>${number ? 'Повернути номер до черги' : 'Взяти дату першої позиції'}</button></div>`;
    }
    return `<label class="field ${width}"><span>${escape(label)}${required ? ' *' : ''}</span>${key === 'manufacturer' ? `<textarea rows="2" data-field="${key}" placeholder="${escape(placeholder)}">${escape(item[key])}</textarea>` : `<input data-field="${key}" value="${escape(item[key])}" placeholder="${escape(placeholder)}" ${required ? 'required' : ''}>`}</label>`;
  }

  function renderItems() {
    applyProtocolDefaults();
    $('#item-count').textContent = state.items.length;
    $('#empty-items').hidden = state.items.length > 0;
    $('#items-list').innerHTML = state.items.map((item, index) => {
      const matches = (item.matches || []).filter(m => state.templates.some(t => String(t.id) === String(m.id))).slice(0, 3);
      return `<article class="panel item-card" data-item="${escape(item.id)}">
        <div class="item-heading"><div class="item-label"><span class="item-number">${String(index + 1).padStart(2, '0')}</span><div><h3 class="item-title">${escape(item.product_name || 'Новий зразок')}</h3><p class="item-subtitle">Окремий протокол випробувань</p></div></div><button type="button" class="icon-button danger remove-item" aria-label="Видалити позицію ${index + 1}" title="Видалити позицію">${icon('trash')}</button></div>
        <div class="item-grid">${itemFields.map(field => renderItemField(item,index,field)).join('')}</div>
        <div class="template-selection"><label class="field"><span>Шаблон протоколу *</span><select data-field="template_id" required>${templateOptions(item.template_id)}</select></label><p class="match-summary ${!matches.length ? 'no-match' : ''}">${matches.length ? 'Пропозиції за найменуванням. Перевірте відповідність продукту перед вибором.' : 'Шаблон ще не підібрано. Оберіть його вручну або знайдіть за назвою.'}</p><div class="match-actions"><button type="button" class="text-button find-matches">Знайти за найменуванням</button>${matches.map(renderMatchChip).join('')}</div></div><p class="item-footnote">Незаповнені необов’язкові поля не успадковують дані з попереднього протоколу.</p>
      </article>`;
    }).join('');
    updateGeneration();
  }

  function invalidateReview() {
    $('#review-confirmed').checked = false;
    $('#download-success').hidden = true;
    $('#step-download').classList.remove('current', 'complete');
    $('#step-review').classList.add('current');
    $('#step-review').classList.remove('complete');
  }

  $('#common-fields').addEventListener('input', event => {
    if (!event.target.dataset.common) return;
    state.common[event.target.dataset.common] = event.target.value;
    if (event.target.dataset.common === 'supplier' && supplierDirectory.selected()?.name !== event.target.value) supplierDirectory.clearSelection();
    event.target.closest('.field').classList.remove('invalid');
    invalidateReview();
    updateGeneration();
  });
  function updateItemField(event) {
    const input = event.target;
    if (!input.dataset.field) return;
    const card = input.closest('[data-item]');
    const item = state.items.find(v => String(v.id) === card.dataset.item);
    if (!item) return;
    item[input.dataset.field] = input.value;
    const first = state.items[0] === item;
    if (input.dataset.field === 'protocol_date') {
      if (!first) item._protocol_date_manual = true;
      applyProtocolDefaults();
    }
    if (input.dataset.field === 'protocol_number') {
      if (first && protocolParts(input.value)) state.sequenceEnabled = true;
      if (!first) item._protocol_number_manual = true;
      applyProtocolDefaults();
    }
    if (input.dataset.field === 'product_name') {
      $('.item-title', card).textContent = input.value || 'Новий зразок';
      item.matches = [];
      item.template_id = '';
      $('select[data-field="template_id"]', card).value = '';
      $$('.match-chip', card).forEach(node => node.remove());
      $('.match-summary', card).textContent = 'Назву змінено. Знайдіть шаблон знову або оберіть його вручну.';
      $('.match-summary', card).classList.add('no-match');
    }
    input.closest('.field').classList.remove('invalid');
    invalidateReview();
    updateGeneration();
  }
  $('#items-list').addEventListener('input', updateItemField);
  $('#items-list').addEventListener('focusout', event => {
    const input = event.target;
    if (input.dataset.field !== 'protocol_date') return;
    const formatted = formatProtocolDate(input.value);
    if (formatted !== input.value) {
      input.value = formatted;
      updateItemField(event);
    }
  });
  $('#items-list').addEventListener('change', event => { if (event.target.tagName === 'SELECT') updateItemField(event); });
  $('#items-list').addEventListener('click', async event => {
    const card = event.target.closest('[data-item]');
    if (!card) return;
    const item = state.items.find(v => String(v.id) === card.dataset.item);
    if (!item) return;
    if (event.target.closest('[data-inherit-date]') || event.target.closest('[data-inherit-number]')) {
      if (event.target.closest('[data-inherit-date]')) item._protocol_date_manual = false;
      else item._protocol_number_manual = false;
      applyProtocolDefaults();
      invalidateReview();
      updateGeneration();
      return;
    }
    if (event.target.closest('.remove-item')) {
      if (item.product_name && !window.confirm(`Видалити позицію «${item.product_name}»?`)) return;
      state.items = state.items.filter(v => v !== item);
      invalidateReview();
      renderItems();
    }
    const matchButton = event.target.closest('[data-match]');
    if (matchButton) {
      item.template_id = matchButton.dataset.match;
      $('select[data-field="template_id"]', card).value = item.template_id;
      $('select[data-field="template_id"]', card).closest('.field').classList.remove('invalid');
      invalidateReview();
      updateGeneration();
    }
    const find = event.target.closest('.find-matches');
    if (find) {
      if (!item.product_name.trim()) { toast('Спочатку вкажіть найменування зразка.', true); return; }
      find.disabled = true;
      find.textContent = 'Шукаємо…';
      try {
        const response = await jsonPost('/api/match', {product_name: item.product_name});
        const data = await response.json();
        item.matches = data.matches || [];
        renderItems();
        if (!item.matches.length) toast('Впевненого збігу немає. Оберіть шаблон вручну або додайте новий у бібліотеку.');
      } catch (error) {
        toast(error.message, true);
        find.disabled = false;
        find.textContent = 'Знайти за найменуванням';
      }
    }
  });

  $('#add-item').addEventListener('click', () => {
    state.items.push(blankItem());
    if (protocolParts(state.items[0].protocol_number)) state.sequenceEnabled = true;
    invalidateReview();
    renderItems();
    const last = $$('.item-card').at(-1);
    last.scrollIntoView({behavior: 'smooth', block: 'center'});
    $('input[data-field="product_name"]', last).focus({preventScroll: true});
  });
  $('#start-manual').addEventListener('click', () => {
    revokeSourceUrl();
    applyExtraction({common: {}, items: [blankItem()], text: '', method: 'manual', warnings: ['Дані введено вручну. Заповніть відомості з акта та вкажіть номер і дату кожного протоколу.']}, 'Ручне введення акта');
  });
  $('#new-act').addEventListener('click', () => {
    if (!window.confirm('Почати новий акт? Поточні незавантажені дані буде очищено.')) return;
    revokeSourceUrl();
    state.items = []; state.common = {}; state.text = ''; state.source = ''; state.sequenceEnabled = false;
    supplierDirectory.clearSelection();
    $('#review-confirmed').checked = false;
    $('#download-success').hidden = true;
    showStage('upload');
    window.scrollTo({top: 0, behavior: 'smooth'});
  });
  $('#reparse').addEventListener('click', async () => {
    const text = $('#raw-text').value.trim();
    if (!text) { toast('Вставте або введіть текст акта для розбору.', true); return; }
    if (!window.confirm('Повторно розібрати текст? Поточні поля та вибрані шаблони буде замінено результатом нового розбору.')) return;
    const button = $('#reparse');
    button.disabled = true; button.textContent = 'Розбираємо…';
    try {
      const response = await jsonPost('/api/parse', {text});
      const data = await response.json();
      if (!data.text) data.text = text;
      applyExtraction(data, state.source || 'Введений текст акта');
      toast('Текст розібрано. Перевірте оновлені поля.');
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = 'Повторно розібрати текст'; }
  });

  function missingFields() {
    const missing = [];
    const numbers = new Set();
    commonFields.forEach(([key, label, required]) => { if (required && !String(state.common[key] || '').trim()) missing.push(label); });
    state.items.forEach((item, index) => {
      itemFields.forEach(([key, label, required]) => { if (required && !String(item[key] || '').trim()) missing.push(`Позиція ${index + 1}: ${label.toLocaleLowerCase('uk')}`); });
      const number = String(item.protocol_number || '').trim().replace(/^№\s*/, '').replace(/[‐‑‒–—−]/g, '-').replace(/\s+/g, '');
      const parts = number.split('-');
      if ((parts.length === 5 && parts.every(part => /^\d*$/.test(part)) && !protocolParts(number)) || (!index && state.sequenceEnabled && !protocolParts(number))) missing.push(`Позиція ${index + 1}: повний номер у форматі 87-015-3000-1305-2`);
      if (number && numbers.has(number)) missing.push(`Позиція ${index + 1}: номер протоколу повторюється`);
      numbers.add(number);
      if (!item.template_id || !state.templates.some(t => String(t.id) === String(item.template_id))) missing.push(`Позиція ${index + 1}: шаблон`);
    });
    return missing;
  }
  function updateGeneration() {
    const missing = missingFields();
    const confirmed = $('#review-confirmed').checked;
    const ready = state.items.length > 0 && missing.length === 0 && confirmed && !state.busy;
    $('#generate').disabled = !ready;
    const help = $('#generation-help');
    help.classList.toggle('ready', ready);
    if (!state.items.length) help.textContent = 'Додайте принаймні одну позицію.';
    else if (missing.length) help.textContent = `Потрібно заповнити: ${missing.slice(0, 3).join('; ')}${missing.length > 3 ? `; ще ${missing.length - 3}` : ''}.`;
    else if (!confirmed) help.textContent = 'Підтвердьте перевірку даних і шаблонів, щоб створити протоколи.';
    else help.textContent = `Готово до створення: ${state.items.length} окремих Word-файлів в одному ZIP-архіві.`;
  }
  $('#review-confirmed').addEventListener('change', updateGeneration);
  $('#generate').addEventListener('click', async () => {
    if (missingFields().length || !state.items.length || !$('#review-confirmed').checked || state.busy) return;
    const button = $('#generate');
    state.busy = true;
    button.disabled = true;
    button.innerHTML = 'Створюємо документи…';
    try {
      const response = await jsonPost('/api/generate', {common: state.common, items: state.items.map(({matches, ...item}) => item), confirmed: true});
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'protokoly.zip';
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      $('#download-success').hidden = false;
      $('#download-success').innerHTML = `${icon('check')}<div><strong>Протоколи створено — ${state.items.length} Word-файлів.</strong><br>Архів передано для завантаження. Відкрийте документи та перевірте оформлення перед використанням.</div>`;
      $('#step-review').classList.remove('current');
      $('#step-review').classList.add('complete');
      $('#step-download').classList.add('current');
      toast('Архів протоколів готовий.');
    } catch (error) { toast(error.message, true); }
    finally {
      state.busy = false;
      button.innerHTML = `${icon('download')}Створити протоколи`;
      updateGeneration();
    }
  });

  function createSupplierDirectory({api,jsonPost,escape,icon,onSelect}) {
    const query = (selector, root = document) => root.querySelector(selector);
    let suppliers = [];
    let selectedId = '';
    let editingId = '';
    let busy = false;
    const panel = document.createElement('div');
    panel.className = 'form-grid';
    panel.style.marginBottom = '18px';
    panel.innerHTML = `<label class="field"><span>Постачальник з довідника</span><select id="supplier-directory-select"><option value="">Ввести постачальника вручну</option></select><small>Код постачальника підставляється у другу частину номера протоколу.</small></label><div class="field"><span>Збережені постачальники</span><button id="supplier-directory-manage" type="button" class="button secondary">${icon('edit')}Довідник постачальників</button></div>`;
    const commonFields = query('#common-fields');
    commonFields.parentNode.insertBefore(panel, commonFields);
    const dialog = document.createElement('dialog');
    dialog.id = 'supplier-directory-dialog';
    dialog.className = 'modal';
    dialog.innerHTML = `<form id="supplier-directory-form"><div class="modal-heading"><div><p class="eyebrow">ПОСТАЧАЛЬНИКИ</p><h2>Довідник постачальників</h2></div><button id="supplier-directory-close" type="button" class="icon-button" aria-label="Закрити довідник">${icon('close')}</button></div><div class="modal-body"><div class="section-heading"><h3 id="supplier-directory-form-title">Новий постачальник</h3><button id="supplier-directory-new" type="button" class="text-button" hidden>Додати іншого</button></div><label class="field"><span>Найменування постачальника *</span><input id="supplier-directory-name" maxlength="300" required placeholder="Наприклад, ТОВ «Назва підприємства»"></label><label class="field"><span>Код для номера протоколу *</span><input id="supplier-directory-code" type="text" inputmode="numeric" pattern="[0-9]{1,10}" maxlength="10" required placeholder="Наприклад, 015"><small>Від 1 до 10 цифр. Початкові нулі зберігаються.</small></label><p id="supplier-directory-error" class="field-error" role="alert" hidden></p><div class="source-actions"><button id="supplier-directory-save" type="submit" class="button primary">Додати постачальника</button></div><div class="section-heading"><h3>Збережені постачальники</h3></div><div id="supplier-directory-list"></div><p id="supplier-directory-empty" class="empty-state">Поки немає збережених постачальників.</p></div><div class="modal-footer"><button id="supplier-directory-done" type="button" class="button secondary">Готово</button></div></form>`;
    document.body.append(dialog);
    const select = query('#supplier-directory-select');
    const nameInput = query('#supplier-directory-name');
    const codeInput = query('#supplier-directory-code');
    const errorNode = query('#supplier-directory-error');
    const selected = () => suppliers.find(supplier => String(supplier.id) === selectedId) || null;
    function showError(message) {
      errorNode.textContent = message;
      errorNode.hidden = !message;
    }
    function resetForm() {
      editingId = '';
      query('#supplier-directory-form').reset();
      query('#supplier-directory-form-title').textContent = 'Новий постачальник';
      query('#supplier-directory-save').textContent = 'Додати постачальника';
      query('#supplier-directory-new').hidden = true;
      showError('');
    }
    function render() {
      select.innerHTML = '<option value="">Ввести постачальника вручну</option>' + suppliers.map(supplier => `<option value="${escape(supplier.id)}">${escape(supplier.name)} · ${escape(supplier.code)}</option>`).join('');
      select.value = selectedId;
      query('#supplier-directory-list').innerHTML = suppliers.map(supplier => `<article class="template-row" data-supplier-id="${escape(supplier.id)}"><div class="template-info"><h3>${escape(supplier.name)}</h3><p>Код: ${escape(supplier.code)}${String(supplier.id) === selectedId ? ' · Обрано для поточного акта' : ''}</p></div><div class="row-actions"><button type="button" class="icon-button supplier-directory-edit" aria-label="Редагувати ${escape(supplier.name)}" title="Редагувати">${icon('edit')}</button><button type="button" class="icon-button danger supplier-directory-delete" aria-label="Видалити ${escape(supplier.name)}" title="Видалити">${icon('trash')}</button></div></article>`).join('');
      query('#supplier-directory-empty').hidden = suppliers.length > 0;
    }
    function setBusy(value) {
      busy = value;
      query('#supplier-directory-save').disabled = value;
      query('#supplier-directory-new').disabled = value;
      query('#supplier-directory-list').querySelectorAll('button').forEach(button => { button.disabled = value; });
    }
    select.addEventListener('change', () => {
      selectedId = select.value;
      render();
      onSelect(selected());
    });
    query('#supplier-directory-manage').addEventListener('click', () => {
      resetForm();
      render();
      dialog.showModal();
    });
    ['#supplier-directory-close','#supplier-directory-done'].forEach(selector => query(selector).addEventListener('click', () => { if (!busy) dialog.close(); }));
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('click', event => { if (event.target === dialog && !busy) dialog.close(); });
    query('#supplier-directory-new').addEventListener('click', () => { if (!busy) { resetForm(); nameInput.focus(); } });
    query('#supplier-directory-list').addEventListener('click', async event => {
      if (busy) return;
      const row = event.target.closest('[data-supplier-id]');
      if (!row) return;
      const supplier = suppliers.find(value => String(value.id) === row.dataset.supplierId);
      if (!supplier) return;
      if (event.target.closest('.supplier-directory-edit')) {
        editingId = String(supplier.id);
        nameInput.value = supplier.name;
        codeInput.value = supplier.code;
        query('#supplier-directory-form-title').textContent = 'Редагувати постачальника';
        query('#supplier-directory-save').textContent = 'Зберегти зміни';
        query('#supplier-directory-new').hidden = false;
        showError('');
        nameInput.focus();
      }
      if (event.target.closest('.supplier-directory-delete')) {
        if (!window.confirm(`Видалити постачальника «${supplier.name}» з довідника?`)) return;
        setBusy(true);
        showError('');
        try {
          await api(`/api/settings/suppliers/${encodeURIComponent(supplier.id)}`, {method:'DELETE'});
          suppliers = suppliers.filter(value => String(value.id) !== String(supplier.id));
          const wasSelected = selectedId === String(supplier.id);
          if (wasSelected) selectedId = '';
          if (editingId === String(supplier.id)) resetForm();
          render();
          if (wasSelected) onSelect(null);
        } catch (error) { showError(error.message); }
        finally { setBusy(false); }
      }
    });
    query('#supplier-directory-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const name = nameInput.value.trim();
      const code = codeInput.value.trim();
      if (!name || !/^[0-9]{1,10}$/.test(code)) {
        showError('Укажіть найменування й код постачальника: від 1 до 10 цифр.');
        return;
      }
      setBusy(true);
      showError('');
      try {
        const wasEditing = !!editingId;
        const response = editingId
          ? await api(`/api/settings/suppliers/${encodeURIComponent(editingId)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name,code})})
          : await jsonPost('/api/settings/suppliers', {name,code});
        const supplier = await response.json();
        const position = suppliers.findIndex(value => String(value.id) === String(supplier.id));
        if (position < 0) suppliers.push(supplier); else suppliers[position] = supplier;
        // A new entry is immediately available for the current act. Editing an
        // unrelated entry leaves the current supplier selection in place.
        const shouldSelect = !wasEditing || selectedId === String(supplier.id);
        if (shouldSelect) selectedId = String(supplier.id);
        resetForm();
        render();
        if (shouldSelect) onSelect(supplier);
      } catch (error) { showError(error.message); }
      finally { setBusy(false); }
    });
    return {
      async load() {
        const response = await api('/api/settings');
        const settings = await response.json();
        suppliers = Array.isArray(settings.suppliers) ? settings.suppliers : [];
        if (!suppliers.some(value => String(value.id) === selectedId)) selectedId = '';
        render();
      },
      clearSelection() { selectedId = ''; render(); },
      syncName(name) {
        const matches = suppliers.filter(supplier => supplier.name.trim() === String(name || '').trim());
        selectedId = matches.length === 1 ? String(matches[0].id) : '';
        render();
      },
      selected,
    };
  }

  loadTemplates();
  supplierDirectory.load().then(() => {
    supplierDirectory.syncName(state.common.supplier);
    applyProtocolDefaults();
  }).catch(error => toast(`Довідник постачальників: ${error.message}`,true));
})();
