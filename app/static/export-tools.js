'use strict';
(() => {
  let capability=null,format='word',converting=false;
  const latest=new Map();
  const announce=(name,detail={})=>document.dispatchEvent(new CustomEvent(name,{detail}));
  async function refresh() {
    try {
      const response=await fetch('/api/word/status');
      if(!response.ok)throw new Error('Не вдалося перевірити локальний редактор.');
      capability=await response.json();
    }catch(error){capability={available:false,reason:error.message,engine:'',label:''};}
    if(!capability.available)format='word';
    announce('protokol:office-status',capability);return capability;
  }
  function download(blob,filename) {
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),60000);
  }
  async function pdf(record) {
    if(record.pdf)return record.pdf;
    const status=capability||await ready;
    if(!status.available)throw new Error(status.reason);
    const form=new FormData();form.append('file',new File([record.blob],record.filename,{type:record.blob.type}));
    const response=await fetch('/api/word/pdf',{method:'POST',body:form});
    if(!response.ok){
      const body=await response.json().catch(()=>({}));
      throw new Error(typeof body.detail==='string'?body.detail:'Не вдалося створити PDF.');
    }
    record.pdf=await response.blob();return record.pdf;
  }
  async function deliver(blob,filename,section) {
    const record={blob,filename};latest.set(section,record);announce('protokol:export-ready',{section});
    return repeat(section,format);
  }
  async function repeat(section,chosen='word') {
    const record=latest.get(section);if(!record)throw new Error('Спочатку створіть документи.');
    if(chosen==='word'){download(record.blob,record.filename);return;}
    if(converting)throw new Error('Дочекайтеся створення PDF.');
    converting=true;announce('protokol:export-state',{busy:true});
    try {
      const blob=await pdf(record);
      const name=record.filename.replace(/\.(zip|docx)$/i,match=>match.toLowerCase()==='.zip'?'-PDF.zip':'.pdf');
      download(blob,name);
    }catch(error){
      throw new Error('Word-файли вже створено. '+error.message+' Повторне завантаження доступне в меню «Ще».');
    }finally{converting=false;announce('protokol:export-state',{busy:false});}
  }
  function invalidate(section){latest.delete(section);announce('protokol:export-ready',{section});}
  document.addEventListener('protokol:assignments-invalidated',()=>invalidate('assignments'));
  document.addEventListener('input',event=>{
    const view=event.target.closest('#acts-view,#assignments-view');
    if(view)invalidate(view.id==='acts-view'?'acts':'assignments');
  });
  const ready=refresh();
  window.OZOffice={ready,refresh,download,deliver,repeat,invalidate,has:section=>latest.has(section),
    get status(){return capability;},get busy(){return converting;},get format(){return format;},
    setFormat(value){format=value==='pdf'&&capability?.available?'pdf':'word';}};
})();
