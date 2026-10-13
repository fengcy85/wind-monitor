"use strict";
const $ = id => document.getElementById(id);
const esc = (v="") => String(v ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const txt = v => String(v ?? "");
const validURL = value => { try {const u = new URL(value); return ["https:","http:"].includes(u.protocol) ? u.href : "";} catch {return "";} };
const linkTo = (url,label="查看官方原文 ↗") => { const safe=validURL(url);return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer" class="external">${esc(label)}</a>` : `<span class="hint">原文链接待核实</span>`; };
const dateText = value => value ? esc(value.slice(0,10)) : "日期待核实";
const zoneTime = iso => {if(!iso)return "尚未完成首次法规抓取";const d=new Date(iso);return Number.isFinite(d.getTime()) ? d.toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false})+"（北京时间）" : "时间待核实";};
const state={policies:[],pending:[],status:[],registry:[],dimensions:[],meta:{},news:[],focus:[],newsStatus:[]};
const TABS={policy:$("policyPanel"),industry:$("industryPanel"),source:$("sourcePanel")};
const STATES={found:["发现法规候选","found"],needs_review:["待人工核实","warn"],search_adapter_required:["检索接口待适配","warn"],no_listing_links:["无可用详情链接","warn"],no_wind_regulations:["本轮无强相关条款",""],partial_failure:["部分页面失败","fail"],robots_denied:["robots 禁止访问","fail"],robots_unavailable:["robots 无法核实","warn"],access_restricted:["访问受限","fail"],rate_limited:["访问频率受限","warn"],network_error:["网络异常","fail"],http_error:["HTTP 异常","fail"],timeout:["请求超时","fail"],not_run:["尚未尝试抓取",""]};

async function safeJSON(path,fallback){try{const response=await fetch(path+"?v="+Date.now(),{cache:"no-store"});return response.ok?await response.json():fallback;}catch{return fallback;}}
function setSelect(id,values,first){const el=$(id),old=el.value;el.replaceChildren();const o=document.createElement("option");o.value="";o.textContent=first;el.appendChild(o);[...new Set(values.filter(Boolean))].sort((a,b)=>txt(a).localeCompare(txt(b),"zh-CN")).forEach(value=>{const opt=document.createElement("option");opt.value=value;opt.textContent=value;el.appendChild(opt);});el.value=old;}
function showTab(which){for(const [key,el] of Object.entries(TABS)){el.classList.toggle("hidden",key!==which);const b=$(key==="policy"?"tabPolicy":key==="industry"?"tabIndustry":"tabSource");b.classList.toggle("active",key===which);b.setAttribute("aria-selected",String(key===which));} }

