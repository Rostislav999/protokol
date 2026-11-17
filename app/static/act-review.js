'use strict';
(() => {
  const controllers=new Map();
  const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const supported=name=>/\.(pdf|png|jpe?g|tiff?|bmp|webp)$/i.test(name||'');
  const cleanExpected=value=>Number.isInteger(value)&&value>=0&&value<=200?value:null;
  const cleanLocation=value=>{
    if(!value||!Number.isInteger(value.page)||value.page<1||value.page>20)return null;
    const result={page:value.page,angle:[0,90,180,270].includes(value.angle)?value.angle:0};
    if(Array.isArray(value.bbox)&&value.bbox.length===4&&value.bbox.every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&value.bbox[2]>value.bbox[0]&&value.bbox[3]>value.bbox[1])result.bbox=[...value.bbox];
    return result;
  };
  const cleanAudit=value=>value&&typeof value==='object'?{
    detected:cleanExpected(value.detected),pages:(Array.isArray(value.pages)?value.pages:[]).slice(0,20).map(page=>{
      const location=cleanLocation(page);return location?{...location,detected:cleanExpected(page.detected)}:null;
    }).filter(Boolean)
  }:null;
  const validDate=value=>{
    const iso=String(value).match(/\b(\d{4})-(\d{2})-(\d{2})\b/);
    const m=iso?[iso[0],iso[3],iso[2],iso[1]]:String(value).match(/\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b/);
    if(!m)return true;
    const d=new Date(0);d.setUTCFullYear(+m[3],+m[2]-1,+m[1]);
    return d.getUTCFullYear()===+m[3]&&d.getUTCMonth()===+m[2]-1&&d.getUTCDate()===+m[1];
  };
  function extras(name){
    const c=controllers.get(name);if(!c)return [];
    const data=c.adapter.get(),issues=[];
    const add=(message,selector,level='warning')=>issues.push({message,selector,level,section:name,audit:true});
    const expected=data.audit_expected??data.audit?.detected;
    if(data.common?.act_date&&!validDate(data.common.act_date))add('Дата акта не існує в календарі. Звірте її зі сканом.','[data-common="act_date"]');
    if(data.audit&&!data.items.length)add('Не визначено жодного зразка. Перевірте скан і додайте пропущені позиції.',c.adapter.root,'error');
    if(Number.isInteger(expected)&&expected!==data.items.length){
      const missing=expected>data.items.length;
      add(`Кількість зразків: за оригіналом ${expected}, у списку ${data.items.length}. ${missing?'Можливо, пропущено рядки — перевірте таблицю.':'Є додаткові позиції — перевірте, чи всі вони потрібні.'}`,
        `#${name}-audit-count`,missing?'error':'warning');
    }else if(data.audit&&expected==null){
      add('Кількість рядків не визначено надійно. Звірте скан і вкажіть кількість зразків за оригіналом.',`#${name}-audit-count`);
    }
    data.items.forEach((item,index)=>{
      for(const field of ['product_name','standard','manufacture_date','batch_size','sample_weight','seal_number']){
        const value=String(item[field]||'').trim();if(!value)continue;
        const selector=c.adapter.selector(item,field);if(!selector)continue;
        let message='';
        if(/[�]|\?{2,}|\[НЕ ЗАПОВНЕНО\]/i.test(value))message='Є нерозпізнані символи';
        if(field==='standard'){
          const marker=value.match(/Д[СC][ТT][УY]|ГОСТ|\bISO\b|ТУ\s+У/i);
          if(marker&&/[^\s.,;:()"'«»_—-]/.test(value.slice(0,marker.index)))message='Зайві символи перед позначенням НД';
          else if(/Д[СC][ТT][УY]\s*[_.,"'—]+\s*\d/i.test(value))message='Між позначенням НД і номером є зайві знаки';
        }
        if(field==='manufacture_date'&&!validDate(value))message='Дата не існує в календарі';
        if(['sample_weight','batch_size'].includes(field)&&(!/\d/.test(value)||/(?:^|\s)-\s*\d/.test(value)))message='Маса прочитана неоднозначно';
        if(message)add(`Зразок ${index+1}: ${message.toLocaleLowerCase('uk')}. Звірте значення зі сканом.`,selector);
      }
    });
    return issues;
  }
  class Review {
    constructor(name,adapter){
      this.name=name;this.adapter=adapter;this.root=document.querySelector(adapter.root);this.panel=document.querySelector(adapter.panel);
      this.panel.className='act-audit panel';this.panel.hidden=true;
      const area=document.createElement('div');area.className='act-work-area';
      this.editor=document.createElement('div');this.editor.className='act-editor-pane';
      while(this.panel.nextElementSibling)this.editor.append(this.panel.nextElementSibling);
      this.strip=document.createElement('div');this.strip.className='audit-scroll-summary';this.strip.hidden=true;
      this.strip.innerHTML='<strong></strong><button type="button" class="text-button">До першої проблеми →</button>';
      this.strip.querySelector('button').onclick=()=>this.jump(0);this.editor.prepend(this.strip);
      this.observer=new IntersectionObserver(entries=>{this.panelVisible=entries[0].isIntersecting;this.strip.hidden=this.panelVisible||!this.issues?.length;});
      this.observer.observe(this.panel);
      this.viewer=document.createElement('aside');this.viewer.className='act-source-pane panel';this.viewer.setAttribute('aria-label','Скан акта поруч із полями');
      this.viewer.innerHTML=`<div class="scan-heading"><div><strong>Оригінал акта</strong><small data-scan-selection>Оберіть зразок для звірки</small></div><button type="button" class="text-button" data-scan-close>Сховати</button></div><div class="scan-toolbar"><button type="button" data-scan-prev aria-label="Попередня сторінка">←</button><label>Сторінка <input data-scan-page type="number" min="1" value="1" aria-label="Сторінка оригіналу"></label><span data-scan-pages>/ —</span><button type="button" data-scan-next aria-label="Наступна сторінка">→</button><button type="button" data-scan-rotate aria-label="Повернути сторінку на 90 градусів">↶ 90°</button><label><span class="sr-only">Масштаб скана</span><select data-scan-zoom><option value="100">За шириною</option><option value="150">150%</option><option value="200">200%</option><option value="300">300%</option><option value="400">400%</option></select></label></div><p data-scan-status role="status"></p><div class="scan-scroll"><div class="scan-paper"><img alt="Сторінка оригіналу акта" hidden><div class="scan-row-highlight" hidden aria-hidden="true"></div></div></div>`;
      area.append(this.editor,this.viewer);this.panel.after(area);this.area=area;
      this.page=1;this.pages=1;this.rotation=0;this.open=false;this.request=0;this.file=null;this.sourceKey='';this.blob='';
      this.$=selector=>this.viewer.querySelector(selector);
      this.initOCR();
      this.panel.addEventListener('click',event=>{
        const jump=event.target.closest('[data-audit-jump]');if(jump)this.jump(+jump.dataset.auditJump);
        if(event.target.closest('[data-audit-next]'))this.jump(0);
        if(event.target.closest('[data-audit-scan]')){this.open=!this.open;this.layout();}
      });
      this.panel.addEventListener('change',event=>{
        if(!event.target.matches('[data-audit-count]')||!event.target.reportValidity())return;
        adapter.setExpected(event.target.value===''?null:Number(event.target.value));this.update();
      });
      this.root.addEventListener('focusin',event=>{
        const card=event.target.closest('[data-item],[data-assignment-item]');
        if(card){
          const field=event.target.dataset.field||event.target.dataset.assignmentField;
          if(this.adapter.ocrFields?.some(([key])=>key===field))this.ocrTarget={id:card.dataset.item||card.dataset.assignmentItem,field};
          if(this.open)this.select(card.dataset.item||card.dataset.assignmentItem);
        }
      });
      this.root.addEventListener('input',()=>this.update());
      this.root.addEventListener('change',()=>this.update());
      this.root.addEventListener('click',event=>{
        const row=event.target.closest('[data-source-row]');if(row){this.open=true;this.select(row.dataset.sourceRow);this.layout();}
        const reread=event.target.closest('[data-reocr-field]');
        if(reread){const card=reread.closest('[data-item],[data-assignment-item]');if(card){this.ocrTarget={id:card.dataset.item||card.dataset.assignmentItem,field:reread.dataset.reocrField};this.beginOCR();}}
      });
      this.$('[data-scan-close]').onclick=()=>{this.open=false;this.selecting=false;this.$('.scan-paper').classList.remove('ocr-selecting');this.cropBox.hidden=true;this.layout();};
      this.$('[data-scan-prev]').onclick=()=>{this.page--;this.loadPage();};
      this.$('[data-scan-next]').onclick=()=>{this.page++;this.loadPage();};
      this.$('[data-scan-page]').onchange=event=>{if(event.target.reportValidity()){this.page=Number(event.target.value);this.loadPage();}};
      this.$('[data-scan-rotate]').onclick=()=>{this.rotation=(this.rotation+90)%360;this.loadPage();};
      this.$('[data-scan-zoom]').onchange=event=>{this.$('.scan-paper').style.width=event.target.value+'%';requestAnimationFrame(()=>this.overlay());};
      document.addEventListener('protokol:package-context',()=>this.update());
    }
    initOCR(){
      const button=document.createElement('button');button.type='button';button.dataset.scanReocr='';button.textContent='Повторити OCR';
      button.onclick=()=>this.beginOCR();this.$('.scan-toolbar').append(button);
      this.cropBox=document.createElement('div');this.cropBox.className='scan-ocr-selection';this.cropBox.hidden=true;this.$('.scan-paper').append(this.cropBox);
      this.ocrDialog=document.createElement('dialog');this.ocrDialog.id=this.name+'-reocr-dialog';this.ocrDialog.className='modal reocr-dialog';
      this.ocrDialog.setAttribute('aria-labelledby',this.name+'-reocr-title');
      this.ocrDialog.innerHTML=`<div class="modal-heading"><h2 id="${this.name}-reocr-title">Повторне розпізнавання фрагмента</h2><button type="button" class="icon-button" data-ocr-close aria-label="Закрити">×</button></div><div class="modal-body"><p data-ocr-sample></p><label class="field"><span>Замінити лише це поле</span><select data-ocr-field>${(this.adapter.ocrFields||[]).map(([key,label])=>`<option value="${key}">${esc(label)}</option>`).join('')}</select></label><img class="reocr-crop-preview" alt="Виділений фрагмент скана"><label class="field"><span>Режим читання</span><select data-ocr-mode><option value="6">Кілька рядків або блок тексту</option><option value="7">Один рядок</option></select></label><div class="reocr-values"><label class="field"><span>Зараз у полі</span><textarea data-ocr-before readonly rows="3"></textarea></label><label class="field"><span>Новий текст — перевірте та за потреби виправте</span><textarea data-ocr-result rows="3" maxlength="12000"></textarea></label></div><p data-ocr-status role="status" aria-live="polite"></p><p class="notice soft">Інші поля, ручні правки, номери й вибрані шаблони зберігаються. Рукописні цифри та результат OCR звіряйте зі сканом.</p></div><div class="modal-footer"><button type="button" data-ocr-close class="button secondary">Скасувати</button><button type="button" data-ocr-run class="button secondary">Розпізнати фрагмент</button><button type="button" data-ocr-apply class="button primary" disabled>Застосувати до поля</button></div>`;
      document.body.append(this.ocrDialog);this.oq=selector=>this.ocrDialog.querySelector(selector);
      this.ocrDialog.querySelectorAll('[data-ocr-close]').forEach(node=>node.onclick=()=>this.ocrDialog.close());
      this.ocrDialog.addEventListener('close',()=>{this.ocrAbort?.abort();this.selecting=false;this.$('.scan-paper').classList.remove('ocr-selecting');this.cropBox.hidden=true;this.$('[data-scan-status]').textContent='';});
      this.oq('[data-ocr-field]').onchange=()=>this.ocrFieldChanged();
      this.oq('[data-ocr-result]').oninput=()=>{this.oq('[data-ocr-apply]').disabled=!this.oq('[data-ocr-result]').value.trim();};
      this.oq('[data-ocr-run]').onclick=()=>this.runOCR();
      this.oq('[data-ocr-apply]').onclick=()=>{
        try{
          if(this.ocrContext.scope!==window.OZWorkspace.scope||this.ocrContext.key!==this.sourceKey)throw new Error('Відкрито інший акт. Результат не застосовано.');
          const value=this.oq('[data-ocr-result]').value.trim().replace(/\s+/g,' ');
          if(!value||value.length>12000)return;
          this.adapter.applyOCR(this.ocrContext.id,this.oq('[data-ocr-field]').value,value,this.ocrExpected);
          this.ocrDialog.close();this.update();
        }catch(error){this.oq('[data-ocr-status]').textContent=error.message;}
      };
      const paper=this.$('.scan-paper');
      const point=event=>{const r=this.$('img').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];};
      const draw=event=>{
        const end=point(event),start=this.cropStart;this.crop=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])];
        const [x,y,right,bottom]=this.crop;Object.assign(this.cropBox.style,{left:x*100+'%',top:y*100+'%',width:(right-x)*100+'%',height:(bottom-y)*100+'%'});this.cropBox.hidden=false;
      };
      paper.addEventListener('pointerdown',event=>{if(!this.selecting||event.button!==0)return;event.preventDefault();this.cropStart=point(event);paper.setPointerCapture(event.pointerId);draw(event);});
      paper.addEventListener('pointermove',event=>{if(this.selecting&&paper.hasPointerCapture(event.pointerId)){event.preventDefault();draw(event);}});
      paper.addEventListener('pointerup',event=>{
        if(!this.selecting||!paper.hasPointerCapture(event.pointerId))return;draw(event);paper.releasePointerCapture(event.pointerId);
        if(this.crop[2]-this.crop[0]<.005||this.crop[3]-this.crop[1]<.005){this.cropBox.hidden=true;return;}
        this.selecting=false;paper.classList.remove('ocr-selecting');this.openOCR();
      });
      paper.addEventListener('pointercancel',()=>{this.selecting=false;paper.classList.remove('ocr-selecting');this.cropBox.hidden=true;});
      document.addEventListener('keydown',event=>{if(event.key==='Escape'&&this.selecting){this.selecting=false;paper.classList.remove('ocr-selecting');this.cropBox.hidden=true;this.$('[data-scan-status]').textContent='Виділення скасовано.';}});
    }
    async beginOCR(){
      if(!this.file)return;
      const items=this.adapter.get().items;
      if(!items.some(item=>item.id===this.ocrTarget?.id))this.ocrTarget={id:this.selected||items[0]?.id,field:this.adapter.ocrFields?.[0]?.[0]};
      if(!this.ocrTarget.id)return;
      const target=this.ocrTarget.id,key=this.sourceKey;
      this.selected=target;const page=this.location()?.page||this.page;
      if(page!==this.page){this.page=page;this.rotation=0;await this.loadPage();}
      else if(this.$('img').hidden||!this.$('img').complete)await this.loadPage();
      try{await this.$('img').decode();}catch{return;}
      if(key!==this.sourceKey||this.ocrTarget.id!==target)return;
      this.open=true;this.layout();this.selecting=true;this.cropBox.hidden=true;
      this.$('.scan-paper').classList.add('ocr-selecting');
      const label=this.adapter.ocrFields.find(([key])=>key===this.ocrTarget.field)?.[1]||'поле';
      this.$('[data-scan-status]').textContent=`Поле «${label}»: виділіть його текст прямокутником на скані. Esc — скасувати.`;
    }
    openOCR(){
      const item=this.adapter.get().items.find(item=>item.id===this.ocrTarget.id);if(!item)return;
      this.ocrContext={id:item.id,scope:window.OZWorkspace.scope,key:this.sourceKey,file:this.file.id,page:this.page,rotation:this.angle||0,bbox:[...this.crop]};
      this.oq('[data-ocr-sample]').textContent=item.product_name||'Новий зразок';
      this.oq('[data-ocr-field]').value=this.ocrTarget.field;this.ocrFieldChanged();
      this.oq('[data-ocr-mode]').value='6';this.oq('[data-ocr-status]').textContent='Натисніть «Розпізнати фрагмент». Жодне поле поки не змінено.';
      this.oq('[data-ocr-run]').disabled=false;
      const image=this.$('img'),[x,y,right,bottom]=this.crop,canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round((right-x)*image.naturalWidth));canvas.height=Math.max(1,Math.round((bottom-y)*image.naturalHeight));
      canvas.getContext('2d').drawImage(image,x*image.naturalWidth,y*image.naturalHeight,canvas.width,canvas.height,0,0,canvas.width,canvas.height);
      this.oq('.reocr-crop-preview').src=canvas.toDataURL('image/png');this.ocrDialog.showModal();
    }
    ocrFieldChanged(){
      const item=this.adapter.get().items.find(item=>item.id===this.ocrContext?.id);
      this.ocrExpected=String(item?.[this.oq('[data-ocr-field]').value]||'');
      this.oq('[data-ocr-before]').value=this.ocrExpected;this.oq('[data-ocr-result]').value='';this.oq('[data-ocr-apply]').disabled=true;
    }
    async runOCR(){
      this.ocrAbort?.abort();const controller=new AbortController();this.ocrAbort=controller;
      this.oq('[data-ocr-run]').disabled=true;this.oq('[data-ocr-apply]').disabled=true;this.oq('[data-ocr-status]').textContent='Повторно читаємо лише виділений фрагмент…';
      this.oq('[data-ocr-field]').disabled=true;this.oq('[data-ocr-result]').disabled=true;
      this.oq('[data-ocr-mode]').disabled=true;
      try{
        const {scope,file,page,rotation,bbox}=this.ocrContext;
        const response=await fetch(`/api/workbench/files/${encodeURIComponent(file)}/reocr`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope,page,rotation,bbox,psm:Number(this.oq('[data-ocr-mode]').value)}),signal:controller.signal});
        const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Не вдалося прочитати фрагмент.');
        if(controller.signal.aborted||!this.ocrDialog.open)return;
        this.oq('[data-ocr-result]').value=result.text;this.oq('[data-ocr-apply]').disabled=!result.text.trim();
        this.oq('[data-ocr-status]').textContent=result.text.trim()?'Перевірте новий текст. Інші поля не змінено.':'Текст не прочитано. Спробуйте більший фрагмент або інший режим.';
      }catch(error){if(error.name!=='AbortError')this.oq('[data-ocr-status]').textContent=error.message;}
      finally{if(this.ocrAbort===controller){this.oq('[data-ocr-run]').disabled=false;this.oq('[data-ocr-field]').disabled=false;this.oq('[data-ocr-result]').disabled=false;this.oq('[data-ocr-mode]').disabled=false;}}
    }
    update(){if(this.scheduled)return;this.scheduled=true;requestAnimationFrame(()=>{this.scheduled=false;this.render();});}
    render(){
      const data=this.adapter.get();
      this.panel.hidden=!data.items.length&&!data.source&&!data.text;
      const all=this.adapter.issues();
      const informational=all.filter(issue=>issue.selector==='#raw-text'||issue.selector==='#assignment-warnings');
      const priority=issue=>issue.selector===`#${this.name}-audit-count`?0:issue.level==='error'?1:issue.audit?2:3;
      const seen=new Set();
      this.issues=all.filter(issue=>!informational.includes(issue)).sort((a,b)=>priority(a)-priority(b)).filter(issue=>{
        const key=issue.selector+'|'+issue.level;if(seen.has(key))return false;seen.add(key);return true;
      });
      const errors=this.issues.filter(issue=>issue.level==='error').length,warnings=this.issues.length-errors;
      this.strip.querySelector('strong').textContent=`${errors?`Виправити: ${errors}`:''}${errors&&warnings?' · ':''}${warnings?`Звірити: ${warnings}`:''}`;
      this.strip.dataset.level=errors?'error':'warning';this.strip.hidden=this.panelVisible||!this.issues.length;
      const indexes=this.issues.map((issue,index)=>index);
      const featured=[...indexes.filter(index=>this.issues[index].level==='error').slice(0,2),...indexes.filter(index=>this.issues[index].level!=='error').slice(0,2)];
      const remaining=indexes.filter(index=>!featured.includes(index));
      const expected=data.audit_expected??data.audit?.detected,matched=Number.isInteger(expected)&&expected===data.items.length;
      const expanded=this.panel.querySelector('[data-audit-list]')?.open||false;
      const countExpanded=this.panel.querySelector('[data-audit-count-details]')?.open||false;
      this.panel.dataset.level=errors?'error':warnings?'warning':'ok';
      this.panel.innerHTML=`<div class="audit-heading"><span class="audit-symbol" aria-hidden="true">${errors?'!':warnings?'?':'✓'}</span><div><h2>Перевірка акта</h2><p>${errors?`Потрібно виправити: ${errors}`:warnings?'Залишилося звірити сумнівні значення':'Обов’язкові поля заповнено'}${warnings?` · Звірити: ${warnings}`:''}</p></div><div class="audit-actions"><button type="button" class="button primary" data-audit-next ${this.issues.length?'':'disabled'}>До першої проблеми ↓</button><button type="button" class="button secondary" data-audit-scan ${supported(data.source)?'':'disabled'}>${this.open?'Сховати скан':'Скан поруч'}</button></div></div><div class="audit-count-line"><span class="audit-count-state ${matched?'matched':''}">${data.items.length} у списку${expected!=null?` / ${expected} за оригіналом`:data.audit?' · кількість ще не звірена':''}</span>${data.audit||data.audit_expected!=null?`<details data-audit-count-details ${countExpanded?'open':''}><summary>Звірити кількість вручну</summary><label for="${this.name}-audit-count">Скільки зразків у повному акті?</label><input id="${this.name}-audit-count" data-audit-count type="number" min="1" max="200" step="1" value="${esc(data.audit_expected??'')}" placeholder="${esc(data.audit?.detected??'За сканом')}"><small>Залиште порожнім для автоматичного підрахунку. Перевіряйте всі сторінки.</small></details>`:''}</div>${this.issues.length?`<ol class="audit-priorities">${featured.map(index=>this.issueHtml(this.issues[index],index)).join('')}</ol>`:''}${remaining.length?`<details class="audit-more" data-audit-list ${expanded?'open':''}><summary>Усі проблеми (${this.issues.length})</summary><ol>${remaining.map(index=>this.issueHtml(this.issues[index],index)).join('')}</ol></details>`:''}${informational.length?`<details class="audit-notes"><summary>Як прочитано документ · ${informational.length}</summary><ul>${[...new Set(informational.map(issue=>issue.message))].map(message=>`<li>${esc(message)}</li>`).join('')}</ul></details>`:''}`;
      this.highlightFields();
      for(const item of data.items){
        const card=document.querySelector(this.adapter.card(item));if(!card)continue;
        if(item.source_location?.page&&!card.querySelector('[data-source-row]')){
          const button=document.createElement('button');button.type='button';button.className='text-button source-row-button';button.dataset.sourceRow=item.id;
          button.textContent=`Скан · стор. ${item.source_location.page}`;card.querySelector('.item-heading')?.append(button);
        }
      }
      const key=[window.OZWorkspace?.scope,data.sourceGroup||'',data.source||''].join('|');
      if(key!==this.sourceKey&&!window.OZWorkspace?.switching){this.sourceKey=key;this.findSource(data,key);}
      else if(this.selected&&!data.items.some(item=>item.id===this.selected))this.select(data.items.find(item=>item.source_location)?.id||null);
      this.layout();
    }
    issueHtml(issue,index){return `<li class="audit-issue ${issue.level}"><span class="audit-issue-tag">${issue.level==='error'?'Виправити':'Звірити'}</span><span>${esc(issue.message)}</span><button type="button" class="text-button" data-audit-jump="${index}">До поля →</button></li>`;}
    highlightFields(){
      for(const node of this.root.querySelectorAll('[data-audit-mark]')){
        node.classList.remove('audit-invalid','audit-suspect');node.removeAttribute('data-audit-mark');
        if(node.dataset.auditAria){node.removeAttribute('aria-invalid');delete node.dataset.auditAria;}
        if(node.dataset.auditDescription){
          const ids=(node.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&id!==node.dataset.auditDescription);
          if(ids.length)node.setAttribute('aria-describedby',ids.join(' '));else node.removeAttribute('aria-describedby');
          delete node.dataset.auditDescription;
        }
      }
      this.root.querySelectorAll('.audit-field-note').forEach(node=>node.remove());
      const seen=new Set();
      for(const issue of this.issues){
        if(seen.has(issue.selector))continue;seen.add(issue.selector);
        const original=document.querySelector(issue.selector);if(!original||!original.matches('input,textarea,select'))continue;
        const field=window.OZTemplatePicker?.control(original)||original;
        field.dataset.auditMark=issue.level;field.classList.add(issue.level==='error'?'audit-invalid':'audit-suspect');
        if(issue.level==='error'&&!field.hasAttribute('aria-invalid')){field.setAttribute('aria-invalid','true');field.dataset.auditAria='1';}
        const note=document.createElement('small');note.className='audit-field-note '+issue.level;
        note.id=`audit-message-${this.name}-${seen.size}`;field.dataset.auditDescription=note.id;
        field.setAttribute('aria-describedby',[field.getAttribute('aria-describedby'),note.id].filter(Boolean).join(' '));
        note.textContent=(issue.level==='error'?'! ':'? ')+issue.message;field.closest('.field')?.append(note);
        const key=original.dataset.field||original.dataset.assignmentField;
        if(supported(this.adapter.get().source)&&this.adapter.ocrFields?.some(([name])=>name===key)){
          const button=document.createElement('button');button.type='button';button.className='text-button reocr-field-button';button.dataset.reocrField=key;button.textContent='Перечитати фрагмент';note.append(button);
        }
      }
    }
    jump(index){
      const issue=this.issues[index];if(!issue)return;
      const original=document.querySelector(issue.selector);if(!original)return;
      const target=window.OZTemplatePicker?.control(original)||original;
      const details=target.closest('details');if(details)details.open=true;
      const card=target.closest('[data-item],[data-assignment-item]');
      if(card){this.open=true;this.select(card.dataset.item||card.dataset.assignmentItem);this.layout();}
      requestAnimationFrame(()=>{target.scrollIntoView({block:'center',behavior:'smooth'});if(!target.matches('input,textarea,select,button,a,[tabindex]'))target.tabIndex=-1;target.focus({preventScroll:true});});
    }
    layout(){this.area.classList.toggle('scan-open',this.open);this.viewer.hidden=!this.open;const toggle=this.panel.querySelector('[data-audit-scan]');if(toggle)toggle.textContent=this.open?'Сховати скан':'Скан поруч';}
    async findSource(data,key){
      this.ocrAbort?.abort();if(this.ocrDialog.open)this.ocrDialog.close();this.selecting=false;this.$('.scan-paper').classList.remove('ocr-selecting');this.cropBox.hidden=true;
      this.file=null;this.page=1;this.pages=1;this.rotation=0;this.selected=null;this.request++;
      if(this.blob){URL.revokeObjectURL(this.blob);this.blob='';}
      this.$('img').hidden=true;this.$('.scan-row-highlight').hidden=true;
      this.viewer.querySelectorAll('button,input,select').forEach(node=>{if(!node.matches('[data-scan-close]'))node.disabled=true;});
      this.open=!!data.source&&supported(data.source);this.layout();
      if(!data.source||!supported(data.source))return;
      try{
        const scope=window.OZWorkspace.scope;
        const response=await fetch('/api/workbench/files?scope='+encodeURIComponent(scope));
        if(!response.ok)throw new Error('Не вдалося знайти оригінал. Спробуйте відкрити пакет ще раз.');
        const entries=(await response.json()).files.filter(file=>file.kind==='source'&&file.filename===data.source&&[this.name,'acts'].includes(file.section)&&(!data.sourceGroup||file.fingerprint===data.sourceGroup));
        if(key!==this.sourceKey)return;
        const own=entries.filter(file=>file.section===this.name);
        this.file=own.find(file=>file.active)||own[0]||entries.find(file=>file.active)||entries[0];
        if(!this.file)throw new Error('Оригінал цього акта не збережено. Для старої чернетки відкрийте вихідний файл окремо.');
        this.scope=scope;
        this.selected=data.items.find(item=>item.source_location)?.id||null;
        this.page=this.location()?.page||1;await this.loadPage();
      }catch(error){if(key===this.sourceKey)this.$('[data-scan-status]').textContent=error.message;}
    }
    location(){return this.adapter.get().items.find(item=>item.id===this.selected)?.source_location;}
    select(id){
      this.selected=id;
      const location=this.location();
      const page=location?.page||this.page;
      if(page!==this.page){this.page=page;this.rotation=0;this.loadPage();}else this.overlay();
    }
    async loadPage(){
      if(!this.file)return;
      const id=++this.request,location=this.location();
      const angle=this.adapter.get().audit?.pages?.find(page=>page.page===this.page)?.angle??(location?.page===this.page?location.angle:0)??0;
      this.angle=(angle+this.rotation)%360;
      const params=new URLSearchParams({scope:this.scope,page:this.page,rotation:(angle+this.rotation)%360});
      this.$('[data-scan-status]').textContent='Завантажуємо сторінку…';this.$('img').hidden=true;this.$('.scan-row-highlight').hidden=true;
      this.viewer.querySelectorAll('button,input,select').forEach(node=>{if(!node.matches('[data-scan-close]'))node.disabled=true;});
      try{
        const response=await fetch(`/api/workbench/files/${encodeURIComponent(this.file.id)}/preview?${params}`);
        if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'Не вдалося відкрити скан.');}
        const blob=await response.blob();if(id!==this.request)return;
        this.pages=Number(response.headers.get('X-Page-Count'))||1;
        if(this.blob)URL.revokeObjectURL(this.blob);this.blob=URL.createObjectURL(blob);
        const image=this.$('img');image.onload=()=>{if(id===this.request)this.overlay();};image.src=this.blob;image.hidden=false;
        this.$('[data-scan-status]').textContent='';
      }catch(error){if(id===this.request)this.$('[data-scan-status]').textContent=error.message;}
      finally{if(id===this.request){
        this.viewer.querySelectorAll('button,input,select').forEach(node=>{node.disabled=false;});
        this.$('[data-scan-prev]').disabled=this.page<=1;this.$('[data-scan-next]').disabled=this.page>=this.pages;
        this.$('[data-scan-page]').value=this.page;this.$('[data-scan-page]').max=this.pages;this.$('[data-scan-pages]').textContent='/ '+this.pages;
      }}
    }
    overlay(){
      const data=this.adapter.get(),index=data.items.findIndex(item=>item.id===this.selected),location=this.location();
      this.$('[data-scan-selection]').textContent=index>=0?`Зразок ${index+1} · ${data.items[index].product_name||'без назви'}`:'Оберіть зразок для звірки';
      const highlight=this.$('.scan-row-highlight');highlight.hidden=true;
      if(!location?.bbox||location.page!==this.page)return;
      const [x1,y1,x2,y2]=location.bbox;
      let points=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]];
      for(let angle=0;angle<this.rotation;angle+=90)points=points.map(([x,y])=>[y,1-x]);
      const xs=points.map(point=>point[0]),ys=points.map(point=>point[1]);
      highlight.style.left=Math.min(...xs)*100+'%';highlight.style.top=Math.min(...ys)*100+'%';
      highlight.style.width=(Math.max(...xs)-Math.min(...xs))*100+'%';highlight.style.height=(Math.max(...ys)-Math.min(...ys))*100+'%';highlight.hidden=false;
      const scroll=this.$('.scan-scroll');scroll.scrollTop=Math.max(0,highlight.offsetTop-scroll.clientHeight*.25);
    }
  }
  window.OZActReview={register(name,adapter){const c=new Review(name,adapter);controllers.set(name,c);c.update();},update(name){controllers.get(name)?.update();},issues:extras,cleanAudit,cleanLocation,cleanExpected};
})();
