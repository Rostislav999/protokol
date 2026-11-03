'use strict';
(() => {
  const panel=document.createElement('section');panel.className='task-progress';panel.hidden=true;panel.setAttribute('aria-label','Обробка документів');
  panel.innerHTML='<div class="task-heading"><strong>Обробка документів</strong><button type="button" class="icon-button" data-task-close aria-label="Закрити повідомлення" hidden>×</button></div><p data-task-label role="status" aria-live="polite"></p><small data-task-file></small><progress max="100" value="0" aria-label="Прогрес обробки"></progress><div class="task-actions"><button type="button" class="button secondary" data-task-cancel>Скасувати обробку</button><button type="button" class="text-button" data-task-report hidden>Зберегти звіт про помилку</button></div>';
  document.body.append(panel);const $=selector=>panel.querySelector(selector);
  let busy=false,jobId='',cancelRequested=false,hideTimer=null;
  async function request(url,options){const response=await fetch(url,options);const value=await response.json().catch(()=>({}));if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'Не вдалося зв’язатися з локальним сервером.');return value;}
  function abort(){const error=new Error('Обробку скасовано.');error.name='AbortError';throw error;}
  function update(value){
    $('[data-task-label]').textContent=value.phase||'Опрацьовуємо документи…';
    $('[data-task-file]').textContent=[value.filename,value.files_total?`Файл ${value.file_index} з ${value.files_total}`:'',value.pages?`Сторінка ${value.page} з ${value.pages}`:''].filter(Boolean).join(' · ');
    $('progress').value=value.percent||0;$('[data-task-cancel]').disabled=value.status==='cancelling';
  }
  $('[data-task-cancel]').onclick=async()=>{
    cancelRequested=true;$('[data-task-cancel]').disabled=true;$('[data-task-label]').textContent='Скасовуємо обробку…';
    if(jobId)try{update(await request(`/api/tasks/${jobId}/cancel`,{method:'POST'}));}catch(error){$('[data-task-label]').textContent=error.message;$('[data-task-cancel]').disabled=false;}
  };
  $('[data-task-close]').onclick=()=>panel.hidden=true;
  $('[data-task-report]').onclick=()=>window.OZDiagnostics?.open();
  window.OZTasks={get busy(){return busy;},async run(operation,files,options={}){
    if(busy)throw new Error('Дочекайтеся завершення або скасуйте поточне завдання.');
    if(files.some(file=>!file.size||file.size>25*1024*1024)||files.reduce((sum,file)=>sum+file.size,0)>100*1024*1024)throw new Error('До 25 МБ на файл і 100 МБ на комплект.');
    busy=true;jobId='';cancelRequested=false;clearTimeout(hideTimer);panel.hidden=false;panel.classList.remove('failed');
    $('[data-task-close]').hidden=true;$('[data-task-report]').hidden=true;$('[data-task-cancel]').hidden=false;$('[data-task-cancel]').disabled=false;
    update({phase:'Зберігаємо вихідні файли…'});
    try{
      await window.OZWorkspace.ready;
      const section=options.section||operation,context={scope:window.OZWorkspace.scope,section,group:crypto.randomUUID().replaceAll('-','')};
      for(let index=0;index<files.length;index++){
        if(cancelRequested)abort();
        update({phase:'Зберігаємо вихідні файли…',filename:files[index].name,file_index:index+1,files_total:files.length});
        await window.OZWorkspace.retainFiles([files[index]],section,'source',context);
      }
      if(cancelRequested)abort();
      const form=new FormData();files.forEach(file=>form.append('files',file));form.append('exact_pages',String(options.exact_pages!==false));
      let state=await request('/api/tasks/'+operation,{method:'POST',body:form});jobId=state.id;
      let failures=0;
      while(true){
        update(state);
        if(state.status==='completed'){
          if(cancelRequested)abort();
          const result=await request(`/api/tasks/${jobId}/result`);
          if(cancelRequested)abort();$('[data-task-cancel]').disabled=true;
          await request('/api/workbench/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:context.scope,section,group:context.group,append:operation==='excel'})});
          result.sourceGroup=context.group;hideTimer=setTimeout(()=>panel.hidden=true,900);return result;
        }
        if(state.status==='cancelled')abort();
        if(state.status==='failed')throw new Error(state.error||'Не вдалося опрацювати файли.');
        await new Promise(resolve=>setTimeout(resolve,350));
        try{state=await request('/api/tasks/'+jobId);failures=0;}
        catch(error){if(++failures>=5)throw error;$('[data-task-label]').textContent='Відновлюємо зв’язок із локальним сервером…';}
      }
    }catch(error){
      panel.classList.toggle('failed',error.name!=='AbortError');$('[data-task-label]').textContent=error.message;
      $('[data-task-close]').hidden=false;$('[data-task-report]').hidden=error.name==='AbortError';
      if(error.name==='AbortError')hideTimer=setTimeout(()=>panel.hidden=true,1500);
      throw error;
    }finally{busy=false;jobId='';$('[data-task-cancel]').hidden=true;}
  }};
})();
