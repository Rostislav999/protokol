"""Exercise real drafts, provenance, and linked review in the embedded renderer.

Run from the repository with: python -m scripts.check_workspace_ui
Requires the Windows build dependencies (PySide6), never Word or a printer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


SCRIPT = r'''void (async () => {try {
  const assert=(value,message)=>{if(!value)throw new Error(message);};
  const wait=async fn=>{for(let i=0;i<400;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw new Error('UI readiness timeout: '+fn.toString());};
  const input=(selector,value,event='input')=>{const field=document.querySelector(selector);assert(field,'Missing field: '+selector);field.value=value;field.dispatchEvent(new Event(event,{bubbles:true}));};
  await wait(()=>window.__providers?.acts && window.__providers?.excel && window.__providers?.assignments && window.__providers?.cover_letters && !document.querySelector('#workspace-save-status').textContent.includes('Завантажуємо'));
  const providers=window.__providers;
  if (!__RESTORE_PHASE__) {
    await wait(()=>document.querySelector('#nav-template-count').textContent==='22');
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
    input('[data-text-size]','large','change');
    await wait(()=>!document.querySelector('[data-text-size]').disabled);
    assert((await(await fetch('/api/preferences')).json()).text_size==='large','Text size preference was not saved');
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
    assert(providers.excel.issues().some(issue=>issue.selector==='#excel-template' && issue.level==='error'),'Missing source workbook did not block export');
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
    console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:true,checks:['four durable drafts','reload and explicit restore','leading zeroes','manual provenance','confirmation reset','workbook reselect guard','assignment manual weight','whole-library template selection and search','manual template restore and re-review','searchable template picker with Word preview','dedicated cover letter section','live A4 preview','persistent action bar','cover letter editor before download','cover letter autosave and restore','outgoing register and status changes','saved signer profiles','editable copy from outgoing history','persistent readable text size','linked date review']}));
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
    phase = {"restore": False}
    profile = QWebEngineProfile(application)

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            if message == "OZ_WORKSPACE_RELOAD":
                phase["restore"] = True
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
    page.loadFinished.connect(lambda success: page.runJavaScript(SCRIPT.replace("__RESTORE_PHASE__", "true" if phase["restore"] else "false")) if success else application.exit(2))
    QTimer.singleShot(90000, lambda: application.exit(3))
    with tempfile.TemporaryDirectory(prefix="oz-workspace-ui-") as temporary:
        with LocalServer(create_app(Path(temporary) / "data")) as server:
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