function badges(reg){const tags=(reg.dimensions||[]).map((name,i)=>`<span class="chip ${i===0?"region":""}">${esc(name)}</span>`).join("");return tags||"<span class=\"chip\">法规维度待识别</span>";}
function orderPolicies(list){const mode=$("sortPolicy").value;return list.slice().sort((a,b)=>{if(mode==="score"){const va=Number.isFinite(a.importance_score)?a.importance_score:-1,vb=Number.isFinite(b.importance_score)?b.importance_score:-1;return vb-va||(b.review_rank||0)-(a.review_rank||0);}if(mode==="newest")return (b.published_at||"").localeCompare(a.published_at||"")||(b.review_rank||0)-(a.review_rank||0);return (b.review_rank||0)-(a.review_rank||0)||(b.published_at||"").localeCompare(a.published_at||"");});}
function filteredPolicies(){const query=$("qPolicy").value.trim().toLocaleLowerCase(),region=$("regionPolicy").value,country=$("countryPolicy").value,dim=$("dimensionPolicy").value;return orderPolicies(state.policies.filter(r=>{const hay=[r.title,r.country,r.authority,r.evidence,r.clause_id,r.product_impact,r.commercial_impact,...(r.dimensions||[])].join(" ").toLocaleLowerCase();return (!region||r.source_region===region)&&(!country||r.country===country)&&(!dim||(r.dimensions||[]).includes(dim))&&(!query||hay.includes(query));}));}
function policyCard(r){const score=Number.isFinite(r.importance_score)?`<span class="chip verified">${esc(r.priority)} · 已核验 ${r.importance_score} 分</span>`:`<span class="chip warning">初筛审阅 ${Number(r.review_rank||0)} · 待正式评分</span>`;
 const condition=(r.relevance||"")+" / "+(r.wind_trace||"");
 const actions=(r.recommended_actions||[]).map(s=>`<li>${esc(s)}</li>`).join("");
 return `<article class="reg-card">
 <div class="reg-top"><span class="chip region">${esc(r.source_region||"")}</span><span class="chip">${esc(r.country||"区域待核实")}</span><span class="chip warning">${esc(r.document_status||"法律效力待核实")}</span>${score}</div>
 <h3 class="reg-title"><a href="${esc(validURL(r.url)||"#")}" target="_blank" rel="noopener noreferrer">${esc(r.title||"原文标题待核实")}</a></h3>
 <div class="dim-tags">${badges(r)}</div>
 <div class="reg-metas"><span>条款：${esc(r.clause_id||"待核实")}</span><span>发布日期：${dateText(r.published_at)}</span><span>来源：${esc(r.source_name||"")}</span></div>
 <p class="detail-lead"><strong>产品设计影响：</strong>${esc(r.product_impact||"未确认")}</p>
 <p class="detail-lead"><strong>商业模式影响：</strong>${esc(r.commercial_impact||"未确认")}</p>
 <details><summary>展开：法规条款原文、影响链条与应对建议</summary>
  <div class="breakout"><span class="detail-label">法规原文证据（节选）</span><div class="evidence">${esc(r.evidence||"暂无可追溯条款摘录")}</div></div>
  <div class="breakout"><span class="detail-label">法规要求 → 受约束主体 → 企业业务活动 → 可能影响 → 应对动作</span>
    <p class="detail-lead">${esc(r.classification_basis||"规则初筛")}</p><p class="detail-lead">${esc(condition)}；受约束主体和实际适用机型须核验后再判断具体合规责任。</p></div>
  <div class="breakout"><span class="detail-label">建议后续核验 / 对策</span><ol class="actions">${actions}</ol></div>
  <div class="breakout"><span class="detail-label">建议协作部门</span>${esc((r.departments||[]).join("、")||"法务与合规")}</div>
  <div class="tip">${esc(r.scoring_note||"")} ${esc(r.impact_status||"")}</div>
 </details>${linkTo(r.url)}</article>`;
}
function renderPolicies(){const rows=filteredPolicies();$("policyCountText").textContent=`显示 ${rows.length} 条候选 · 数据库 ${state.policies.length} 条 · 不代表均已核实法律效力`;
 $("policyList").innerHTML=rows.slice(0,300).map(policyCard).join("");$("policyEmpty").classList.toggle("hidden",rows.length!==0);
 if(rows.length>300)$("policyCountText").textContent+="（仅展示前 300 条，可通过筛选缩小结果）";
 $("reviewPill").textContent=state.pending.length+" 条";
 $("reviewList").innerHTML=state.pending.slice(0,120).map(r=>`<div class="review-item"><div class="reg-top"><span class="chip warning">${esc(r.document_type||"待核实")}</span><span>${esc(r.country||"")}</span><span>${esc(r.primary_dimension||"")}</span></div>${linkTo(r.url, r.title||"官方链接")}
  <p>原文定位：${esc(r.clause_id||"待核实")} · ${esc(r.wind_trace||"需核验适用范围")}</p></div>`).join("");$("reviewEmpty").classList.toggle("hidden",state.pending.length>0);
}
function renderStats(){const m=state.meta,reg=state.registry;$("sourceCount").textContent=(m.source_total??reg.length)||"—";$("candidateCount").textContent=state.policies.length;$("reviewCount").textContent=state.pending.length;$("foundSourceCount").textContent=m.sources_with_candidate_evidence??0;$("scoredCount").textContent=state.policies.filter(r=>Number.isFinite(r.importance_score)).length;
 $("lastUpdate").textContent="法规检查："+zoneTime(m.last_run_at);
 if(!m.last_run_at)$("globalNotice").innerHTML="<strong>尚未执行首次部署抓取</strong>：已载入渠道登记信息，但还没有实际成功或失败记录。请运行 Actions 中的更新任务；页面不会展示虚构的法规条目。";
}
function statusRow(s){const [label,kind]=STATES[s.status]||[s.status||"未运行","warn"];const fails=(s.failure_details||[]).slice(0,5).map(f=>`${f.stage}：${f.type} — ${f.detail}`).join("\n");return `<article class="source-card"><header><h3>${esc(s.name||"")}</h3><span class="status-pill ${kind}">${esc(label)}</span></header>
 <small>${esc(s.region)} · ${esc(s.country)} · ${esc(s.strategy||"待配置")}</small><code>${esc(s.url||"")}</code>
 <small>本轮检查：${Number(s.articles_checked||0)} 页 · 发现链接：${Number(s.links_discovered||0)} · 条款候选：${Number(s.accepted||0)} · 待核：${Number(s.pending||0)}</small>
 <details><summary>查看适配与失败详情</summary><p>${esc(s.status_message||"等待首次运行")}</p><p>采用：${esc((s.adapter_used||[]).join(" / ")||"尚未尝试")}</p>${fails?`<p class="source-fail">${esc(fails)}</p>`:""}</details>
 </article>`;}
