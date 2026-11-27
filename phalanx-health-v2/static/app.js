(function(){
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
/* ---- dark / large text ---- */
const root=document.documentElement;
const dark=$('#darkBtn');
function paintDark(){}
if(dark){ paintDark(); dark.onclick=()=>{ const t=root.dataset.theme==='dark'?'light':'dark'; root.dataset.theme=t;
  try{localStorage.setItem('theme',t)}catch(e){}; paintDark(); }; }
const large=$('#largeBtn');
if(large){ const p=()=>large.textContent=root.classList.contains('large')?'On':'Off'; p();
  large.onclick=()=>{ root.classList.toggle('large'); try{localStorage.setItem('large',root.classList.contains('large')?'1':'0')}catch(e){}; p(); }; }
/* ---- notification toggles ---- */
$$('.toggle[data-key]').forEach(t=>t.onclick=()=>{ t.classList.toggle('off'); const on=!t.classList.contains('off');
  fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:t.dataset.key,on})}); });
/* ---- check-in helpers ---- */
const ta=$('#complaint');
if(ta){
  const words=["chest pain","difficulty breathing","shortness of breath","can't breathe","cannot breathe","unconscious","fainted","seizure","stroke","heart attack","severe bleeding","heavy bleeding","overdose","choking"];
  ta.addEventListener('input',()=>{ if(words.some(w=>ta.value.toLowerCase().includes(w))) $('#urg-em').checked=true; });
  $$('.quick button').forEach(b=>b.onclick=()=>{ ta.value=(ta.value?ta.value+', ':'')+b.textContent; ta.focus(); });
}
/* ---- password strength ---- */
const pw=$('#regpw'), hint=$('#pwhint');
if(pw&&hint) pw.addEventListener('input',()=>{ let s=0; const v=pw.value;
  if(v.length>=6)s++; if(v.length>=10)s++; if(/[A-Z]/.test(v)&&/[a-z]/.test(v))s++; if(/\d/.test(v)&&/[^A-Za-z0-9]/.test(v))s++;
  hint.textContent='Password strength: '+(!v?'—':s<=1?'weak':s<=2?'medium':'strong'); });
/* ---- delete confirm ---- */
$$('[data-confirm]').forEach(b=>b.onclick=()=>{ const box=$('#confirm-'+b.dataset.confirm); if(box){ $$('.confirm').forEach(x=>x.hidden=true); box.hidden=false; box.scrollIntoView({block:'nearest'}); }});
$$('[data-cancel]').forEach(b=>b.onclick=()=>b.closest('.confirm').hidden=true);
/* ---- live refresh ---- */
const bc=$('#board-card');
if(bc) setInterval(()=>fetch('/partial/board').then(r=>r.text()).then(h=>{bc.innerHTML=h}).catch(()=>{}),5000);
const me=$('[data-stage]');
if(me) setInterval(()=>fetch('/api/status').then(r=>r.json()).then(j=>{ if(String(j.stage)!==me.dataset.stage||String(j.ahead)!==me.dataset.ahead) location.reload(); }).catch(()=>{}),6000);
/* ---- no-show countdown ---- */
const cd=$('#noshow-timer');
if(cd){ let s=120; const t=setInterval(()=>{ s--; if(s<0){clearInterval(t);return}
  cd.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0'); },1000); }
/* ---- prescription low-stock badge ---- */
const rx=$('#rx'), low=$('#lowstock');
if(rx&&low){ const f=()=>{ const o=rx.selectedOptions[0]; low.hidden=!(o&&o.dataset.low==='1'); }; rx.onchange=f; f(); }
/* ---- benchmark ---- */
const bb=$('#benchBtn');
if(bb) bb.onclick=()=>{ const out=$('#benchOut'); out.textContent='Running…';
  fetch('/benchmark',{method:'POST'}).then(r=>r.json()).then(j=>{
    out.innerHTML='Results ('+j.n+' patients): '+j.results.map(r=>r.name+' '+r.ms+' ms ('+r.big_o+')').join(' · '); }); };
})();
