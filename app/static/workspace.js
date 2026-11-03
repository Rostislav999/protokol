'use strict';

// Drafts live on the local server, not under a browser's changing localhost port.
(() => {
  const providers = new Map();
  const drafts = new Map();
  let activeSection = 'acts';
  let loaded = false;
  let loadError = '';
  let scope = 'workspace', packageInfo = null, switching = false;
  const sectionNames = ['acts','assignments','excel','cover_letters'];
  const draftBase = (value=scope) => value==='workspace'?'/api/workspace/drafts':`/api/packages/${encodeURIComponent(value)}/drafts`;
  const restoring = new Set();
  const $ = selector => document.querySelector(selector);
  const ui = document.createElement('section');
  ui.className = 'workspace-tools panel';
  ui.setAttribute('aria-label', 'Збереження та перевірка роботи');
  ui.innerHTML = `<div class="workspace-actions"><button type="button" class="button secondary" id="workspace-save">Зберегти чернетку</button><button type="button" class="button secondary" id="workspace-review">Перевірити перед експортом</button><button type="button" class="button secondary" id="workspace-backups">Резервні копії</button></div><p id="workspace-save-status" role="status" aria-live="polite">Завантажуємо збережену роботу…</p><div id="workspace-draft-list"></div>`;
  $('.main-content').prepend(ui);

  function state(section) {
    if (!drafts.has(section)) drafts.set(section, {revision: 0, entry: null, pending: false, dirty: false, generation: 0, timer: null, flight: null});
    return drafts.get(section);
  }
  function status(message, error = false) {
    const target = $('#workspace-save-status');
    target.textContent = message;
    target.classList.toggle('workspace-error', error);
  }
  function errorMessage(error) { return error?.message || 'Не вдалося зберегти дані.'; }
  function time(value) {
    if (!value) return '';
    return new Date(value).toLocaleString('uk-UA', {timeZone: 'Europe/Kyiv', dateStyle: 'short', timeStyle: 'short'});
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(typeof body.detail === 'string' ? body.detail : `Помилка збереження (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function button(label, action) {
    const result = document.createElement('button');
    result.type = 'button'; result.className = 'text-button'; result.textContent = label;
    result.addEventListener('click', action);
    return result;
  }
  function renderDrafts() {
    const list = $('#workspace-draft-list'); list.replaceChildren();
    for (const [section, item] of drafts) {
      if (!item.pending || !item.entry || !providers.has(section)) continue;
      const row = document.createElement('div'); row.className = 'workspace-draft notice soft';
      row.hidden=section!==activeSection;
      const label = document.createElement('span');
      label.textContent = `${providers.get(section).title}: є збережена чернетка від ${time(item.entry.updated_at)}.`;
      row.append(label, button('Відновити', () => restore(section)), button('Зберегти поточні замість неї', () => replaceSaved(section)));
      for (const action of row.querySelectorAll('button')) action.disabled = restoring.has(section);
      list.append(row);
    }
    if (loadError) list.append(button('Повторити завантаження чернеток', () => load()));
    ui.classList.toggle('has-visible-drafts',!!list.querySelector('.workspace-draft:not([hidden])'));
  }
  async function load() {
    try {
      const saved = await request(draftBase());
      for (const section of ['acts', 'assignments', 'excel', 'cover_letters']) {
        const current = state(section);
        current.revision = saved.revisions?.[section] ?? saved.sections?.[section]?.revision ?? 0;
        current.entry = saved.sections?.[section] || null;
        current.pending = Boolean(current.entry);
      }
      loaded = true; loadError = '';
      status('Чернетки зберігаються автоматично на цьому комп’ютері.');
      renderDrafts();
      for (const [section, item] of drafts) if (item.dirty && !item.pending) schedule(section);
    } catch (error) {
      loaded = false; loadError = errorMessage(error);
      status(`Автозбереження недоступне: ${loadError}`, true); renderDrafts();
    }
  }
  function changed(section) {
    if (restoring.has(section) || !providers.has(section)) return;
    const item = state(section); item.dirty = true; item.generation += 1;
    if (item.pending) { status('Є попередня чернетка. Відновіть її або збережіть поточні дані замість неї.'); return; }
    status('Є незбережені зміни…'); schedule(section);
  }
  function schedule(section) {
    const item = state(section); clearTimeout(item.timer);
    item.timer = setTimeout(() => persist(section).catch(error => status(errorMessage(error), true)), 700);
  }
  async function persist(section) {
    const item = state(section); clearTimeout(item.timer);
    if (!loaded) throw new Error(loadError || 'Дочекайтеся завантаження чернеток.');
    if (item.pending) throw new Error('Спочатку відновіть збережену чернетку або підтвердьте її заміну.');
    if (item.flight) { await item.flight; if (item.dirty) return persist(section); return; }
    if (!item.dirty) return;
    const provider = providers.get(section);
    const snapshot = provider?.snapshot();
    const generation = item.generation;
    const operation = async () => {
      try {
        if (snapshot == null && !item.entry) { item.dirty = false; return; }
        const data = snapshot == null
          ? await request(`${draftBase()}/${section}?expected_revision=${item.revision}`, {method: 'DELETE'})
          : await request(`${draftBase()}/${section}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({data: snapshot, expected_revision: item.revision})});
        item.revision = data.revision; item.entry = snapshot == null ? null : data;
        item.dirty = generation !== item.generation;
        document.dispatchEvent(new CustomEvent('protokol:workspace-saved',{detail:{scope,section}}));
        status(snapshot == null ? 'Порожню чернетку очищено.' : `Чернетку збережено: ${time(data.updated_at)}.`);
      } catch (error) {
        if (error.status === 409) {
          const latest = await request(draftBase());
          item.revision = latest.revisions?.[section] ?? latest.sections?.[section]?.revision ?? 0;
          item.entry = latest.sections?.[section] || null;
          item.pending = true;
          renderDrafts();
          if (!item.entry) {
            const row = document.createElement('div'); row.className = 'workspace-draft notice warning';
            row.append(document.createTextNode('Чернетку очищено в іншому вікні. Поточні зміни залишилися тут. '), button('Зберегти ці зміни як нову чернетку', () => replaceSaved(section)));
            $('#workspace-draft-list').append(row);
          }
          throw new Error('Чернетку змінили в іншому вікні. Ваші поточні зміни не перезаписано. Оберіть, яку версію зберегти.');
        }
        throw error;
      }
    };
    item.flight = operation();
    try { await item.flight; } finally { item.flight = null; }
    if (item.dirty) schedule(section);
  }
  async function restore(section) {
    const item = state(section); const provider = providers.get(section);
    if (!item.entry || !provider || restoring.has(section)) return;
    if (item.dirty && !confirm('Замінити поточні незбережені дані збереженою чернеткою?')) return;
    clearTimeout(item.timer); restoring.add(section); renderDrafts();
    const navigationSection = provider.navigationSection || section;
    const pane = document.getElementById(navigationSection + '-view');
    if (pane) { pane.inert = true; pane.setAttribute('aria-busy', 'true'); }
    try {
      await provider.restore(JSON.parse(JSON.stringify(item.entry.data)));
      await provider.attachFiles?.();
      item.pending = false; item.dirty = false;
      status('Чернетку відновлено. Перед експортом перевірте дані й підтвердьте їх повторно.');
      renderDrafts();
      document.dispatchEvent(new CustomEvent('protokol:navigate', {detail: {name: navigationSection}}));
    } catch (error) { status(`Не вдалося відновити чернетку: ${errorMessage(error)}`, true); }
    finally {
      if (pane) { pane.inert = false; pane.removeAttribute('aria-busy'); }
      restoring.delete(section); renderDrafts();
    }
  }
  async function replaceSaved(section) {
    const item = state(section);
    if (!confirm('Замінити попередню чернетку поточними даними цього розділу?')) return;
    item.pending = false; item.dirty = true; item.generation += 1;
    renderDrafts();
    try { await persist(section); } catch (error) { status(errorMessage(error), true); }
  }
  async function saveAll() {
    for (const [section, item] of drafts) {
      if (item.dirty) await persist(section);
      else if (item.flight) await item.flight;
    }
  }
  $('#workspace-save').addEventListener('click', async () => {
    try {
      if (state(activeSection).pending) return replaceSaved(activeSection);
      changed(activeSection); await saveAll();
    } catch (error) { status(errorMessage(error), true); }
  });
  document.addEventListener('protokol:view', event => { if (['acts', 'assignments', 'excel', 'cover_letters'].includes(event.detail?.name)) activeSection = event.detail.name; renderDrafts(); });
  document.addEventListener('protokol:workspace-section', event => {
    if (providers.has(event.detail?.name)) activeSection = event.detail.name;
  });
  window.addEventListener('beforeunload', event => {
    if ([...drafts.values()].some(item => item.dirty || item.flight)) { event.preventDefault(); event.returnValue = ''; }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveAll().catch(() => {}); });

  const reviewDialog = document.createElement('dialog'); reviewDialog.className = 'modal workspace-modal';
  reviewDialog.innerHTML = `<div class="modal-heading"><h2>Перевірка перед експортом</h2><button type="button" class="icon-button" aria-label="Закрити">×</button></div><div class="modal-body"><p id="workspace-review-summary"></p><div id="workspace-review-issues"></div><p class="workspace-help">Ця перевірка допомагає знайти незаповнені та некоректні поля. Зіставте розпізнані значення з оригіналом і перевірте результати випробувань окремо.</p></div><div class="modal-footer"><button type="button" id="workspace-review-close" class="button secondary">Закрити</button><button type="button" id="workspace-review-continue" class="button primary" hidden>Продовжити створення</button></div>`;
  document.body.append(reviewDialog);
  reviewDialog.querySelector('.icon-button').onclick = () => reviewDialog.close();
  $('#workspace-review-close').onclick = () => reviewDialog.close();
  let resumeExport = null;
  let reviewSection = null;
  function collectIssues(section) {
    const issues = [];
    for (const [name, provider] of providers) {
      if (section && section !== name) continue;
      if (!section && (provider.hasData ? !provider.hasData() : provider.snapshot?.() == null)) continue;
      try { for (const issue of provider.issues?.() || []) issues.push({section: name, ...issue}); }
      catch { issues.push({section: name, level: 'error', message: 'Не вдалося перевірити розділ. Оновіть дані та спробуйте знову.'}); }
    }
    return issues;
  }
  async function reveal(issue) {
    reviewDialog.close();
    const provider = providers.get(issue.section);
    document.dispatchEvent(new CustomEvent('protokol:navigate', {detail: {name: provider?.navigationSection || issue.section}}));
    if (provider?.reveal) await provider.reveal(issue);
    else if (issue.selector) {
      const node = document.querySelector(issue.selector);
      if (node) {
        for (let parent = node.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
        node.scrollIntoView({behavior: 'smooth', block: 'center'}); node.focus({preventScroll: true});
      }
    }
  }
  function review(section = null, resume = null) {
    reviewSection = section; resumeExport = resume;
    const issues = collectIssues(section);
    const errors = issues.filter(issue => issue.level === 'error').length;
    $('#workspace-review-summary').textContent = `${section ? providers.get(section)?.title || 'Поточний розділ' : 'Усі робочі розділи'}: ${errors} помилок, ${issues.length - errors} попереджень.`;
    const list = $('#workspace-review-issues'); list.replaceChildren();
    if (!issues.length) { const ok = document.createElement('p'); ok.className = 'notice success'; ok.textContent = 'Автоматичних зауважень немає.'; list.append(ok); }
    for (const issue of issues) {
      const row = document.createElement('div'); row.className = `workspace-issue ${issue.level === 'error' ? 'workspace-error' : ''}`;
      const label = document.createElement('span'); label.textContent = `${providers.get(issue.section)?.title || ''}: ${issue.message}`;
      row.append(label);
      if (issue.selector || providers.get(issue.section)?.reveal) row.append(button('Перейти до поля', () => reveal(issue)));
      list.append(row);
    }
    const next = $('#workspace-review-continue'); next.hidden = !resume; next.disabled = Boolean(errors);
    if (!reviewDialog.open) reviewDialog.showModal();
  }
  $('#workspace-review').onclick = () => review();
  $('#workspace-review-continue').onclick = async () => {
    if (collectIssues(reviewSection).some(issue => issue.level === 'error')) return review(reviewSection, resumeExport);
    const resume = resumeExport; reviewDialog.close();
    if (resume) resume();
  };
  const bypass = new WeakSet();
  document.addEventListener('click', event => {
    const target = event.target.closest('button');
    const section = {generate: 'acts', 'assignment-generate': 'assignments', 'excel-generate': 'excel'}[target?.id];
    if (!section || target.disabled || !providers.has(section)) return;
    if (bypass.has(target)) { bypass.delete(target); return; }
    event.preventDefault(); event.stopImmediatePropagation();
    review(section, () => { bypass.add(target); target.click(); });
  }, true);

  const backupDialog = document.createElement('dialog'); backupDialog.className = 'modal workspace-modal';
  backupDialog.innerHTML = `<div class="modal-heading"><h2>Резервні копії</h2><button type="button" class="icon-button" aria-label="Закрити">×</button></div><div class="modal-body"><p>Копія містить довідники, обидві бібліотеки шаблонів, нумерацію, видані завдання та збережені чернетки. Збережіть ZIP також на іншому носії.</p><button type="button" id="workspace-backup-export" class="button primary">Завантажити резервну копію</button><h3>Автоматичні копії</h3><div id="workspace-backup-history"></div><h3>Відновлення з ZIP</h3><label class="field"><span>Резервна копія програми</span><input type="file" id="workspace-backup-file" accept=".zip"></label><label class="confirmation"><input type="checkbox" id="workspace-backup-confirmed"><span>Замінити поточні дані даними з копії, включно з нумерацією та чернетками. Перед заміною програма збереже поточні дані в окрему копію.</span></label><button type="button" id="workspace-backup-restore" class="button secondary">Відновити дані</button><p id="workspace-backup-status" role="status" aria-live="polite"></p></div>`;
  document.body.append(backupDialog);
  backupDialog.querySelector('.icon-button').onclick = () => backupDialog.close();
  const backupStatus = (message, error = false) => { const node = $('#workspace-backup-status'); node.textContent = message; node.classList.toggle('workspace-error', error); };
  async function backupHistory() {
    const history = await request('/api/backups/status');
    const list = $('#workspace-backup-history'); list.replaceChildren();
    const entries = history.backups || history.recent || [];
    if (!entries.length) { const note = document.createElement('p'); note.textContent = 'Автоматична копія з’явиться перед першим запуском нової версії зі збереженими даними або перед відновленням.'; list.append(note); }
    for (const entry of entries) {
      const row = document.createElement('p'); const link = document.createElement('a');
      link.href = '/api/backups/files/' + encodeURIComponent(entry.name);
      link.textContent = `${time(entry.created_at)} — ${String(entry.reason).includes('restore') ? 'перед відновленням' : 'перед оновленням'}`;
      row.append(link); list.append(row);
    }
  }
  $('#workspace-backups').onclick = async () => {
    backupStatus(''); $('#workspace-backup-confirmed').checked = false; backupDialog.showModal();
    try { await backupHistory(); } catch (error) { backupStatus(errorMessage(error), true); }
  };
  async function downloadBackup() {
    await saveAll();
    const response = await fetch('/api/backups/export');
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || 'Не вдалося створити резервну копію.'); }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = `OZtaUSYa-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.zip`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  $('#workspace-backup-export').onclick = async event => {
    event.target.disabled = true; backupStatus('Створюємо резервну копію…');
    try { await downloadBackup(); backupStatus('Копію підготовлено. Оберіть місце збереження у вікні завантаження.'); }
    catch (error) { backupStatus(errorMessage(error), true); }
    finally { event.target.disabled = false; }
  };
  $('#workspace-backup-restore').onclick = async event => {
    const file = $('#workspace-backup-file').files[0];
    if (!file || !$('#workspace-backup-confirmed').checked) { backupStatus('Оберіть ZIP і підтвердьте заміну поточних даних.', true); return; }
    event.target.disabled = true; backupStatus('Перевіряємо копію та відновлюємо дані…');
    try {
      await saveAll();
      const form = new FormData(); form.append('file', file); form.append('confirmed', 'true');
      await request('/api/backups/restore', {method: 'POST', body: form});
      for (const item of drafts.values()) { item.dirty = false; clearTimeout(item.timer); }
      window.location.reload();
    } catch (error) { backupStatus(errorMessage(error), true); }
    finally { event.target.disabled = false; }
  };
  window.OZWorkspace = {
    register(section, provider) { providers.set(section, provider); state(section); renderDrafts(); },
    async replaceWith(section,data) {
      if(!loaded)throw new Error(loadError||'Дочекайтеся завантаження чернеток.');
      const provider=providers.get(section),item=state(section);
      if(!provider)throw new Error('Редактор ще не готовий.');
      if((item.pending||provider.hasData?.())&&!window.confirm('Відкрити копію з журналу замість поточного комплекту та збереженої чернетки супровідних?'))return false;
      clearTimeout(item.timer);if(item.flight)await item.flight;
      restoring.add(section);
      try{await provider.restore(data);}finally{restoring.delete(section);}
      item.pending=false;item.dirty=true;item.generation++;renderDrafts();
      await persist(section);return true;
    },
    changed, review, flush: saveAll,
    async flushSection(section){await ready;await persist(section);},
    get ready(){return ready;},
    get scope(){return scope;},get package(){return packageInfo;},get switching(){return switching;},
    get hasData(){return [...providers.values()].some(provider=>provider.snapshot?.()!=null)||[...drafts.values()].some(item=>item.entry!=null);},
    updatePackage(value){if(value?.id===scope){packageInfo=value;document.dispatchEvent(new CustomEvent('protokol:package-context',{detail:{scope,package:packageInfo}}));}},
    get busy(){return switching||restoring.size>0||[...providers.values()].some(provider=>provider.isBusy?.())||!!window.OZTasks?.busy||!!window.OZOffice?.busy;},
    snapshot(section){return JSON.parse(JSON.stringify(providers.get(section)?.snapshot()||{}));},
    async bundle(){
      await saveAll();
      return Object.fromEntries(sectionNames.map(section=>[section,state(section).pending?state(section).entry?.data:providers.get(section)?.snapshot()]).filter(([,value])=>value!=null));
    },
    context(section){return {scope,section,draft:this.snapshot(section)};},
    async retainFiles(files,section,kind='source',context={scope,section}){
      for(const file of files){
        const form=new FormData();form.append('file',file);form.append('scope',context.scope);form.append('section',section);form.append('kind',kind);
        if(context.group)form.append('group',context.group);
        if(context.draft)form.append('draft',JSON.stringify(context.draft));
        await request('/api/workbench/files',{method:'POST',body:form});
      }
      document.dispatchEvent(new CustomEvent('protokol:package-files',{detail:{scope:context.scope}}));
    },
    async sourceFile(section,name,kind='source',group=''){
      const list=await request('/api/workbench/files?scope='+encodeURIComponent(scope));
      const entries=list.files.filter(file=>file.section===section&&file.kind===kind&&file.filename===name&&(!group||file.fingerprint===group));
      const entry=entries.find(file=>file.active)||entries[0];
      if(!entry)return null;
      const response=await fetch(`/api/workbench/files/${entry.id}?scope=${encodeURIComponent(scope)}`);
      if(!response.ok)throw new Error('Не вдалося відкрити збережений вихідний файл.');
      return new File([await response.blob()],entry.filename,{type:response.headers.get('Content-Type')||''});
    },
    async activateScope(next,initialized=false){
      if(!initialized)await ready;
      if(this.busy)throw new Error('Дочекайтеся завершення або скасуйте поточну обробку документів.');
      const previousScope=scope,previousPackage=packageInfo,previousDrafts=new Map(drafts);
      const previousValues=new Map([...providers].map(([name,provider])=>[name,provider.snapshot?.()]));
      const panes=sectionNames.map(name=>document.getElementById(name+'-view')).filter(Boolean);
      switching=true;panes.forEach(pane=>pane.inert=true);sectionNames.forEach(name=>restoring.add(name));
      try{
        await saveAll();
        const nextData=await request(draftBase(next));
        const nextPackage=next==='workspace'?null:(await request('/api/packages/'+encodeURIComponent(next))).package;
        if(nextPackage?.status==='archived')throw new Error('Поверніть пакет з архіву в роботу перед редагуванням.');
        scope=next;packageInfo=nextPackage;drafts.clear();
        for(const name of sectionNames){
          const item=state(name),entry=nextData.sections?.[name];
          item.revision=nextData.revisions?.[name]||entry?.revision||0;item.entry=entry||null;
          const provider=providers.get(name);
          if(entry){await provider.restore(JSON.parse(JSON.stringify(entry.data)));await provider.attachFiles?.();}
          else if(provider.reset)await provider.reset();
          else await provider.restore({items:[],records:[],letters:[],common:{}});
          item.pending=false;item.dirty=false;
        }
        await request('/api/workbench/session',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope})});
        window.OZOffice?.clear();
        loaded=true;loadError='';
        document.dispatchEvent(new CustomEvent('protokol:package-context',{detail:{scope,package:packageInfo}}));
        status('Пакет відкрито. Перевірте дані перед створенням документів.');
      }catch(error){
        scope=previousScope;packageInfo=previousPackage;drafts.clear();for(const [name,value] of previousDrafts)drafts.set(name,value);
        for(const [name,value] of previousValues){try{const provider=providers.get(name);if(value)await provider.restore(value);else if(provider.reset)await provider.reset();}catch{}}
        throw error;
      }finally{switching=false;sectionNames.forEach(name=>restoring.delete(name));panes.forEach(pane=>pane.inert=false);renderDrafts();}
    },
    get pending() { return [...drafts.values()].some(item => item.dirty || item.flight); }
  };
  const ready=(async()=>{
    try{const session=await request('/api/workbench/session');scope=session.scope;packageInfo=session.package;}
    catch(error){status(errorMessage(error),true);}
    await load();
    if(loaded&&scope!=='workspace'){
      try{await window.OZWorkspace.activateScope(scope,true);}catch(error){status('Не вдалося відкрити пакет: '+errorMessage(error),true);}
    }
    document.dispatchEvent(new CustomEvent('protokol:package-context',{detail:{scope,package:packageInfo}}));
  })();
})();
