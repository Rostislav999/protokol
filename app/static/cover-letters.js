'use strict';
(() => {
  const host = document.querySelector('#excel-view');
  if (!host) return;
  const panel = document.createElement('section');
  panel.className = 'panel common-panel';
  panel.innerHTML = `<h2>Супровідні листи</h2><p>Завантажте разом протоколи DOCX/PDF (або ZIP) та вашу таблицю XLSX. Буде створено одну загальну супровідну на Агенцію оборонних закупівель і Управління безпечності харчових продуктів та ветеринарної медицини, а також окрему на кожну фірму.</p><label class="field"><span>Протоколи та Excel</span><input type="file" multiple accept=".docx,.pdf,.zip,.xlsx" data-files></label><button type="button" class="button secondary" data-import>Прочитати комплект</button><p role="status" data-status></p><div role="alert" class="notice warning" data-error hidden></div><div data-review hidden><div data-warnings class="notice warning"></div><div data-records></div><h3>Підписи та додатки</h3><p>Перевірте актуальність підписантів перед формуванням.</p><div data-signers class="form-grid"></div><label class="field"><span>Виконавець і телефон</span><input data-contact></label><label class="field"><span>Інші додатки до загального листа (кожен з нового рядка)</span><textarea data-extra placeholder="Вкажіть назву документа, кількість аркушів та примірників лише для додатків, які справді надсилаєте."></textarea></label><label class="confirmation"><input type="checkbox" data-excel><span>Додати до переліку додатків загального листа зведену таблицю Excel</span></label><label class="confirmation"><input type="checkbox" data-confirm><span>Перевірено фірми, номери, дати, кількість аркушів, підписи та перелік додатків.</span></label><button type="button" class="button primary" data-generate>Завантажити супровідні ZIP</button><a data-download hidden class="button secondary" download="Супровідні.zip">Завантажити ZIP повторно</a></div>`;
  host.append(panel);
  const $ = s => panel.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let records = [], busy = false, url = '';
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
  }
  function invalidate() {
    if (url) URL.revokeObjectURL(url);
    url=''; $('[data-download]').hidden=true;
    $('[data-confirm]').checked=false;
  }
  panel.addEventListener('input',e=>{ if (!e.target.matches('[data-confirm]')) invalidate(); });
  $('[data-files]').addEventListener('change',()=>{
    records=[]; $('[data-review]').hidden=true; invalidate();
  });
  $('[data-import]').addEventListener('click',async()=>{
    if (busy) return;
    const files=[...$('[data-files]').files];
    if (!files.length) return error(new Error('Оберіть протоколи та, за потреби, Excel.'));
    lock(true); $('[data-error]').hidden=true; $('[data-status]').textContent='Читаємо комплект…';
    records=[]; $('[data-review]').hidden=true; invalidate();
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
  $('[data-generate]').addEventListener('click',async()=>{
    if (busy) return;
    if (!$('[data-confirm]').checked) return error(new Error('Підтвердьте перевірку реквізитів.'));
    $('[data-error]').hidden=true;
    panel.querySelectorAll('[data-key]').forEach(n=>records[Number(n.dataset.index)][n.dataset.key]=n.dataset.key==='pages'?Number(n.value):n.value);
    const payload={records,confirmed:true,include_excel:$('[data-excel]').checked,contact:$('[data-contact]').value,extra_attachments:$('[data-extra]').value};
    panel.querySelectorAll('[data-signer]').forEach(n=>payload[n.dataset.signer]=n.value);
    lock(true);
    try {
      const form=new FormData(); form.append('payload',JSON.stringify(payload));
      const blob=await (await request('generate',form)).blob();
      if(url) URL.revokeObjectURL(url); url=URL.createObjectURL(blob);
      const link=$('[data-download]'); link.href=url; link.hidden=false; link.click();
      $('[data-status]').textContent='Супровідні готові. ZIP містить Word-документи; вихідні протоколи та Excel до нього не копіюються.';
    } catch(e) { error(e); } finally { lock(false); }
  });
})();
