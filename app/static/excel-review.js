'use strict';

(() => {
  const zoomSteps = [20, 35, 50, 75, 100, 125, 150, 200, 300, 400];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[char]
  ));

  function paneMarkup(id, title) {
    return `<section class="excel-review-pane" data-review-pane="${id}" aria-label="${title}">
      <div class="excel-review-source">
        <div><h3>${title}</h3><p data-document-name></p></div>
        <a class="text-button" data-document-original hidden target="_blank" rel="noopener noreferrer" aria-label="Відкрити оригінал: ${title}">Оригінал</a>
      </div>
      <div class="excel-review-tools" role="group" aria-label="Перегляд: ${title}">
        <button type="button" data-page-step="-1" title="Попередня сторінка" aria-label="Попередня сторінка">‹</button>
        <label class="excel-review-page-number"><input type="number" min="1" value="1" data-page-number aria-label="Сторінка: ${title}"><span data-page-count>/ —</span></label>
        <button type="button" data-page-step="1" title="Наступна сторінка" aria-label="Наступна сторінка">›</button>
        <button type="button" data-zoom-step="-1" title="Зменшити" aria-label="Зменшити">−</button>
        <select data-zoom aria-label="Масштаб: ${title}">
          <option value="page">Ціла сторінка</option><option value="width">За шириною</option>
          ${zoomSteps.map(scale => `<option value="${scale}">${scale}%</option>`).join('')}
        </select>
        <button type="button" data-zoom-step="1" title="Збільшити" aria-label="Збільшити">+</button>
        <button type="button" data-rotate title="Повернути на 90°" aria-label="Повернути на 90°">↻</button>
      </div>
      <div class="excel-review-scroll" tabindex="0" aria-label="Сторінка: ${title}">
        <p class="excel-review-message" data-document-status role="status"></p>
        <div class="excel-review-document" hidden></div>
      </div>
    </section>`;
  }

  function fileURL(source, route = '', extra = {}) {
    const params = new URLSearchParams({scope:source.scope, ...extra});
    if (source.member) params.set('member',source.member);
    return '/api/workbench/files/' + encodeURIComponent(source.id) + route + '?' + params;
  }

  class DocumentPane {
    constructor(root, request) {
      this.$ = selector => root.querySelector(selector);
      this.request = request;
      this.surface = this.$('.excel-review-document');
      this.scroll = this.$('.excel-review-scroll');
      this.zoom = this.$('[data-zoom]');
      this.zoom.onchange = () => this.resize(true);
      root.querySelectorAll('[data-zoom-step]').forEach(button => {
        button.onclick = () => this.changeZoom(Number(button.dataset.zoomStep));
      });
      root.querySelectorAll('[data-page-step]').forEach(button => {
        button.onclick = () => this.goTo(this.page + Number(button.dataset.pageStep));
      });
      this.$('[data-page-number]').onchange = event => {
        const page = event.target;
        if (page.value && page.checkValidity()) this.goTo(page.valueAsNumber);
        else page.value = this.page;
      };
      this.$('[data-rotate]').onclick = () => {
        this.rotation = (this.rotation + 270) % 360;
        this.load();
      };
      this.scroll.addEventListener('wheel',event => {
        if (!event.ctrlKey || !this.visiblePage) return;
        event.preventDefault();
        if (event.timeStamp - (this.lastWheel || 0) > 70 && event.deltaY) {
          this.lastWheel = event.timeStamp;
          this.changeZoom(event.deltaY < 0 ? 1 : -1);
        }
      },{passive:false});
      new ResizeObserver(() => this.resize()).observe(this.scroll);
      this.reset('');
    }

    reset(message, filename = '') {
      this.abort?.abort();
      this.abort = null;
      window.OZDocumentPreview.invalidate(this.surface);
      this.source = this.visiblePage = this.wordPages = null;
      this.page = 1;
      this.pages = this.rotation = 0;
      this.surface.replaceChildren();
      this.surface.hidden = true;
      this.$('[data-document-name]').textContent = filename;
      this.$('[data-document-name]').title = filename;
      this.$('[data-document-original]').hidden = true;
      this.status(message);
      this.updateControls();
    }

    show(source, page = 1) {
      this.reset('',source.member?.split('/').at(-1) || source.filename);
      this.source = source;
      this.page = page;
      this.kind = /\.docx$/i.test(source.member || source.filename) ? 'word' :
        /\.txt$/i.test(source.filename) ? 'text' : 'scan';
      this.original = fileURL(source,source.section === 'excel' ? '/protocol' : '');
      const original = this.$('[data-document-original]');
      original.href = this.original;
      original.hidden = false;
      return this.load();
    }

    status(message) {
      const node = this.$('[data-document-status]');
      node.textContent = message;
      node.hidden = !message;
    }

    goTo(page) {
      if (!this.source || page === this.page || page < 1 || page > (this.pages || 20)) return;
      this.page = page;
      this.load();
    }

    async load() {
      this.abort?.abort();
      const controller = new AbortController();
      this.abort = controller;
      const {signal} = controller;
      this.status('Завантажуємо сторінку…');
      this.visiblePage = null;
      this.surface.hidden = true;
      this.updateControls();
      try {
        if (this.kind === 'scan') {
          const angle = this.source.angles?.get(this.page) ?? 0;
          const rotation = (angle + this.rotation) % 360;
          const response = await this.request(fileURL(this.source,'/preview',{page:this.page,rotation}),{signal});
          const blob = await response.blob();
          if (signal.aborted) return;
          this.pages = Number(response.headers.get('X-Page-Count')) || 1;
          const url = URL.createObjectURL(blob);
          try {
            const image = new Image();
            image.alt = `Сторінка ${this.page}`;
            image.draggable = false;
            image.src = url;
            await image.decode();
            if (signal.aborted) return;
            this.enablePan(image);
            this.surface.replaceChildren(image);
            this.visiblePage = image;
          } finally { URL.revokeObjectURL(url); }
        } else if (this.kind === 'word') {
          if (!this.wordPages) {
            const response = await this.request(this.original,{signal});
            const data = await response.arrayBuffer();
            if (signal.aborted) return;
            this.surface.hidden = false;
            await window.OZDocumentPreview.render(data,this.surface,{className:'ozdocx-review',adaptive:false});
            if (signal.aborted) return;
            this.wordPages = [...this.surface.querySelectorAll('.word-page')];
            this.pages = this.wordPages.length;
            if (!this.pages) throw new Error('У документі немає сторінок для перегляду.');
          }
          this.page = Math.min(this.page,this.pages);
          this.wordPages.forEach((element,index) => {element.hidden = index !== this.page - 1;});
          this.visiblePage = this.wordPages[this.page - 1];
        } else {
          const response = await this.request(this.original,{signal});
          const text = await response.text();
          if (signal.aborted) return;
          const pre = document.createElement('pre');
          pre.textContent = text;
          this.surface.replaceChildren(pre);
          this.pages = 1;
          this.visiblePage = pre;
        }
        this.surface.hidden = false;
        this.status('');
        this.scroll.scrollTo(0,0);
        this.resize();
      } catch (error) {
        if (signal.aborted) return;
        this.surface.hidden = true;
        this.status(error.message);
      } finally {
        if (this.abort === controller) {
          this.abort = null;
          this.updateControls();
        }
      }
    }

    resize(center = false) {
      const page = this.visiblePage;
      if (!page || !this.scroll.clientWidth) return;
      const width = page.naturalWidth || page.offsetWidth;
      const height = page.naturalHeight || page.offsetHeight;
      const fitWidth = Math.min(1,(this.scroll.clientWidth - 24) / width);
      const fitHeight = Math.min(1,(this.scroll.clientHeight - 24) / height);
      const scale = this.zoom.value === 'page' ? Math.min(fitWidth,fitHeight) :
        this.zoom.value === 'width' ? fitWidth : Number(this.zoom.value) / 100;
      const x = (this.scroll.scrollLeft + this.scroll.clientWidth / 2) / this.scroll.scrollWidth;
      const y = (this.scroll.scrollTop + this.scroll.clientHeight / 2) / this.scroll.scrollHeight;
      this.scale = Math.max(.08,scale);
      page.style.zoom = this.scale;
      if (center) this.scroll.scrollTo(x*this.scroll.scrollWidth - this.scroll.clientWidth/2,
        y*this.scroll.scrollHeight - this.scroll.clientHeight/2);
      this.updateControls();
    }

    changeZoom(direction) {
      const scale = this.scale * 100;
      const next = direction > 0 ? zoomSteps.find(step => step > scale + .1) :
        zoomSteps.findLast(step => step < scale - .1);
      if (next) {
        this.zoom.value = String(next);
        this.resize(true);
      }
    }

    updateControls() {
      const input = this.$('[data-page-number]');
      input.value = this.page;
      input.max = Math.max(1,this.pages);
      input.disabled = !this.pages || !!this.abort;
      this.$('[data-page-count]').textContent = `/ ${this.pages || '—'}`;
      this.$('[data-page-step="-1"]').disabled = input.disabled || this.page <= 1;
      this.$('[data-page-step="1"]').disabled = input.disabled || this.page >= this.pages;
      this.$('[data-rotate]').disabled = this.kind !== 'scan' || !this.visiblePage;
      this.zoom.disabled = !this.visiblePage;
      this.$('[data-zoom-step="-1"]').disabled = this.zoom.disabled || this.scale <= zoomSteps[0]/100;
      this.$('[data-zoom-step="1"]').disabled = this.zoom.disabled || this.scale >= zoomSteps.at(-1)/100;
    }

    enablePan(image) {
      let start;
      image.onpointerdown = event => {
        if (event.button !== 0 || event.pointerType !== 'mouse') return;
        start = [event.clientX,event.clientY,this.scroll.scrollLeft,this.scroll.scrollTop];
        image.setPointerCapture(event.pointerId);
        image.classList.add('dragging');
      };
      image.onpointermove = event => {
        if (start) this.scroll.scrollTo(start[2]+start[0]-event.clientX,start[3]+start[1]-event.clientY);
      };
      image.onlostpointercapture = () => {
        start = null;
        image.classList.remove('dragging');
      };
    }
  }

  class ExcelReview {
    constructor({request, numberKey, updateValue}) {
      this.request = request;
      this.numberKey = numberKey;
      this.updateValue = updateValue;
      this.dialog = document.createElement('dialog');
      this.dialog.className = 'excel-review-dialog';
      this.dialog.setAttribute('aria-labelledby','excel-review-title');
      this.dialog.innerHTML = `<header class="excel-review-header">
        <h2 id="excel-review-title">Звірка сканів</h2>
        <nav class="excel-review-nav" aria-label="Протоколи">
          <button type="button" class="button secondary" data-protocol-step="-1" title="Попередній протокол (Alt+←)" aria-label="Попередній протокол">←</button>
          <select data-protocol-index aria-label="Протокол зі списку" autofocus></select>
          <span data-protocol-count aria-live="polite"></span>
          <button type="button" class="button secondary" data-protocol-step="1" title="Наступний протокол (Alt+→)" aria-label="Наступний протокол">→</button>
        </nav>
        <button type="button" class="icon-button" data-review-close aria-label="Закрити звірку">×</button>
      </header>
      <div class="excel-review-panes">${paneMarkup('act','Акт відбору')}${paneMarkup('protocol','Протокол')}</div>
      <details class="excel-review-fields">
        <summary>Поля звірки <span data-fields-count></span></summary>
        <div class="excel-review-field-body">
          <div class="excel-review-field-header">
            <label data-review-position-label>Позиція акта <select data-review-position></select></label>
            <p data-comparison-status role="status"></p>
          </div>
          <div class="excel-review-table"></div>
        </div>
      </details>`;
      document.body.append(this.dialog);
      this.$ = selector => this.dialog.querySelector(selector);
      this.act = new DocumentPane(this.$('[data-review-pane="act"]'),request);
      this.protocol = new DocumentPane(this.$('[data-review-pane="protocol"]'),request);
      this.$('[data-review-close]').onclick = () => this.close();
      this.$('[data-protocol-index]').onchange = event => this.showProtocol(Number(event.target.value));
      this.dialog.querySelectorAll('[data-protocol-step]').forEach(button => {
        button.onclick = () => this.showProtocol(this.index + Number(button.dataset.protocolStep));
      });
      this.dialog.addEventListener('keydown',event => {
        if (!event.altKey || !['ArrowLeft','ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        this.showProtocol(this.index + (event.key === 'ArrowLeft' ? -1 : 1));
      });
      this.dialog.addEventListener('close',() => {
        if (!this.dialog.open) this.clear();
      });
      this.$('[data-review-position]').onchange = event => {
        this.selections.set(this.record.id,event.target.value);
        this.updateComparison(true);
      };
      this.$('.excel-review-table').oninput = event => {
        const column = event.target.dataset.reviewValue;
        if (!column || !this.record) return;
        this.updateValue(this.record,column,event.target.value);
        if (column === 'D' || column === 'G') this.setOption(this.index);
        this.updateComparison();
      };
      this.$('.excel-review-table').onchange = event => {
        if (event.target.dataset.reviewValue === 'D' && this.record && !this.selections.has(this.record.id)) {
          this.choosePosition();
          this.updateComparison(true);
        }
      };
    }

    close() {
      if (!this.dialog.open) return;
      this.clear();
      this.dialog.close();
    }

    clear() {
      this.loading?.abort();
      this.loading = this.record = null;
      this.act.reset('');
      this.protocol.reset('');
    }

    async open(records, fields, first, act) {
      this.clear();
      this.records = records.filter(record => record.original);
      if (!this.records.length) return;
      this.fields = fields;
      this.index = Math.max(0,this.records.indexOf(first));
      this.selections = new Map();
      this.files = this.positions = [];
      this.actData = act;
      this.scope = window.OZWorkspace.scope;
      const controller = new AbortController();
      this.loading = controller;
      const {signal} = controller;
      const selector = this.$('[data-protocol-index]');
      selector.replaceChildren(...this.records.map((_,index) => new Option('',String(index))));
      this.records.forEach((_,index) => this.setOption(index));
      selector.value = String(this.index);
      this.updateNav(true);
      this.$('[data-fields-count]').textContent = '';
      this.$('[data-review-position-label]').hidden = true;
      this.$('[data-comparison-status]').textContent = '';
      this.$('.excel-review-table').replaceChildren();
      this.$('.excel-review-fields').open = false;
      this.act.reset(act.source ? 'Завантажуємо акт…' : 'Завантажте скан акта у розділі Excel.',act.source);
      this.protocol.reset('Завантажуємо протокол…',this.records[this.index].source);
      if (!this.dialog.open) this.dialog.showModal();

      const files = this.request('/api/workbench/files?' + new URLSearchParams({scope:this.scope}),{signal})
        .then(response => response.json());
      const positions = act.items?.length ? this.request('/api/excel/from-act',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({common:act.common,items:act.items,source:act.source}),signal
      }).then(response => response.json()) : Promise.resolve({records:[]});
      const [originals, converted] = await Promise.allSettled([files,positions]);
      if (signal.aborted) return;
      this.loading = null;
      this.files = originals.value?.files || [];
      this.positions = converted.value?.records || [];
      this.originalsError = originals.reason?.message;
      this.positionsError = converted.reason?.message;
      const position = this.$('[data-review-position]');
      position.replaceChildren(new Option('Оберіть позицію',''), ...this.positions.map(({values},index) =>
        new Option(`${index+1}. ${[values.D,values.G].filter(Boolean).join(' · ')}`,String(index))));
      this.$('[data-review-position-label]').hidden = !this.positions.length;
      this.sourceAct = this.files.find(file => file.section === 'acts' && file.kind === 'source' &&
        (act.sourceGroup ? file.fingerprint === act.sourceGroup : file.active && file.filename === act.source));
      this.showProtocol(this.index);
    }

    setOption(index) {
      const {source,values} = this.records[index];
      this.$('[data-protocol-index]').options[index].textContent =
        [values.D,values.G || source].filter(Boolean).join(' · ');
    }

    updateNav(waiting = false) {
      this.$('[data-protocol-index]').disabled = waiting || this.records.length < 2;
      this.$('[data-protocol-step="-1"]').disabled = waiting || this.index === 0;
      this.$('[data-protocol-step="1"]').disabled = waiting || this.index + 1 === this.records.length;
      this.$('[data-protocol-count]').textContent = `${this.index+1} / ${this.records.length}`;
    }

    showProtocol(index) {
      if (this.loading || !this.dialog.open || !this.records[index] || this.record === this.records[index]) return;
      this.index = index;
      const record = this.record = this.records[index];
      this.$('[data-protocol-index]').value = String(index);
      this.updateNav();
      const original = record.original;
      const source = this.files.find(file => file.section === 'excel' && file.kind === 'source' &&
        file.filename === original.filename && file.digest === original.digest && file.fingerprint === original.group);
      if (source) this.protocol.show({...source,scope:this.scope,member:original.member});
      else this.protocol.reset(this.originalsError || 'Оригінал цього протоколу не знайдено.',record.source);
      this.choosePosition();
      if (!this.act.source) {
        if (this.sourceAct) {
          const angles = new Map((this.actData.audit?.pages || []).map(({page,angle}) => [page,angle]));
          const page = this.selectedLocation()?.page || 1;
          this.act.show({...this.sourceAct,scope:this.scope,angles},this.sourceAct.filename.toLowerCase().endsWith('.docx') ? 1 : page);
        } else if (this.actData.source) {
          this.act.reset(this.originalsError || 'Оригінал акта не знайдено. Завантажте скан у розділі Excel.',this.actData.source);
        }
      }
      this.renderFields();
      this.updateComparison(true);
    }

    choosePosition() {
      const number = this.numberKey(this.record.values.D);
      const matches = this.positions.flatMap((item,index) => number && this.numberKey(item.values.D) === number ? [index] : []);
      this.$('[data-review-position]').value = this.selections.get(this.record.id) ??
        (matches.length === 1 ? String(matches[0]) : '');
    }

    selectedLocation() {
      const position = this.$('[data-review-position]').value;
      return position === '' ? null : this.actData.items?.[Number(position)]?.source_location;
    }

    renderFields() {
      this.$('.excel-review-table').innerHTML = `<table>
        <thead><tr><th scope="col">Поле</th><th scope="col">Акт відбору</th>
          <th scope="col">Протокол · можна виправити</th><th scope="col">Звірка</th></tr></thead>
        <tbody>${this.fields.map(field => `<tr data-review-field="${field.column}">
          <th scope="row"><label for="excel-review-value-${field.column}">${esc(field.label)}</label></th>
          <td data-act-value></td><td><input id="excel-review-value-${field.column}" data-review-value="${field.column}"
            ${field.type === 'number' ? 'inputmode="decimal"' : ''} maxlength="4000" value="${esc(this.record.values[field.column])}"></td>
          <td data-field-status></td></tr>`).join('')}</tbody></table>`;
      this.$('.excel-review-field-body').scrollTop = 0;
    }

    normalize(field, value) {
      const text = String(value || '').trim();
      if (field.column === 'D') return this.numberKey(text);
      if (field.type === 'number' && text) {
        const amount = Number(text.replace(/\s/g,'').replace(',','.'));
        if (Number.isFinite(amount)) return String(amount);
      }
      return text.normalize('NFC').replace(/\s+/g,' ').toLocaleLowerCase('uk-UA');
    }

    updateComparison(jump = false) {
      const position = this.$('[data-review-position]').value;
      const values = position === '' ? null : this.positions[Number(position)].values;
      const location = this.selectedLocation();
      if (jump && location?.page && this.act.kind === 'scan') this.act.goTo(location.page);
      this.$('[data-comparison-status]').textContent = values ? '' : this.positionsError ||
        (this.positions.length ? 'Оберіть позицію акта для звірки.' : 'У поточному акті немає позицій для звірки.');
      let differences = 0;
      let missing = 0;
      for (const field of this.fields) {
        const row = this.$(`[data-review-field="${field.column}"]`);
        row.querySelector('[data-act-value]').textContent = values?.[field.column] || '—';
        const left = this.normalize(field,values?.[field.column]);
        const right = this.normalize(field,this.record.values[field.column]);
        const empty = values && Boolean(left) !== Boolean(right);
        const different = values && left && right && left !== right;
        if (empty) missing++;
        if (different) differences++;
        row.classList.toggle('excel-review-difference',Boolean(empty || different));
        row.querySelector('[data-field-status]').textContent = different ? 'Розбіжність' : empty ? 'Бракує даних' :
          values && left ? 'Збіг' : '—';
      }
      this.$('[data-fields-count]').textContent = values ?
        ` · Розбіжності: ${differences} · Бракує даних: ${missing}` : '';
    }
  }

  window.OZExcelReview = ExcelReview;
})();
