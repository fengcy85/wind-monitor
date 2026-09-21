let allNews=[],allSources=[],meta={},weeklyFocus={items:[]},focusTag="";
const TAG_ORDER=["竞对资讯","招标采购","市场机制","电网建设","宏观政策","审批政策"];
const $=id=>document.getElementById(id);
const esc=(s="")=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
function ageDays(d){return Math.floor((new Date()-new Date(d+"T00:00:00"))/86400000)}
function setOptions(el,vals,label){
  const old=el.value;el.innerHTML=`<option value="">${label}</option>`;
  [...new Set(vals.filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN')).forEach(v=>{
    const o=document.createElement("option");o.value=v;o.textContent=v;el.appendChild(o)
  });el.value=old;
}
function withinDays(item,days){if(days>=9999)return true;const a=ageDays(item.published_at);return a>=0&&a<=days}
function tagChips(tags=[]){return tags.map(t=>`<span class="tag-chip">${esc(t)}</span>`).join("")}
function renderNews(){
  const q=$("searchInput").value.trim().toLowerCase();
  const tag=$("tagFilter").value,country=$("countryFilter").value,source=$("sourceFilter").value;
  const days=Number($("daysFilter").value||90);
  const data=allNews.filter(n=>{
    const hay=[n.country,n.source_name,n.title,n.translated_title,...(n.tags||[])].join(" ").toLowerCase();
    return withinDays(n,days)&&(!q||hay.includes(q))&&(!tag||(n.tags||[]).includes(tag))&&(!country||n.country===country)&&(!source||n.source_name===source);
  });
  $("newsTableBody").innerHTML=data.map(n=>`<tr>
    <td><span class="country-badge">${esc(n.country||"")}</span></td>
    <td class="source-cell">${esc(n.source_name||"")}</td>
    <td class="title-cell">${esc(n.title||"")}</td>
    <td class="translation-cell">
      <div>${n.translated_title?esc(n.translated_title):'<span class="translation-empty">—</span>'}</div>
      <div class="tag-row">${tagChips(n.tags||[])}</div>
    </td>
    <td class="date-cell">${esc(n.published_at||"")}</td>
    <td class="link-cell"><a class="open-link" href="${esc(n.url||'#')}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a></td>
  </tr>`).join("");
  $("emptyState").classList.toggle("hidden",data.length>0);
  $("weekCount").textContent=allNews.filter(n=>withinDays(n,7)).length;
  $("totalCount").textContent=allNews.length;
  $("resultInfo").textContent=`当前显示 ${data.length} 条 · 战略监测库共 ${allNews.length} 条`;
}
function stateLabel(s){if(s==="success")return["抓取成功","success"];if(s==="no_news")return["暂无相关新闻","no_news"];if(s==="error")return["抓取异常","error"];return["尚未运行","pending"]}
function renderSources(){
  const c={success:0,no_news:0,error:0};allSources.forEach(s=>{if(c[s.status]!==undefined)c[s.status]++});
  $("successCount").textContent=c.success;$("noNewsCount").textContent=c.no_news;$("errorCount").textContent=c.error;
  $("sourceGrid").innerHTML=allSources.map(s=>{
    const [label,cls]=stateLabel(s.status);const err=s.error?`<br><small class="bad-text">${esc(s.error)}</small>`:"";
    const kept=s.strategic_kept_total!==undefined?`<br><small><b>战略监测库：</b>${Number(s.strategic_kept_total||0)} 条</small>`:"";
    return `<div class="source-card"><div class="source-top"><strong>${esc(s.name)}</strong><span class="state ${cls}">${label}</span></div>
      <small>${esc(s.country)} · ${esc(s.role||"")}</small><div class="adapter"><small><b>适配：</b>${esc(s.adapter||"通用")}</small>
      <br><small>本轮识别：${Number(s.found_count||0)} 条</small>${kept}${err}</div></div>`;
  }).join("");
}
function renderFocusFilters(){
  const order=weeklyFocus.tag_order||TAG_ORDER,counts=weeklyFocus.tag_counts||{},total=(weeklyFocus.items||[]).length;
  const buttons=[["",`全部 ${total}`],...order.map(c=>[c,`${c} ${Number(counts[c]||0)}`])];
  $("focusFilters").innerHTML=buttons.map(([value,label])=>
    `<button type="button" class="focus-filter ${focusTag===value?'active':''}" data-tag="${esc(value)}">${esc(label)}</button>`
  ).join("");
  $("focusFilters").querySelectorAll(".focus-filter").forEach(btn=>btn.addEventListener("click",()=>{focusTag=btn.dataset.tag||"";renderFocus()}));
}
function renderFocus(){
  const items=(weeklyFocus.items||[]).filter(x=>!focusTag||(x.tags||[]).includes(focusTag));
  renderFocusFilters();
  const p=weeklyFocus.period||{};
  $("focusInfo").textContent=p.start&&p.end?`统计区间 ${p.start} 至 ${p.end} · 自动精选 ${(weeklyFocus.items||[]).length} 条`:"尚未完成首次重点动态生成";
  $("focusList").innerHTML=items.map((n,i)=>{
    const tags=tagChips(n.tags||[]);
    const primary=n.translated_title||n.title||"";
    const original=n.translated_title&&n.title&&n.translated_title!==n.title?`<div class="focus-original">${esc(n.title)}</div>`:"";
    const reason=n.importance_reason?`<div class="focus-reason">${esc(n.importance_reason)}</div>`:"";
    return `<article class="focus-item">
      <div class="focus-rank">${String(i+1).padStart(2,"0")}</div>
      <div class="focus-content"><div class="tag-row focus-tag-row">${tags}</div>
        <a class="focus-title" href="${esc(n.url||'#')}" target="_blank" rel="noopener noreferrer">${esc(primary)}</a>
        ${original}<div class="focus-meta">${esc(n.country||"")} · ${esc(n.source_name||"")} · ${esc(n.published_at||"")}</div></div>
      <div class="focus-side">${reason}<a class="open-link" href="${esc(n.url||'#')}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a></div>
    </article>`;
  }).join("");
  $("focusEmpty").classList.toggle("hidden",items.length>0);
}
async function safeJson(url,fallback){try{const r=await fetch(url);return r.ok?await r.json():fallback}catch(_e){return fallback}}
async function loadAll(){
  const ts=Date.now();
  const [n,s,m,f]=await Promise.all([
    safeJson(`./data/news.json?ts=${ts}`,[]),safeJson(`./data/source_status.json?ts=${ts}`,[]),
    safeJson(`./data/meta.json?ts=${ts}`,{}),safeJson(`./data/weekly_focus.json?ts=${ts}`,{items:[]})
  ]);
  allNews=n;allSources=s;meta=m;weeklyFocus=f;
  setOptions($("tagFilter"),TAG_ORDER,"全部标签");
  setOptions($("countryFilter"),allNews.map(x=>x.country),"全部国家/地区");
  setOptions($("sourceFilter"),allNews.map(x=>x.source_name),"全部来源");
  renderFocus();renderNews();renderSources();
  $("lastRun").textContent=meta.finished_at?`最近自动更新：${new Date(meta.finished_at).toLocaleString("zh-CN")}`:"尚未完成首次自动抓取";
}
["searchInput","tagFilter","countryFilter","sourceFilter","daysFilter"].forEach(id=>{
  $(id).addEventListener(id==="searchInput"?"input":"change",renderNews)
});
loadAll();
