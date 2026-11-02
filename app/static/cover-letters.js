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
  let records = [], letters = [], busy = false, url = '', settingsEdited = false;
  const changed = () => window.OZWorkspace?.changed('cover_letters');
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
  panel.addEventListener('focusin',()=>document.dispatchEvent(new CustomEvent('protokol:workspace-section',{detail:{name:'cover_letters'}})));
  panel.addEventListener('input',e=>{
    if (e.target.matches('[data-confirm],[data-files]')) return;
    settingsEdited=true;
    invalidate();
    if (!e.target.matches('[data-letter-field]')) clearLetters();
    changed();
  });
  $('[data-files]').addEventListener('change',()=>{
    invalidate();
  });
  $('[data-import]').addEventListener('click',async()=>{
    if (busy) return;
    const files=[...$('[data-files]').files];
    if (!files.length) return error(new Error('Оберіть протоколи та, за потреби, Excel.'));
    if ((records.length || letters.length) && !window.confirm('Замінити поточний комплект супровідних новими файлами?')) return;
    lock(true); $('[data-error]').hidden=true; $('[data-status]').textContent='Читаємо комплект…';
    invalidate();
    try {
      const form=new FormData(); files.forEach(f=>form.append('files',f));
      const data=await (await request('import',form)).json(); records=data.records;
      clearLetters();
      $('[data-warnings]').textContent=(data.warnings||[]).join('\n');
      $('[data-warnings]').style.whiteSpace='pre-line';
      $('[data-warnings]').hidden=!(data.warnings||[]).length;
      $('[data-excel]').checked=data.has_excel;
      renderRecords();
      $('[data-review]').hidden=false;
      $('[data-status]').textContent=`Прочитано протоколів: ${records.length}. Перевірте реквізити нижче.`;
      changed();
    } catch(e) { error(e); $('[data-status]').textContent=''; }
    finally { lock(false); }
  });
  function renderRecords() {
    $('[data-records]').innerHTML=records.map((r,i)=>`<article class="panel"><h3>${i+1}. ${esc(r.source)}</h3><div class="form-grid">${[['number','Номер протоколу'],['date','Дата протоколу'],['supplier','Назва фірми'],['code','Код постачальника'],['pages','Кількість аркушів']].map(([key,label])=>`<label class="field"><span>${label}</span><input data-index="${i}" data-key="${key}" value="${esc(r[key])}" ${key==='pages'?'type="number" min="1" max="10000" step="1"':'type="text"'}></label>`).join('')}</div></article>`).join('');
  }
  function payload(numericPages = true) {
    const values=records.map(record=>({...record}));
    panel.querySelectorAll('[data-key]').forEach(n=>values[Number(n.dataset.index)][n.dataset.key]=n.dataset.key==='pages' && numericPages?Number(n.value):n.value);
    const result={records:values,confirmed:$('[data-confirm]').checked,include_excel:$('[data-excel]').checked,contact:$('[data-contact]').value,extra_attachments:$('[data-extra]').value};
    panel.querySelectorAll('[data-signer]').forEach(n=>result[n.dataset.signer]=n.value);
    return result;
  }
  const fields = [
    ['recipients','Адресат (кожен з нового рядка)',3],
    ['body','Основний текст (кожен рядок — окремий абзац)',7],
    ['attachments','Додатки (кожен з нового рядка, без нумерації)',6],
    ['position','Посада підписанта',3],['signer','Звання та ім’я підписанта',1],['contact','Виконавець і телефон',1]
  ];
  function renderLetters() {
    editor.innerHTML=letters.length?'<h3>Редактор супровідних</h3><p>Змініть текст кожного листа перед завантаженням. Оформлення Word-зразка збережеться. Після зміни реквізитів комплекту сформуйте листи заново.</p>'+letters.map((letter,i)=>`<details class="panel" data-letter="${esc(letter.id)}" ${i===0?'open':''}><summary><strong>${esc(letter.title)}</strong></summary><div class="details-body">${fields.map(([key,label,rows])=>`<label class="field"><span>${label}</span><textarea rows="${rows}" data-letter-field="${key}">${esc(Array.isArray(letter.content[key])?letter.content[key].join('\n'):letter.content[key])}</textarea></label>`).join('')}</div></details>`).join(''):'';
    editor.hidden=!letters.length;
    $('[data-generate]').disabled=!letters.length;
  }
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
      renderLetters();
      $('[data-status]').textContent=`Листів для редагування: ${letters.length}. Перевірте текст і підтвердьте завантаження нижче.`;
      changed();
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
  window.OZWorkspace?.register('cover_letters',{
    title:'Супровідні листи',navigationSection:'excel',
    hasData:()=>!!records.length || !!letters.length || settingsEdited,
    snapshot:()=>{
      if (!records.length && !letters.length && !settingsEdited) return null;
      const edits=editedLetters();
      return {...payload(false),confirmed:false,warnings:$('[data-warnings]').textContent,
        letters:letters.map(letter=>({...letter,content:edits[letter.id]}))};
    },
    restore:async data=>{
      if (busy) throw new Error('Дочекайтеся завершення обробки супровідних.');
      records=(Array.isArray(data.records)?data.records:[]).slice(0,100).map(record=>Object.fromEntries(
        ['source','number','date','supplier','code','pages'].map(key=>[key,String(record?.[key]??'').slice(0,2000)])));
      letters=(Array.isArray(data.letters)?data.letters:[]).slice(0,101).filter(letter=>letter && typeof letter.id==='string' && letter.content && typeof letter.content==='object').map(letter=>({
        id:letter.id.slice(0,100),title:String(letter.title||'Супровідна').slice(0,1000),filename:String(letter.filename||'').slice(0,1000),
        content:Object.fromEntries(fields.map(([key])=>[key,['recipients','body','attachments'].includes(key)
          ? (Array.isArray(letter.content[key])?letter.content[key]:[]).slice(0,key==='attachments'?400:100).map(line=>String(line).slice(0,12000))
          : String(letter.content[key]??'').slice(0,2000)]))
      }));
      panel.querySelectorAll('[data-signer]').forEach(node=>{node.value=String(data[node.dataset.signer]??signers[node.dataset.signer][1]);});
      $('[data-contact]').value=String(data.contact??'');
      $('[data-extra]').value=String(data.extra_attachments??'');
      $('[data-excel]').checked=!!data.include_excel;
      $('[data-files]').value='';
      $('[data-warnings]').textContent=String(data.warnings??'');
      $('[data-warnings]').style.whiteSpace='pre-line';
      $('[data-warnings]').hidden=!data.warnings;
      $('[data-review]').hidden=!records.length;
      $('[data-error]').hidden=true;
      settingsEdited=true;
      invalidate();renderRecords();renderLetters();
      $('[data-status]').textContent='Чернетку супровідних відновлено. Перевірте реквізити й текст перед завантаженням.';
    },
    issues:()=>[],
    reveal:()=>{document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'excel'}}));panel.scrollIntoView({block:'start'});}
  });
})();
