'use strict';
(() => {
  const utilities=document.createElement('div');utilities.className='sidebar-utilities';
  const backups=document.querySelector('#workspace-backups');
  backups.className='button quiet';backups.innerHTML='<svg class="icon"><use href="#i-shield"/></svg><span class="utility-label">Резервні копії</span>';backups.title='Резервні копії';
  utilities.append(backups);
  const version=document.createElement('small');version.className='app-version';version.textContent='ОЗтаУСЯ · 1.6.2';utilities.append(version);
  document.querySelector('.sidebar').append(utilities);
  document.body.dataset.activeView='acts';
  document.addEventListener('protokol:view',event=>document.body.dataset.activeView=event.detail.name);
})();
