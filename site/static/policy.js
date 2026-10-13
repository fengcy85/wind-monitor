/* Wind Monitor V5.2 | compact table, verified publication dates and source diagnostics. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char =>
  ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const safeHref = value => {
  try {
    const url = new URL(String(value || ''), window.location.href);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch (_) { return ''; }
};
const isInactive = item => /失效|废止|已终止|inactive|repealed|revoked/i.test(
  String(item.validity_status || '') + ' ' + String(item.title || '').slice(0, 14));
const isoDay = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value || '')) ? value : '';
function insideDays(value, days, today = new Date()) {
  if (days === 'all') return true;
  const day = isoDay(value);
  if (!day) return false;
  const date = new Date(`${day}T00:00:00+08:00`);
  const chinaDay = new Intl.DateTimeFormat('en-CA', {
    timeZone:'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit'
  }).format(today);
  const end = new Date(`${chinaDay}T00:00:00+08:00`);
  const age = Math.round((end.getTime() - date.getTime()) / 86400000);
  return age >= 0 && age < Number(days);
}

let policies = [], sources = [], statuses = [], dimensions = [], crawlState = {};
let searchTerm = '';
function statusLabel(code) {
  return ({ok_with_results:'已发现候选', ok_no_recent:'已检查·无近期结果',
    ok_no_relevant:'已检查·无相关结果', ok_no_candidates:'入口可读·未识别链接',
    blocked:'访问受限', timeout:'连接超时', connection_error:'连接失败',
    error:'抓取异常', detail_error:'详情抓取失败', needs_adapter:'待专用适配',
    not_checked:'尚未轮询'})[code] || '状态未确认';
}
function isOkay(code) { return String(code || '').startsWith('ok_'); }
function linkHtml(url, text, klass='') {
  const safe = safeHref(url);
  return safe ? `<a class="${esc(klass)}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : '—';
}
function options(id, values, allLabel) {
  const select = $(id);
  if (!select) return;
  const selected = select.value;
  select.replaceChildren();
  const base = new Option(allLabel, '');
  select.add(base);
  [...new Set(values.filter(Boolean))].sort((a,b) => a.localeCompare(b,'zh-CN'))
    .forEach(value => select.add(new Option(value,value)));
  select.value = Array.from(select.options).some(x=>x.value===selected) ? selected : '';
}
function filteredPolicies(now = new Date()) {
  const q = $('policySearch').value.trim().toLocaleLowerCase();
  const days = $('policyDays').value;
  const region = $('policyRegion').value, province = $('policyProvince').value;
  const dim = $('policyDimension').value, type = $('policyType').value;
  const validity = $('policyValidity').value;
  return policies.filter(item => {
    const hay = [item.title,item.source_name,item.country,item.province,item.source_excerpt,...(item.dimensions||[])].join(' ').toLocaleLowerCase();
    const expired = isInactive(item);
    return (!q || hay.includes(q)) && insideDays(item.published_at,days,now)
      && (!region || item.region===region) && (!province || item.province===province || item.country===province)
      && (!dim || (item.dimensions||[]).includes(dim)) && (!type || item.doc_type===type)
      && (validity==='all' || (validity==='inactive' ? expired : !expired));
  }).sort((a,b) => (b.published_at||'').localeCompare(a.published_at||'') ||
    String(a.title||'').localeCompare(String(b.title||''),'zh-CN'));
}
function renderPolicies() {
  const items = filteredPolicies();
  const body = items.map((item, index) => {
    const title = item.title || '未命名法规';
    const validity = String(item.validity_status || '效力待核验');
    const inactive = isInactive(item);
    const country = item.province || item.country || item.region || '—';
    const labels = (item.dimensions || []).join('、') || '待分类';
    const date = isoDay(item.published_at) || '待核实';
    const dateTip = item.date_evidence ? `发布日期依据：${item.date_evidence}` : '尚未核实官方发布日期';
    const priority = item.importance_level || item.priority || '待评分';
    const source = item.source_name || '—';
    const id = `policy-detail-${index}`;
    const titleLink = linkHtml(item.url, title, 'policy-title-link');
    return `<tr class="policy-row">
      <td class="policy-nowrap" title="${esc(dateTip)}">${esc(date)}</td>
      <td class="policy-nowrap" title="${esc(country)}">${esc(country)}</td>
      <td title="${esc(title)}">${titleLink}</td>
      <td class="policy-dimension-cell" title="${esc(labels)}">${esc(labels)}</td>
      <td title="${esc(validity)}"><span class="policy-inline-badge ${inactive?'inactive':'unknown'}">${esc(inactive?'失效/废止':'待核验')}</span></td>
      <td><span class="policy-inline-badge unknown">${esc(priority)}</span></td>
      <td class="policy-nowrap" title="${esc(source)}">${esc(source)}</td>
      <td><div class="policy-row-actions">${linkHtml(item.url,'原文','policy-original')}<button type="button" class="policy-action" data-toggle="${id}" aria-expanded="false" aria-controls="${id}">详情⌄</button></div></td>
    </tr><tr id="${id}" class="policy-detail-row" hidden><td colspan="8"><div class="policy-expand">
      <section><h4>基础信息</h4><p>原始标题：${esc(title)}<br>发布日期：${esc(date)}（${esc(item.date_evidence||'依据待核实')}）<br>成文日期：${esc(item.issued_at||'—')} · 生效日期：${esc(item.effective_at||'—')}<br>文件类型：${esc(item.doc_type||'—')}</p></section>
      <section><h4>法规分类与核验状态</h4><p>维度：${esc(labels)}<br>效力：${esc(validity)}<br>核验状态：${esc(item.verification_status||'初筛候选')}</p></section>
      <section><h4>产品设计影响</h4><p>${esc(item.impact_design||'待进一步核验具体条款')}</p></section>
      <section><h4>商业模式影响</h4><p>${esc(item.impact_business||'待进一步核验适用范围')}</p></section>
      <section class="full"><h4>原文证据（节选）</h4><p>${esc(item.source_excerpt||'暂无经过核实的原文摘录')}</p></section>
      <section class="full"><h4>应对建议</h4><p>${esc(item.next_step||'核对官方全文与生效状态后再决策。')}</p></section>
    </div></td></tr>`;
  }).join('');
  $('policyList').innerHTML = `<div class="policy-table-wrap"><table class="policy-table"><thead><tr><th>发布日期</th><th>地区</th><th>法规政策名称</th><th>法规维度</th><th>效力</th><th>重要度</th><th>发布机构</th><th>操作</th></tr></thead><tbody>${body}</tbody></table></div><div class="policy-list-note">一项法规一行；点击“详情”展开法规条款证据与影响分析。发布日期缺失或无法核实的文件不计入近期检索。</div>`;
  $('policyEmpty').classList.toggle('hidden', items.length !== 0);
  $('policySummary').textContent = `显示 ${items.length} 条 · 历史候选库 ${policies.length} 条 · 当前时间范围：${$('policyDays').selectedOptions[0]?.textContent || ''} · 候选数据不代表法律效力已确认`;
  $('policyCount').textContent = policies.filter(item=>insideDays(item.published_at,'14')).length;
}
function statusGroup(code) {
  if (isOkay(code)) return 'ok';
  if (code==='blocked') return 'blocked';
  if (['timeout','connection_error','error','detail_error'].includes(code)) return 'error';
  return code;
}
function filteredSources() {
  const term = $('sourceSearch').value.trim().toLocaleLowerCase();
  const region = $('sourceRegion').value, wanted = $('sourceStatusFilter').value;
  const byId = new Map(statuses.map(x=>[x.id,x]));
  return sources.map(src=>({src,status:byId.get(src.id)||{status:'not_checked',message:'尚未轮询'}}))
    .filter(({src,status}) => {
      const hay = [src.name,src.country,src.province,src.url,src.group].join(' ').toLocaleLowerCase();
      return (!term || hay.includes(term)) && (!region || src.region===region)
        && (!wanted || statusGroup(status.status)===wanted);
    });
}
function renderSources() {
  const visible = filteredSources();
  const byId = new Map(statuses.map(x=>[x.id,x]));
  const checked = sources.filter(s => (byId.get(s.id)?.status||'not_checked') !== 'not_checked');
  const errorCount = checked.filter(s => ['error','blocked','timeout','connection_error','detail_error'].includes(byId.get(s.id).status)).length;
  const adapted = checked.filter(s => byId.get(s.id).status === 'needs_adapter').length;
  $('sourceTotal').textContent = sources.length;
  $('cnSourceCount').textContent = sources.filter(x=>x.region==='中国').length;
  $('intlSourceCount').textContent = sources.filter(x=>x.region!=='中国').length;
  $('checkedSourceCount').textContent = checked.length;
  $('sourcesSummary').textContent = `当前显示 ${visible.length} 个来源 · 已轮询 ${checked.length} 个 · 连接/详情错误 ${errorCount} · 待适配 ${adapted} · 未轮询 ${sources.length-checked.length}`;
  $('policySourceGrid').innerHTML = visible.map(({src,status}) => {
    const state = status.status||'not_checked';
    const sourceTitle = linkHtml(src.url, src.name||src.url, 'policy-source-link');
    const detail = cleanSafeStatus(status.message || '尚无监测说明');
    const error = ['blocked','timeout','connection_error','error','detail_error','needs_adapter'].includes(state);
    return `<article class="source-card"><div class="source-top"><strong>${sourceTitle}</strong><span class="status-tiny ${esc(state)}">${esc(statusLabel(state))}</span></div>
      <small>${esc(src.region||'')} · ${esc(src.province||src.country||'')} · ${esc(src.source_type||'')}</small>
      <div class="source-subtle">本轮候选 ${Number(status.found_count||0)} 条 · 链接 ${Number(status.candidate_links||0)} 条 · 检索 ${Number(status.checked_days||0)||'—'} 天 · ${esc(status.checked_at?status.checked_at.slice(0,10):'未运行')}</div>
      <p class="${error?'source-error':'source-subtle'}">${esc(detail)}</p>
    </article>`;
  }).join('');
  $('sourceEmpty').classList.toggle('hidden', visible.length !== 0);
}
function cleanSafeStatus(message) { return String(message||'').slice(0,240); }
function renderDimensions() {
  $('dimensionsGrid').innerHTML = dimensions.map((dim,i) => `<div class="dimension-item"><strong>${esc(dim.name||'法规维度')}</strong><small>${esc(dim.description||dim.scope||'用于初筛分类与人工复核')}</small></div>`).join('');
}
async function loadJSON(file, fallback) {
  try {
    const response = await fetch(`./data/${file}?v=${Date.now()}`,{cache:'no-store'});
    return response.ok ? await response.json() : fallback;
  } catch (_) { return fallback; }
}
async function loadAll() {
  const data = await Promise.all([
    loadJSON('policies.json', []), loadJSON('policy_sources.json', []),
    loadJSON('policy_source_status.json', []), loadJSON('regulatory_dimensions.json', []),
    loadJSON('policy_crawl_state.json', {})
  ]);
  [policies,sources,statuses,dimensions,crawlState] = data;
  if (!Array.isArray(policies)) policies=[];
  if (!Array.isArray(sources)) sources=[];
  if (!Array.isArray(statuses)) statuses=[];
  if (!Array.isArray(dimensions)) dimensions=[];
  if (!crawlState || typeof crawlState !== 'object') crawlState={};
  options('policyProvince',policies.flatMap(x=>[x.province||x.country]).filter(Boolean),'全部省份/国家');
  options('policyDimension', dimensions.map(x=>x.name).concat(policies.flatMap(x=>x.dimensions||[])),'全部法规维度');
  options('policyType',policies.map(x=>x.doc_type),'全部文件类型');
  $('policyUpdatedAt').textContent = crawlState.last_run
    ? `最近抓取：${new Date(crawlState.last_run).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间） · 上次检索 ${crawlState.last_lookback_days||'—'} 天`
    : '尚未执行本版抓取任务';
  renderPolicies(); renderSources(); renderDimensions();
}
function ready() {
  ['policySearch','policyDays','policyRegion','policyProvince','policyDimension','policyType','policyValidity']
    .forEach(id=>$(`${id}`).addEventListener(id==='policySearch'?'input':'change',renderPolicies));
  ['sourceSearch','sourceRegion','sourceStatusFilter']
    .forEach(id=>$(id).addEventListener(id==='sourceSearch'?'input':'change',renderSources));
  $('policyList').addEventListener('click', event => {
    const button = event.target.closest('[data-toggle]');
    if (!button) return;
    const detail = document.getElementById(button.dataset.toggle);
    if (!detail) return;
    detail.hidden = !detail.hidden;
    button.setAttribute('aria-expanded',String(!detail.hidden));
    button.textContent = detail.hidden ? '详情⌄' : '收起⌃';
  });
  loadAll();
}
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',ready);
  else ready();
}
if (typeof module !== 'undefined') module.exports = {
  esc, safeHref, isoDay, insideDays, isInactive, statusGroup, statusLabel
};