function renderSources(){const q=$("qSource").value.trim().toLowerCase(),region=$("regionSource").value,status=$("statusSource").value;
 const statusById=new Map(state.status.map(x=>[x.id,x]));const items=state.registry.map(reg=>({...reg,...(statusById.get(reg.id)||{status:"not_run",status_message:"等待首次执行",accepted:0,pending:0,articles_checked:0,links_discovered:0})}));
 const filtered=items.filter(s=>{const hay=[s.name,s.country,s.url,s.status,s.status_message,...(s.failure_details||[]).map(x=>x.detail)].join(" ").toLowerCase();return (!q||hay.includes(q))&&(!region||s.region===region)&&(!status||s.status===status);});
 $("sourceCards").innerHTML=filtered.map(statusRow).join("");$("sourceSummary").textContent=`${filtered.length} / ${items.length} 渠道`;
 const map={};items.forEach(s=>{map[s.status]=(map[s.status]||0)+1});$("diagnosticIntro").textContent=`真实状态统计：${Object.entries(map).map(([key,count])=>(STATES[key]?.[0]||key)+" "+count).join(" · ")}。检索门户、政府动态 API、PDF 扫描件及访问受限页面可能需要单独授权或人工适配；系统不会绕过访问控制。`;
}
function newsCard(n){const title=n.translated_title||n.title||"未命名动态";const original=n.translated_title&&n.title&&n.translated_title!==n.title?`<div class="news-original">原文：${esc(n.title)}</div>`:"";return `<article class="news-item"><div class="news-head"><span class="chip region">${esc(n.country||"")}</span><span>${esc(n.source_name||"")}</span><span>${dateText(n.published_at)}</span>${(n.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("")}</div>${linkTo(n.url,title)}${original}</article>`;}
function renderIndustry(){const q=$("qNews").value.trim().toLowerCase(),tag=$("newsTag").value,filtered=state.news.filter(n=>{const hay=[n.title,n.translated_title,n.country,n.source_name,...(n.tags||[])].join(" ").toLowerCase();return (!q||hay.includes(q))&&(!tag||(n.tags||[]).includes(tag));});$("newsCountLabel").textContent=`${filtered.length} / ${state.news.length} 条`;$("newsList").innerHTML=filtered.slice(0,100).map(newsCard).join("");$("focusList").innerHTML=state.focus.slice(0,10).map(newsCard).join("");$("newsEmpty").classList.toggle("hidden",filtered.length>0);}
function downloadCSV(){const rows=filteredPolicies();const cols=["country","title","document_type","document_status","clause_id","primary_dimension","dimensions","evidence","product_impact","commercial_impact","importance_score","priority","url"];
 const text=[cols.join(","),...rows.map(item=>cols.map(key=>{const value=Array.isArray(item[key])?item[key].join("；"):txt(item[key]);return '"'+value.replace(/\r?\n/g," ").replace(/"/g,'""')+'"';}).join(","))].join("\r\n");
 const blob=new Blob(["\ufeff",text],{type:"text/csv;charset=utf-8;"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="wind_policy_candidates.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function init(){const [policies,pending,status,registry,dimensions,meta,news,focus,newsStatus]=await Promise.all([
 safeJSON("./data/policies.json",[]),safeJSON("./data/policy_review.json",[]),safeJSON("./data/policy_source_status.json",[]),
 safeJSON("./data/policy_source_registry.json",[]),safeJSON("./data/policy_dimensions.json",[]),safeJSON("./data/policy_meta.json",{}),
 safeJSON("./data/news.json",[]),safeJSON("./data/weekly_focus.json",{}),safeJSON("./data/source_status.json",[])
 ]);
 state.policies=Array.isArray(policies)?policies:[];state.pending=Array.isArray(pending)?pending:[];state.status=Array.isArray(status)?status:[];
 state.registry=Array.isArray(registry)?registry:[];state.dimensions=Array.isArray(dimensions)?dimensions:[];state.meta=meta||{};
 state.news=Array.isArray(news)?news:[];state.focus=Array.isArray(focus.items)?focus.items:[];state.newsStatus=Array.isArray(newsStatus)?newsStatus:[];
 setSelect("countryPolicy",state.policies.map(x=>x.country),"全部国家/地区");setSelect("dimensionPolicy",state.dimensions.map(x=>x.name),"全部法规维度");
 setSelect("statusSource",state.status.map(x=>x.status),"全部状态");setSelect("newsTag",state.news.flatMap(x=>x.tags||[]),"全部行业标签");
 renderStats();renderPolicies();renderSources();renderIndustry();
}
[["tabPolicy","policy"],["tabIndustry","industry"],["tabSource","source"]].forEach(([id,key])=>$(id).addEventListener("click",()=>showTab(key)));
["qPolicy","regionPolicy","countryPolicy","dimensionPolicy","sortPolicy"].forEach(id=>$(id).addEventListener(id==="qPolicy"?"input":"change",renderPolicies));
["qSource","regionSource","statusSource"].forEach(id=>$(id).addEventListener(id==="qSource"?"input":"change",renderSources));
["qNews","newsTag"].forEach(id=>$(id).addEventListener(id==="qNews"?"input":"change",renderIndustry));
$("exportPolicy").addEventListener("click",downloadCSV);
init();
