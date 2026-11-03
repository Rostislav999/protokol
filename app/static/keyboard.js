'use strict';
(() => {
  const views=['packages','acts','assignments','cover_letters','outgoing','excel','library'];
  const editing=target=>target?.matches('input,textarea,select,[contenteditable=true]');
  document.addEventListener('keydown',event=>{
    if(event.isComposing||event.defaultPrevented)return;
    const dialog=document.querySelector('dialog[open]'),modifier=event.ctrlKey||event.metaKey;
    if(event.key==='F1'){event.preventDefault();window.OZDiagnostics?.open();return;}
    if(modifier&&event.key.toLowerCase()==='s'){
      event.preventDefault();
      if(dialog){const form=dialog.querySelector('form');if(form&&!form.querySelector('[type=submit]:disabled'))form.requestSubmit();else dialog.querySelector('[data-history-save]:not(:disabled)')?.click();}
      else document.querySelector('#workspace-save')?.click();
      return;
    }
    if(modifier&&event.key==='Enter'){
      event.preventDefault();const action=dialog?.querySelector('.modal-footer .button.primary:not(:disabled)')||(!dialog?document.querySelector('.action-dock:not([hidden]) [data-dock-primary]:not(:disabled)'):null);action?.click();return;
    }
    if(event.altKey&&!modifier&&/^[1-7]$/.test(event.key)&&!dialog&&!window.OZWorkspace.switching){event.preventDefault();document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:views[Number(event.key)-1]}}));return;}
    if(event.key==='/'&&!modifier&&!event.altKey&&!editing(event.target)){
      const root=dialog||document.getElementById((document.body.dataset.activeView||'acts')+'-view');
      const field=root?.querySelector('input[type=search]');if(field){event.preventDefault();field.focus();}
    }
  });
})();
