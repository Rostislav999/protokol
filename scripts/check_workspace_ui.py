"""Exercise real drafts, provenance, and linked review in the embedded renderer.

Run from the repository with: python -m scripts.check_workspace_ui
Requires the Windows build dependencies (PySide6), never Word or a printer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time


SCRIPT = r'''void (async () => {try {
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  const wait=async fn=>{for(let i=0;i<400;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw new Error('UI readiness timeout: '+fn.toString());};
  const input=(selector,value,event='input')=>{const field=document.querySelector(selector);assert(field,'Missing field: '+selector);field.value=value;field.dispatchEvent(new Event(event,{bubbles:true}));};
  await wait(()=>window.__providers?.acts && window.__providers?.excel && window.__providers?.assignments && window.__providers?.cover_letters && !document.querySelector('#workspace-save-status').textContent.includes('Завантажуємо'));
  const providers=window.__providers;
  if (__PACKAGE_PHASE__) {
    await window.OZWorkspace.ready;await window.OZAppearance.ready;
    const session=await(await fetch('/api/workbench/session')).json();
    assert(session.package?.title==='Пакет перевірки UI' && window.OZWorkspace.scope===session.scope,'Active package did not survive restart');
    assert(providers.acts.snapshot().common.customer==='Замовник Ω','Act was lost when reopening its package');
    assert(providers.cover_letters.snapshot().letters.find(value=>value.id==='general').content.body[1]==='Другий абзац.','Letter text was lost when reopening its package');
    assert(!document.querySelector('[data-confirm]').checked && !document.querySelector('#review-confirmed').checked,'Package restart restored an approval');
    assert(Object.values(providers.assignments.snapshot().items[0].departments).some(value=>value.sample_weight==='7 кг'),'Package restart lost a manual assignment weight');
    assert((await(await fetch('/api/template-choices')).json()).choices.some(value=>value.template_id==='sample-1053' && value.favorite),'Favorite template was lost on restart');
    assert((await(await fetch('/api/workbench/files?scope='+session.scope)).json()).files.some(value=>value.kind==='source'),'Package lost its original files');
    console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:true,checks:['four durable drafts','reload and explicit restore','leading zeroes','manual provenance','confirmation reset','built-in Excel form and legacy draft migration','assignment manual weight','whole-library template selection and search','manual template restore and re-review','searchable template picker with Word preview','dedicated cover letter section','live A4 preview','persistent action bar','cover letter editor before download','cover letter autosave and restore','outgoing register and status changes','saved signer profiles','editable copy from outgoing history','persistent readable text size','appearance preview save cancel and reset','theme accent density and default editor survive restart','unchanged document ink draft and approval','linked date review','isolated document packages','package restart and retained original files','automatic and named letter revisions','favorite and recent templates','technical report without document bodies','keyboard shortcuts','cancellable background imports','printer selection refresh cancellation and no silent fallback','single-sided and double-sided print selection','prepend and append samples without renumbering','manual sample numbers and draft restore','copy next with separate identity and number','independent copied fields and durable drafts','product name autocomplete from the library','bulk copies with count and number limits','undo sample deletion without lost fields or renumbering','standard citation autocomplete without automatic changes','new acts without replacing previous drafts','pending local and concurrent drafts preserved separately','delete active and inactive packages with cancellation','source pages row overlays rotation and zoom','visible field warnings and keyboard jumps','sample count discrepancy blocks export until checked','review evidence survives save without restoring approval']}));
    return;
  }
  if (!__RESTORE_PHASE__) {
    await wait(()=>document.querySelector('#nav-template-count').textContent==='22');
    // Exercise printer choices without calling the real print worker or queue.
    const realFetch=window.fetch, printCalls=[];
    let printerNames=['Printer A','Printer B'];
    window.fetch=async (url,options)=>{
      if(String(url)==='/api/printing/status')return new Response(JSON.stringify({available:printerNames.length>0,printer:'Printer A',printers:printerNames.map(name=>({name,is_default:name==='Printer A',selectable:true})),reason:'No printers'}),{status:200});
      if(String(url)==='/api/assignments/print'){
        const request=JSON.parse(options.body);printCalls.push(request);
        return new Response(JSON.stringify({documents:1,copies:2,total_copies:2,printer:request.printer}),{status:200});
      }
      return realFetch(url,options);
    };
    try {
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'assignments'}}));
      document.dispatchEvent(new CustomEvent('protokol:assignments-ready',{detail:{payload:{job_id:'printer-ui-check',confirmed:true,items:[{id:'one',departments:{vet:{template_id:'check'}}}]}}}));
      await wait(()=>!document.querySelector('#assignment-print').disabled);
      document.querySelector('#assignment-print').click();
      await wait(()=>!document.querySelector('#assignment-print-confirm').disabled);
      assert(document.querySelector('#assignment-printer').value==='Printer A','Initial Windows default was not offered');
      input('#assignment-printer','Printer B','change');
      input('#assignment-duplex','long_edge','change');
      document.querySelector('#assignment-print-dialog [data-print-close]').click();
      await new Promise(resolve=>setTimeout(resolve,0));
      assert(printCalls.length===0,'Cancel started a print job');
      document.querySelector('#assignment-print').click();
      await wait(()=>!document.querySelector('#assignment-print-confirm').disabled);
      input('#assignment-printer','Printer B','change');document.querySelector('#assignment-print-confirm').click();
      await wait(()=>!document.querySelector('#assignment-print-dialog').open);
      assert(printCalls.length===1&&printCalls[0].printer==='Printer B','Selected printer was not sent to the API');
      assert(printCalls[0].duplex==='long_edge'&&printCalls[0].copies===2,'Two-copy duplex choice did not reach the API');
      document.querySelector('#assignment-print').click();
      await wait(()=>!document.querySelector('#assignment-print-confirm').disabled);
      assert(document.querySelector('#assignment-printer').value==='Printer B','Last printer was not retained');
      assert(document.querySelector('#assignment-duplex').value==='long_edge','Duplex choice was lost when reopening the dialog');
      printerNames=['Printer A','Printer C'];document.querySelector('[data-print-refresh]').click();
      await wait(()=>document.querySelector('#assignment-printer option[value="Printer C"]'));
      assert(document.querySelector('#assignment-printer').value===''&&document.querySelector('#assignment-print-confirm').disabled,'A missing printer silently switched to another device');
      input('#assignment-printer','Printer C','change');input('#assignment-duplex','simplex','change');document.querySelector('#assignment-print-confirm').click();
      await wait(()=>!document.querySelector('#assignment-print-dialog').open);
      assert(printCalls.length===2&&printCalls[1].printer==='Printer C','Refresh did not allow a newly connected printer');
      assert(printCalls[1].duplex==='simplex','Switching back to one-sided printing was ignored');
      assert(printCalls[0].generation.job_id===printCalls[1].generation.job_id,'Printer selection changed the issued assignment generation');
    } finally {
      window.fetch=realFetch;
      document.dispatchEvent(new CustomEvent('protokol:assignments-invalidated'));
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    }
    const copyTemplate=(await(await fetch('/api/assignments/templates')).json()).templates.find(value=>value.product_name&&value.standard);
    await providers.assignments.restore({initialNumber:'1418',items:[
      {id:'copy-source',product_name:copyTemplate.product_name,standard:'НД для ручної перевірки',sample_number:'1418',sample_weight:'4 кг',
       departments:{[copyTemplate.department]:{enabled:true,template_id:copyTemplate.id,sample_weight:'7 кг',weight_edited:true}}},
      {id:'copy-tail',product_name:'Інший зразок',standard:'Інший НД',sample_number:'1419',departments:{}}
    ]});
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'assignments'}}));
    const copySource=document.querySelector('[data-assignment-item="copy-source"]');
    const nameInput=copySource.querySelector('[data-assignment-field="product_name"]');
    assert(nameInput.list?.options.length>0,'Product name has no autocomplete library');
    const suggestedNames=[...nameInput.list.options].map(option=>option.value);
    assert(suggestedNames.includes(copyTemplate.product_name)&&new Set(suggestedNames).size===suggestedNames.length,'Autocomplete lost or duplicated library names');
    copySource.querySelector('[data-dept-override]').click();
    document.querySelector('#assignment-confirmed').checked=true;
    copySource.querySelector('.assignment-copy').click();
    await wait(()=>document.querySelectorAll('[data-assignment-item]').length===3&&!document.querySelector('.assignment-copy').disabled);
    let copyDraft=providers.assignments.snapshot();
    const copied=copyDraft.items[1];
    assert(copied.id!=='copy-source'&&copied.id!=='copy-tail','Copy reused an existing sample identity');
    assert(copyDraft.items.map(item=>item.sample_number).join(',')==='1418,1420,1419','Copy renumbered existing samples or reused a number');
    assert(copied.product_name===copyTemplate.product_name&&copied.standard==='НД для ручної перевірки'&&copied.sample_weight==='4 кг','Copy lost sample fields');
    assert(copied.departments[copyTemplate.department].template_id===copyTemplate.id&&copied.departments[copyTemplate.department].sample_weight==='7 кг','Copy lost template or manual department weight');
    const copyCard=document.querySelector(`[data-assignment-item="${copied.id}"]`);
    assert(!document.querySelector('#assignment-confirmed').checked&&!copyCard.querySelector('[data-dept-override]').checked,'Copy inherited review confirmations');
    input(`[data-assignment-item="${copied.id}"] [data-dept-weight="${copyTemplate.department}"]`,'9 кг');
    assert(providers.assignments.snapshot().items[0].departments[copyTemplate.department].sample_weight==='7 кг','Editing a copy changed its source');
    input(`[data-assignment-item="${copied.id}"] [data-assignment-field="product_name"]`,suggestedNames.find(name=>name!==copied.product_name));
    assert(providers.assignments.snapshot().items[0].product_name===copyTemplate.product_name,'Autocomplete selection changed another sample');
    copyDraft=providers.assignments.snapshot();
    await providers.assignments.restore(copyDraft);
    assert(providers.assignments.snapshot().items[1].departments[copyTemplate.department].sample_weight==='9 кг','Copy was lost on draft restore');
    const standardField=document.querySelector(`[data-assignment-item="${copied.id}"] [data-assignment-field="standard"]`);
    standardField.focus();
    assert([...standardField.list.options].some(option=>option.value===copyTemplate.standard),'Standard autocomplete is missing a library citation');
    assert([...standardField.list.options].some(option=>option.value==='Інший НД'),'Standard autocomplete is missing another sample citation');
    assert(standardField.value==='НД для ручної перевірки','Showing suggestions changed an entered standard');
    input(`[data-assignment-item="${copied.id}"] [data-assignment-field="standard"]`,copyTemplate.standard);
    assert(providers.assignments.snapshot().items[0].standard==='НД для ручної перевірки','Choosing a standard changed another sample');
    const openCopies=()=>document.querySelector(`[data-assignment-item="${copied.id}"] .assignment-copy-many`).click();
    openCopies();document.querySelector('#assignment-copy-dialog [data-copy-close]').click();
    assert(providers.assignments.snapshot().items.length===3,'Cancelling the copy dialog added samples');
    openCopies();input('#assignment-copy-count','100');
    document.querySelector('#assignment-copy-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
    assert(providers.assignments.snapshot().items.length===3,'An oversized copy count changed the list');
    input('#assignment-copy-count','2');document.querySelector('#assignment-copy-submit').click();
    await wait(()=>!document.querySelector('#assignment-copy-dialog').open&&!document.querySelector('.assignment-copy').disabled);
    const bulkDraft=providers.assignments.snapshot();
    assert(bulkDraft.items.map(item=>item.sample_number).join(',')==='1418,1420,1421,1422,1419','Bulk copies changed existing numbers or failed to allocate distinct ones');
    assert(new Set(bulkDraft.items.map(item=>item.id)).size===5,'Bulk copies share an identity');
    assert(bulkDraft.items.slice(2,4).every(item=>item.departments[copyTemplate.department].sample_weight==='9 кг'&&item.standard===copyTemplate.standard),'Bulk copies lost fields or department weights');
    document.querySelector(`[data-assignment-item="${bulkDraft.items[2].id}"] .assignment-remove`).click();
    assert(providers.assignments.snapshot().items.length===4&&!document.querySelector('#assignment-undo').hidden,'Deletion has no undo action');
    assert(providers.assignments.snapshot().items.map(item=>item.sample_number).join(',')==='1418,1420,1422,1419','Deletion renumbered remaining samples');
    document.querySelector('#assignment-undo-delete').click();
    await wait(()=>!document.querySelector('.assignment-copy').disabled&&document.querySelectorAll('[data-assignment-item]').length===5);
    assert(JSON.stringify(providers.assignments.snapshot().items)===JSON.stringify(bulkDraft.items),'Undo did not restore the sample, fields, identity and original position');
    document.querySelector('[data-assignment-item="copy-source"] .assignment-remove').click();
    document.querySelector('[data-assignment-item="copy-tail"] .assignment-remove').click();
    document.querySelector('#assignment-undo-delete').click();
    await wait(()=>!document.querySelector('.assignment-copy').disabled&&document.querySelectorAll('[data-assignment-item]').length===4);
    document.querySelector('#assignment-undo-delete').click();
    await wait(()=>!document.querySelector('.assignment-copy').disabled&&document.querySelectorAll('[data-assignment-item]').length===5);
    assert(JSON.stringify(providers.assignments.snapshot().items)===JSON.stringify(bulkDraft.items),'Repeated undo changed sample order or contents');
    document.querySelector('[data-assignment-item="copy-tail"] .assignment-remove').click();
    await providers.assignments.restore({initialNumber:'1',items:Array.from({length:99},(_,index)=>({id:'capacity-'+index,product_name:'',standard:'',departments:{}}))});
    assert(document.querySelector('#assignment-undo').hidden,'Deleted samples leaked into a different draft');
    document.querySelector('.assignment-copy-many').click();
    assert(document.querySelector('#assignment-copy-count').max==='1','Copy dialog ignores the remaining sample capacity');
    input('#assignment-copy-count','1');document.querySelector('#assignment-copy-submit').click();
    await wait(()=>!document.querySelector('#assignment-copy-dialog').open);
    assert(providers.assignments.snapshot().items.length===100&&[...document.querySelectorAll('.assignment-copy,.assignment-copy-many')].every(button=>button.disabled),'Copy controls allow more than 100 samples');
    await providers.assignments.restore({initialNumber:'2147483646',items:[{id:'last-number',sample_number:'2147483646',product_name:'Свинина тушкована',departments:{}}]});
    document.querySelector('.assignment-copy-many').click();input('#assignment-copy-count','2');document.querySelector('#assignment-copy-submit').click();
    await new Promise(resolve=>setTimeout(resolve,0));
    assert(providers.assignments.snapshot().items.length===1&&document.querySelector('#assignment-copy-dialog').open,'Number overflow partially added copies');
    document.querySelector('#assignment-copy-dialog [data-copy-close]').click();
    await providers.assignments.restore({initialNumber:'1418',items:Array.from({length:15},(_,index)=>({id:'range-'+index,product_name:'',standard:'',departments:{}}))});
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'assignments'}}));
    const visibleNumbers=()=>[...document.querySelectorAll('.assignment-number-preview')].map(node=>node.textContent);
    assert(visibleNumbers().join(',')===Array.from({length:15},(_,i)=>String(1418+i)).join(','),'Initial range preview is incorrect');
    document.querySelector('#assignment-add-before').click();
    await wait(()=>!document.querySelector('#assignment-add-item').disabled&&visibleNumbers().length===16);
    assert(visibleNumbers()[0]==='1417'&&visibleNumbers()[1]==='1418'&&visibleNumbers().at(-1)==='1432','Adding before renumbered existing samples');
    document.querySelector('#assignment-add-after-bottom').click();
    await wait(()=>!document.querySelector('#assignment-add-item').disabled&&visibleNumbers().length===17);
    assert(visibleNumbers().join(',')===Array.from({length:17},(_,i)=>String(1417+i)).join(','),'Adding after did not preserve the range');
    const rangeDraft=providers.assignments.snapshot();
    await providers.assignments.restore(rangeDraft);
    assert(visibleNumbers().join(',')===Array.from({length:17},(_,i)=>String(1417+i)).join(','),'Explicit per-sample numbers were lost on restore');
    input('[data-assignment-number]','1416');
    await new Promise(resolve=>setTimeout(resolve,350));
    await wait(()=>!document.querySelector('#assignment-add-item').disabled);
    assert(visibleNumbers()[0]==='1416'&&visibleNumbers()[1]==='1418','Editing one sample number renumbered other samples');
    // Synthetic two-page PDF: exercise the real retained-original renderer
    // without storing a user's scan in the repository or calling Office.
    const pdfObjects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 5 0 R >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 6 0 R >>',
      ...['1 0 0 rg 30 30 540 740 re f\n','0 0 1 rg 30 30 540 740 re f\n'].map(stream=>`<< /Length ${stream.length} >>\nstream\n${stream}endstream`)];
    let pdfSource='%PDF-1.4\n',pdfOffsets=[0];
    pdfObjects.forEach((object,index)=>{pdfOffsets.push(pdfSource.length);pdfSource+=`${index+1} 0 obj\n${object}\nendobj\n`;});
    const xref=pdfSource.length;pdfSource+=`xref\n0 7\n0000000000 65535 f \n`+pdfOffsets.slice(1).map(offset=>String(offset).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
    const reviewGroup=crypto.randomUUID().replaceAll('-',''),reviewScope=window.OZWorkspace.scope;
    await window.OZWorkspace.retainFiles([new File([pdfSource],'review-fixture.pdf',{type:'application/pdf'})],'acts','source',{scope:reviewScope,group:reviewGroup});
    assert((await fetch('/api/workbench/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:reviewScope,section:'acts',group:reviewGroup})})).ok,'Review source did not activate');
    await providers.acts.restore({source:'review-fixture.pdf',sourceGroup:reviewGroup,method:'ocr-table',common:{customer:'Замовник',sampling_description:'Адреса відбору'},
      audit:{detected:5,pages:[{page:1,angle:0,detected:1},{page:2,angle:0,detected:4}]},
      items:[1,2].map((number,index)=>({id:'audit-'+number,product_name:'Свинина тушкована',standard:index?'ee) ДСТУ 4450:2005':'ДСТУ 4450:2005',protocol_number:'AUDIT-'+number,protocol_date:'17.11.2026',template_id:'sample-1053',source_location:{page:number,angle:0,bbox:[.1,.2,.9,.4]}}))});
    window.OZWorkspace.changed('acts');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    await wait(()=>document.querySelector('#review-section .act-source-pane img')?.naturalWidth>0&&document.querySelector('#extraction-warnings .audit-issue'));
    assert(document.querySelector('#extraction-warnings').dataset.level==='error'&&document.querySelector('#generate').disabled,'Missing samples do not block generation');
    assert(document.querySelector('#extraction-warnings .audit-issue').textContent.includes('за оригіналом 5'),'Count discrepancy is buried below other notices');
    document.querySelector('#extraction-warnings [data-audit-jump]').click();
    assert(document.querySelector('#acts-audit-count').closest('details').open,'Count issue does not reveal its correction control');
    const suspicious=document.querySelector('[data-item="audit-2"] [data-field="standard"]');
    assert(suspicious.classList.contains('audit-suspect'),'OCR noise has no field highlight');
    assert((suspicious.getAttribute('aria-describedby')||'').split(' ').some(id=>document.getElementById(id)?.classList.contains('audit-field-note')),'Suspicious field does not describe its warning to assistive technology');
    document.querySelector('[data-source-row="audit-2"]').click();
    await wait(()=>document.querySelector('#review-section [data-scan-page]').value==='2'&&!document.querySelector('#review-section .scan-row-highlight').hidden);
    assert(document.querySelector('#review-section [data-scan-pages]').textContent==='/ 2','Preview lost the PDF page count');
    document.querySelector('#review-section [data-scan-rotate]').click();
    await wait(()=>document.querySelector('#review-section .act-source-pane img').naturalWidth>document.querySelector('#review-section .act-source-pane img').naturalHeight&&!document.querySelector('#review-section [data-scan-rotate]').disabled);
    input('#review-section [data-scan-zoom]','400','change');
    assert(document.querySelector('#review-section .scan-paper').style.width==='400%','Scan zoom did not apply');
    const beforeRepeatOCR=JSON.stringify(providers.acts.snapshot().items);
    suspicious.focus();document.querySelector('#review-section [data-scan-reocr]').click();
    await wait(()=>document.querySelector('#review-section .scan-paper').classList.contains('ocr-selecting'));
    assert(document.querySelector('#review-section [data-scan-status]').textContent.includes('НД'),'Repeat OCR lost the focused target field');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert(!document.querySelector('#review-section .scan-paper').classList.contains('ocr-selecting')&&JSON.stringify(providers.acts.snapshot().items)===beforeRepeatOCR,'Cancelling repeat OCR changed sample fields');
    input('[data-item="audit-2"] [data-field="standard"]','ДСТУ 4450:2005');
    await wait(()=>!document.querySelector('[data-item="audit-2"] [data-field="standard"]').classList.contains('audit-suspect'));
    input('#acts-audit-count','2','change');
    document.querySelector('#review-confirmed').checked=true;document.querySelector('#review-confirmed').dispatchEvent(new Event('change',{bubbles:true}));
    await wait(()=>!document.querySelector('#generate').disabled);
    assert(providers.acts.snapshot().audit_expected===2,'Manual count was not captured in the draft');
    input('[data-item="audit-1"] select[data-field="template_id"]','','change');
    await wait(()=>document.querySelector('[data-item="audit-1"] [data-template-picker-control]')?.classList.contains('audit-invalid'));
    const templateIssue=[...document.querySelectorAll('#extraction-warnings .audit-issue')].find(row=>row.textContent.includes('Позиція 1: шаблон'));
    assert(templateIssue,'Missing template is absent from the persistent review panel');
    templateIssue.closest('details')?.setAttribute('open','');templateIssue.querySelector('[data-audit-jump]').click();
    await wait(()=>document.activeElement.matches('[data-template-picker-control]')&&document.activeElement.closest('[data-item]').dataset.item==='audit-1');
    input('[data-item="audit-1"] select[data-field="template_id"]','sample-1053','change');
    await window.OZWorkspace.flush();
    const auditSaved=(await(await fetch('/api/workspace/drafts')).json()).sections.acts.data;
    assert(auditSaved.audit_expected===2&&auditSaved.items[1].source_location.page===2,'Saved draft lost review facts');
    await providers.acts.restore(auditSaved);await providers.acts.attachFiles();
    assert(!document.querySelector('#review-confirmed').checked,'Restoring source evidence restored an export approval');
    document.querySelector('#prepare-assignments').click();
    await wait(()=>!window.OZWorkspace.busy&&providers.assignments.snapshot()?.items.length===2);
    assert(providers.assignments.snapshot().audit_expected===2&&providers.assignments.snapshot().items[1].source_location.page===2,'Transfer lost count evidence or source-page association');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    document.querySelector('#start-manual').click();
    await wait(()=>!window.OZWorkspace.busy&&providers.acts.snapshot()?.method==='manual');
    // Keep exercising explicit legacy-workspace restore separately from the
    // new default of starting acts in independent document packages.
    const manualAct=providers.acts.snapshot();
    await window.OZWorkspace.activateScope('workspace');
    await providers.acts.restore(manualAct);window.OZWorkspace.changed('acts');
    input('[data-common="customer"]','Замовник Ω');
    input('[data-common="sampling_description"]','Відібрано замовником');
    input('[data-field="product_name"]','Свинина тушкована');
    input('[data-field="protocol_number"]','87-015-3000-1400-2');
    input('[data-field="protocol_date"]','29.10.2026');
    input('select[data-field="template_id"]','sample-1053','change');
    const protocolSelect=document.querySelector('select[data-field="template_id"]');
    await wait(()=>protocolSelect.previousElementSibling?.matches('[data-template-picker-control]'));
    protocolSelect.previousElementSibling.click();
    await wait(()=>document.querySelector('#template-picker[open]'));
    input('[data-picker-search]','свинина');
    document.querySelector('[data-picker-id="sample-1053"]').click();
    await wait(()=>document.querySelector('[data-picker-document] .word-page'));
    document.querySelector('[data-picker-apply]').click();
    assert(protocolSelect.value==='sample-1053','Protocol template picker did not apply');
    await providers.excel.restore({records:[{id:'saved-excel',source:'act.pdf',values:{D:'001',E:'29.10.2026',G:'Test',M:'00001234',N:'12345678'},sources:{M:{kind:'manual',label:'Введено вручну'}}}],templateWasSelected:true,templateName:'old.xlsx'});
    const templates=(await(await fetch('/api/assignments/templates')).json()).templates;
    const template=templates.find(value=>value.sample_weight && value.standard && value.product_name);
    assert(template,'Missing assignment fixture');
    await providers.assignments.restore({source:'act.pdf',text:'Текст акта',initialNumber:'1400',items:[{id:'saved-assignment',product_name:'Назва, яку автоматичний підбір не впізнав',standard:'НД з акта для ручного вибору',departments:{[template.department]:{enabled:true,template_id:template.id,sample_weight:'7 кг',weight_edited:true}}}]});
    const templateSelect=document.querySelector(`[data-dept-template="${template.department}"]`);
    assert(templateSelect.value===template.id,'Manual template outside recommendations was lost');
    assert(templateSelect.options.length===templates.filter(t=>t.department===template.department).length+1,'Full template library is not available');
    input(`[data-dept-search="${template.department}"]`,template.filename);
    assert(templateSelect.value===template.id,'Template search cleared the selected value');
    await wait(()=>templateSelect.previousElementSibling?.matches('[data-template-picker-control]'));
    templateSelect.previousElementSibling.click();
    await wait(()=>document.querySelector('#template-picker[open]'));
    input('[data-picker-search]',template.filename);
    const pickerOption=document.querySelector(`[data-picker-id="${template.id}"]`);
    assert(pickerOption,'Template picker search did not find the full filename');pickerOption.click();
    await wait(()=>document.querySelector('[data-picker-document] .word-page'));
    document.querySelector('[data-picker-apply]').click();
    assert(document.querySelector(`[data-dept-template="${template.department}"]`).value===template.id,'Picker did not apply the selected template');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));
    assert(!document.querySelector('#cover_letters-view').hidden,'Dedicated cover letter section did not open');
    const protocol=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true,common:{customer:'Замовник',supplier:'ТОВ «Приклад»',sampling_description:'Одеса'},items:[{id:'cover-fixture',protocol_number:'87-015-3000-1400-2',protocol_date:'02.11.2026',product_name:'Свинина тушкована',template_id:'sample-1053'}]})});
    assert(protocol.ok,'Cover letter protocol fixture failed');
    const files=new DataTransfer(); files.items.add(new File([await protocol.arrayBuffer()],'protocols.zip',{type:'application/zip'}));
    const upload=document.querySelector('[data-files]'); upload.files=files.files; upload.dispatchEvent(new Event('change',{bubbles:true}));
    // Native renderer validation must not start a separately installed Office.
    // Actual pagination/conversion is covered by the office integration checks.
    document.querySelector('[data-exact-pages]').checked=false;
    document.querySelector('[data-import]').click();
    await wait(()=>document.querySelector('[data-records] [data-key="pages"]'));
    input('[data-records] [data-key="supplier"]','ТОВ «Приклад», код ЄДРПОУ: 00123456');
    input('[data-records] [data-key="pages"]','3');
    await wait(()=>!document.querySelector('[data-preview]').disabled);
    document.querySelector('[data-preview]').click();
    await wait(()=>document.querySelectorAll('[data-letter]').length===2);
    // Narrower/scaled windows deliberately start in text mode.
    document.querySelector('[data-editor-mode="preview"]').click();
    await wait(()=>document.querySelector('[data-page-preview] .word-page'));
    const paper=document.querySelector('[data-page-preview] .word-page').getBoundingClientRect();
    assert(paper.width>0 && paper.height>paper.width,'A4 portrait preview is not visible');
    await wait(()=>document.querySelector('[data-page-preview] img')?.naturalWidth>0);
    const stamp=document.querySelector('[data-page-preview] article table');
    assert(stamp,'Missing cover letter stamp');
    assert([...stamp.querySelectorAll('span')].filter(node=>node.textContent.trim()).every(node=>getComputedStyle(node).color==='rgb(46, 117, 182)'),'Stamp text uses inconsistent ink');
    assert(getComputedStyle(stamp.querySelector('img')).filter.includes('stamp-ink-'),'Emblem preview did not apply the Word stamp ink');
    const companyId=[...document.querySelector('[data-letter-select]').options].find(option=>option.value.startsWith('company-')).value;
    input('[data-letter-select]',companyId,'change');
    await wait(()=>document.querySelector('[data-page-preview]').textContent.includes('ТОВ «Приклад»'));
    const companyAddress=document.querySelector('[data-letter]:not([hidden]) [data-letter-field="recipients"]').value;
    assert(companyAddress==='ТОВ «Приклад»','Company address still includes its tax code');
    assert(!document.querySelector('[data-page-preview]').textContent.includes('00123456'),'Company DOCX preview still includes its tax code');
    input('[data-letter-select]','general','change');
    await wait(()=>document.querySelector('[data-page-preview]').textContent.includes('00123456'));
    document.querySelector('[data-editor-mode="edit"]').click();
    input('[data-letter="general"] [data-letter-field="body"]','Текст, відредагований перед завантаженням.\nДругий абзац.');
    assert(!document.querySelector('[data-confirm]').checked,'Edited letter kept its old approval');
    assert(document.querySelector('[data-letter="general"] [data-letter-field="body"]').value.includes('Другий абзац.'),'Letter editor lost paragraphs');
    document.querySelector('[data-editor-mode="preview"]').click();
    await wait(()=>document.querySelector('[data-page-preview]').textContent.includes('Другий абзац.'));
    await wait(()=>document.querySelector('[data-dock-primary]').textContent.includes('супровідні'));
    document.querySelector('[data-editor-mode="edit"]').click();
    document.querySelector('[data-manage-signers]').click();
    await wait(()=>document.querySelector('#signer-profiles[open]'));
    input('#signer-profiles [name="name"]','Підпис для перевірки');
    input('#signer-profiles [name="position"]','Начальник лабораторії');
    input('#signer-profiles [name="signer"]','майор Ірина ПРИКЛАД');
    input('#signer-profiles [name="contact"]','Виконавець 00 000');
    document.querySelector('[data-profile-save]').click();
    await wait(()=>document.querySelector('[data-profile-message]').textContent.includes('збережено'));
    const profile=(await(await fetch('/api/signers')).json()).profiles[0];
    assert(profile?.signer==='майор Ірина ПРИКЛАД','Signer profile did not persist');
    document.querySelector('[data-signers-close]').click();
    const profileSelect=document.querySelector('[data-letter="general"] [data-apply-profile="individual"]');
    profileSelect.closest('details').open=true;
    input('[data-letter="general"] [data-apply-profile="individual"]',profile.id,'change');
    assert(document.querySelector('[data-letter="general"] [data-letter-field="signer"]').value===profile.signer,'Signer profile was not applied');
    assert(document.querySelector('[data-letter="general"] [data-letter-field="contact"]').value===profile.contact,'Profile contact was not applied');
    document.querySelector('[data-letter="general"] .letter-registration').open=true;
    input('[data-letter="general"] [data-registration-field="date"]','2026-11-03');
    input('[data-letter="general"] [data-registration-field="number"]','UI-001');
    document.querySelector('[data-journal-draft]').click();
    await wait(()=>document.querySelector('[data-status]').textContent.includes('збережено в журналі'));
    const journal=(await(await fetch('/api/outgoing')).json()).entries;
    assert(journal.length===2 && journal.every(entry=>entry.status==='draft'),'Draft letters were not registered');
    assert(journal.find(entry=>entry.identity==='general').outgoing_number==='UI-001','Outgoing number did not reach the register');
    document.querySelector('[data-view="outgoing"]').click();
    await wait(()=>document.querySelectorAll('[data-open-letter]').length===2);
    assert(document.querySelector('.action-dock').hidden,'Editor actions overlap the outgoing register');
    input('[data-journal-search]','ui-001');
    await wait(()=>document.querySelectorAll('[data-open-letter]').length===1);
    document.querySelector('[data-open-letter]').click();
    await wait(()=>document.querySelector('#outgoing-letter[open]'));
    input('[data-journal-entry-status]','sent','change');
    input('[data-journal-notes]','Передано для перевірки');
    document.querySelector('[data-journal-save]').click();
    await wait(()=>document.querySelector('[data-journal-message]').textContent.includes('Статус збережено'));
    const sent=(await(await fetch('/api/outgoing?status=sent')).json()).entries;
    assert(sent.length===1 && sent[0].outgoing_number==='UI-001','Sent status did not persist');
    const original=await fetch('/api/outgoing/'+sent[0].id+'/docx');
    assert(original.ok && (await original.arrayBuffer()).byteLength>10000,'Stored Word letter cannot be downloaded');
    document.querySelector('[data-journal-copy]').click();
    await wait(()=>!document.querySelector('#cover_letters-view').hidden && !document.querySelector('#outgoing-letter').open);
    assert(document.querySelector('[data-letter="general"] [data-registration-field="number"]').value==='','Editing a copy reused an official outgoing number');
    assert(document.querySelector('[data-letter="general"] [data-letter-field="signer"]').value===profile.signer,'Copying lost the saved signature');
    const beforeAppearance=JSON.stringify(providers.cover_letters.snapshot());
    const beforeMode=document.querySelector('[data-editor-layout]').dataset.mode;
    document.querySelector('[data-confirm]').checked=true;
    document.querySelector('[data-appearance-open]').click();
    await wait(()=>document.querySelector('#appearance-settings[open]'));
    input('#appearance-settings [name="theme"]','dark','change');
    input('#appearance-settings [name="text_size"]','extra_large','change');
    input('#appearance-settings [name="density"]','compact','change');
    document.querySelector('#appearance-settings [name="accent"][value="blue"]').click();
    input('#appearance-settings [name="editor_mode"]','preview','change');
    assert(document.documentElement.dataset.theme==='dark' && document.documentElement.dataset.accent==='blue','Live appearance preview was not applied');
    await wait(()=>document.querySelector('[data-page-preview] .word-page'));
    assert(getComputedStyle(document.querySelector('[data-page-preview] .word-page')).backgroundColor==='rgb(255, 255, 255)','Dark theme changed the document paper');
    assert(getComputedStyle(document.querySelector('[data-page-preview] .word-page')).color==='rgb(0, 0, 0)','Dark theme changed the document ink');
    assert([...document.querySelectorAll('[data-page-preview] article table span')].filter(node=>node.textContent.trim()).every(node=>getComputedStyle(node).color==='rgb(46, 117, 182)'),'Interface accent changed the stamp ink');
    assert(document.querySelector('[data-confirm]').checked,'Appearance preview cleared document approval');
    assert(JSON.stringify(providers.cover_letters.snapshot())===beforeAppearance,'Appearance preview changed the document draft');
    document.querySelector('#appearance-settings [data-appearance-cancel]').click();
    await wait(()=>!document.querySelector('#appearance-settings').open);
    assert(document.documentElement.dataset.theme==='light','Cancel did not restore the saved theme');
    assert(document.querySelector('[data-editor-layout]').dataset.mode===beforeMode,'Cancel did not restore the editor view');
    assert((await(await fetch('/api/preferences')).json()).theme==='light','Live preview wrote preferences before Save');
    document.querySelector('[data-appearance-open]').click();
    input('#appearance-settings [name="theme"]','dark','change');
    input('#appearance-settings [name="density"]','compact','change');
    document.querySelector('#appearance-settings [name="accent"][value="blue"]').click();
    input('#appearance-settings [name="editor_mode"]','preview','change');
    document.querySelector('[data-appearance-save]').click();
    await wait(()=>!document.querySelector('#appearance-settings').open);
    const appearance=(await(await fetch('/api/preferences')).json());
    assert(appearance.theme==='dark' && appearance.accent==='blue' && appearance.density==='compact' && appearance.editor_mode==='preview','Appearance settings were not saved');
    document.querySelector('[data-appearance-open]').click();
    document.querySelector('[data-appearance-reset]').click();
    assert(document.documentElement.dataset.theme==='light' && document.documentElement.dataset.density==='comfortable','Default appearance preview failed');
    document.querySelector('#appearance-settings [data-appearance-cancel]').click();
    assert(document.documentElement.dataset.theme==='dark' && document.documentElement.dataset.density==='compact','Cancel after reset lost saved preferences');
    input('[data-text-size]','large','change');
    await wait(()=>!document.querySelector('[data-text-size]').disabled);
    assert((await(await fetch('/api/preferences')).json()).text_size==='large','Text size preference was not saved');
    assert((await(await fetch('/api/preferences')).json()).theme==='dark','Quick text size reset the saved theme');
    assert(getComputedStyle(document.querySelector('[data-letter="general"] [data-letter-field="body"]')).fontSize==='18px','Larger form text was not applied');
    document.querySelector('[data-confirm]').checked=true;
    let automaticCoverDraft=null;
    for(let i=0;i<100;i++) {
      const drafts=await(await fetch('/api/workspace/drafts')).json();
      automaticCoverDraft=drafts.sections.cover_letters?.data;
      if(automaticCoverDraft?.letters?.find(letter=>letter.id==='general')?.content?.body?.[1]==='Другий абзац.')break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert(automaticCoverDraft?.letters?.find(letter=>letter.id==='general')?.content?.body?.[1]==='Другий абзац.','Cover letter did not autosave without clicking Save');
    await window.OZWorkspace.flush();
    const saved=await(await fetch('/api/workspace/drafts')).json();
    assert(Object.keys(saved.sections).length===4,'All four drafts were not saved');
    assert(saved.sections.acts.data.common.customer==='Замовник Ω','Act edits were not saved');
    assert(saved.sections.excel.data.records[0].values.M==='00001234','Leading zeroes were lost');
    const coverDraft=saved.sections.cover_letters.data;
    assert(coverDraft.letters.find(letter=>letter.id==='general').content.body[1]==='Другий абзац.','Edited cover letter was not saved');
    assert(!coverDraft.confirmed,'Cover letter approval was persisted');
    console.log('OZ_WORKSPACE_RELOAD');
  } else {
    await wait(()=>document.querySelectorAll('.workspace-draft').length===4);
    for (let i=0;i<4;i++) {
      const count=document.querySelectorAll('.workspace-draft').length;
      document.querySelector('.workspace-draft button').click();
      await wait(()=>document.querySelectorAll('.workspace-draft').length===count-1);
    }
    assert(document.querySelector('[data-common="customer"]').value==='Замовник Ω','Act did not restore');
    assert(providers.acts.snapshot().sources.common.customer.kind==='manual','Act provenance did not restore');
    assert(!document.querySelector('#review-confirmed').checked,'Act approval survived restore');
    assert(!document.querySelector('#assignment-confirmed').checked,'Assignment approval survived restore');
    assert(!document.querySelector('#excel-confirmed').checked,'Excel approval survived restore');
    assert(providers.excel.snapshot().records[0].values.M==='00001234','Excel code did not restore');
    assert(!document.querySelector('#excel-template'),'Excel template upload should be removed');
    assert(!providers.excel.issues().some(issue=>issue.level==='error'),'Built-in Excel form still requires an uploaded workbook');
    assert(providers.excel.snapshot().legacyTemplateName==='old.xlsx','Legacy workbook notice was lost after restart');
    assert(!document.querySelector('#excel-legacy-template').hidden,'Legacy workbook migration notice is hidden');
    const selection=Object.values(providers.assignments.snapshot().items[0].departments).find(value=>value.enabled);
    assert(selection.sample_weight==='7 кг' && selection.weight_edited,'Manual assignment weight did not restore');
    assert(selection.template_id,'Manual template choice did not restore');
    const manualReview=document.querySelector('[data-dept-override]');
    assert(manualReview && !manualReview.checked,'Manual choice review was not reset on restore');
    assert(document.querySelector('[data-letter="general"] [data-letter-field="body"]').value==='Текст, відредагований перед завантаженням.\nДругий абзац.','Cover letter edits did not restore');
    assert(document.querySelector('[data-records] [data-key="pages"]').value==='3','Cover letter pages did not restore');
    assert(!document.querySelector('[data-confirm]').checked,'Cover letter approval survived restore');
    assert(document.querySelector('[data-files]').files.length===0,'Source file input should be empty after reload');
    assert(!document.querySelector('#cover_letters-view').hidden,'Cover draft restored into the wrong section');
    await wait(()=>document.querySelector('[data-text-size]').value==='large');
    assert(document.querySelector('[data-text-size]').value==='large','Text size did not survive restart');
    assert(document.documentElement.dataset.theme==='dark' && document.documentElement.dataset.accent==='blue' && document.documentElement.dataset.density==='compact','Appearance choices did not survive restart');
    assert(document.querySelector('[data-editor-layout]').dataset.mode==='preview','Default editor view did not survive restart');
    assert((await(await fetch('/api/signers')).json()).profiles[0]?.signer==='майор Ірина ПРИКЛАД','Signer profile was lost after restart');
    assert((await(await fetch('/api/outgoing?status=sent')).json()).total===1,'Outgoing history was lost after restart');
    const letterBody=document.querySelector('[data-letter="general"] [data-letter-field="body"]'),savedBody=letterBody.value;
    input('[data-letter="general"] [data-letter-field="body"]','');
    window.OZWorkspace.review('cover_letters');
    const coverIssue=[...document.querySelectorAll('.workspace-issue')].find(row=>row.textContent.includes('основний текст'));
    assert(coverIssue,'Missing letter text was not found by review');coverIssue.querySelector('button').click();
    assert(document.activeElement===letterBody,'Cover letter review did not focus its field');
    input('[data-letter="general"] [data-letter-field="body"]',savedBody);
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    input('[data-field="protocol_date"]','31.02.2026');
    document.querySelector('#workspace-review').click();
    assert(document.querySelector('.workspace-modal[open]'),'Unified review did not open');
    const issue=[...document.querySelectorAll('.workspace-issue')].find(row=>row.textContent.includes('некоректна дата протоколу'));
    assert(issue,'Invalid date is missing from unified review'); issue.querySelector('button').click();
    await new Promise(resolve=>setTimeout(resolve,150));
    assert(document.activeElement.dataset.field==='protocol_date','Review link did not focus the incorrect date');
    await window.OZWorkspace.flush();
    // The old ungrouped workspace remains available while cases get independent drafts.
    input('[data-field="protocol_date"]','29.10.2026');await window.OZWorkspace.flush();
    const previousWork=(await(await fetch('/api/workspace/drafts')).json());
    const competing={...previousWork.sections.assignments.data,source:'Чернетка з іншого вікна'};
    assert((await fetch('/api/workspace/drafts/assignments',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:competing,expected_revision:previousWork.sections.assignments.revision})})).ok,'Conflict fixture did not save');
    input('[data-assignment-field="standard"]','Поточна правка перед новим актом');
    try{await window.OZWorkspace.flush();}catch{}
    assert(document.querySelector('.workspace-draft'),'Stale draft was silently overwritten');
    const packagesBefore=new Set((await(await fetch('/api/packages')).json()).packages.map(value=>value.id));
    const oldConfirm=window.confirm;let newActPrompts=0;window.confirm=()=>{newActPrompts++;return false;};
    document.querySelector('#new-act').click();
    await wait(()=>window.OZWorkspace.scope!=='workspace'&&!window.OZWorkspace.busy&&!document.querySelector('#upload-section').hidden);
    const newActScope=window.OZWorkspace.scope;
    const renameFetch=window.fetch;let releaseRename=null;
    window.fetch=async(url,options)=>{
      if(String(url)==='/api/packages/'+newActScope&&options?.method==='PATCH')await new Promise(resolve=>{releaseRename=resolve;});
      return renameFetch(url,options);
    };
    const rename=window.OZWorkspace.startNewAct('новий-акт.txt');
    try {
      await wait(()=>releaseRename!==null);
      assert(window.OZWorkspace.busy,'Prepared act can switch cases while its import context is being named');
      let blocked=false;try{await window.OZWorkspace.activateScope('workspace');}catch{blocked=true;}
      assert(blocked&&window.OZWorkspace.scope===newActScope,'Concurrent navigation redirected a new act into another case');
    } finally {releaseRename?.();await rename;window.fetch=renameFetch;}
    assert(newActPrompts===0&&!document.querySelector('.workspace-draft'),'Starting a new act still asks to replace old drafts');
    assert(Object.values(providers).every(provider=>provider.snapshot()===null),'A new act inherited prior section fields');
    const afterNew=(await(await fetch('/api/workspace/drafts')).json());
    assert(afterNew.sections.acts.data.common.customer==='Замовник Ω'&&afterNew.sections.assignments.data.source===competing.source,'New act overwrote previous or concurrent work');
    const preserved=(await(await fetch('/api/packages')).json()).packages.filter(value=>!packagesBefore.has(value.id)&&value.id!==newActScope);
    assert(preserved.length===1,'Unsaved old work was not preserved separately');
    const preservedDrafts=(await(await fetch('/api/packages/'+preserved[0].id+'/drafts')).json()).sections;
    assert(preservedDrafts.assignments.data.items[0].standard==='Поточна правка перед новим актом','Pending current edits were lost when starting a new act');
    const actText='Замовник: Новий замовник\n№ | Найменування продукції | Нормативний документ\n'+Array.from({length:5},(_,index)=>`${index+1} | Свинина тушкована | ДСТУ 4450:2005`).join('\n');
    const actFiles=new DataTransfer();actFiles.items.add(new File([actText],'новий-акт.txt',{type:'text/plain'}));
    const actInput=document.querySelector('#act-file');actInput.files=actFiles.files;actInput.dispatchEvent(new Event('change',{bubbles:true}));
    await wait(()=>providers.acts.snapshot()?.source==='новий-акт.txt'&&providers.acts.snapshot()?.items.length===5&&!window.OZTasks.busy);
    assert(window.OZWorkspace.scope===newActScope,'Uploading into a prepared new act created another package');
    document.querySelector('#prepare-assignments').click();
    await wait(()=>providers.assignments.snapshot()?.items.length===5&&!window.OZWorkspace.busy&&document.querySelector('.assignment-copy')?.disabled===false);
    await window.OZWorkspace.flush();
    document.querySelector('#assignment-new').click();
    await wait(()=>window.OZWorkspace.scope!==newActScope&&!window.OZWorkspace.busy);
    assert(newActPrompts===0&&Object.values(providers).every(provider=>provider.snapshot()===null),'New assignment act asks to discard previous sections');
    const importedDrafts=(await(await fetch('/api/packages/'+newActScope+'/drafts')).json()).sections;
    assert(importedDrafts.acts.data.items.length===5&&importedDrafts.assignments.data.items.length===5,'Previous act was not saved before opening the next one');
    assert((await(await fetch('/api/workbench/files?scope='+newActScope)).json()).files.some(file=>file.filename==='новий-акт.txt'),'New act did not retain its original file');
    window.confirm=oldConfirm;
    await window.OZWorkspace.activateScope('workspace');
    document.querySelector('[data-view="packages"]').click();
    document.querySelector('[data-package-new]').click();
    await wait(()=>document.querySelector('#package-form[open]'));
    input('#package-form [name="title"]','Пакет перевірки UI');
    assert(document.querySelector('#package-form [name="copy"]').checked,'Current work is not offered when creating a first package');
    document.querySelector('[data-package-form-save]').click();
    await wait(()=>window.OZWorkspace.scope!=='workspace' && !window.OZWorkspace.switching && !document.querySelector('#package-form').open);
    const firstPackage=window.OZWorkspace.scope;
    assert(providers.acts.snapshot().common.customer==='Замовник Ω','Creating a package lost the act');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    const choice=document.querySelector('select[data-field="template_id"]');
    choice.previousElementSibling.click();await wait(()=>document.querySelector('#template-picker[open]'));
    input('[data-picker-search]','свинина');
    const favorite=document.querySelector('[data-picker-favorite="sample-1053"]');
    assert(favorite,'Favorite template action is missing');if(favorite.getAttribute('aria-pressed')!=='true')favorite.click();
    await wait(()=>document.querySelector('[data-picker-favorite="sample-1053"]')?.getAttribute('aria-pressed')==='true');
    input('[data-picker-shortcuts]','favorites','change');
    assert(document.querySelector('[data-picker-id="sample-1053"]'),'Favorites filter did not show the saved template');
    document.querySelector('[data-picker-close]').click();
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));
    document.querySelector('[data-editor-mode="edit"]').click();
    document.querySelector('[data-letter-history]').click();await wait(()=>document.querySelector('#letter-history[open]'));
    input('[data-history-label]','UI контрольна версія');document.querySelector('[data-history-save]').click();
    await wait(()=>document.querySelector('[data-history-list]').textContent.includes('UI контрольна версія'));
    document.querySelector('[data-history-close]').click();
    input('[data-letter="general"] [data-letter-field="body"]','Тимчасова зміна для перевірки історії.');
    await window.OZWorkspace.flush();
    document.querySelector('[data-letter-history]').click();await wait(()=>document.querySelector('#letter-history[open]'));
    await wait(()=>[...document.querySelectorAll('[data-history-version]')].some(button=>button.textContent.includes('UI контрольна версія')));
    [...document.querySelectorAll('[data-history-version]')].find(button=>button.textContent.includes('UI контрольна версія')).click();
    await wait(()=>!document.querySelector('[data-history-restore]').disabled);
    assert(document.querySelector('.history-changed'),'History comparison did not show a changed field');
    document.querySelector('[data-history-restore]').click();await wait(()=>!document.querySelector('#letter-history').open);
    assert(providers.cover_letters.snapshot().letters.find(value=>value.id==='general').content.body[1]==='Другий абзац.','History restore did not recover the selected letter');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'packages'}}));
    document.querySelector('[data-package-new]').click();input('#package-form [name="title"]','Порожній пакет UI');
    assert(!document.querySelector('#package-form [name="copy"]').checked,'A new case implicitly copies another case');
    document.querySelector('[data-package-form-save]').click();
    await wait(()=>window.OZWorkspace.scope!==firstPackage && !window.OZWorkspace.switching && !document.querySelector('#package-form').open);
    assert(Object.values(providers).every(provider=>provider.snapshot()===null),'A new package inherited another package’s fields');
    const emptyPackage=window.OZWorkspace.scope;
    await window.OZPackages.open();
    await wait(()=>document.querySelector('[data-package-delete]'));
    const deleteConfirm=window.confirm;window.confirm=()=>false;
    document.querySelector('[data-package-delete]').click();
    await wait(()=>!document.querySelector('[data-package-delete]').disabled);
    assert(window.OZWorkspace.scope===emptyPackage&&(await fetch('/api/packages/'+emptyPackage)).ok,'Cancelling deletion removed or closed a package');
    window.confirm=()=>true;document.querySelector('[data-package-delete]').click();
    await wait(()=>window.OZWorkspace.scope==='workspace'&&!document.querySelector('[data-package-list-view]').hidden);
    assert((await fetch('/api/packages/'+emptyPackage)).status===404,'Deleting an active package left it on the server');
    assert((await fetch('/api/packages/'+firstPackage)).ok,'Deleting an active package removed another package');
    window.confirm=deleteConfirm;
    await window.OZWorkspace.activateScope(firstPackage);
    const inactive=(await(await fetch('/api/packages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Видалити неактивний пакет',drafts:{acts:{items:[]}}})})).json());
    await window.OZPackages.open();document.querySelector('[data-package-back]').click();
    await wait(()=>document.querySelector(`[data-package-select="${inactive.id}"]`));
    document.querySelector(`[data-package-select="${inactive.id}"]`).click();
    await wait(()=>document.querySelector('[data-package-delete]')&&!document.querySelector('[data-package-detail]').hidden&&document.querySelector('.package-heading').textContent.includes('Видалити неактивний'));
    document.querySelector('[data-package-delete]').click();
    await wait(()=>!document.querySelector('[data-package-list-view]').hidden);
    assert((await fetch('/api/packages/'+inactive.id)).status===404&&window.OZWorkspace.scope===firstPackage,'Deleting another package changed the active case');
    assert(providers.acts.snapshot().common.customer==='Замовник Ω','Package deletion changed an unrelated open act');
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'cover_letters'}}));
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'s',ctrlKey:true,bubbles:true,cancelable:true}));await window.OZWorkspace.flush();
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'1',altKey:true,bubbles:true,cancelable:true}));
    assert(!document.querySelector('#packages-view').hidden,'Keyboard navigation did not open packages');
    const beforeCancellation=JSON.stringify(providers.acts.snapshot());
    const cancelled=window.OZTasks.run('act',[new File(['Перевірка скасування'],'cancel-check.txt',{type:'text/plain'})],{section:'acts'}).then(()=>false,error=>error.name==='AbortError');
    await wait(()=>document.querySelector('[data-task-label]').textContent.includes('Читаємо сторінку'));
    document.querySelector('[data-task-cancel]').click();assert(await cancelled,'Background cancellation did not finish');
    assert(JSON.stringify(providers.acts.snapshot())===beforeCancellation,'Cancelled import changed the act');
    const cancelledSources=(await(await fetch('/api/workbench/files?scope='+firstPackage)).json()).files;assert(cancelledSources.some(file=>file.filename==='cancel-check.txt'&&!file.active),'Cancelled import lost its original file or activated a partial result');
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'F1',bubbles:true,cancelable:true}));
    assert(document.querySelector('#diagnostics[open]'),'Keyboard help did not open');document.querySelector('[data-diagnostics-close]').click();
    const diagnostics=await fetch('/api/diagnostics/export',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    assert(diagnostics.ok,'Technical report could not be created');
    const diagnosticArchive=await JSZip.loadAsync(await diagnostics.arrayBuffer());
    const diagnosticJSON=await diagnosticArchive.file('diagnostic.json').async('string');
    assert(JSON.parse(diagnosticJSON).system.python && !diagnosticJSON.includes('Другий абзац.'),'Diagnostic report contains document text');
    const packageReport=await(await fetch('/api/packages/'+firstPackage+'/check')).json();assert(Array.isArray(packageReport.issues),'Package consistency report is unavailable');
    console.log('OZ_PACKAGES_RELOAD');
  }
} catch(error) {console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:false,error:error.stack||String(error),viewport:{width:innerWidth,height:innerHeight},editorMode:document.querySelector('[data-editor-layout]')?.dataset.mode,activeView:document.querySelector('.nav-button.active')?.dataset.view}));}})();'''


def main() -> int:
    if os.name != 'nt':
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication
    from app.main import create_app
    from desktop import LocalServer

    application = QApplication([])
    result = {"ok": False, "error": "Workspace UI timed out"}
    phase = {"restore": False, "packages": False}
    profile = QWebEngineProfile(application)

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            if message == "OZ_WORKSPACE_RELOAD":
                phase["restore"] = True
                QTimer.singleShot(0, view.reload)
            elif message == "OZ_PACKAGES_RELOAD":
                phase["packages"] = True
                QTimer.singleShot(0, view.reload)
            elif message.startswith("OZ_WORKSPACE_RESULT:"):
                result.update(json.loads(message.split(":", 1)[1]))
                if not result['ok']:
                    capture_path = Path('build/windows/native-ui-failure.png')
                    capture_path.parent.mkdir(parents=True, exist_ok=True)
                    view.grab().save(str(capture_path))
                application.exit(0 if result["ok"] else 1)
            elif "Error" in message:
                print("JavaScript:", message, flush=True)

    page = Page(profile, application)
    view = QWebEngineView(); view.setPage(page)
    view.resize(int(os.environ.get('PROTOKOL_UI_WIDTH', '1440')), 1000)
    capture = QWebEngineScript()
    capture.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    capture.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    capture.setSourceCode("""(() => {
      let workspace=null; window.__providers={}; window.confirm=()=>true;
      Object.defineProperty(window,'OZWorkspace',{get:()=>workspace,set:value=>{
        const register=value.register.bind(value);
        value.register=(name,provider)=>{window.__providers[name]=provider;register(name,provider);};
        workspace=value;
      }});
    })();""")
    page.scripts().insert(capture)
    page.loadFinished.connect(lambda success: page.runJavaScript(SCRIPT.replace("__RESTORE_PHASE__", "true" if phase["restore"] else "false").replace("__PACKAGE_PHASE__", "true" if phase["packages"] else "false")) if success else application.exit(2))
    QTimer.singleShot(90000, lambda: application.exit(3))
    with tempfile.TemporaryDirectory(prefix="oz-workspace-ui-") as temporary:
        app = create_app(Path(temporary) / "data")
        original_act_processor = app.state.tasks.processors['act']
        def cancellable_fixture(files, options):
            if files[0][0] != 'cancel-check.txt':
                return original_act_processor(files, options)
            from app import progress
            progress.file(1, 1, files[0][0])
            for number in range(1, 1001):
                progress.page(number, 1000)
                time.sleep(.01)
            return original_act_processor(files, options)
        app.state.tasks.processors['act'] = cancellable_fixture
        with LocalServer(app) as server:
            view.setUrl(QUrl(server.url)); view.show()
            code = application.exec()
        view.close(); page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
    if result['ok']:
        result.pop('error', None)
    report = json.dumps(result, ensure_ascii=False, indent=2)
    report_path = Path('build/windows/native-ui-result.json')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
            summary.write('### Native workspace check\n\n```json\n' + report + '\n```\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["ok"] and os.environ.get("GITHUB_ACTIONS") == "true":
        error = str(result["error"]).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Native workspace check::{error}", flush=True)
    return code if code else (0 if result["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
