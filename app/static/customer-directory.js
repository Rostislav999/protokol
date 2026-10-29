'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const commonFields = $('#common-fields');
  if (!commonFields) return;
  let customers = [], selectedId = '', editingId = '', busy = false, lastName = '';
  const panel = document.createElement('div');
  panel.className = 'form-grid';
  panel.style.marginBottom = '18px';
  panel.innerHTML = `<label class="field"><span>Замовник з довідника</span><select id="customer-directory-select"><option value="">Ввести замовника вручну</option></select><small>Обраний замовник буде вказаний у всіх протоколах поточного акта.</small></label><div class="field"><span>Збережені замовники</span><button id="customer-directory-manage" type="button" class="button secondary">${icon('edit')}Довідник замовників</button></div><p id="customer-directory-load-error" class="field-error" role="alert" hidden></p>`;
  commonFields.before(panel);
  const dialog = document.createElement('dialog');
  dialog.id = 'customer-directory-dialog';
  dialog.className = 'modal';
  dialog.innerHTML = `<form id="customer-directory-form"><div class="modal-heading"><div><p class="eyebrow">ЗАМОВНИКИ</p><h2>Довідник замовників</h2></div><button id="customer-directory-close" type="button" class="icon-button" aria-label="Закрити довідник">${icon('close')}</button></div><div class="modal-body"><div class="section-heading"><h3 id="customer-directory-form-title">Новий замовник</h3><button id="customer-directory-new" type="button" class="text-button" hidden>Додати іншого</button></div><label class="field"><span>Найменування замовника *</span><input id="customer-directory-name" maxlength="500" required placeholder="Наприклад, ТОВ «Назва підприємства»"></label><p id="customer-directory-error" class="field-error" role="alert" hidden></p><div class="source-actions"><button id="customer-directory-save" type="submit" class="button primary">Додати замовника</button></div><div class="section-heading"><h3>Збережені замовники</h3></div><div id="customer-directory-list"></div><p id="customer-directory-empty" class="empty-state">Поки немає збережених замовників.</p></div><div class="modal-footer"><button id="customer-directory-done" type="button" class="button secondary">Готово</button></div></form>`;
  document.body.append(dialog);
  const select = $('#customer-directory-select');
  const nameInput = $('#customer-directory-name');
  const errorNode = $('#customer-directory-error');
  const selected = () => customers.find(customer => customer.id === selectedId) || null;
  const emitSelection = customer => document.dispatchEvent(new CustomEvent('protokol:customer-selected', {detail:{customer}}));

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    let value;
    try { value = await response.json(); } catch { throw new Error('Не вдалося прочитати довідник замовників.'); }
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Перевірте найменування замовника та повторіть дію.');
    return value;
  }
  function showError(message) {
    errorNode.textContent = message;
    errorNode.hidden = !message;
  }
  function resetForm() {
    editingId = '';
    $('#customer-directory-form').reset();
    $('#customer-directory-form-title').textContent = 'Новий замовник';
    $('#customer-directory-save').textContent = 'Додати замовника';
    $('#customer-directory-new').hidden = true;
    showError('');
  }
  function render() {
    select.innerHTML = '<option value="">Ввести замовника вручну</option>' + customers.map(customer => `<option value="${escape(customer.id)}">${escape(customer.name)}</option>`).join('');
    select.value = selectedId;
    $('#customer-directory-list').innerHTML = customers.map(customer => `<article class="template-row" data-customer-id="${escape(customer.id)}"><div class="template-info"><h3>${escape(customer.name)}</h3>${customer.id === selectedId ? '<p>Обрано для поточного акта</p>' : ''}</div><div class="row-actions"><button type="button" class="icon-button customer-directory-edit" aria-label="Редагувати ${escape(customer.name)}" title="Редагувати">${icon('edit')}</button><button type="button" class="icon-button danger customer-directory-delete" aria-label="Видалити ${escape(customer.name)}" title="Видалити">${icon('trash')}</button></div></article>`).join('');
    $('#customer-directory-empty').hidden = customers.length > 0;
  }
  function syncName(name) {
    lastName = String(name || '').trim();
    const matches = customers.filter(customer => customer.name.trim() === lastName);
    selectedId = matches.length === 1 ? matches[0].id : '';
    render();
  }
  async function load() {
    const settings = await api('/api/settings');
    customers = Array.isArray(settings.customers) ? settings.customers : [];
    syncName($('[data-common="customer"]')?.value ?? lastName);
    $('#customer-directory-load-error').hidden = true;
  }
  function setBusy(value) {
    busy = value;
    $('#customer-directory-save').disabled = value;
    $('#customer-directory-new').disabled = value;
    $('#customer-directory-list').querySelectorAll('button').forEach(button => { button.disabled = value; });
  }
  document.addEventListener('protokol:act-loaded', event => syncName(event.detail?.customer || ''));
  commonFields.addEventListener('input', event => {
    if (event.target.dataset.common !== 'customer') return;
    lastName = event.target.value;
    if (selected()?.name !== lastName) { selectedId = ''; render(); }
  });
  select.addEventListener('change', () => {
    selectedId = select.value;
    const customer = selected();
    if (customer) lastName = customer.name;
    render();
    emitSelection(customer);
  });
  $('#customer-directory-manage').addEventListener('click', async () => {
    if (busy) return;
    try {
      await load();
      resetForm();
      render();
      dialog.showModal();
    } catch (error) {
      $('#customer-directory-load-error').textContent = error.message;
      $('#customer-directory-load-error').hidden = false;
    }
  });
  ['#customer-directory-close','#customer-directory-done'].forEach(selector => $(selector).addEventListener('click', () => { if (!busy) dialog.close(); }));
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('click', event => { if (event.target === dialog && !busy) dialog.close(); });
  $('#customer-directory-new').addEventListener('click', () => { if (!busy) { resetForm(); nameInput.focus(); } });
  $('#customer-directory-list').addEventListener('click', async event => {
    if (busy) return;
    const row = event.target.closest('[data-customer-id]');
    if (!row) return;
    const customer = customers.find(value => value.id === row.dataset.customerId);
    if (!customer) return;
    if (event.target.closest('.customer-directory-edit')) {
      editingId = customer.id;
      nameInput.value = customer.name;
      $('#customer-directory-form-title').textContent = 'Редагувати замовника';
      $('#customer-directory-save').textContent = 'Зберегти зміни';
      $('#customer-directory-new').hidden = false;
      showError('');
      nameInput.focus();
    }
    if (event.target.closest('.customer-directory-delete')) {
      if (!window.confirm(`Видалити замовника «${customer.name}» з довідника?`)) return;
      setBusy(true);
      showError('');
      try {
        await api(`/api/settings/customers/${encodeURIComponent(customer.id)}`, {method:'DELETE'});
        customers = customers.filter(value => value.id !== customer.id);
        const wasSelected = selectedId === customer.id;
        if (wasSelected) selectedId = '';
        if (editingId === customer.id) resetForm();
        render();
        if (wasSelected) emitSelection(null);
      } catch (error) { showError(error.message); }
      finally { setBusy(false); }
    }
  });
  $('#customer-directory-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const name = nameInput.value.trim();
    if (!name) return showError('Укажіть найменування замовника.');
    setBusy(true);
    showError('');
    try {
      const wasEditing = !!editingId;
      const customer = await api(`/api/settings/customers${editingId ? `/${encodeURIComponent(editingId)}` : ''}`, {
        method:editingId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})
      });
      const index = customers.findIndex(value => value.id === customer.id);
      if (index < 0) customers.push(customer); else customers[index] = customer;
      const shouldSelect = !wasEditing || selectedId === customer.id;
      if (shouldSelect) { selectedId = customer.id; lastName = customer.name; }
      resetForm();
      render();
      if (shouldSelect) emitSelection(customer);
    } catch (error) { showError(error.message); }
    finally { setBusy(false); }
  });
  load().catch(error => {
    $('#customer-directory-load-error').textContent = error.message;
    $('#customer-directory-load-error').hidden = false;
  });
})();
