'use strict';
(() => {
  const events=[];
  function record(kind,value){let script='interface';try{const path=new URL(value.filename||'',location.href).pathname;if(path.startsWith('/static/'))script=path.split('/').pop();}catch{}events.push({kind,script,line:Number(value.lineno)||0});if(events.length>30)events.shift();}
  window.addEventListener('error',event=>record('js-error',event));
  window.addEventListener('unhandledrejection',()=>record('unhandled-promise',{}));
  const button=document.createElement('button');button.type='button';button.className='button quiet utility-help';button.title='Допомога та звіт про помилку';button.innerHTML='<svg class="icon"><use href="#i-info"/></svg><span class="utility-label">Допомога</span>';
  document.querySelector('.sidebar-utilities').insertBefore(button,document.querySelector('.app-version'));
  const dialog=document.createElement('dialog');dialog.className='modal diagnostics-dialog';dialog.id='diagnostics';dialog.setAttribute('aria-labelledby','diagnostics-title');
  dialog.innerHTML=`<form><div class="modal-heading"><h2 id="diagnostics-title">Допомога та звіт</h2><button type="button" class="icon-button" data-diagnostics-close aria-label="Закрити допомогу">×</button></div><div class="modal-body"><p>Збережіть технічний звіт, якщо програма працює неправильно. У ньому будуть версії компонентів, технічні події та ваш опис проблеми.</p><label class="field"><span>Що сталося і яку дію ви виконували?</span><textarea name="description" maxlength="3000" rows="4" placeholder="Наприклад, помилка під час читання комплекту протоколів"></textarea></label><p class="section-description">Тексти документів, їхні файли та довідники не додаються. Звіт зберігається на комп’ютері й нікуди автоматично не надсилається.</p><details><summary>Клавіатурні скорочення</summary><div class="keyboard-shortcuts"><p><span>Зберегти роботу / відкриту форму</span><kbd>Ctrl + S</kbd></p><p><span>Основна дія / підтвердження діалогу</span><kbd>Ctrl + Enter</kbd></p><p><span>Перехід між розділами</span><kbd>Alt + 1…7</kbd></p><p><span>Пошук у поточному розділі</span><kbd>/</kbd></p><p><span>Рух у списку шаблонів</span><kbd>↑ ↓</kbd></p><p><span>Відкрити допомогу</span><kbd>F1</kbd></p></div></details><p class="error-text" data-diagnostics-error role="alert" hidden></p></div><div class="modal-footer"><button type="button" class="button secondary" data-diagnostics-close>Закрити</button><button type="submit" class="button primary" data-diagnostics-download>Зберегти звіт</button></div></form>`;
  document.body.append(dialog);const form=dialog.querySelector('form');
  function open(){if(document.querySelector('dialog[open]'))return;dialog.querySelector('[data-diagnostics-error]').hidden=true;dialog.showModal();form.elements.description.focus();}
  button.onclick=open;dialog.querySelectorAll('[data-diagnostics-close]').forEach(close=>close.onclick=()=>dialog.close());
  form.onsubmit=async event=>{
    event.preventDefault();const submit=dialog.querySelector('[data-diagnostics-download]');submit.disabled=true;
    const values={description:form.elements.description.value,view:document.body.dataset.activeView||'',width:innerWidth,height:innerHeight,theme:document.documentElement.dataset.theme||'',events};
    try{
      const response=await fetch('/api/diagnostics/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
      if(!response.ok)throw new Error('Серверний звіт недоступний.');window.OZOffice.download(await response.blob(),'OZtaUSYA-diagnostics.zip');
    }catch(exc){
      window.OZOffice.download(new Blob([JSON.stringify({format:'oztausya-interface-diagnostics',created_at:new Date().toISOString(),...values},null,2)],{type:'application/json'}),'OZtaUSYA-interface-report.json');
      const error=dialog.querySelector('[data-diagnostics-error]');error.textContent='Підготовлено звіт інтерфейсу. '+exc.message;error.hidden=false;
    }finally{submit.disabled=false;}
  };
  window.OZDiagnostics={open};
})();
