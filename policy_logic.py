"""Conservative, evidence-first wind regulation clause screening.

This is a rules-based first pass, *not* an LLM or legal determination.
The public page must not call inferred duties mandatory or assign verified P0-P4 scores.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

HERE = Path(__file__).resolve().parent
DIMENSIONS = json.loads((HERE / 'regulatory_dimensions.json').read_text(encoding='utf-8'))

# Each rule references exactly one of the names supplied in the user's dimension table.
HINTS = {
 '市场准入与外资限制':['市场准入','外资','投资审查','进口许可','供应商资格','foreign investment','fdi screening','market access','licensing of equipment','supplier eligibility','market entry','licença de importação'],
 '本地化及原产地':['本地化','本国制造','当地采购','原产地','国产化','本地含量','local content','local manufacturing','domestic content','country of origin','teknoloji transferi','yerli katkı','tỷ lệ nội địa'],
 '产品认证与技术标准':['产品认证','技术标准','标准认证','型式认证','型式试验','产品测试','61400','certification','type approval','type certificate','technical standard','technical specification','conformity assessment','conformidade','certificação','IEC 61400'],
 '电网建设与接入':['电网接入','电网建设','送出工程','接入容量','输电线路','接入申请','grid connection','transmission access','grid access','interconnection','transmission planning','network connection','grid capacity','acesso à rede','connessione alla rete'],
 '并网技术规范':['并网技术','低电压穿越','高电压穿越','故障穿越','频率响应','无功','电能质量','LVRT','HVRT','fault ride through','grid code','grid compliance','reactive power','power quality','frequency response','codigo de rede','grid-code','lưới điện'],
 '可再生能源开发与电价':['上网电价','差价合约','绿证','电力交易','可再生能源配额','电价机制','风电规划','cfd','feed-in tariff','power purchase agreement','ppa','renewable auction','electricity tariff','renewable energy target','tarifa de energia','tariffa','điện gió'],
 '招投标与政府采购':['招投标','政府采购','招标采购','投标资格','投标保证金','技术评分','tender','procurement','public auction','bid eligibility','bidding','licitação','leilão','ihale'],
 '关税、税收与贸易救济':['关税','税收','反倾销','反补贴','贸易救济','碳边境','进口税','customs duty','anti-dumping','tariff','import tax','trade remedy','cbam','tax exemption','tributação','tarifa aduaneira'],
 '运输与物流准入':['超限运输','大件运输','道路许可','桥梁承载','港口吊装','运输限高','oversize transport','abnormal load','heavy lift','road permit','special transport','heavy haulage','logística especial'],
 '土地、环境与生态许可':['环境影响评价','环境许可','环评','鸟类保护','土地使用','噪声标准','生态补偿','environmental impact assessment','eia','environmental permit','noise emission','birds and bats','land use','licenciamento ambiental','impacto ambiental'],
 '航空、雷达及国防限制':['航空限高','净空','障碍灯','雷达干扰','机场保护','国防限制','aviation obstruction','radar interference','airspace','height restriction','aviation lighting','obstacle limitation','radar'],
 '气候与自然条件':['台风','极端低温','盐雾','雷击防护','抗震','抗台风','extreme wind','climatic class','salt spray','seismic design','lightning protection','typhoon resilience','extreme temperature'],
 '网络安全与数据合规':['网络安全','数据出境','数据本地化','远程运维','个人信息保护','scada','cybersecurity','cyber security','data localization','data protection','critical infrastructure','remote access','segurança cibernética'],
 '安全生产与劳动法规':['安全生产','消防安全','施工安全','职业资格','吊装安全','劳动安全','occupational safety','electrical safety','industrial safety','fire safety','construction safety','saúde e segurança'],
 '海上风电专项政策':['海上风电','海域使用','海缆','海事许可','海上施工','offshore wind','marine spatial','seabed lease','offshore transmission','maritime permit','eólica offshore','eolico offshore','điện gió ngoài khơi'],
 '融资与项目担保':['贷款担保','项目担保','融资条件','本地融资','政策性融资','financial guarantee','project finance','financing requirement','sovereign guarantee','bank guarantee','financiamento','garanti'],
 '运维、质保及退役':['运维','质保','退役','拆除','保修责任','maintenance obligation','warranty','decommissioning','end of life','recycling obligation','operational lifetime','descomissionamento'],
 '供应链、ESG及碳足迹':['碳足迹','供应链尽调','环境社会治理','原材料溯源','esg','carbon footprint','supply chain due diligence','traceability','embedded emissions','carbon disclosure','sustentabilidade'],
}
assert set(HINTS) == {x['name'] for x in DIMENSIONS}

WIND = ['风电','风能','风力发电','风机','风力发电机','风电场','风力机','风电机组','风轮',
 'wind power','wind energy','wind farm','wind turbine','wind generation','wind project','wind electricity','offshore wind','onshore wind',
 'eolica','eólica','eolico','eólico','éolienne','éolien','energia do vento','rüzgar enerjisi','rüzgâr enerjisi','điện gió','shamol','ветроэнерг','ветряной','طاقة الرياح']
REGULATORY = ['法律','法规','条例','规章','规定','规范','办法','通知','征求意见','标准','强制','实施细则','监管','审批','规范性文件','管理要求',
 'regulation','regulatory','law','legislation','decree','directive','order','standard','grid code','code of practice','rule','rules','requirement',
 'policy','ordinance','act ','tender','procurement','license','licence','compliance','legislação','regulamento','decreto','resolução','norma','reglamento','mevzuat','quy định','nghị định','thông tư']
OBLIGATIONS = ['应当','必须','不得','禁止','要求','达到','符合','不少于','不超过','需取得','才可','申请条件','有效期','投标资格','比例不得','强制性','生效','履行','限期','取得许可','须','规定',
 'shall','must','required','mandatory','prohibited','may not','cannot','minimum','maximum','limit','no later than','compliance','requires','obliged','subject to','eligible','permit is required',
 'deve','obrigatório','obrigatoria','não pode','devem','deberá','deberán','zorunlu','gerekir','yêu cầu','phải','bắt buộc']
DRAFT = ['征求意见','征求公众意见','征求意见稿','公开征询','草案','征求建议','draft','consultation','proposed regulation','public comment','proposta','projeto de lei','taslak']
REPEALED = ['已废止','失效','废止公告','废止','repealed','revoked','rescinded','superseded','revogado','yürürlükten kaldırılmış']
GENERIC_APPLICABILITY = ['发电设备','发电设施','并网电源','发电机组','电力设备制造商','所有发电项目','发电项目','发电企业','电网接入技术','电网运行',
 'power generating facility','all generators','generation facility','generating plants','power generating units','wind generators','grid connected generation','grid connection of generators','electricity generating plants','power generating equipment','发电厂']
NEGATIVE = ['招聘','招人','演讲稿','讲座','直播报名','会议纪要','宣传报道','图片新闻','活动预告','访问团','企业访谈',
 'newsletter','podcast','webinar','photo gallery','career','vacancy','press release','opinion','event registration','advertisement','interview','sponsored']

# Each domain below generates tailored but tentative product / business checks, NOT unverified claims.
IMPACT = [
 (('并网技术规范',), ['变流器、主控和并网算法','并网模型及型式试验'], ['核对适用机型的故障穿越、频率/无功响应参数与试验要求','组织电气与控制、认证与测试联合核验']),
 (('产品认证与技术标准',), ['型式认证、测试计划及技术文档'], ['核查标准版本是否被当地法规、认证规则或合同引用','对照在售机型认证差距与过渡期']),
 (('本地化及原产地',), ['供应链选址、零部件来源及本地工厂模式'], ['核对是否有可执行的本地含量比例/原产地计算规则','让供应链与国际销售评估合规交付路径']),
 (('关税、税收与贸易救济',), ['进口设备报价、完税成本与供应来源'], ['按产品海关编码核实税率、适用期间和豁免','由财务与贸易合规复核成本情景']),
 (('招投标与政府采购',), ['供应商资格、投标技术评分及联合体结构'], ['逐条核实招标资格、技术评分及担保条件','由国际销售与法务建立投标准入检查清单']),
 (('市场准入与外资限制',), ['销售资格、进口许可及当地经营主体'], ['核实法律适用的制造商/进口商与项目主体','由法务与国际销售评估准入路径']),
 (('电网建设与接入',), ['升压站、电网接入方案及交付边界'], ['核对风电项目接网主体、容量和工程责任','由项目开发与电气团队确认机型/升压站接口']),
 (('土地、环境与生态许可','航空、雷达及国防限制'), ['噪声控制、风机高度与选址参数'], ['核对环境许可、限高及适用项目类别','让产品研发和项目开发复核噪声与布局约束']),
 (('网络安全与数据合规',), ['SCADA、远程访问、数据存储与控制安全'], ['确认要求是否适用于风机制造商、运维商或业主','由电气控制和法务核对接口/数据方案']),
 (('运输与物流准入',), ['叶片运输、塔筒分段与大件运输计划'], ['核对运输尺寸、许可与道路限制','由物流交付与产品研发确认包装/分段措施']),
 (('海上风电专项政策',), ['海上机型适用性、港口及海缆交付边界'], ['核实海域、许可及海上工程约束主体','联合研发和项目开发评估适用机型/交付路径']),
 (('运维、质保及退役',), ['质保周期、退役责任与运维合同'], ['核实责任主体及适用过渡期','由运维与法务复核相关合同义务']),
 (('可再生能源开发与电价','融资与项目担保'), ['项目收益预期、报价及融资/担保结构'], ['核对是否影响目标项目的电价/PPA/融资条件','由市场战略、财务与国际销售评估订单敏感性']),
 (('气候与自然条件',), ['环境适应性、冷却/防腐及载荷设计'], ['核对适用技术规范的具体环境等级和数值阈值','由研发确认机型设计验证与认证要求']),
 (('安全生产与劳动法规',), ['电气安全、现场吊装与维护方案'], ['核验受约束主体、现场作业及安全认证条件','由交付和运维部门复核工艺/手册']),
 (('供应链、ESG及碳足迹',), ['碳数据、供应链追溯与采购证明'], ['核验适用产品、报送范围及供应链证据','由供应链与法务确认申报责任']),
]

DEPT = {'并网技术规范':['电气与控制','认证与测试'],'产品认证与技术标准':['认证与测试','产品研发'],
'本地化及原产地':['制造与本地化','供应链与采购'],'市场准入与外资限制':['法务与合规','国际销售'],
'电网建设与接入':['项目开发','电气与控制'],'招投标与政府采购':['国际销售','法务与合规'],
'关税、税收与贸易救济':['财务与融资','供应链与采购'],'运输与物流准入':['物流与交付','产品研发'],
'土地、环境与生态许可':['项目开发','产品研发'],'航空、雷达及国防限制':['产品研发','项目开发'],
'网络安全与数据合规':['电气与控制','法务与合规'],'可再生能源开发与电价':['市场战略','国际销售'],
'海上风电专项政策':['项目开发','产品研发'],'融资与项目担保':['财务与融资','国际销售'],
'运维、质保及退役':['运维服务','法务与合规'],'气候与自然条件':['产品研发','认证与测试'],
'供应链、ESG及碳足迹':['供应链与采购','法务与合规'],'安全生产与劳动法规':['物流与交付','运维服务']}


def contains(text: str, word: str) -> bool:
    text = text.casefold(); word=word.casefold()
    # Short latin strings should not match inside unrelated words (e.g. 'ppa' in 'apparent').
    if re.fullmatch(r'[a-z]{1,4}',word):
        return bool(re.search(r'(?<![a-z])'+re.escape(word)+r'(?![a-z])',text))
    return word in text


def matches(text: str, words: list[str]) -> list[str]:
    return [w for w in words if contains(text,w)]


def canonical_url(url: str) -> str:
    try:
        p=urlsplit(url)
        if p.scheme.lower() not in ('http','https') or not p.netloc:return ''
        qs=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.casefold() not in ('fbclid','gclid','ref','source') and not k.casefold().startswith('utm_')]
        return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/') or '/',urlencode(qs),''))
    except ValueError:return ''


def normalize_text(text: str) -> str:
    return re.sub(r'\s+',' ',(text or '').replace('\u00a0',' ')).strip()


def clause_parts(text: str, limit: int=120) -> list[str]:
    # Preserve supplied PDF/HTML paragraphs, including legal numbering.
    parts=re.split(r'(?:\n{2,}|(?=第[一二三四五六七八九十百0-9]{1,6}条)|(?=\bArticle\s+\d+[a-z]?\s*[:.])|(?=\bArtigo\s+\d+\s*[º°]?))',text or '',flags=re.I)
    clean=[]
    for part in parts:
        part=normalize_text(part)
        if 28 <= len(part) <= 9000:
            if len(part)>1400:
                # Avoid page-wide text marked as one enforceable clause.
                part=part[:1400]
            clean.append(part)
    return clean[:limit]


def extract_clause_id(text: str, n: int) -> str:
    m=re.search(r'(第[一二三四五六七八九十百0-9]+条|Article\s+\d+[a-z]?|Artigo\s+\d+|\b\d{1,3}(?:\.\d{1,3}){0,2}[.)、])',text,re.I)
    return m.group(1) if m else f'段落{n}'


def document_kind(title: str, snippet: str) -> str:
    combined=title+' '+snippet[:500]
    if matches(combined,DRAFT):return '政策观察'
    if matches(combined,REPEALED):return '历史法规'
    if matches(title,REGULATORY) or matches(snippet[:300],REGULATORY):return '法规政策与标准'
    return '未能确认文件性质'


def dimension_hits(text: str) -> list[tuple[str,int]]:
    counts=[(name,len(matches(text,words))) for name,words in HINTS.items()]
    return sorted(((name,count) for name,count in counts if count),key=lambda kv:(-kv[1],list(HINTS).index(kv[0])))


def impact_suggestion(dimensions: list[str]) -> dict:
    design=[];business=[];actions=[]
    for dims,design_frag,steps in IMPACT:
        if any(d in dimensions for d in dims):
            if any(x in dims for x in ('并网技术规范','产品认证与技术标准','电网建设与接入','土地、环境与生态许可','航空、雷达及国防限制','网络安全与数据合规','运输与物流准入','海上风电专项政策','气候与自然条件','安全生产与劳动法规')):
                design.extend(design_frag)
            else: business.extend(design_frag)
            actions.extend(steps)
    if not (design or business):actions=['核实条款适用主体与风电业务影响','补充企业目标产品及业务模式后再作结论']
    departments=[]
    for dim in dimensions:
        departments.extend(DEPT.get(dim,[]))
    return {'product_impact':('建议核对：'+'；'.join(dict.fromkeys(design))) if design else '暂未从所摘录条款确认直接产品设计影响',
        'commercial_impact':('建议核对：'+'；'.join(dict.fromkeys(business))) if business else '暂未从所摘录条款确认直接商业模式影响',
        'recommended_actions':list(dict.fromkeys(actions))[:5],
        'departments':list(dict.fromkeys(departments))[:4],
        'impact_status':'规则辅助识别，未经适用性和法律效力核验'}


def review_rank(direct: bool, dims: list[str], sentence: str, kind: str, dated: bool) -> int:
    # Only ranks which candidates should be read by a human first. NOT a regulation score.
    high=['并网技术规范','产品认证与技术标准','市场准入与外资限制','本地化及原产地','招投标与政府采购']
    score=25+(24 if direct else 0)+min(18,6*len(dims)) + (10 if matches(sentence,OBLIGATIONS) else 0)
    score+=(10 if any(d in high for d in dims) else 0) + (3 if dated else 0)
    if kind=='政策观察':score-=8
    return min(100,max(0,score))


def analyze_document(*, source: dict, url: str, title: str, body: str, paragraphs: list[str], published_at: str|None=None, observed_at: str|None=None) -> tuple[list[dict],list[dict]]:
    """Return (documented candidates, needs-human-review records). Not verified legal requirements."""
    url=canonical_url(url); title=normalize_text(title)
    if not url or not title or len(title)<6:return [],[]
    if matches(title,NEGATIVE):return [],[]
    combined='\n\n'.join(paragraphs) if paragraphs else body
    combined=combined[:35000]
    # A homepage with one wind keyword in its navigation must not become an "article".
    if len(normalize_text(combined)) < (40 if re.search(r"[一-龥]",combined) else 80):return [],[]
    doc_kind=document_kind(title,combined)
    if not matches(title+' '+combined[:1400],REGULATORY):return [],[]
    has_wind=bool(matches(title+' '+combined,WIND))
    # A cross-sector rule can affect wind without saying 'wind' (grid code / cyber / oversized transport).
    # Such indirect applicability is *only* a pending lead, never an accepted legal duty.
    general_scope=bool(matches(title+' '+combined[:3500],GENERIC_APPLICABILITY))
    general_standard=bool(matches(title+' '+combined[:3500],['grid code','并网技术规范','网络安全','运输许可','type certification','型式认证']))
    if not (has_wind or (general_scope and general_standard)):return [],[]
    inspected=clause_parts(combined)
    if not inspected: return [],[]
    direct_in_title=bool(matches(title,WIND))
    pool=[];pending=[]
    for n,part in enumerate(inspected,1):
        dim_scores=dimension_hits(title+' '+part)
        if not dim_scores:continue
        if not matches(part+' '+title,OBLIGATIONS):continue
        direct=bool(matches(part,WIND)) or direct_in_title
        # Generic grid/ESG rules have no proven wind scope from a keyword-only filter.
        # Keep for review rather than imply applicability.
        traceable= has_wind and (bool(matches(part,WIND)) or direct_in_title)
        # If wind appears only in an unrelated footer elsewhere, do not accept it as traceable.
        related_label='待核实：风电适用范围' if not traceable else '正文/标题含风电线索，条款效力仍待核验'
        rank=review_rank(direct,[x for x,_ in dim_scores],part,doc_kind,bool(published_at))
        clause_id=extract_clause_id(part,n)
        evidence=part[:550]
        normalized_id=hashlib.sha256((url+'\0'+clause_id+'\0'+normalize_text(part[:220])).encode()).hexdigest()[:18]
        top_dims=[x for x,_ in dim_scores[:4]]
        kind = doc_kind
        record={'id':normalized_id,'source_id':source['id'],'source_name':source['name'],
          'source_region':source['region'],'country':source['country'],
          'title':title,'url':url,'original_url':url,'authority':source['name'],
          'document_type':kind,'document_status':'待核实' if kind=='法规政策与标准' else kind,
          'published_at':published_at,'effective_at':None,'observed_at':observed_at,
          'clause_id':clause_id,'evidence':evidence,'requirements_excerpt':part[:500],
          'dimensions':top_dims,'primary_dimension':top_dims[0],
          'wind_trace':related_label,'relevance':'直接风电线索' if direct else '待核实通用规定适用性',
          'review_rank':rank,'legal_force_confirmed':False,'scope_confirmed':False,
          'evidence_status':'条款文本待人工核验','importance_score':None,'priority':'待评分',
          'scoring_note':'需核验法律效力、企业产品/目标市场、D/B/C/A/U 五项证据后评分。',
          'classification_basis':'条款摘录含监管/义务线索与法规维度；不等于法律适用性已确认。'}
        record.update(impact_suggestion(top_dims))
        if kind=='政策观察':pending.append(record)
        elif direct and traceable:pool.append(record)
        else:pending.append(record)
    pool.sort(key=lambda d:-d['review_rank']);pending.sort(key=lambda d:-d['review_rank'])
    # Bounded per document. No acceptance based solely on wind appearing on a generic index.
    return pool[:8],pending[:6]


def priority_from_score(score: int) -> str:
    if score>=85:return 'P0 极高'
    if score>=70:return 'P1 高'
    if score>=50:return 'P2 中'
    if score>=30:return 'P3 低'
    return 'P4 较低'


def score_verified_assessment(assessment:dict) -> dict:
    """Scores *only fully evidenced & verified* D/B/C/A/U (0..5).

    No `None`, missing value, inferred defaults, or unverified draft can yield a final priority.
    """
    keys=['D','B','C','A','U'];weights=[6,5,4,3,2]
    if not assessment.get('legal_verified') or not assessment.get('scope_verified') or assessment.get('legal_status')!='有效/待生效':
        return {'score':None,'priority':'待评分','reason':'效力、业务适用性或生效状态尚未核实'}
    if not isinstance(assessment.get('official_evidence_url'),str) or not assessment['official_evidence_url'].startswith('https://'):
        return {'score':None,'priority':'待评分','reason':'缺少可核验的官方依据链接'}
    for k in keys:
        v=assessment.get(k)
        if type(v) is not int or not 0<=v<=5:
            return {'score':None,'priority':'待评分','reason':f'{k} 缺少0—5分可核验证据'}
        if not isinstance(assessment.get(k+'_reason'),str) or len(assessment[k+'_reason'].strip())<5:
            return {'score':None,'priority':'待评分','reason':f'{k} 缺少评分依据说明'}
    score=sum(weights[i]*assessment[k] for i,k in enumerate(keys))
    priority=priority_from_score(score)
    forced=bool(assessment.get('blocking_verified') and assessment.get('business_affected_verified') and
        assessment.get('deadline_within_90_days_verified') and assessment.get('consequence_verified'))
    if forced:priority='P0 极高'
    return {'score':score,'base_priority':priority_from_score(score),'priority':priority,
            'forced_p0':forced,'reason':'已核验评分；需持续复核法规更新及企业业务变化'}


def apply_assessments(items:list[dict],manual_assessments:dict) -> list[dict]:
    for item in items:
        assessment=manual_assessments.get(item['id'])
        if assessment:
            result=score_verified_assessment(assessment)
            item['importance_score']=result.get('score');item['priority']=result['priority']
            item['scoring_note']=result['reason']
            if result['score'] is not None:
                item['legal_force_confirmed']=True;item['scope_confirmed']=True
                item['document_status']=assessment.get('legal_status','待核实')
                item['effective_at']=assessment.get('effective_at')
                item['assessment']={k:assessment[k] for k in ('D','B','C','A','U')}
    return items


def unique_records(records:list[dict]) -> list[dict]:
    out={}
    for r in records:
        if r['id'] not in out or r.get('observed_at','')>out[r['id']].get('observed_at',''):
            out[r['id']]=r
    return sorted(out.values(),key=lambda r:(r.get('importance_score') or -1,r.get('review_rank',0),r.get('published_at') or ''),reverse=True)
