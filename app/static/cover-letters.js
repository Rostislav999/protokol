'use strict';
(() => {
  const host = document.querySelector('#excel-view');
  if (!host) return;
  const panel = document.createElement('section');
  panel.className = 'panel common-panel';
  panel.innerHTML = `<h2>Супровідні листи</h2><p>Завантажте разом протоколи DOCX/PDF (або ZIP) та вашу таблицю XLSX. Буде створено одну загальну супровідну на Агенцію оборонних закупівель і Управління безпечності харчових продуктів та ветеринарної медицини, а також окрему на кожну фірму.</p><label class="field"><span>Протоколи та Excel</span><input type="file" multiple accept=".docx,.pdf,.zip,.xlsx" data-files></label><button type="button" class="button secondary" data-import>Прочитати комплект</button><p role="status" data-status></p><div role="alert" class="notice warning" data-error hidden></div><div data-review hidden><div data-warnings class="notice warning"></div><div data-records></div><h3>Підписи та додатки</h3><p>Перевірте актуальність підписантів перед формуванням.</p><div data-signers class="form-grid"></div><label class="field"><span>Виконавець і телефон</span><input data-contact></label><label class="field"><span>Інші додатки до загального листа (кожен з нового рядка)</span><textarea data-extra placeholder="Вкажіть назву документа, кількість аркушів та примірників лише для додатків, які справді надсилаєте."></textarea></label><label class="confirmation"><input type="checkbox" data-excel><span>Додати до переліку додатків загального листа зведену таблицю Excel</span></label><label class="confirmation"><input type="checkbox" data-confirm><span>Перевірено фірми, номери, дати, кількість аркушів, підписи та перелік додатків.</span></label><button type="button" class="button primary" data-generate>Завантажити супровідні ZIP</button><a data-download hidden class="button secondary" download="Супровідні.zip">Завантажити ZIP повторно</a></div>`;
  host.append(panel);
  const previewButton = document.createElement('button');
  previewButton.type = 'button';
  previewButton.className = 'button secondary';
  previewButton.dataset.preview = '';
  previewButton.textContent = 'Переглянути й відредагувати листи';
  const editor = document.createElement('div');
  editor.dataset.letters = '';
  editor.hidden = true;
  const confirmation = panel.querySelector('[data-confirm]').closest('label');
  confirmation.before(previewButton,editor);
  panel.querySelector('[data-generate]').disabled = true;
  const $ = s => panel.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let records = [], letters = [], busy = false, url = '';
  const signers = {
    general_position:['Посада підписанта загального листа','Командир військової частини А0873'],
    general_signer:['Звання та ім’я підписанта загального листа','полковник Олександр КАЛЮЖНИЙ'],
    company_position:['Посада підписанта листів фірмам','Тимчасово виконуючий обов’язки начальника лабораторії безпечності харчових продуктів - заступника командира військової частини А0873'],
    company_signer:['Звання та ім’я підписанта листів фірмам','майор Ростислав АДРІАНОВ']
  };
  $('[data-signers]').innerHTML = Object.entries(signers).map(([key,[label,value]])=>`<label class="field"><span>${esc(label)}</span><input data-signer="${key}" value="${esc(value)}"></label>`).join('');
  function error(e) { $('[data-error]').textContent=e.message; $('[data-error]').hidden=false; }
  async function request(path,body) {
    const response = await fetch('/api/cover-letters/'+path,{method:'POST',body});
    if (!response.ok) {
      const data=await response.json();
      throw new Error(typeof data.detail==='string'?data.detail:(data.detail||[]).map(v=>v.msg).join('; ')||'Не вдалося обробити комплект.');
    }
    return response;
  }
  function lock(value) {
    busy=value;
    panel.querySelectorAll('input,textarea,button').forEach(n=>n.disabled=value);
    if (!value) $('[data-generate]').disabled = !letters.length;
  }
  function invalidate() {
    if (url) URL.revokeObjectURL(url);
    url=''; $('[data-download]').hidden=true;
    $('[data-confirm]').checked=false;
  }
  function clearLetters() {
    letters=[];
    editor.replaceChildren();
    editor.hidden=true;
    $('[data-generate]').disabled=true;
  }
  panel.addEventListener('input',e=>{
    if (e.target.matches('[data-confirm]')) return;
    invalidate();
    if (!e.target.matches('[data-letter-field]')) clearLetters();
  });
  $('[data-files]').addEventListener('change',()=>{
    records=[]; $('[data-review]').hidden=true; invalidate(); clearLetters();
  });
  $('[data-import]').addEventListener('click',async()=>{
    if (busy) return;
    const files=[...$('[data-files]').files];
    if (!files.length) return error(new Error('Оберіть протоколи та, за потреби, Excel.'));
    lock(true); $('[data-error]').hidden=true; $('[data-status]').textContent='Читаємо комплект…';
    records=[]; $('[data-review]').hidden=true; invalidate(); clearLetters();
    try {
      const form=new FormData(); files.forEach(f=>form.append('files',f));
      const data=await (await request('import',form)).json(); records=data.records;
      $('[data-warnings]').textContent=(data.warnings||[]).join('\n');
      $('[data-warnings]').style.whiteSpace='pre-line';
      $('[data-warnings]').hidden=!(data.warnings||[]).length;
      $('[data-excel]').checked=data.has_excel;
      $('[data-records]').innerHTML=records.map((r,i)=>`<article class="panel"><h3>${i+1}. ${esc(r.source)}</h3><div class="form-grid">${[['number','Номер протоколу'],['date','Дата протоколу'],['supplier','Назва фірми'],['code','Код постачальника'],['pages','Кількість аркушів']].map(([key,label])=>`<label class="field"><span>${label}</span><input data-index="${i}" data-key="${key}" value="${esc(r[key])}" ${key==='pages'?'type="number" min="1" max="10000" step="1"':'type="text"'}></label>`).join('')}</div></article>`).join('');
      $('[data-review]').hidden=false;
      $('[data-status]').textContent=`Прочитано протоколів: ${records.length}. Перевірте реквізити нижче.`;
    } catch(e) { error(e); $('[data-status]').textContent=''; }
    finally { lock(false); }
  });
  function payload() {
    panel.querySelectorAll('[data-key]').forEach(n=>records[Number(n.dataset.index)][n.dataset.key]=n.dataset.key==='pages'?Number(n.value):n.value);
    const result={records,confirmed:$('[data-confirm]').checked,include_excel:$('[data-excel]').checked,contact:$('[data-contact]').value,extra_attachments:$('[data-extra]').value};
    panel.querySelectorAll('[data-signer]').forEach(n=>result[n.dataset.signer]=n.value);
    return result;
  }
  const fields = [
    ['recipients','Адресат (кожен з нового рядка)',3],
    ['body','Основний текст (кожен рядок — окремий абзац)',7],
    ['attachments','Додатки (кожен з нового рядка, без нумерації)',6],
    ['position','Посада підписанта',3],['signer','Звання та ім’я підписанта',1],['contact','Виконавець і телефон',1]
  ];
  $('[data-preview]').addEventListener('click',async()=>{
    if (busy) return;
    if (letters.length && !window.confirm('Сформувати листи заново з реквізитів? Ручні зміни тексту буде замінено.')) return;
    $('[data-error]').hidden=true;
    const values=payload(); values.confirmed=false;
    invalidate(); lock(true);
    $('[data-status]').textContent='Готуємо листи для редагування…';
    try {
      const form=new FormData(); form.append('payload',JSON.stringify(values));
      letters=(await (await request('preview',form)).json()).letters;
      editor.innerHTML='<h3>Редактор супровідних</h3><p>Змініть текст кожного листа перед завантаженням. Оформлення Word-зразка збережеться. Після зміни реквізитів комплекту сформуйте листи заново.</p>'+letters.map((letter,i)=>`<details class="panel" data-letter="${esc(letter.id)}" ${i===0?'open':''}><summary><strong>${esc(letter.title)}</strong></summary><div class="details-body">${fields.map(([key,label,rows])=>`<label class="field"><span>${label}</span><textarea rows="${rows}" data-letter-field="${key}">${esc(Array.isArray(letter.content[key])?letter.content[key].join('\n'):letter.content[key])}</textarea></label>`).join('')}</div></details>`).join('');
      editor.hidden=false;
      $('[data-status]').textContent=`Листів для редагування: ${letters.length}. Перевірте текст і підтвердьте завантаження нижче.`;
    } catch(e) { error(e); $('[data-status]').textContent=''; }
    finally { lock(false); }
  });
  function editedLetters() {
    return Object.fromEntries(letters.map(letter=>{
      const section=[...editor.querySelectorAll('[data-letter]')].find(n=>n.dataset.letter===letter.id);
      const content={};
      fields.forEach(([key])=>{
        const value=section.querySelector(`[data-letter-field="${key}"]`).value.replace(/\r\n?/g,'\n');
        content[key]=key==='body'?value.split('\n') : ['recipients','attachments'].includes(key)?value.split('\n').map(s=>s.trim()).filter(Boolean):value.replace(/\n/g,' ');
      });
      return [letter.id,content];
    }));
  }
  $('[data-generate]').addEventListener('click',async()=>{
    if (busy) return;
    if (!letters.length) return error(new Error('Спочатку підготуйте листи для редагування.'));
    if (!$('[data-confirm]').checked) return error(new Error('Підтвердьте перевірку реквізитів і відредагованих листів.'));
    $('[data-error]').hidden=true;
    const values={...payload(),edits:editedLetters()};
    lock(true);
    try {
      const form=new FormData(); form.append('payload',JSON.stringify(values));
      const blob=await (await request('generate',form)).blob();
      if(url) URL.revokeObjectURL(url); url=URL.createObjectURL(blob);
      const link=$('[data-download]'); link.href=url; link.hidden=false; link.click();
      $('[data-status]').textContent='Супровідні готові. ZIP містить Word-документи; вихідні протоколи та Excel до нього не копіюються.';
    } catch(e) { error(e); } finally { lock(false); }
  });
})();
