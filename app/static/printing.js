'use strict';

(() => {
  let generation = null, capability = null, printing = false, checking = false;
  let revision = 0, requestId = 0, lastPrinter = '';
  const success = document.querySelector('#assignment-success');
  if (!success) return;
  const section = document.createElement('section');
  section.id = 'assignment-printing';
  section.className = 'panel generation-panel';
  section.hidden = true;
  section.innerHTML = '<div><h3>Друк завдань</h3><p id="assignment-print-help"></p><small>Друк використовує останній сформований комплект. Після виправлення даних сформуйте комплект повторно.</small><p id="assignment-print-result" role="status" hidden></p><button id="assignment-print-recheck" type="button" class="text-button">Оновити список принтерів</button></div><button id="assignment-print" type="button" class="button secondary">Вибрати принтер і друкувати</button>';
  success.after(section);
  const button = section.querySelector('#assignment-print');
  const help = section.querySelector('#assignment-print-help');
  const result = section.querySelector('#assignment-print-result');
  const recheck = section.querySelector('#assignment-print-recheck');
  const dialog = document.createElement('dialog');
  dialog.id = 'assignment-print-dialog';
  dialog.className = 'modal assignment-print-dialog';
  dialog.setAttribute('aria-labelledby','assignment-print-title');
  dialog.innerHTML = '<div class="print-dialog-frame"><div class="modal-heading"><h2 id="assignment-print-title">Друк завдань</h2><button type="button" class="icon-button" data-print-close aria-label="Закрити">×</button></div><div class="modal-body"><p data-print-summary></p><label class="field"><span>Принтер</span><select id="assignment-printer" aria-describedby="assignment-printer-status"><option value="">Виберіть принтер</option></select></label><button type="button" class="text-button" data-print-refresh>Оновити список</button><p id="assignment-printer-status" role="status" aria-live="polite"></p><small>Перевірте сформовані завдання перед друком. Кожен документ буде надруковано у двох примірниках.</small></div><div class="modal-footer"><button type="button" class="button quiet" data-print-close>Скасувати</button><button type="button" class="button primary" id="assignment-print-confirm" disabled>Друкувати</button></div></div>';
  document.body.append(dialog);
  const select = dialog.querySelector('#assignment-printer');
  const confirm = dialog.querySelector('#assignment-print-confirm');
  const refresh = dialog.querySelector('[data-print-refresh]');
  const status = dialog.querySelector('#assignment-printer-status');

  async function jsonRequest(url, options) {
    const response = await fetch(url, options);
    let data;
    try { data = await response.json(); } catch { throw new Error('Сервер повернув невідомий результат друку.'); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не вдалося передати завдання до принтера.');
    return data;
  }
  function documentCount() {
    return (generation?.items || []).reduce((total, item) => total + Object.keys(item.departments || {}).length, 0);
  }
  function render() {
    section.hidden = !generation;
    button.disabled = !generation || printing || checking;
    button.textContent = printing ? 'Передаємо до принтера…' : 'Вибрати принтер і друкувати';
    recheck.disabled = printing || checking;
    if (printing) help.textContent = 'Microsoft Word передає документи до черги вибраного принтера.';
    else if (checking) help.textContent = 'Оновлюємо список принтерів…';
    else if (capability && !capability.available) help.textContent = capability.reason || 'Прямий друк недоступний.';
    else help.textContent = `Для кожного з ${documentCount()} завдань буде надруковано 2 примірники. Принтер можна вибрати перед друком.`;
    select.disabled = printing || checking || !capability?.printers?.length;
    const selected = capability?.printers?.find(value => value.name === select.value && value.selectable);
    confirm.disabled = printing || checking || !generation || !capability?.available || !selected;
    confirm.textContent = printing ? 'Передаємо до принтера…' : 'Друкувати';
    refresh.disabled = printing || checking;
    refresh.textContent = checking ? 'Оновлюємо список…' : 'Оновити список';
    dialog.querySelectorAll('[data-print-close]').forEach(node => { node.disabled = printing; });
    dialog.querySelector('[data-print-summary]').textContent = `Завдань: ${documentCount()}. Примірників кожного: 2. Загалом: ${documentCount()*2}.`;
  }
  async function checkCapability(preferred = select.value || lastPrinter) {
    const current = revision, request = ++requestId;
    checking = true;
    status.textContent = 'Завантажуємо принтери Windows…';
    status.className = '';
    render();
    try {
      const data = await jsonRequest('/api/printing/status');
      if (current !== revision || request !== requestId) return;
      capability = data;
      select.replaceChildren(new Option('Виберіть принтер',''));
      for (const printer of data.printers || []) {
        const label = printer.name + (printer.is_default ? ' — за замовчуванням у Windows' : '') + (!printer.selectable ? ' — збереження у файл' : '');
        const option = new Option(label,printer.name);option.disabled = !printer.selectable;select.append(option);
      }
      const previous = data.printers?.find(value => value.name === preferred && value.selectable);
      const initial = !preferred && data.printers?.find(value => value.is_default && value.selectable);
      select.value = previous?.name || initial?.name || '';
      status.textContent = !data.available ? data.reason : preferred && !previous ? 'Попередній принтер відсутній у списку. Виберіть інший пристрій.' : 'Можна вибрати інший принтер або оновити список після підключення пристрою.';
      status.className = !data.available || preferred && !previous ? 'notice warning' : '';
    } catch (error) {
      if (current !== revision || request !== requestId) return;
      capability = {available:false,printers:[],reason:error.message};
      select.replaceChildren(new Option('Не вдалося отримати список',''));
      status.textContent = error.message;status.className = 'notice warning';
    } finally {
      if (current === revision && request === requestId) { checking = false;render(); }
    }
  }
  document.addEventListener('protokol:assignments-ready', event => {
    generation = JSON.parse(JSON.stringify(event.detail?.payload || null));
    revision++;requestId++;checking = false;
    if (dialog.open && !printing) dialog.close();
    result.hidden = true;render();checkCapability(lastPrinter);
  });
  document.addEventListener('protokol:assignments-invalidated', () => {
    generation = null;revision++;requestId++;checking = false;
    if (!printing) { result.hidden = true;dialog.close(); }
    render();
  });
  recheck.addEventListener('click', () => checkCapability(lastPrinter));
  refresh.addEventListener('click', () => checkCapability());
  select.addEventListener('change', render);
  dialog.querySelectorAll('[data-print-close]').forEach(node => node.addEventListener('click', () => { if (!printing) dialog.close(); }));
  dialog.addEventListener('cancel', event => { if (printing) event.preventDefault(); });
  dialog.addEventListener('close', () => { if (!printing && !dialog.open) { requestId++;checking = false;render(); } });
  button.addEventListener('click', () => {
    if (!generation || printing) return;
    render();dialog.showModal();checkCapability(lastPrinter);
  });
  confirm.addEventListener('click', async () => {
    if (!generation || printing || checking || confirm.disabled) return;
    const payload = generation, printer = select.value;
    const editor = document.querySelector('#assignment-editor'), wasDisabled = editor?.disabled;
    if (editor) editor.disabled = true;
    printing = true;lastPrinter = printer;result.hidden = true;
    status.textContent = `Передаємо завдання до принтера «${printer}»…`;status.className = '';
    render();
    try {
      const data = await jsonRequest('/api/assignments/print', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({generation:payload,printer,copies:2,confirmed:true})});
      result.textContent = `До черги принтера «${data.printer}» передано ${data.documents} завдань, по ${data.copies} примірники кожного (${data.total_copies} примірників загалом).`;
      result.className = 'notice success';result.hidden = false;dialog.close();
    } catch (error) {
      result.textContent = error.message;result.className = 'notice warning';result.hidden = false;
      status.textContent = error.message;status.className = 'notice warning';
    } finally {
      printing = false;
      if (editor) editor.disabled = !!wasDisabled;
      render();
    }
  });
  render();
})();
