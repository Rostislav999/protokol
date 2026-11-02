'use strict';
(() => {
  const dock=document.createElement('section');dock.className='action-dock';dock.setAttribute('aria-label','Збереження та створення документів');
  dock.innerHTML='<div class="dock-status"><span class="dock-status-dot"></span><div><strong data-dock-status role="status">Автозбереження увімкнено</strong><small data-dock-help></small></div></div><div class="dock-actions"><button type="button" class="button secondary" data-dock-save>Зберегти</button><button type="button" class="button secondary" data-dock-review>Перевірити</button><button type="button" class="button primary" data-dock-primary disabled>Створити документ</button></div>';
  document.body.append(dock);
  const $=selector=>dock.querySelector(selector);
  const sources={acts:'#generate',assignments:'#assignment-generate',excel:'#excel-generate',cover_letters:'#cover_letters-view [data-generate]'};
  const hints={acts:'#generation-help',assignments:'#assignment-generation-help',excel:'#excel-generation-help'};
  let active='acts',source=null,queued=false;
  function update() {
    dock.hidden=!sources[active];
    source=document.querySelector(sources[active]||':not(*)');
    const primary=$('[data-dock-primary]');
    if(source){
      if(!source.classList.contains('dock-source-action'))source.classList.add('dock-source-action');
      if(primary.innerHTML!==source.innerHTML)primary.innerHTML=source.innerHTML;
      primary.disabled=source.matches(':disabled')||!!source.closest('[hidden]');
    }else primary.disabled=true;
    const original=document.querySelector('#workspace-save-status'),text=original?.textContent||'';
    const error=original?.classList.contains('workspace-error');
    const waiting=window.OZWorkspace?.pending && !text.includes('попередня чернетка');
    const message=error?text:waiting?'Зберігаємо…':text.includes('Чернетку збережено')?'Збережено':text.includes('Є збережена')||text.includes('попередня чернетка')?'Є збережена чернетка':'Автозбереження увімкнено';
    $('[data-dock-status]').textContent=message;$('[data-dock-status]').title=text;
    dock.classList.toggle('save-error',!!error);dock.classList.toggle('saving',!!waiting);
    let help=document.querySelector(hints[active]||':not(*)')?.textContent||'';
    if(active==='cover_letters')help=document.querySelector('#cover_letters-view [data-confirm]')?.checked?'Листи перевірено. Можна завантажувати.':'Перевірте листи й підтвердьте дані перед завантаженням.';
    $('[data-dock-help]').textContent=help;
    $('[data-dock-help]').title=help;
  }
  function schedule(){if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;update();});}}
  $('[data-dock-save]').onclick=()=>document.querySelector('#workspace-save').click();
  $('[data-dock-review]').onclick=()=>window.OZWorkspace?.review(active);
  $('[data-dock-primary]').onclick=()=>{if(source && !source.matches(':disabled'))source.click();};
  document.addEventListener('protokol:view',event=>{active=event.detail.name;schedule();});
  document.addEventListener('input',schedule);document.addEventListener('change',schedule);
  new MutationObserver(schedule).observe(document.querySelector('.main-content'),{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled','hidden','class']});
  update();
})();
