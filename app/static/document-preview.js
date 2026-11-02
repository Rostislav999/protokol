'use strict';
(() => {
  function textPoint(root, offset) {
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let node,last=null;
    while ((node=walker.nextNode())) {
      last=node;
      if (offset<=node.length) return [node,offset];
      offset-=node.length;
    }
    return last?[last,last.length]:[root,0];
  }
  function fragment(node,start,end) {
    const range=document.createRange();
    range.setStart(...textPoint(node,start));range.setEnd(...textPoint(node,end));
    const result=node.cloneNode(false);result.append(range.cloneContents());return result;
  }
  function paginate(container,className) {
    for (const original of [...container.querySelectorAll(`section.${className}`)]) {
      const article=original.querySelector('article');
      if (!article) continue;
      const style=getComputedStyle(original);
      const height=parseFloat(style.minHeight)||parseFloat(style.height);
      if (!height || height<300) continue;
      const bottomPadding=parseFloat(style.paddingBottom)||0;
      const queue=[...article.childNodes];article.replaceChildren();
      let page=original,body=article,created=1;
      body.style.minHeight='0';body.style.height='auto';
      const fits=node=>node.getBoundingClientRect().bottom+(parseFloat(getComputedStyle(node).marginBottom)||0)
        <=page.getBoundingClientRect().top+height-bottomPadding+1;
      const nextPage=()=>{
        if (++created>60) throw new Error('Документ надто довгий для перегляду. Його можна завантажити у Word.');
        const next=original.cloneNode(false);
        for (const child of original.children) {
          const copy=child.cloneNode(child!==article);
          if (child===article) {body=copy;body.style.minHeight='0';body.style.height='auto';}
          next.append(copy);
        }
        page.after(next);page=next;
      };
      while (queue.length) {
        const node=queue.shift();body.append(node);
        if (node.nodeType!==Node.ELEMENT_NODE || fits(node)) continue;
        if (body.children.length>1) {node.remove();nextPage();queue.unshift(node);continue;}
        if (node.tagName==='P' && node.textContent.length>1) {
          const length=node.textContent.length;
          let low=1,high=length-1,cut=0;
          while(low<=high) {
            const middle=Math.floor((low+high)/2),probe=fragment(node,0,middle);
            node.replaceWith(probe);const ok=fits(probe);probe.replaceWith(node);
            if(ok){cut=middle;low=middle+1;}else high=middle-1;
          }
          if(cut) {
            const word=node.textContent.lastIndexOf(' ',cut);
            if(word>cut/2)cut=word+1;
            const rest=fragment(node,cut,length);node.replaceWith(fragment(node,0,cut));
            nextPage();queue.unshift(rest);
          }
        } else if(node.tagName==='TABLE' && node.rows.length>1) {
          const rows=[...node.rows];
          let cut=0;
          const tableFor=items=>{
            const table=node.cloneNode(false);
            for(const child of node.children)if(child.tagName==='COLGROUP')table.append(child.cloneNode(true));
            const tbody=document.createElement('tbody');items.forEach(row=>tbody.append(row.cloneNode(true)));table.append(tbody);return table;
          };
          for(let i=1;i<rows.length;i++){
            const probe=tableFor(rows.slice(0,i));node.replaceWith(probe);const ok=fits(probe);probe.replaceWith(node);
            if(!ok)break;cut=i;
          }
          if(cut){node.replaceWith(tableFor(rows.slice(0,cut)));nextPage();queue.unshift(tableFor(rows.slice(cut)));}
        }
      }
    }
    const pages=[...container.querySelectorAll(`section.${className}`)];
    pages.forEach((page,index)=>{
      page.classList.add('word-page');page.setAttribute('aria-label',`Сторінка ${index+1} з ${pages.length}`);
    });
    return pages.length;
  }
  function fit(target,value='fit') {
    target.dataset.zoom=value;
    const stack=target.querySelector('.word-sheet-stack'),page=stack?.querySelector('.word-page');
    if(!page)return;
    const width=parseFloat(getComputedStyle(page).width)||794;
    stack.style.zoom=value==='fit'?Math.max(.2,Math.min(1,(target.clientWidth-32)/width)):Number(value)/100;
  }
  async function render(data,target,{className='ozdocx'}={}) {
    const revision=(target.previewRevision||0)+1;target.previewRevision=revision;
    const stage=document.createElement('div');stage.className='word-preview-staging';
    const styles=document.createElement('div'),content=document.createElement('div');
    stage.append(styles,content);target.append(stage);
    try {
      await docx.renderAsync(data,content,styles,{className,inWrapper:false,breakPages:true,
        ignoreLastRenderedPageBreak:true,useBase64URL:true,renderAltChunks:false,experimental:true});
      // The document pane is read-only; links must not navigate the app window.
      content.querySelectorAll('a').forEach(link=>{link.removeAttribute('href');link.removeAttribute('target');});
      content.querySelectorAll('script,iframe,object,embed').forEach(node=>node.remove());
      await document.fonts.ready;
      const pages=paginate(content,className);
      if(target.previewRevision!==revision){stage.remove();return null;}
      stage.className='word-sheet-stack';
      target.replaceChildren(stage);
      if(!target.previewObserver){target.previewObserver=new ResizeObserver(()=>fit(target,target.dataset.zoom||'fit'));target.previewObserver.observe(target);}
      fit(target,target.dataset.zoom||'fit');
      return pages;
    }catch(error){stage.remove();throw error;}
  }
  function invalidate(target){if(target)target.previewRevision=(target.previewRevision||0)+1;}
  window.OZDocumentPreview={render,fit,invalidate};
})();
