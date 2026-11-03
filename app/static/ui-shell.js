'use strict';
(() => {
  const dock=document.createElement('section');dock.className='action-dock';dock.setAttribute('aria-label','Збереження та створення документів');
  dock.innerHTML=`<div class="dock-status"><span class="dock-status-dot"></span><div><strong data-dock-status role="status">Автозбереження</strong><small data-dock-help></small><small data-dock-error role="alert" hidden></small></div></div><div class="dock-actions"><details class="dock-more"><summary class="button quiet" aria-label="Додаткові дії">Ще</summary><div class="dock-menu"><button type="button" data-dock-save>Зберегти чернетку зараз</button><button type="button" data-dock-last-word disabled>Завантажити останній Word</button><button type="button" data-dock-last-pdf disabled>Завантажити останній PDF</button><button type="button" data-dock-new-package>Зберегти в новий пакет</button><button type="button" data-dock-check-package>Перевірити комплект пакета</button><button type="button" data-dock-retry-archive hidden>Повторити збереження файлів у пакеті</button><button type="button" data-dock-report>Допомога та звіт</button><button type="button" data-office-refresh>Перевірити доступність PDF</button></div></details><button type="button" class="button secondary" data-dock-review>Перевірити</button><label class="dock-format"><span>Формат</span><select data-export-format aria-label="Формат завантаження"><option value="word">Word · ZIP</option><option value="pdf" disabled>PDF · ZIP</option></select></label><button type="button" class="button primary" data-dock-primary disabled>Створити документи</button></div>`;
  document.body.append(dock);
  const $=selector=>dock.querySelector(selector);
  const sources={acts:'#generate',assignments:'#assignment-generate',excel:'#excel-generate',cover_letters:'#cover_letters-view [data-generate]'};
  let active='acts',source=null,queued=false;
  function update() {
    dock.hidden=!sources[active];
    source=document.querySelector(sources[active]||':not(*)');
    const primary=$('[data-dock-primary]');
    if(source){
      if(!source.classList.contains('dock-source-action'))source.classList.add('dock-source-action');
      const caption=window.OZOffice?.busy?'Створюємо PDF…':source.innerHTML;
      if(primary.innerHTML!==caption)primary.innerHTML=caption;
      primary.disabled=source.matches(':disabled')||!!source.closest('[hidden]')||!!window.OZOffice?.busy;
    }else primary.disabled=true;
    const original=document.querySelector('#workspace-save-status'),text=original?.textContent||'';
    const error=original?.classList.contains('workspace-error');
    const waiting=window.OZWorkspace?.pending && !text.includes('попередня чернетка');
    $('[data-dock-status]').textContent=error?text:waiting?'Зберігаємо…':text.includes('Чернетку збережено')?'Збережено':text.includes('Є збережена')||text.includes('попередня чернетка')?'Є збережена чернетка':'Автозбереження';
    $('[data-dock-status]').title=text;
    $('[data-dock-help]').textContent='';
    dock.classList.toggle('save-error',!!error);dock.classList.toggle('saving',!!waiting);
    const office=window.OZOffice?.status,format=$('[data-export-format]');
    format.querySelector('[value="pdf"]').disabled=!office?.available;
    format.disabled=!!window.OZOffice?.busy;
    format.title=office?.available?'PDF створюється локально у '+office.label:(office?.reason||'Перевіряємо доступність PDF');
    format.value=window.OZOffice?.format||'word';
    $('.dock-format').hidden=active==='excel';
    const archiveError=window.OZOffice?.archiveError(active)||'',message=$('[data-dock-error]');
    if(archiveError){message.textContent=archiveError;message.hidden=false;message.dataset.kind='archive';}
    else if(message.dataset.kind==='archive'){message.hidden=true;message.textContent='';}
    $('[data-dock-retry-archive]').hidden=!archiveError;
    $('[data-dock-check-package]').disabled=window.OZWorkspace?.scope==='workspace'||window.OZWorkspace?.busy;
    $('[data-dock-new-package]').disabled=!!window.OZWorkspace?.busy;
    $('[data-dock-last-word]').textContent=active==='excel'?'Завантажити останній Excel':'Завантажити останній Word';
    $('[data-dock-last-pdf]').hidden=active==='excel';
    const has=!!window.OZOffice?.has(active);
    $('[data-dock-last-word]').disabled=!has;
    $('[data-dock-last-pdf]').disabled=!has||!office?.available||!!window.OZOffice?.busy;
  }
  function schedule(){if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;update();});}}
  $('[data-dock-save]').onclick=()=>{document.querySelector('#workspace-save').click();$('.dock-more').open=false;};
  $('[data-dock-review]').onclick=()=>window.OZWorkspace?.review(active);
  $('[data-dock-primary]').onclick=()=>{if(source && !source.matches(':disabled')&&!window.OZOffice?.busy)source.click();};
  $('[data-export-format]').onchange=event=>{window.OZOffice.setFormat(event.target.value);schedule();};
  for(const [selector,format] of [['[data-dock-last-word]','word'],['[data-dock-last-pdf]','pdf']]){
    $(selector).onclick=async()=>{
      $('.dock-more').open=false;$('[data-dock-error]').hidden=true;
      try{await window.OZOffice.repeat(active,format);}catch(error){$('[data-dock-error]').textContent=error.message;$('[data-dock-error]').hidden=false;}
    };
  }
  $('[data-dock-new-package]').onclick=()=>{try{window.OZPackages.create();$('.dock-more').open=false;}catch(error){$('[data-dock-error]').textContent=error.message;$('[data-dock-error]').hidden=false;}};
  $('[data-dock-check-package]').onclick=()=>{window.OZPackages.check().catch(error=>{$('[data-dock-error]').textContent=error.message;$('[data-dock-error]').hidden=false;});$('.dock-more').open=false;};
  $('[data-dock-report]').onclick=()=>{window.OZDiagnostics.open();$('.dock-more').open=false;};
  $('[data-dock-retry-archive]').onclick=async()=>{await window.OZOffice.retryArchive(active);schedule();};
  document.addEventListener('protokol:package-context',schedule);
  $('[data-office-refresh]').onclick=()=>{window.OZOffice.refresh();$('.dock-more').open=false;};
  document.addEventListener('protokol:view',event=>{active=event.detail.name;$('[data-dock-error]').hidden=true;schedule();});
  for(const event of ['protokol:office-status','protokol:export-state','protokol:export-ready'])document.addEventListener(event,schedule);
  document.addEventListener('input',schedule);document.addEventListener('change',schedule);
  new MutationObserver(schedule).observe(document.querySelector('.main-content'),{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled','hidden','class']});
  update();
})();
