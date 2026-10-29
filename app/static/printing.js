'use strict';

(() => {
  let generation = null;
  let capability = null;
  let printing = false;
  let revision = 0;
  const section = document.createElement('section');
  section.id = 'assignment-printing';
  section.className = 'panel generation-panel';
  section.hidden = true;
  section.innerHTML = '<div><h3>Друк завдань</h3><p id="assignment-print-help">Перевіряємо доступність друку…</p><small>Друк використовує останній сформований комплект. Після виправлення даних у формі сформуйте комплект повторно.</small><p id="assignment-print-result" role="status" hidden></p><button id="assignment-print-recheck" type="button" class="text-button" hidden>Перевірити доступність друку</button></div><button id="assignment-print" type="button" class="button secondary" disabled>Друкувати по 2 примірники</button>';
  const success = document.querySelector('#assignment-success');
  if (!success) return;
  success.after(section);
  const button = section.querySelector('#assignment-print');
  const help = section.querySelector('#assignment-print-help');
  const result = section.querySelector('#assignment-print-result');
  const recheck = section.querySelector('#assignment-print-recheck');

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
    button.disabled = !generation || !capability?.available || printing;
    button.textContent = printing ? 'Передаємо до принтера…' : 'Друкувати по 2 примірники';
    recheck.hidden = printing || !generation || capability?.available === true;
    if (printing) help.textContent = 'Microsoft Word передає документи до черги принтера. Дочекайтеся результату.';
    else if (!capability) help.textContent = 'Перевіряємо доступність друку…';
    else if (!capability.available) help.textContent = capability.reason || 'Прямий друк недоступний.';
    else help.textContent = `Для кожного з ${documentCount()} завдань буде надруковано 2 примірники. Принтер: ${capability.printer || 'вибраний у Windows за замовчуванням'}. Для друку використовується Microsoft Word.`;
  }
  async function checkCapability() {
    const current = revision;
    try {
      const status = await jsonRequest('/api/printing/status');
      if (current !== revision) return;
      capability = status;
    } catch (error) {
      if (current !== revision) return;
      capability = {available:false, reason:error.message};
    }
    render();
  }
  document.addEventListener('protokol:assignments-ready', event => {
    generation = JSON.parse(JSON.stringify(event.detail?.payload || null));
    revision++;
    result.hidden = true;
    render();
    checkCapability();
  });
  document.addEventListener('protokol:assignments-invalidated', () => {
    generation = null;
    revision++;
    if (!printing) result.hidden = true;
    render();
  });
  recheck.addEventListener('click', () => { capability = null; render(); checkCapability(); });
  button.addEventListener('click', async () => {
    if (!generation || printing || !capability?.available) return;
    const count = documentCount();
    if (!window.confirm(`Ви перевірили сформований комплект? Надрукувати ${count} завдань, кожне у 2 примірниках, на принтері ${capability.printer || 'за замовчуванням'}?`)) return;
    const payload = generation;
    const editor = document.querySelector('#assignment-editor');
    const wasDisabled = editor?.disabled;
    if (editor) editor.disabled = true;
    printing = true;
    result.hidden = true;
    render();
    try {
      const data = await jsonRequest('/api/assignments/print', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({generation:payload,copies:2,confirmed:true})});
      result.textContent = `До черги принтера «${data.printer}» передано ${data.documents} завдань, по ${data.copies} примірники кожного (${data.total_copies} примірників загалом).`;
      result.className = 'notice success';
      result.hidden = false;
    } catch (error) {
      result.textContent = error.message;
      result.className = 'notice warning';
      result.hidden = false;
    } finally {
      printing = false;
      if (editor) editor.disabled = !!wasDisabled;
      render();
    }
  });
})();
