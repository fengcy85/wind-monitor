const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const src=JSON.parse(fs.readFileSync(path.join(root,'policy_sources.json'),'utf8'));
const dims=JSON.parse(fs.readFileSync(path.join(root,'regulatory_dimensions.json'),'utf8'));
const doc={
 title:'关于风电机组并网技术规范的通知 <script>BAD</script>',country:'中国',region:'中国',province:'北京',source_name:'北京市发展和改革委员会',published_at:'2026-10-10',doc_type:'待核验法规候选',
 source_excerpt:'第一条 风力发电机组应当提交并网检测资料。',dimensions:['并网技术规范'],primary_dimension:'并网技术规范',url:'https://example.gov.cn/wind-policy',
 impact_design:'根据第一条，需核实低电压穿越软件参数。',impact_business:'需要核实并网测试延误对项目里程碑的影响。',impact_chain:'第一条 → 待核实主体 → 并网测试 → 产品适配核查 → 电气团队',
 affected_entities:'需核实',affected_scope:'陆上/海上待核实',consequence:'待证实',responsible_teams:'电气与控制',uncertainty:'不得推断法定后果',
 clauses:[{clause:'第一条',excerpt:'第一条 风力发电机组应当提交并网检测资料。'}],
 clause_scoring:[{clause:'第一条',importance:{D:3,B:4,C:2,A:3,U:3,score:67,priority:'P2',reason:'文档与企业范围经核验'}}],importance:{D:3,B:4,C:2,A:3,U:3,score:67,priority:'P2',reason:'测试证据'}
};
const data={
 'policy_sources.json':src,
 'regulatory_dimensions.json':dims,
 'policies.json':[doc],
 'policy_source_status.json':src.map((s,i)=>({id:s.id,status:i===0?'ok_with_results':'connection_error',found_count:i===0?1:0,initialized:i===0,phase:i===0?'daily_increment':'initial_month',window_start:'2026-09-14',window_end:'2026-10-13',checked_at:'2026-10-13T00:00:00Z',candidate_links:i===0?1:0,attempts:[]})),
 'policy_crawl_state.json':{bootstrap_days:30,pending_initialization_count:120,last_run:'2026-10-13T08:00:00Z'}
};
const els=new Map();
function makeElement(id){return {id,innerHTML:'',textContent:'',value:'',classList:{toggle(){}},children:[],appendChild(x){this.children.push(x)},addEventListener(){}}}
const document={getElementById(id){if(!els.has(id))els.set(id,makeElement(id));return els.get(id)},createElement(tag){return makeElement(tag)}};
const fetch=async url=>{const file=url.split('/').pop().split('?')[0];if(!(file in data))return {status:404,ok:false,json:async()=>null};return {status:200,ok:true,json:async()=>data[file]}};
vm.runInNewContext(fs.readFileSync(path.join(root,'site/static/policy.js'),'utf8'),{document,fetch,console,Date,setTimeout});
setTimeout(()=>{
 try{
 const policy=els.get('policyList').innerHTML;
 assert(policy.includes('法规原文节选'));
 assert(policy.includes('风力发电机组应当提交并网检测资料'));
 assert(policy.includes('并网技术规范'));
 assert(policy.includes('法规影响分析'));
 assert(policy.includes('D/B/C/A/U'));
 assert(policy.includes('条款—主体—活动—影响—应对链条'));
 assert(policy.includes('第一条'));
 assert(!policy.includes('<script>BAD</script>'),'XSS content not escaped');
 assert(policy.includes('&lt;script&gt;BAD&lt;/script&gt;'));
 assert.strictEqual(String(els.get('sourceTotal').textContent),'121');
 assert.strictEqual(String(els.get('sourceProblemCount').textContent),'120');
 assert.strictEqual(String(els.get('policyCount').textContent),'1');
 assert.strictEqual(String(els.get('sourceInitializedCount').textContent),'1');
 assert(policy.includes('第一条'));
 assert(els.get('policySourceGrid').innerHTML.includes('首次近30天'));
 assert.strictEqual(String(els.get('policyUpdatedAt').textContent).includes('首次30天'),true);
 const reg=fs.readFileSync(path.join(root,'site/index.html'),'utf8');
 const news=fs.readFileSync(path.join(root,'site/news.html'),'utf8');
 assert(reg.includes('href="./news.html"'));assert(news.includes('href="./index.html"'));
 console.log('FRONTEND PASS: 121 source cards, 18 dimensions, full legal fields, dual navigation, detail scores, HTML escaping.');
 }catch(e){console.error(e);process.exitCode=1;}
},100);
