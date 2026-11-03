'use strict';
(() => {
  const section=document.createElement('section');section.id='outgoing-view';section.hidden=true;
  section.setAttribute('aria-labelledby','outgoing-title');
  section.innerHTML=`<div class="page-heading"><div><h1 id="outgoing-title">Журнал вихідних</h1><p class="subtitle">Збережені листи, протоколи та статус відправлення.</p></div><button type="button" class="button secondary" data-journal-editor>До супровідних</button></div><div class="journal-toolbar"><label class="search-box"><svg class="icon"><use href="#i-search"/></svg><input type="search" data-journal-search aria-label="Знайти лист" placeholder="Фірма, номер листа або протоколу"></label><label class="field"><span class="sr-only">Статус</span><select data-journal-status aria-label="Статус листа"><option value="">Усі статуси</option value="draft">Чернетки</option><option value="ready">Готові</option><option value="sent">Надіслані</option></select></label><details class="journal-date-filter"><summary class="button quiet">Дати</summary><div><label class="field"><span>Від</span><input type="date" data-journal-from></label><label class="field"><span>До</span><input type="date" data-journal-to></label><button type="button" class="text-button" data-journal-reset>Скинути фільтри</button></div></details><button type="button" class="button quiet" data-journal-refresh>Оновити</button></div><p class="error-text" role="alert" data-journal-error hidden></p><div class="journal-table-wrap"><table class="journal-table"><thead><tr><th>Номер і дата</th><th>Адресат</th><th>Протоколи</th><th>Статус</th><th><span class="sr-only">Дії</span></th></tr></thead><tbody data-journal-rows></tbody></table><div class="journal-empty" data-journal-empty hidden><h2>Листів поки немає</h2><p>Готові супровідні з’являться тут після завантаження. Чернетку можна зберегти з редактора.</p><button type="button" class="button secondary" data-journal-editor>Підготувати супровідні</button></div></div><div class="journal-pagination"><span data-journal-total role="status"></span><div><button type="button" class="button quiet" data-journal-prev disabled>← Назад</button><button type="button" class="button quiet" data-journal-next disabled>Далі →</button></div></div>`;
  document.querySelector('.main-content').append(section);
  const dialog=document.createElement('dialog');dialog.id='outgoing-letter';dialog.className='modal journal-dialog';dialog.setAttribute('aria-labelledby','journal-letter-title');
  dialog.innerHTML=`<div class="modal-heading"><h2 id="journal-letter-title"></h2><button type="button" class="icon-button" data-journal-close aria-label="Закрити"><svg class="icon"><use href="#i-close"/></svg></button></div><form data-journal-form><div class="modal-body"><p data-journal-registration></p><details class="journal-protocol-details"><summary data-journal-protocol-count></summary><ul data-journal-protocols></ul></details><label class="field"><span>Статус у журналі</span><select data-journal-entry-status><option value="draft">Чернетка</option><option value="ready">Готово</option><option value="sent">Надіслано</option></select></label><label class="field"><span>Примітка</span><textarea rows="3" maxlength="3000" data-journal-notes placeholder="Наприклад, спосіб і дата відправлення"></textarea></label><p data-journal-message role="status"></p><div class="journal-file-actions"><a class="button secondary" data-journal-word>Завантажити Word</a><button type="button" class="button secondary" data-journal-pdf>Завантажити PDF</button><button type="button" class="text-button" data-journal-copy>Редагувати копію</button></div></div><div class="modal-footer"><button type="button" class="button quiet" data-journal-close>Закрити</button><button type="submit" class="button primary" data-journal-save>Зберегти статус</button></div></form>`;
  document.body.append(dialog);
  const $=selector=>section.querySelector(selector),d=selector=>dialog.querySelector(selector);
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const statusLabels={draft:'Чернетка',ready:'Готово',sent:'Надіслано'};
  const date=value=>value?value.slice(0,10).split('-').reverse().join('.'):'';
  let offset=0,total=0,current=null,controller=null,revision=0,detailRevision=0,timer=null,updating=false;
  async function request(url,options){
    const response=await fetch(url,options);
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:'Не вдалося відкрити журнал.');}
    return response;
  }
  function error(message){$('[data-journal-error]').textContent=message;$('[data-journal-error]').hidden=!message;}
  function message(text,isError=false){d('[data-journal-message]').textContent=text;d('[data-journal-message]').classList.toggle('error-text',isError);}
  async function load(){
    controller?.abort();controller=new AbortController();const requestRevision=++revision;
    error('');$('[data-journal-total]').textContent='Завантажуємо…';$('.journal-table-wrap').setAttribute('aria-busy','true');
    const query=new URLSearchParams({q:$('[data-journal-search]').value,status:$('[data-journal-status]').value,date_from:$('[data-journal-from]').value,date_to:$('[data-journal-to]').value,offset:String(offset),limit:'30'});
    try{
      const result=await(await request('/api/outgoing?'+query,{signal:controller.signal})).json();
      if(requestRevision!==revision)return;total=result.total;
      $('[data-journal-rows]').innerHTML=result.entries.map(entry=>`<tr><td data-label="Номер і дата"><strong>${entry.outgoing_number?'№ '+esc(entry.outgoing_number):'Без номера'}</strong><small>${entry.outgoing_date?date(entry.outgoing_date):'Створено '+date(entry.day)}</small></td><td data-label="Адресат" class="journal-recipient">${esc(entry.title)}</td><td data-label="Протоколи"><span>${esc(entry.protocols[0]||'—')}</span>${entry.protocols.length>1?`<small>ще ${entry.protocols.length-1}</small>`:''}</td><td data-label="Статус"><span class="journal-state ${entry.status}">${statusLabels[entry.status]}</span></td><td><button type="button" class="text-button" data-open-letter="${entry.id}">Відкрити</button></td></tr>`).join('');
      $('[data-journal-empty]').hidden=!!total;
      $('[data-journal-empty] h2').textContent=query.get('q')||query.get('status')||query.get('date_from')||query.get('date_to')?'Листів за цими умовами немає':'Листів поки немає';
      $('.journal-table').hidden=!total;
      $('[data-journal-total]').textContent=total?`${offset+1}–${Math.min(offset+30,total)} з ${total}`:'Листів: 0';
      $('[data-journal-prev]').disabled=offset===0;$('[data-journal-next]').disabled=offset+30>=total;
    }catch(exc){if(exc.name!=='AbortError'&&requestRevision===revision){error(exc.message);$('[data-journal-total]').textContent='';}}
    finally{if(requestRevision===revision)$('.journal-table-wrap').removeAttribute('aria-busy');}
  }
  function pdfAvailability(){
    if(!current)return;
    d('[data-journal-pdf]').disabled=!current.has_pdf&&!window.OZOffice?.status?.available;
    d('[data-journal-pdf]').title=window.OZOffice?.status?.reason||'';
  }
  async function open(id){
    const version=++detailRevision;
    try{
      const entry=await(await request('/api/outgoing/'+id)).json();
      if(version!==detailRevision)return;current=entry;
      d('#journal-letter-title').textContent=current.title;
      d('[data-journal-registration]').textContent=(current.outgoing_number?'№ '+current.outgoing_number:'Вихідний номер не присвоєно')+(current.outgoing_date?' · '+date(current.outgoing_date):'');
      d('[data-journal-protocol-count]').textContent='Протоколів: '+current.protocols.length;
      d('[data-journal-protocols]').innerHTML=current.protocols.map(number=>'<li>'+esc(number)+'</li>').join('');
      d('[data-journal-entry-status]').value=current.status;d('[data-journal-notes]').value=current.notes;
      d('[data-journal-word]').href='/api/outgoing/'+current.id+'/docx';
      pdfAvailability();message('');if(!dialog.open)dialog.showModal();
    }catch(exc){error(exc.message);}
  }
  section.addEventListener('click',event=>{
    const row=event.target.closest('[data-open-letter]');if(row)open(row.dataset.openLetter);
    if(event.target.closest('[data-journal-editor]'))document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));
  });
  $('[data-journal-search]').oninput=()=>{clearTimeout(timer);offset=0;timer=setTimeout(load,250);};
  for(const selector of ['[data-journal-status]','[data-journal-from]','[data-journal-to]'])$(selector).onchange=()=>{offset=0;load();};
  $('[data-journal-reset]').onclick=()=>{for(const selector of ['[data-journal-search]','[data-journal-status]','[data-journal-from]','[data-journal-to]'])$(selector).value='';offset=0;load();};
  $('[data-journal-refresh]').onclick=()=>{window.OZOffice?.refresh();load();};
  $('[data-journal-prev]').onclick=()=>{offset=Math.max(0,offset-30);load();};
  $('[data-journal-next]').onclick=()=>{offset+=30;load();};
  dialog.querySelectorAll('[data-journal-close]').forEach(button=>button.onclick=()=>dialog.close());
  d('[data-journal-form]').onsubmit=async event=>{
    event.preventDefault();if(!current||updating)return;
    const entry=current;updating=true;d('[data-journal-save]').disabled=true;d('[data-journal-entry-status]').disabled=true;d('[data-journal-notes]').disabled=true;
    try{
      const values={status:d('[data-journal-entry-status]').value,notes:d('[data-journal-notes]').value,expected_updated_at:entry.updated_at};
      const updated=await(await request('/api/outgoing/'+entry.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)})).json();
      if(current.id===entry.id){current={...current,...updated};message('Статус збережено.');}load();
    }catch(exc){if(current.id===entry.id)message(exc.message,true);}finally{updating=false;d('[data-journal-save]').disabled=false;d('[data-journal-entry-status]').disabled=false;d('[data-journal-notes]').disabled=false;}
  };
  d('[data-journal-pdf]').onclick=async event=>{
    if(!current)return;const entry=current;event.target.disabled=true;message('Створюємо PDF…');
    try{const response=await request('/api/outgoing/'+entry.id+'/pdf');window.OZOffice.download(await response.blob(),entry.filename.replace(/\.docx$/i,'.pdf'));if(current.id===entry.id){current.has_pdf=true;message('PDF готовий.');}}
    catch(exc){message(exc.message,true);}finally{pdfAvailability();}
  };
  d('[data-journal-copy]').onclick=async event=>{
    if(!current)return;event.target.disabled=true;
    try{
      const payload={...current.payload,confirmed:false,generation_id:crypto.randomUUID(),registrations:{}};
      const form=new FormData();form.append('payload',JSON.stringify(payload));
      const result=await(await request('/api/cover-letters/preview',{method:'POST',body:form})).json();
      dialog.close();
      if(!await window.OZCoverLetters.restore({...payload,letters:result.letters,selectedLetterId:current.identity}))dialog.showModal();
    }catch(exc){if(!dialog.open)dialog.showModal();message(exc.message,true);}finally{event.target.disabled=false;}
  };
  document.addEventListener('protokol:view',event=>{if(event.detail.name==='outgoing'){offset=0;load();window.OZOffice?.refresh();}else{detailRevision++;if(dialog.open)dialog.close();}});
  document.addEventListener('protokol:journal-changed',()=>{if(!section.hidden)load();});
  document.addEventListener('protokol:office-status',pdfAvailability);
})();
