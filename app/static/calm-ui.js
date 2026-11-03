'use strict';
(() => {
  const utilities=document.createElement('div');utilities.className='sidebar-utilities';
  const backups=document.querySelector('#workspace-backups');
  backups.className='button quiet';backups.innerHTML='<svg class="icon"><use href="#i-shield"/></svg><span class="utility-label">Резервні копії</span>';backups.title='Резервні копії';
  utilities.append(backups);
  const version=document.createElement('small');version.className='app-version';version.textContent='ОЗтаУСЯ · 1.4.0';utilities.append(version);
  document.querySelector('.sidebar').append(utilities);
  const control=document.createElement('label');control.className='type-control';
  control.innerHTML='<span aria-hidden="true">Aa</span><select aria-label="Розмір тексту" data-text-size><option value="normal">Звичайний текст</option><option value="large">Збільшений текст</option></select>';
  document.querySelector('.topbar').append(control);
  const select=control.querySelector('select');let saved='normal';
  function apply(value){document.documentElement.dataset.uiTextSize=value;select.value=value;}
  fetch('/api/preferences').then(response=>{if(!response.ok)throw new Error('Налаштування вигляду недоступні.');return response.json();}).then(data=>{saved=data.text_size;apply(saved);}).catch(error=>control.title=error.message);
  select.onchange=async()=>{
    const value=select.value;apply(value);select.disabled=true;
    try{
      const response=await fetch('/api/preferences',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text_size:value})});
      if(!response.ok)throw new Error('Не вдалося зберегти розмір тексту.');saved=value;control.title='';
    }catch(error){apply(saved);control.title=error.message;}finally{select.disabled=false;}
    document.dispatchEvent(new CustomEvent('protokol:appearance-changed'));
  };
  document.body.dataset.activeView='acts';
  document.addEventListener('protokol:view',event=>document.body.dataset.activeView=event.detail.name);
})();
