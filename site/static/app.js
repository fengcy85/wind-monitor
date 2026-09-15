let allNews=[],allSources=[],meta={};
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
function renderNews(){
  const q=$("searchInput").value.trim().toLowerCase();
  const country=$("countryFilter").value,source=$("sourceFilter").value;
  const days=Number($("daysFilter").value||90);
  const data=allNews.filter(n=>{
    const hay=[n.country,n.source_name,n.title,n.translated_title].join(" ").toLowerCase();
    return withinDays(n,days)&&(!q||hay.includes(q))&&(!country||n.country===country)&&(!source||n.source_name===source);
  });
  $("newsTableBody").innerHTML=data.map(n=>`<tr>
    <td><span class="country-badge">${esc(n.country||"")}</span></td>
    <td class="source-cell">${esc(n.source_name||"")}</td>
    <td class="title-cell">${esc(n.title||"")}</td>
    <td class="translation-cell">${n.translated_title?esc(n.translated_title):'<span class="translation-empty">—</span>'}</td>
    <td class="date-cell">${esc(n.published_at||"")}</td>
    <td class="link-cell"><a class="open-link" href="${esc(n.url||'#')}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a></td>
  </tr>`).join("");
  $("emptyState").classList.toggle("hidden",data.length>0);
  $("weekCount").textContent=allNews.filter(n=>withinDays(n,7)).length;
  $("totalCount").textContent=allNews.length;
  $("resultInfo").textContent=`当前显示 ${data.length} 条记录 · 历史共 ${allNews.length} 条`;
}
function stateLabel(s){if(s==="success")return["抓取成功","success"];if(s==="no_news")return["暂无相关新闻","no_news"];if(s==="error")return["抓取异常","error"];return["尚未运行","pending"]}
function renderSources(){
  const c={success:0,no_news:0,error:0};allSources.forEach(s=>{if(c[s.status]!==undefined)c[s.status]++});
  $("successCount").textContent=c.success;$("noNewsCount").textContent=c.no_news;$("errorCount").textContent=c.error;
  $("sourceGrid").innerHTML=allSources.map(s=>{
    const [label,cls]=stateLabel(s.status);const err=s.error?`<br><small class="bad-text">${esc(s.error)}</small>`:"";
    return `<div class="source-card"><div class="source-top"><strong>${esc(s.name)}</strong><span class="state ${cls}">${label}</span></div>
      <small>${esc(s.country)} · ${esc(s.role||"")}</small><div class="adapter"><small><b>适配：</b>${esc(s.adapter||"通用")}</small>
      <br><small>本轮识别：${Number(s.found_count||0)} 条</small>${err}</div></div>`;
  }).join("");
}
async function loadAll(){
  try{
    const ts=Date.now();
    const [n,s,m]=await Promise.all([
      fetch(`./data/news.json?ts=${ts}`),
      fetch(`./data/source_status.json?ts=${ts}`),
      fetch(`./data/meta.json?ts=${ts}`)
    ]);
    allNews=await n.json();allSources=await s.json();meta=await m.json();
    setOptions($("countryFilter"),allNews.map(x=>x.country),"全部国家/地区");
    setOptions($("sourceFilter"),allNews.map(x=>x.source_name),"全部来源");
    renderNews();renderSources();
    $("lastRun").textContent=meta.finished_at?`最近自动更新：${new Date(meta.finished_at).toLocaleString("zh-CN")}`:"尚未完成首次自动抓取";
  }catch(e){$("resultInfo").textContent="数据读取失败，请稍后刷新页面。"}
}
$("searchInput").addEventListener("input",renderNews);
$("countryFilter").addEventListener("change",renderNews);
$("sourceFilter").addEventListener("change",renderNews);
$("daysFilter").addEventListener("change",renderNews);
loadAll();
