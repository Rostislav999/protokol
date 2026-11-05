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
    console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:true,checks:['four durable drafts','reload and explicit restore','leading zeroes','manual provenance','confirmation reset','built-in Excel form and legacy draft migration','assignment manual weight','whole-library template selection and search','manual template restore and re-review','searchable template picker with Word preview','dedicated cover letter section','live A4 preview','persistent action bar','cover letter editor before download','cover letter autosave and restore','outgoing register and status changes','saved signer profiles','editable copy from outgoing history','persistent readable text size','appearance preview save cancel and reset','theme accent density and default editor survive restart','unchanged document ink draft and approval','linked date review','isolated document packages','package restart and retained original files','automatic and named letter revisions','favorite and recent templates','technical report without document bodies','keyboard shortcuts','cancellable background imports','printer selection refresh cancellation and no silent fallback']}));
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
      document.querySelector('#assignment-print-dialog [data-print-close]').click();
      await new Promise(resolve=>setTimeout(resolve,0));
      assert(printCalls.length===0,'Cancel started a print job');
      document.querySelector('#assignment-print').click();
      await wait(()=>!document.querySelector('#assignment-print-confirm').disabled);
      input('#assignment-printer','Printer B','change');document.querySelector('#assignment-print-confirm').click();
      await wait(()=>!document.querySelector('#assignment-print-dialog').open);
      assert(printCalls.length===1&&printCalls[0].printer==='Printer B','Selected printer was not sent to the API');
      document.querySelector('#assignment-print').click();
      await wait(()=>!document.querySelector('#assignment-print-confirm').disabled);
      assert(document.querySelector('#assignment-printer').value==='Printer B','Last printer was not retained');
      printerNames=['Printer A','Printer C'];document.querySelector('[data-print-refresh]').click();
      await wait(()=>document.querySelector('#assignment-printer option[value="Printer C"]'));
      assert(document.querySelector('#assignment-printer').value===''&&document.querySelector('#assignment-print-confirm').disabled,'A missing printer silently switched to another device');
      input('#assignment-printer','Printer C','change');document.querySelector('#assignment-print-confirm').click();
      await wait(()=>!document.querySelector('#assignment-print-dialog').open);
      assert(printCalls.length===2&&printCalls[1].printer==='Printer C','Refresh did not allow a newly connected printer');
      assert(printCalls[0].generation.job_id===printCalls[1].generation.job_id,'Printer selection changed the issued assignment generation');
    } finally {
      window.fetch=realFetch;
      document.dispatchEvent(new CustomEvent('protokol:assignments-invalidated'));
      document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    }
    document.querySelector('#start-manual').click();
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
    await window.OZWorkspace.activateScope(firstPackage);
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
    return code if code else (0 if result["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
