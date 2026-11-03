'use strict';
(() => {
  let state={profiles:[],general_id:'',company_id:''},editing='',applying=false,busy=false;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const dialog=document.createElement('dialog');dialog.className='modal signer-dialog';dialog.id='signer-profiles';
  dialog.setAttribute('aria-labelledby','signers-title');
  dialog.innerHTML=`<div class="modal-heading"><h2 id="signers-title">Профілі підписантів</h2><button type="button" class="icon-button" data-signers-close aria-label="Закрити"><svg class="icon"><use href="#i-close"/></svg></button></div><div class="signers-layout"><aside><div class="signers-list" data-profile-list></div><button type="button" class="text-button" data-profile-new>+ Новий профіль</button><details class="signer-defaults"><summary>Підписи для нових листів</summary><label class="field"><span>Загальна супровідна</span><select data-default-general></select></label><label class="field"><span>Листи фірмам</span><select data-default-company></select></label></details></aside><form data-profile-form><div class="modal-body"><label class="field"><span>Назва профілю *</span><input name="name" maxlength="150" required placeholder="Наприклад, начальник лабораторії"></label><label class="field"><span>Посада *</span><textarea name="position" rows="3" maxlength="1000" required></textarea></label><label class="field"><span>Звання, ім’я та прізвище *</span><input name="signer" maxlength="500" required></label><label class="field"><span>Виконавець і телефон</span><input name="contact" maxlength="500"></label><p data-profile-message role="status"></p></div><div class="modal-footer"><button type="button" class="button quiet danger-text" data-profile-delete disabled>Видалити</button><button type="submit" class="button primary" data-profile-save>Зберегти профіль</button></div></form></div>`;
  document.body.append(dialog);
  const $=selector=>dialog.querySelector(selector),form=$('[data-profile-form]');
  async function request(url,options){
    const response=await fetch(url,options),data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:(data.detail||[]).map(item=>item.msg).join('; ')||'Не вдалося зберегти профіль.');
    return data;
  }
  function message(value,error=false){$('[data-profile-message]').textContent=value;$('[data-profile-message]').classList.toggle('error-text',error);}
  function options(){return '<option value="">Вручну</option>'+state.profiles.map(profile=>`<option value="${profile.id}">${esc(profile.name)}</option>`).join('');}
  function populate(){
    for(const select of document.querySelectorAll('[data-apply-profile]')){
      const value=select.value;select.innerHTML=options();select.value=state.profiles.some(profile=>profile.id===value)?value:'';
    }
    $('[data-profile-list]').innerHTML=state.profiles.length?state.profiles.map(profile=>`<button type="button" data-edit-profile="${profile.id}" class="profile-list-item ${profile.id===editing?'selected':''}"><strong>${esc(profile.name)}</strong><small>${esc(profile.signer)}</small></button>`).join(''):'<p class="empty-note">Збережіть підпис один раз, щоб обирати його в наступних листах.</p>';
    $('[data-default-general]').innerHTML=options();$('[data-default-general]').value=state.general_id;
    $('[data-default-company]').innerHTML=options();$('[data-default-company]').value=state.company_id;
  }
  function defaults(){
    if(!window.OZCoverLetters?.canApplyDefaults())return;
    for(const kind of ['general','company']){
      const profile=state.profiles.find(item=>item.id===state[kind+'_id']);
      const select=document.querySelector(`[data-apply-profile="${kind}"]`);if(select)select.value=profile?.id||'';
      for(const key of ['position','signer']){
        const field=document.querySelector(`[data-signer="${kind}_${key}"]`);
        if(field)field.value=profile?profile[key]:field.defaultValue;
      }
      const contact=document.querySelector(`[data-role-contact="${kind}"]`);
      if(contact)contact.value=profile?profile.contact:'';
    }
  }
  async function load(){state=await request('/api/signers');populate();defaults();return state;}
  function fill(profile){
    editing=profile?.id||'';
    for(const key of ['name','position','signer','contact'])form.elements.namedItem(key).value=profile?.[key]||'';
    $('[data-profile-delete]').disabled=!editing;message('');populate();
  }
  async function open(profile){
    try{await load();fill(profile||(state.profiles[0]||{}));if(!dialog.open)dialog.showModal();form.elements.namedItem('name').focus();}
    catch(error){if(!dialog.open)dialog.showModal();message(error.message,true);}
  }
  document.addEventListener('protokol:letters-rendered',populate);
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-manage-signers]'))open();
    const save=event.target.closest('[data-save-current-signer]');
    if(save){
      const section=save.closest('[data-letter]');
      const values=Object.fromEntries(['position','signer','contact'].map(key=>[key,section.querySelector(`[data-letter-field="${key}"]`).value.replace(/\n/g,' ')]));
      open({...values,name:values.signer.slice(0,150)});
    }
  });
  document.addEventListener('change',event=>{
    const select=event.target.closest('[data-apply-profile]');if(!select)return;
    const profile=state.profiles.find(item=>item.id===select.value);if(!profile)return;
    const section=select.closest('[data-letter]'),kind=select.dataset.applyProfile;
    applying=true;
    try{
      for(const key of ['position','signer','contact']){
        const field=section?section.querySelector(`[data-letter-field="${key}"]`):document.querySelector(key==='contact'?`[data-role-contact="${kind}"]`:`[data-signer="${kind}_${key}"]`);
        field.value=profile[key];field.dispatchEvent(new Event('input',{bubbles:true}));
      }
    }finally{applying=false;}
  });
  document.addEventListener('input',event=>{
    if(applying)return;
    const field=event.target;
    if(field.matches('[data-signer],[data-role-contact]')){
      const kind=field.dataset.roleContact||(field.dataset.signer.startsWith('general_')?'general':'company');
      document.querySelector(`[data-apply-profile="${kind}"]`).value='';
    }
    if(field.matches('[data-contact]'))for(const kind of ['general','company']){
      document.querySelector(`[data-role-contact="${kind}"]`).value=field.value;
      document.querySelector(`[data-apply-profile="${kind}"]`).value='';
    }
    if(field.matches('[data-letter-field="position"],[data-letter-field="signer"],[data-letter-field="contact"]')){
      const select=field.closest('[data-letter]').querySelector('[data-apply-profile]');if(select)select.value='';
    }
  });
  $('[data-signers-close]').onclick=()=>dialog.close();
  $('[data-profile-new]').onclick=()=>{fill({});form.elements.namedItem('name').focus();};
  $('[data-profile-list]').onclick=event=>{const item=event.target.closest('[data-edit-profile]');if(item)fill(state.profiles.find(profile=>profile.id===item.dataset.editProfile));};
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||!form.reportValidity())return;
    busy=true;$('[data-profile-save]').disabled=true;
    try{
      const values=Object.fromEntries(['name','position','signer','contact'].map(key=>[key,form.elements.namedItem(key).value.replace(/\n/g,' ')]));
      const result=await request('/api/signers'+(editing?'/'+editing:''),{method:editing?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
      await load();fill(result);message('Профіль збережено.');
    }catch(error){message(error.message,true);}finally{busy=false;$('[data-profile-save]').disabled=false;}
  };
  $('[data-profile-delete]').onclick=async()=>{
    if(busy||!editing||!window.confirm('Видалити цей профіль підписанта?'))return;
    busy=true;
    try{await request('/api/signers/'+editing,{method:'DELETE'});await load();fill(state.profiles[0]||{});message('Профіль видалено.');}
    catch(error){message(error.message,true);}finally{busy=false;}
  };
  for(const selector of ['[data-default-general]','[data-default-company]'])$(selector).onchange=async()=>{
    if(busy)return;busy=true;
    try{state=await request('/api/signer-defaults',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({general_id:$('[data-default-general]').value,company_id:$('[data-default-company]').value})});populate();defaults();message('Підписи для нових листів збережено.');}
    catch(error){populate();message(error.message,true);}finally{busy=false;}
  };
  window.OZSigners={populate,defaults};
  load().catch(error=>{message(error.message,true);document.querySelectorAll('[data-manage-signers]').forEach(button=>button.title=error.message);});
})();
