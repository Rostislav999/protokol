'use strict';
(() => {
  const defaults=Object.freeze({text_size:'normal',theme:'light',density:'comfortable',accent:'sage',editor_mode:'auto'});
  const system=matchMedia('(prefers-color-scheme: dark)'), narrow=matchMedia('(max-width:1100px)');
  let saved={...defaults}, current={...defaults}, opening=null, openingMode=null, committed=false, busy=false, loadError='';
  const tools=document.createElement('div');tools.className='appearance-tools';
  tools.innerHTML=`<label class="type-control"><span aria-hidden="true">Aa</span><select aria-label="Розмір тексту" data-text-size disabled><option value="normal">Звичайний текст</option><option value="large">Збільшений текст</option><option value="extra_large">Великий текст</option></select></label><button type="button" class="button secondary appearance-open" data-appearance-open disabled><svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M9 4v6M15 14v6"/></svg>Вигляд</button><small class="appearance-feedback" role="status" hidden></small>`;
  document.querySelector('.topbar').append(tools);
  const quick=tools.querySelector('[data-text-size]'),openButton=tools.querySelector('[data-appearance-open]'),feedback=tools.querySelector('[role=status]');
  const dialog=document.createElement('dialog');dialog.id='appearance-settings';dialog.className='modal appearance-dialog';dialog.setAttribute('aria-labelledby','appearance-title');
  dialog.innerHTML=`<form novalidate>
    <div class="modal-heading"><div><h2 id="appearance-title">Вигляд програми</h2><p>Налаштуйте зручний для себе інтерфейс.</p></div><button type="button" class="icon-button" aria-label="Закрити налаштування вигляду" data-appearance-cancel>✕</button></div>
    <div class="modal-body">
      <p class="appearance-intro">Зміни видно одразу. Збережений вигляд застосовується після кожного запуску для вашого профілю Windows.</p>
      <div class="appearance-grid">
        <label class="field"><span>Тема</span><select name="theme"><option value="light">Світла</option><option value="dark">Темна</option><option value="system">Як у системі</option></select></label>
        <label class="field"><span>Розмір тексту</span><select name="text_size"><option value="normal">Звичайний · 16</option><option value="large">Збільшений · 18</option><option value="extra_large">Великий · 20</option></select></label>
        <label class="field"><span>Відстань між елементами</span><select name="density"><option value="comfortable">Просторо</option><option value="compact">Компактно</option></select></label>
        <label class="field"><span>Редактор листів при відкритті</span><select name="editor_mode"><option value="auto">Автоматично за шириною вікна</option><option value="edit">Текст</option><option value="preview">Сторінка</option><option value="split">Текст і сторінка поруч</option></select><small>У вузькому вікні режим «Поруч» відкриває текст.</small></label>
      </div>
      <fieldset class="appearance-accents"><legend>Колір акценту</legend><div>${[['sage','Зелений'],['blue','Синій'],['violet','Фіолетовий'],['slate','Сірий']].map(([value,label])=>`<label class="accent-choice"><input type="radio" name="accent" value="${value}"><span class="accent-dot accent-${value}" aria-hidden="true"></span><span>${label}</span></label>`).join('')}</div></fieldset>
      <div class="appearance-sample" aria-label="Приклад оформлення"><span class="appearance-sample-mark" aria-hidden="true">✓</span><div><strong>Зручно читати й працювати</strong><p>Ваші документи зберігають свій шрифт, поля та колір штампа.</p></div></div>
      <p class="error-text" data-appearance-error role="alert" hidden></p>
    </div>
    <div class="modal-footer"><button type="button" class="text-button appearance-reset" data-appearance-reset>Стандартний вигляд</button><button type="button" class="button secondary" data-appearance-cancel>Скасувати</button><button type="submit" class="button primary" data-appearance-save>Зберегти</button></div>
  </form>`;
  document.body.append(dialog);
  const form=dialog.querySelector('form'),error=dialog.querySelector('[data-appearance-error]');
  function editorMode(value=current.editor_mode) {return value==='auto'?(narrow.matches?'edit':'split'):value==='split'&&narrow.matches?'edit':value;}
  function apply(values,detail={}) {
    current={...values};const root=document.documentElement;
    root.dataset.theme=current.theme==='system'?(system.matches?'dark':'light'):current.theme;
    root.dataset.accent=current.accent;root.dataset.density=current.density;root.dataset.uiTextSize=current.text_size;quick.value=current.text_size;
    document.dispatchEvent(new CustomEvent('protokol:appearance-changed',{detail}));
  }
  function fill(values) {for(const [name,value] of Object.entries(values)){if(name==='accent')form.querySelector(`[name=accent][value="${value}"]`).checked=true;else form.elements.namedItem(name).value=value;}}
  function read() {return Object.fromEntries(Object.keys(defaults).map(name=>[name,name==='accent'?form.querySelector('[name=accent]:checked').value:form.elements.namedItem(name).value]));}
  function showError(message) {error.textContent=message;error.hidden=!message;}
  function lock(value) {busy=value;for(const field of form.querySelectorAll('button,select,input'))field.disabled=value;}
  async function save(changes) {
    const response=await fetch('/api/preferences',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)});
    if(!response.ok)throw new Error('Не вдалося зберегти вигляд. Спробуйте ще раз.');
    return {...defaults,...await response.json()};
  }
  function rollback() {apply(saved,{reason:'cancel',...(openingMode?{editorMode:openingMode}:{})});}
  function close() {if(busy)return;rollback();committed=true;dialog.close();}
  openButton.onclick=()=>{
    opening={...saved};openingMode=document.querySelector('[data-editor-layout]')?.dataset.mode||null;committed=false;
    fill(saved);showError(loadError);feedback.hidden=true;dialog.showModal();
  };
  form.onchange=event=>{if(busy)return;const values=read();apply(values,{reason:'preview',...(event.target.name==='editor_mode'?{editorMode:editorMode(values.editor_mode)}:{})});};
  form.querySelector('[data-appearance-reset]').onclick=()=>{fill(defaults);apply(defaults,{reason:'preview',editorMode:editorMode(defaults.editor_mode)});showError('');};
  form.querySelectorAll('[data-appearance-cancel]').forEach(button=>button.onclick=close);
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('close',()=>{if(!dialog.open&&!committed)rollback();});
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;lock(true);showError('');
    try{
      const values=read(),changes=Object.fromEntries(Object.entries(values).filter(([key,value])=>value!==opening[key]));
      saved=await save(changes);loadError='';apply(saved,{reason:'save'});committed=true;dialog.close();
    }catch(exc){showError(exc.message);}finally{lock(false);}
  };
  quick.onchange=async()=>{
    const value=quick.value;quick.disabled=true;openButton.disabled=true;feedback.hidden=true;apply({...saved,text_size:value},{reason:'preview'});
    try{saved=await save({text_size:value});apply(saved,{reason:'save'});loadError='';}
    catch(exc){apply(saved,{reason:'cancel'});feedback.textContent=exc.message;feedback.hidden=false;}
    finally{quick.disabled=false;openButton.disabled=false;}
  };
  system.addEventListener('change',()=>{if(current.theme==='system')apply(current,{reason:'system'});});
  window.OZAppearance={editorMode,get values(){return {...current};}};
  window.OZAppearance.ready=(async()=>{
    try{
      const response=await fetch('/api/preferences');if(!response.ok)throw new Error('Збережений вигляд недоступний. Перевірте резервну копію налаштувань.');
      saved={...defaults,...await response.json()};apply(saved,{reason:'load',editorMode:editorMode(saved.editor_mode)});
    }catch(exc){loadError=exc.message;apply(saved,{reason:'load'});}
    finally{quick.disabled=false;openButton.disabled=false;}
  })();
})();
