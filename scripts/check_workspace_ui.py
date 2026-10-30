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
  const wait=async fn=>{for(let i=0;i<400;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw new Error('UI readiness timeout');};
  const input=(selector,value,event='input')=>{const field=document.querySelector(selector);assert(field,'Missing field: '+selector);field.value=value;field.dispatchEvent(new Event(event,{bubbles:true}));};
  await wait(()=>window.__providers?.acts && window.__providers?.excel && window.__providers?.assignments && !document.querySelector('#workspace-save-status').textContent.includes('Завантажуємо'));
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
    await providers.excel.restore({records:[{id:'saved-excel',source:'act.pdf',values:{D:'001',E:'29.10.2026',G:'Test',M:'00001234',N:'12345678'},sources:{M:{kind:'manual',label:'Введено вручну'}}}],templateWasSelected:true,templateName:'old.xlsx'});
    const templates=(await(await fetch('/api/assignments/templates')).json()).templates;
    const template=templates.find(value=>value.sample_weight && value.standard && value.product_name);
    assert(template,'Missing assignment fixture');
    await providers.assignments.restore({source:'act.pdf',text:'Текст акта',initialNumber:'1400',items:[{id:'saved-assignment',product_name:template.product_name,standard:template.standard,departments:{[template.department]:{enabled:true,template_id:template.id,sample_weight:'7 кг',weight_edited:true}}}]});
    await window.OZWorkspace.flush();
    const saved=await(await fetch('/api/workspace/drafts')).json();
    assert(Object.keys(saved.sections).length===3,'All three drafts were not saved');
    assert(saved.sections.acts.data.common.customer==='Замовник Ω','Act edits were not saved');
    assert(saved.sections.excel.data.records[0].values.M==='00001234','Leading zeroes were lost');
    console.log('OZ_WORKSPACE_RELOAD');
  } else {
    await wait(()=>document.querySelectorAll('.workspace-draft').length===3);
    for (let i=0;i<3;i++) {
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
    document.dispatchEvent(new CustomEvent('protokol:navigate',{detail:{name:'acts'}}));
    input('[data-field="protocol_date"]','31.02.2026');
    document.querySelector('#workspace-review').click();
    assert(document.querySelector('.workspace-modal[open]'),'Unified review did not open');
    const issue=[...document.querySelectorAll('.workspace-issue')].find(row=>row.textContent.includes('некоректна дата протоколу'));
    assert(issue,'Invalid date is missing from unified review'); issue.querySelector('button').click();
    await new Promise(resolve=>setTimeout(resolve,150));
    assert(document.activeElement.dataset.field==='protocol_date','Review link did not focus the incorrect date');
    await window.OZWorkspace.flush();
    console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:true,checks:['three durable drafts','reload and explicit restore','leading zeroes','manual provenance','confirmation reset','workbook reselect guard','assignment manual weight','linked date review']}));
  }
} catch(error) {console.log('OZ_WORKSPACE_RESULT:'+JSON.stringify({ok:false,error:error.stack||String(error)}));}})();'''


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
                application.exit(0 if result["ok"] else 1)
            elif "Error" in message:
                print("JavaScript:", message, flush=True)

    page = Page(profile, application)
    view = QWebEngineView(); view.setPage(page)
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
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code if code else (0 if result["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
