'use strict';
(() => {
  const host = document.querySelector('.main-content');
  if (!host) return;
  const panel = document.createElement('section');
  panel.id='cover_letters-view';panel.hidden=true;panel.className='cover-page';
  panel.setAttribute('aria-labelledby','cover-title');
  panel.innerHTML = `
    <div class="page-heading"><div><h1 id="cover-title">Супровідні листи</h1><p class="subtitle">Оберіть протоколи, перевірте лист і завантажте Word або PDF.</p></div><button type="button" class="button quiet" data-manage-signers>Підписанти</button></div>
    <ol class="cover-steps" aria-label="Етапи підготовки супровідних"><li data-cover-step="1" class="current"><span>1</span>Комплект протоколів</li><li data-cover-step="2"><span>2</span>Текст і вигляд листів</li><li data-cover-step="3"><span>3</span>Перевірка й завантаження</li></ol>
    <details class="panel cover-batch" data-batch-details open><summary><strong>Комплект протоколів</strong><span data-batch-summary>Додайте файли для підготовки листів</span></summary><div class="details-body">
      <p class="section-description">Додайте DOCX, PDF або ZIP із протоколами та, за потреби, таблицю XLSX. Буде підготовлено загальну супровідну й окремі листи фірмам.</p>
      <div class="cover-import-row"><label class="field"><span>Протоколи та Excel *</span><input type="file" multiple accept=".docx,.pdf,.zip,.xlsx" data-files></label><button type="button" class="button secondary" data-import>Прочитати комплект</button></div>
      <label class="confirmation office-count"><input type="checkbox" data-exact-pages checked><span>Порахувати аркуші в локальному редакторі <small data-office-count-hint></small></span></label>
      <div data-review hidden><div data-warnings class="notice warning"></div><details class="cover-records" open><summary>Реквізити протоколів</summary><div data-records></div></details>
        <details class="cover-common"><summary>Спільні підписи та додатки</summary><div class="details-body"><div class="form-grid"><label class="field"><span>Профіль для загального листа</span><select data-apply-profile="general"><option value="">Вручну</option></select></label><label class="field"><span>Профіль для фірм</span><select data-apply-profile="company"><option value="">Вручну</option></select></label></div><div data-signers class="form-grid"></div><label class="field"><span>Виконавець і телефон</span><input data-contact></label><label class="field"><span>Інші додатки до загального листа</span><textarea data-extra rows="3" placeholder="Кожен документ з нового рядка: назва, кількість аркушів і примірників."></textarea></label><label class="confirmation"><input type="checkbox" data-excel><span>Додати до переліку зведену таблицю Excel</span></label></div></details>
        <div class="cover-prepare"><p>Перевірте реквізити й перейдіть до редагування кожного листа.</p><button type="button" class="button primary" data-preview>Переглянути й відредагувати листи</button></div>
      </div>
    </div></details>
    <p role="status" class="cover-status" data-status></p><div role="alert" class="notice warning" data-error hidden></div>
    <section data-letters hidden aria-label="Редактор супровідних"></section>
    <section class="panel generation-panel cover-generation" data-cover-generation hidden><div><label class="confirmation"><input type="checkbox" data-confirm><span>Реквізити, аркуші та текст листів перевірено.</span></label><div class="cover-final-links"><a data-download hidden class="text-button" download="Супровідні.zip">Повторно завантажити Word</a><button type="button" class="text-button" data-journal-draft>Зберегти в журнал як чернетку</button><button type="button" class="text-button" data-open-journal>Відкрити журнал</button></div></div><button type="button" class="button primary" data-generate disabled>Завантажити супровідні ZIP</button></section>`;
  host.append(panel);
  const editor=panel.querySelector('[data-letters]');
  const $ = s => panel.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let records = [], letters = [], busy = false, url = '', settingsEdited = false;
  let selectedLetterId='',previewTimer=null,previewController=null,previewGeneration=0;
  let generationId=crypto.randomUUID();
  const changed = () => window.OZWorkspace?.changed('cover_letters');
  const signers = {
    general_position:['Посада підписанта загального листа','Командир військової частини А0873'],
    general_signer:['Звання та ім’я підписанта загального листа','полковник Олександр КАЛЮЖНИЙ'],
    company_position:['Посада підписанта листів фірмам','Тимчасово виконуючий обов’язки начальника лабораторії безпечності харчових продуктів - заступника командира військової частини А0873'],
    company_signer:['Звання та ім’я підписанта листів фірмам','майор Ростислав АДРІАНОВ']
  };
  $('[data-signers]').innerHTML = Object.entries(signers).map(([key,[label,value]])=>`<label class="field"><span>${esc(label)}</span><input data-signer="${key}" value="${esc(value)}"></label>`).join('')+'<label class="field"><span>Виконавець і телефон загального листа</span><input data-role-contact="general"></label><label class="field"><span>Виконавець і телефон листів фірмам</span><input data-role-contact="company"></label>';
  $('[data-contact]').closest('.field').hidden=true;
  function error(e) { $('[data-error]').textContent=e.message; $('[data-error]').hidden=false; }
  async function request(path,body,options={}) {
    const response = await fetch('/api/cover-letters/'+path,{method:'POST',body,...options});
    if (!response.ok) {
      const data=await response.json();
      throw new Error(typeof data.detail==='string'?data.detail:(data.detail||[]).map(v=>v.msg).join('; ')||'Не вдалося обробити комплект.');
    }
    return response;
  }
  function lock(value) {
    busy=value;
    panel.querySelectorAll('input,textarea,select,button').forEach(n=>n.disabled=value);
    if (!value) $('[data-generate]').disabled = !letters.length;
    if (!value)refreshOffice();
  }
  function invalidate() {
    generationId=crypto.randomUUID();window.OZOffice?.invalidate('cover_letters');
    if (url) URL.revokeObjectURL(url);
    url=''; $('[data-download]').hidden=true;
    $('[data-confirm]').checked=false;
    updateSteps(letters.length?2:1);
  }
  function clearLetters() {
    letters=[];
    selectedLetterId='';clearTimeout(previewTimer);previewController?.abort();previewGeneration++;
    editor.replaceChildren();
    editor.hidden=true;
    $('[data-generate]').disabled=true;
    $('[data-cover-generation]').hidden=true;$('[data-batch-details]').open=true;
    document.querySelector('#nav-letter-count').textContent='0';
    updateSteps(1);
  }
  panel.addEventListener('focusin',()=>document.dispatchEvent(new CustomEvent('protokol:workspace-section',{detail:{name:'cover_letters'}})));
  panel.addEventListener('input',e=>{
    if (e.target.matches('[data-confirm]')) {updateSteps(e.target.checked?3:2);return;}
    if (e.target.matches('[data-files],[data-page-zoom],[data-exact-pages],[data-apply-profile],[data-letter-select]')) return;
    e.target.closest('.field')?.classList.remove('invalid');
    settingsEdited=true;
    invalidate();
    if (!e.target.matches('[data-letter-field],[data-registration-field]')) clearLetters();
    else queuePreview();
    if(e.target.matches('[data-letter-field="recipients"],[data-letter-field="position"],[data-letter-field="signer"],[data-letter-field="contact"]'))fitShortFields();
    changed();
  });
  $('[data-files]').addEventListener('change',()=>{
    invalidate();
  });
  $('[data-import]').addEventListener('click',async()=>{
    if (busy) return;
    const files=[...$('[data-files]').files];
    const exactPages=$('[data-exact-pages]').checked&&!!window.OZOffice?.status?.available;
    if (!files.length) return error(new Error('Оберіть протоколи та, за потреби, Excel.'));
    if ((records.length || letters.length) && !window.confirm('Замінити поточний комплект супровідних новими файлами?')) return;
    lock(true); $('[data-error]').hidden=true; $('[data-status]').textContent='Читаємо комплект…';
    invalidate();
    try {
      const form=new FormData(); files.forEach(f=>form.append('files',f));
      form.append('exact_pages',String(exactPages));
      const data=await (await request('import',form)).json(); records=data.records;
      clearLetters();
      $('[data-warnings]').textContent=(data.warnings||[]).join('\n');
      $('[data-warnings]').style.whiteSpace='pre-line';
      $('[data-warnings]').hidden=!(data.warnings||[]).length;
      $('[data-excel]').checked=data.has_excel;
      renderRecords();
      $('[data-review]').hidden=false;
      $('[data-status]').textContent=`Прочитано протоколів: ${records.length}. Перевірте реквізити нижче.`;
      $('[data-batch-summary]').textContent=`Протоколів: ${records.length}`;
      changed();
    } catch(e) { error(e); $('[data-status]').textContent=''; }
    finally { lock(false); }
  });
  function renderRecords() {
    $('[data-records]').innerHTML=records.map((r,i)=>`<article class="panel"><h3>${i+1}. ${esc(r.source)}</h3><div class="form-grid">${[['number','Номер протоколу'],['date','Дата протоколу'],['supplier','Назва фірми'],['code','Код постачальника'],['pages','Кількість аркушів']].map(([key,label])=>`<label class="field"><span>${label}${key!=='code'?' *':''}</span><input data-index="${i}" data-key="${key}" value="${esc(r[key])}" ${key!=='code'?'required':''} ${key==='pages'?'type="number" min="1" max="10000" step="1"':'type="text"'}></label>`).join('')}</div></article>`).join('');
  }
  function payload(numericPages = true) {
    const values=records.map(record=>({...record}));
    panel.querySelectorAll('[data-key]').forEach(n=>values[Number(n.dataset.index)][n.dataset.key]=n.dataset.key==='pages' && numericPages?Number(n.value):n.value);
    const result={records:values,confirmed:$('[data-confirm]').checked,include_excel:$('[data-excel]').checked,contact:$('[data-contact]').value,general_contact:$('[data-role-contact="general"]').value,company_contact:$('[data-role-contact="company"]').value,extra_attachments:$('[data-extra]').value,generation_id:generationId,registrations:editedRegistrations()};
    panel.querySelectorAll('[data-signer]').forEach(n=>result[n.dataset.signer]=n.value);
    return result;
  }
  const fields = [
    ['recipients','Адресат',2],
    ['body','Текст листа',9],
    ['attachments','Перелік додатків',5],
    ['position','Посада підписанта',3],['signer','Звання та ім’я підписанта',1],['contact','Виконавець і телефон',1]
  ];
  function updateSteps(step) {
    panel.querySelectorAll('[data-cover-step]').forEach(node=>{
      node.classList.toggle('current',Number(node.dataset.coverStep)===step);
      node.classList.toggle('complete',Number(node.dataset.coverStep)<step);
    });
  }
  function fieldMarkup(key) {
    const [,label,rows]=fields.find(field=>field[0]===key),required=!['attachments','contact'].includes(key);
    return `<label class="field"><span>${label}${required?' *':''}</span><textarea rows="${rows}" data-letter-field="${key}" ${required?'required':''}></textarea>${key==='body'?'<small>Кожен рядок — окремий абзац.</small>':''}</label>`;
  }
  function letterMarkup(letter) {
    const registration=letter.registration||{};
    return `<div data-letter="${esc(letter.id)}" ${letter.id===selectedLetterId?'':'hidden'}>
      <details class="letter-extra letter-registration" ${registration.number?'open':''}><summary>Номер і дата листа <small>для штампа й журналу</small></summary><div class="form-grid"><label class="field"><span>Вихідний номер</span><input data-registration-field="number" maxlength="100" value="${esc(registration.number||'')}" placeholder="Не присвоєно"></label><label class="field"><span>Дата листа</span><input type="date" data-registration-field="date" value="${esc(registration.date||'')}"></label></div></details>
      ${fieldMarkup('recipients')}${fieldMarkup('body')}
      <details class="letter-extra"><summary>Додатки <small>${letter.content.attachments?.length||0}</small></summary><div>${fieldMarkup('attachments')}<small>Кожен додаток з нового рядка. Нумерація додається автоматично.</small></div></details>
      <details class="letter-extra"><summary>Підпис і контакти</summary><div class="letter-signature-fields"><label class="field"><span>Збережений підпис</span><select data-apply-profile="individual"><option value="">Вручну</option></select></label>${fieldMarkup('position')}${fieldMarkup('signer')}${fieldMarkup('contact')}<button type="button" class="text-button" data-save-current-signer>Зберегти цей підпис як профіль</button></div></details>
    </div>`;
  }
  let editorModeManuallyChosen=false;
  function renderLetters() {
    if(!letters.some(letter=>letter.id===selectedLetterId))selectedLetterId=letters[0]?.id||'';
    const mode=window.OZAppearance?.editorMode()||(matchMedia('(max-width:1100px)').matches?'edit':'split');
    editorModeManuallyChosen=false;
    editor.innerHTML=letters.length?`<div class="cover-workbench"><section class="letter-document-area">
      <div class="letter-toolbar"><div class="letter-switcher"><label class="field"><span>Лист</span><select data-letter-select aria-label="Лист комплекту">${letters.map(letter=>`<option value="${esc(letter.id)}">${esc(letter.title)}</option>`).join('')}</select></label><div class="letter-paging"><button type="button" class="button quiet" data-letter-move="-1" aria-label="Попередній лист">←</button><span data-letter-index></span><button type="button" class="button quiet" data-letter-move="1" aria-label="Наступний лист">→</button></div></div><h2 class="sr-only" data-letter-title></h2><div class="letter-mobile-tabs" role="group" aria-label="Вигляд редактора"><button type="button" class="button quiet ${mode==='edit'?'active':''}" data-editor-mode="edit">Текст</button><button type="button" class="button quiet ${mode==='preview'?'active':''}" data-editor-mode="preview">Сторінка</button><button type="button" class="button quiet ${mode==='split'?'active':''}" data-editor-mode="split">Поруч</button></div></div>
      <div class="letter-editor-layout" data-editor-layout data-mode="${mode}"><div class="letter-fields">${letters.map(letterMarkup).join('')}</div><aside class="letter-preview"><div class="preview-heading"><span data-page-status role="status">Готуємо сторінку…</span><label><span class="sr-only">Масштаб сторінки</span><select data-page-zoom aria-label="Масштаб сторінки"><option value="fit">За шириною</option><option value="75">75%</option><option value="100">100%</option></select></label></div><div class="word-preview" data-page-preview></div><p class="preview-note">Попередній вигляд. Точний макет — у Word та експортованому PDF.</p></aside></div>
    </section></div>`:'';
    for(const letter of letters){
      const section=[...editor.querySelectorAll('[data-letter]')].find(node=>node.dataset.letter===letter.id);
      for(const [key] of fields)section.querySelector(`[data-letter-field="${key}"]`).value=Array.isArray(letter.content[key])?letter.content[key].join('\n'):letter.content[key]||'';
    }
    editor.hidden=!letters.length;
    $('[data-generate]').disabled=!letters.length;
    $('[data-cover-generation]').hidden=!letters.length;
    $('[data-batch-details]').open=!letters.length;
    $('[data-batch-summary]').textContent=records.length?`Протоколів: ${records.length} · Листів: ${letters.length}`:'Додайте файли для підготовки листів';
    document.querySelector('#nav-letter-count').textContent=String(letters.length);
    updateSteps(letters.length?2:1);
    if(letters.length)selectLetter(selectedLetterId,false);
    document.dispatchEvent(new CustomEvent('protokol:letters-rendered'));
  }
  function selectLetter(id,save=true) {
    selectedLetterId=id;
    editor.querySelectorAll('[data-letter]').forEach(node=>node.hidden=node.dataset.letter!==id);
    if($('[data-letter-select]'))$('[data-letter-select]').value=id;
    if($('[data-letter-index]'))$('[data-letter-index]').textContent=`${letters.findIndex(letter=>letter.id===id)+1} / ${letters.length}`;
    const letter=letters.find(value=>value.id===id);
    $('[data-letter-title]').textContent=letter?.title||'Супровідна';
    fitShortFields();
    $('[data-page-preview]').replaceChildren();queuePreview(0);
    if(save)changed();
  }
  function fitShortFields() {
    const section=editor.querySelector('[data-letter]:not([hidden])');if(!section)return;
    for(const key of ['recipients','position','signer','contact']){
      const field=section.querySelector(`[data-letter-field="${key}"]`);
      if(field?.offsetWidth){field.style.height='0px';field.style.height=Math.min(340,Math.max(44,field.scrollHeight+2))+'px';}
    }
  }
  function setEditorMode(mode) {
    const layout=$('[data-editor-layout]');if(!layout)return;
    layout.dataset.mode=mode;
    editor.querySelectorAll('[data-editor-mode]').forEach(button=>button.classList.toggle('active',button.dataset.editorMode===mode));
    fitShortFields();window.OZDocumentPreview.fit($('[data-page-preview]'),$('[data-page-zoom]').value);
    if(mode!=='edit')queuePreview(0);
  }
  document.addEventListener('protokol:appearance-changed',event=>{
    const detail=event.detail||{};
    if(detail.editorMode && (detail.reason!=='load'||!editorModeManuallyChosen))setEditorMode(detail.editorMode);
    fitShortFields();if($('[data-page-preview]'))window.OZDocumentPreview.fit($('[data-page-preview]'),$('[data-page-zoom]')?.value);
  });
  window.addEventListener('resize',fitShortFields);
  function queuePreview(delay=500) {
    clearTimeout(previewTimer);previewController?.abort();previewGeneration++;
    window.OZDocumentPreview.invalidate($('[data-page-preview]'));
    const status=$('[data-page-status]');if(status)status.textContent='Оновлюємо сторінку…';
    previewTimer=setTimeout(renderCurrentPreview,delay);
  }
  async function renderCurrentPreview() {
    const target=$('[data-page-preview]'),status=$('[data-page-status]'),id=selectedLetterId;
    if(!target || !id || !target.offsetWidth)return;
    const current=++previewGeneration;
    previewController=new AbortController();
    target.setAttribute('aria-busy','true');
    try {
      const form=new FormData();
      form.append('payload',JSON.stringify({...payload(),confirmed:false,edits:{[id]:editedLetters()[id]}}));
      form.append('letter_id',id);
      const response=await request('preview-document',form,{signal:previewController.signal});
      const data=await response.arrayBuffer();if(current!==previewGeneration)return;
      const count=await window.OZDocumentPreview.render(data,target,{className:'ozdoc-cover'});
      if(current===previewGeneration)status.textContent=`А4 · ${count||1} стор.`;
    }catch(error){
      if(error.name!=='AbortError' && current===previewGeneration){
        status.textContent='Перевірте текст листа';target.replaceChildren();
        const message=document.createElement('p');message.className='preview-empty';message.textContent=error.message;target.append(message);
      }
    }finally{if(current===previewGeneration)target.removeAttribute('aria-busy');}
  }
  editor.addEventListener('click',event=>{
    const move=event.target.closest('[data-letter-move]');
    if(move){const index=letters.findIndex(letter=>letter.id===selectedLetterId);selectLetter(letters[(index+Number(move.dataset.letterMove)+letters.length)%letters.length].id);}
    const mode=event.target.closest('[data-editor-mode]');
    if(mode){editorModeManuallyChosen=true;setEditorMode(mode.dataset.editorMode);}
  });
  editor.addEventListener('change',event=>{
    if(event.target.matches('[data-letter-select]'))selectLetter(event.target.value);
    if(event.target.matches('[data-page-zoom]'))window.OZDocumentPreview.fit($('[data-page-preview]'),event.target.value);
  });
  document.addEventListener('protokol:view',event=>{if(event.detail.name==='cover_letters' && letters.length){fitShortFields();queuePreview(0);}});
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
  function editedRegistrations() {
    return Object.fromEntries(letters.map(letter=>{
      const section=[...editor.querySelectorAll('[data-letter]')].find(node=>node.dataset.letter===letter.id);
      return [letter.id,Object.fromEntries(['number','date'].map(key=>[key,section?.querySelector(`[data-registration-field="${key}"]`)?.value??letter.registration?.[key]??'']))];
    }));
  }
  function refreshOffice() {
    const status=window.OZOffice?.status;
    $('[data-exact-pages]').disabled=busy||!status?.available;
    $('[data-office-count-hint]').textContent=status?.available?'('+status.label+')':status?'Word або LibreOffice не знайдено — аркуші можна вказати вручну.':'Перевіряємо редактор…';
  }
  document.addEventListener('protokol:office-status',refreshOffice);
  refreshOffice();
  $('[data-open-journal]').onclick=()=>document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'outgoing'}}));
  $('[data-journal-draft]').onclick=async()=>{
    if(busy||!letters.length)return;
    lock(true);$('[data-error]').hidden=true;
    try{
      const form=new FormData();form.append('payload',JSON.stringify({...payload(),confirmed:false,edits:editedLetters()}));
      await request('save-draft',form);
      $('[data-status]').textContent='Чернетку листів збережено в журналі.';
      document.dispatchEvent(new CustomEvent('protokol:journal-changed'));
    }catch(exc){error(exc);}finally{lock(false);}
  };
  $('[data-generate]').addEventListener('click',async()=>{
    if (busy) return;
    if (!letters.length) return error(new Error('Спочатку підготуйте листи для редагування.'));
    if (!$('[data-confirm]').checked) return error(new Error('Підтвердьте перевірку реквізитів і відредагованих листів.'));
    $('[data-error]').hidden=true;
    const values={...payload(),edits:editedLetters()};
    lock(true);
    try {
      const form=new FormData(); form.append('payload',JSON.stringify(values));
      const response=await request('generate',form),blob=await response.blob();
      if(url) URL.revokeObjectURL(url); url=URL.createObjectURL(blob);
      const link=$('[data-download]'); link.href=url; link.hidden=false;
      document.dispatchEvent(new CustomEvent('protokol:journal-changed'));
      updateSteps(3);
      $('[data-status]').textContent='Листи збережено в журналі. Повторне завантаження доступне без нового формування.';
      await window.OZOffice.deliver(blob,'Супровідні.zip','cover_letters');
    } catch(e) { error(e); } finally { lock(false); }
  });
  function reviewIssues() {
    const issues=[],values=payload(false);
    const add=(message,selector,letterId='')=>issues.push({message,selector,letterId,section:'cover_letters',level:'error'});
    if(!records.length)add('Додайте протоколи для супровідних.','#cover_letters-view [data-files]');
    values.records.forEach((record,i)=>{
      for(const [key,label] of [['number','номер протоколу'],['date','дату протоколу'],['supplier','назву фірми']])
        if(!String(record[key]||'').trim())add(`Протокол ${i+1}: заповніть ${label}.`,`#cover_letters-view [data-index="${i}"][data-key="${key}"]`);
      const pages=Number(record.pages);
      if(!Number.isInteger(pages)||pages<1||pages>10000)add(`Протокол ${i+1}: перевірте кількість аркушів.`,`#cover_letters-view [data-index="${i}"][data-key="pages"]`);
    });
    if(records.length && !letters.length)add('Підготуйте листи для редагування.','#cover_letters-view [data-preview]');
    const contents=editedLetters();
    for(const letter of letters){
      const content=contents[letter.id];
      const registration=editedRegistrations()[letter.id];
      if(registration.number&&!registration.date)add(`${letter.title}: укажіть дату вихідного листа.`,`#cover_letters-view [data-letter="${CSS.escape(letter.id)}"] [data-registration-field="date"]`,letter.id);
      for(const [key,label] of [['recipients','адресата'],['body','основний текст'],['position','посаду підписанта'],['signer','звання та ім’я підписанта']]){
        const value=content[key],filled=Array.isArray(value)?value.some(line=>line.trim()):String(value||'').trim();
        if(!filled)add(`${letter.title}: заповніть ${label}.`,`#cover_letters-view [data-letter="${CSS.escape(letter.id)}"] [data-letter-field="${key}"]`,letter.id);
      }
    }
    return issues;
  }
  const provider={
    title:'Супровідні листи',navigationSection:'cover_letters',
    hasData:()=>!!records.length || !!letters.length || settingsEdited,
    snapshot:()=>{
      if (!records.length && !letters.length && !settingsEdited) return null;
      const edits=editedLetters();
      return {...payload(false),confirmed:false,selectedLetterId,warnings:$('[data-warnings]').textContent,
        letters:letters.map(letter=>({...letter,registration:editedRegistrations()[letter.id],content:edits[letter.id]}))};
    },
    restore:async data=>{
      if (busy) throw new Error('Дочекайтеся завершення обробки супровідних.');
      records=(Array.isArray(data.records)?data.records:[]).slice(0,100).map(record=>Object.fromEntries(
        ['source','number','date','supplier','code','pages','pages_source'].map(key=>[key,String(record?.[key]??'').slice(0,2000)])));
      letters=(Array.isArray(data.letters)?data.letters:[]).slice(0,101).filter(letter=>letter && typeof letter.id==='string' && letter.content && typeof letter.content==='object').map(letter=>({
        id:letter.id.slice(0,100),title:String(letter.title||'Супровідна').slice(0,1000),filename:String(letter.filename||'').slice(0,1000),
        registration:{number:String(letter.registration?.number??data.registrations?.[letter.id]?.number??'').slice(0,100),date:String(letter.registration?.date??data.registrations?.[letter.id]?.date??'').slice(0,30)},
        content:Object.fromEntries(fields.map(([key])=>[key,['recipients','body','attachments'].includes(key)
          ? (Array.isArray(letter.content[key])?letter.content[key]:[]).slice(0,key==='attachments'?400:100).map(line=>String(line).slice(0,12000))
          : String(letter.content[key]??'').slice(0,2000)]))
      }));
      panel.querySelectorAll('[data-signer]').forEach(node=>{node.value=String(data[node.dataset.signer]??signers[node.dataset.signer][1]);});
      $('[data-contact]').value=String(data.contact??'');
      for(const kind of ['general','company'])$(`[data-role-contact="${kind}"]`).value=String(data[kind+'_contact']??data.contact??'');
      panel.querySelectorAll('[data-apply-profile]').forEach(select=>select.value='');
      $('[data-extra]').value=String(data.extra_attachments??'');
      $('[data-excel]').checked=!!data.include_excel;
      $('[data-files]').value='';
      $('[data-warnings]').textContent=String(data.warnings??'');
      $('[data-warnings]').style.whiteSpace='pre-line';
      $('[data-warnings]').hidden=!data.warnings;
      $('[data-review]').hidden=!records.length;
      $('[data-error]').hidden=true;
      settingsEdited=true;
      selectedLetterId=String(data.selectedLetterId||'');
      invalidate();renderRecords();renderLetters();
      generationId=/^[A-Za-z0-9_-]{1,100}$/.test(data.generation_id||'')?data.generation_id:crypto.randomUUID();
      $('[data-status]').textContent='Чернетку супровідних відновлено. Перевірте реквізити й текст перед завантаженням.';
    },
    issues:reviewIssues,
    reveal:issue=>{
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));
      if(issue?.letterId){
        selectLetter(issue.letterId,false);
        $('[data-editor-layout]').dataset.mode='edit';
        editor.querySelectorAll('[data-editor-mode]').forEach(button=>button.classList.toggle('active',button.dataset.editorMode==='edit'));
      }
      const target=issue?.selector?document.querySelector(issue.selector):panel;
      target?.closest('.field')?.classList.add('invalid');
      for(let parent=target?.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
      target?.scrollIntoView({block:'center',behavior:'smooth'});target?.focus({preventScroll:true});
    }
  };
  window.OZWorkspace?.register('cover_letters',provider);
  window.OZCoverLetters={restore:async data=>{
    if(busy)throw new Error('Дочекайтеся обробки листів.');
    if(!await window.OZWorkspace.replaceWith('cover_letters',data))return false;
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));return true;
  },canApplyDefaults:()=>!records.length&&!letters.length&&!settingsEdited};
})();
