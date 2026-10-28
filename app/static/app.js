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
  const state = {templates: [], common: {}, items: [], text: '', source: '', sourceUrl: '', busy: false, editingTemplate: null, libraryLoaded: false};
  const commonFields = [
    ['customer', 'Замовник', true, 'Наприклад, ТОВ «Назва підприємства»'],
    ['supplier', 'Постачальник', false, 'Назва, код ЄДРПОУ'],
    ['sampling_description', 'Відомості про відбір зразків', true, 'Адреса, хто відібрав зразки, присутні представники'],
    ['contract', 'Договір', false, 'Номер і дата договору']
  ];
  const itemFields = [
    ['product_name', 'Найменування зразка', true, 'Повна назва продукту з акта', 'full'],
    ['protocol_number', 'Номер протоколу / зразка', true, 'Наприклад, 87-015-3000-1305-2', ''],
    ['protocol_date', 'Дата протоколу', true, 'Наприклад, 28.10.2026', ''],
    ['catalog_number', 'Номер за Каталогом', false, 'Якщо зазначений в акті', ''],
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

  function switchView(name) {
    $('#acts-view').hidden = name !== 'acts';
    $('#library-view').hidden = name !== 'library';
    $('#breadcrumb-current').textContent = name === 'library' ? 'Бібліотека шаблонів' : 'Акти відбору';
    $$('.nav-button').forEach(button => {
      const active = button.dataset.view === name;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (name === 'library' && !state.libraryLoaded) loadTemplates();
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  $$('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));

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
    return {id: uid(), product_name: '', protocol_number: '', protocol_date: '', catalog_number: '', manufacturer: '', manufacture_date: '', batch_size: '', template_id: '', matches: []};
  }

  function applyExtraction(data, source) {
    state.common = Object.fromEntries(commonFields.map(([key]) => [key, data.common?.[key] || '']));
    state.items = (data.items || []).map(item => ({...blankItem(), ...item, id: item.id || uid(), template_id: item.template_id || '', matches: item.matches || []}));
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

  function renderItems() {
    $('#item-count').textContent = state.items.length;
    $('#empty-items').hidden = state.items.length > 0;
    $('#items-list').innerHTML = state.items.map((item, index) => {
      const matches = (item.matches || []).filter(m => state.templates.some(t => String(t.id) === String(m.id))).slice(0, 3);
      return `<article class="panel item-card" data-item="${escape(item.id)}">
        <div class="item-heading"><div class="item-label"><span class="item-number">${String(index + 1).padStart(2, '0')}</span><div><h3 class="item-title">${escape(item.product_name || 'Новий зразок')}</h3><p class="item-subtitle">Окремий протокол випробувань</p></div></div><button type="button" class="icon-button danger remove-item" aria-label="Видалити позицію ${index + 1}" title="Видалити позицію">${icon('trash')}</button></div>
        <div class="item-grid">${itemFields.map(([key, label, required, placeholder, width]) => `<label class="field ${width}"><span>${escape(label)}${required ? ' *' : ''}</span>${key === 'manufacturer' ? `<textarea rows="2" data-field="${key}" placeholder="${escape(placeholder)}">${escape(item[key])}</textarea>` : `<input data-field="${key}" value="${escape(item[key])}" placeholder="${escape(placeholder)}" ${required ? 'required' : ''}>`}</label>`).join('')}</div>
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
  $('#items-list').addEventListener('change', event => { if (event.target.tagName === 'SELECT') updateItemField(event); });
  $('#items-list').addEventListener('click', async event => {
    const card = event.target.closest('[data-item]');
    if (!card) return;
    const item = state.items.find(v => String(v.id) === card.dataset.item);
    if (!item) return;
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
    state.items = []; state.common = {}; state.text = ''; state.source = '';
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
    commonFields.forEach(([key, label, required]) => { if (required && !String(state.common[key] || '').trim()) missing.push(label); });
    state.items.forEach((item, index) => {
      itemFields.forEach(([key, label, required]) => { if (required && !String(item[key] || '').trim()) missing.push(`Позиція ${index + 1}: ${label.toLocaleLowerCase('uk')}`); });
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

  loadTemplates();
})();
