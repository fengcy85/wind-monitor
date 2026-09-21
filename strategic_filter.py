from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
DATA = BASE / "site" / "data"
NEWS_FILE = DATA / "news.json"
STATUS_FILE = DATA / "source_status.json"
META_FILE = DATA / "meta.json"
CSV_FILE = DATA / "wind_news.csv"
REPORT_FILE = DATA / "filter_report.json"
RULES_FILE = BASE / "monitor_rules.json"

TAG_ORDER = ["竞对资讯", "招标采购", "市场机制", "电网建设", "宏观政策", "审批政策"]

DEFAULT_RULES = {
    "competitors": [
        "Vestas", "Siemens Gamesa", "Nordex", "GE Vernova", "Goldwind",
        "Envision", "MingYang", "SANY Renewable Energy", "Sany", "Suzlon",
        "Enercon", "Windey", "CRRC", "Dongfang Electric", "Shanghai Electric",
        "CSSC Haizhuang", "Ørsted", "Orsted", "RWE", "Iberdrola",
        "EDF Renewables", "ENGIE", "Engie", "Equinor", "BP", "TotalEnergies",
        "Copenhagen Infrastructure Partners", "CIP", "Masdar", "ACWA Power",
        "Adani Green", "ReNew", "NTPC Renewable Energy", "NTPC REL", "EnBW",
        "Statkraft", "EDP Renewables", "Vattenfall", "Ocean Winds",
        "SSE Renewables", "Mainstream Renewable Power", "Corio Generation",
        "Skyborn", "Invenergy", "Pattern Energy", "Brookfield",
    ],
    "wind_terms": [
        "wind", "wind power", "wind energy", "wind farm", "windfarm",
        "offshore wind", "onshore wind", "turbine", "turbines", "repowering",
        "风电", "风能", "风机", "风力发电", "海上风电", "陆上风电",
        "eólica", "eolica", "eólico", "eolico", "energia eólica", "energia eolica",
        "éolien", "éolienne", "windkraft", "windenergie", "rüzgar", "ruzgar",
        "ветр", "ветро", "điện gió", "dien gio", "gió ngoài khơi",
        "طاقة الرياح", "رياح", "shamol",
    ],
    "negative_energy_terms": [
        "solar", "photovoltaic", " pv ", "battery", "bess", "hydrogen",
        "hydropower", "hydro power", "geothermal", "biomass", "nuclear",
    ],
    "low_value_terms": [
        "40 under 40", "newsletter", "podcast", "webinar", "photo gallery",
        "sponsored content", "people 10.", "people 09.", "people 08.", "people 07.",
        "how to", "career", "jobs", "event registration", "conference agenda",
    ],
}

GENERIC_TITLES = {
    "news", "latest news", "home", "homepage", "projects", "project",
    "international news", "wind power", "onshore wind", "offshore wind",
    "renewable energy", "hybrids and energy storage", "media & news",
}
DATE_ONLY_RE = re.compile(
    r"^(?:"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}"
    r"|\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+20\d{2}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r")$",
    re.I,
)
DOMAIN_TITLE_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?[a-z0-9.-]+\.(?:com|net|org|news|eu|it|vn|in|gov)(?:/)?$",
    re.I,
)
CAPACITY_RE = re.compile(r"(?<![\w.])(\d{1,4}(?:[.,]\d+)?)\s*[-–—]?\s*(GW|MW)\b", re.I)

TAG_PATTERNS = {
    "招标采购": [
        "tender", "auction", "bid", "bidding", "invites bids", "invited bids",
        "floats tender", "call for tender", "call for tenders", "procurement",
        "rfp", "rfq", "request for proposal", "request for proposals",
        "awards contract", "awarded contract", "wins contract", "won contract",
        "supply contract", "supply agreement", "turbine order", "wind turbine order",
        "epc contract", "o&m contract", "framework agreement", "procure ",
        "招标", "投标", "采购", "竞标", "拍卖", "中标", "授标", "订单", "供货合同",
    ],
    "市场机制": [
        "electricity price", "power price", "energy price", "tariff", "feed-in tariff",
        "feed in tariff", "fit scheme", "cfd", "contract for difference",
        "strike price", "auction price", "bid price", "price cap", "price floor",
        "capacity market", "power market", "electricity market", "balancing market",
        "balancing service", "market reform", "market mechanism", "support scheme",
        "subsidy", "premium scheme", "green certificate", "renewable certificate",
        "renewable purchase obligation", "rpo", "ppa framework", "ppa mechanism",
        "curtailment compensation", "negative price", "settlement mechanism",
        "电价", "上网电价", "市场机制", "电力市场", "容量市场", "差价合约",
        "补贴机制", "竞价机制", "结算机制", "绿色证书", "消纳责任权重",
    ],
    "电网建设": [
        "transmission", "transmission line", "transmission network", "grid connection",
        "grid-connected", "grid expansion", "grid upgrade", "grid reinforcement",
        "grid infrastructure", "power grid", "grid code", "connection queue",
        "interconnection", "interconnector", "substation", "offshore substation",
        "hvdc", "export cable", "cable route", "network operator", "tso ",
        "电网", "输电", "输电线路", "并网", "联网", "互联", "升压站", "变电站",
        "海缆", "送出工程", "接网", "电网建设", "电网升级", "柔直",
    ],
    "宏观政策": [
        "energy strategy", "renewable strategy", "wind strategy", "national strategy",
        "energy plan", "renewable plan", "national plan", "climate plan",
        "roadmap", "wind target", "renewable target", "capacity target",
        "government target", "national target", "energy policy", "renewable policy",
        "industrial policy", "local content", "local-content", "localisation",
        "localization", "domestic content", "manufacturing requirement",
        "domestic manufacturing", "environmental policy", "climate policy",
        "energy law", "renewable energy law", "offshore wind framework",
        "macro policy", "national programme", "national program",
        "能源战略", "能源规划", "风电规划", "发展规划", "路线图", "装机目标",
        "可再生能源目标", "本地化", "本土化", "本地含量", "国产化要求",
        "产业政策", "环保政策", "环境政策", "气候政策", "能源法", "可再生能源法",
        "国家政策", "宏观政策",
    ],
    "审批政策": [
        "permit", "permitting", "permitting reform", "planning consent",
        "development consent", "development consent order", " dco ",
        "environmental approval", "environmental permit", "environmental clearance",
        "environmental impact assessment", " eia ", "licence", "license",
        "licensing", "authorisation", "authorization", "approval", "approved",
        "regulatory approval", "seabed lease", "seabed leasing", "marine licence",
        "marine license", "court ruling", "judicial review", "planning approval",
        "项目审批", "审批", "核准", "许可", "环境许可", "环境审批", "环评",
        "规划许可", "建设许可", "开发许可", "海域使用", "海床租赁", "监管批准",
    ],
}

COMPETITOR_ACTIONS = [
    "wins", "win ", "won ", "awarded", "award ", "secures", "secure ",
    "signs", "sign ", "contract", "order", "supply", "procure", "develop",
    "development", "build", "construction", "commission", "inaugurat",
    "installs", "installation", "acquire", "acquisition", "buys", "buy ",
    "sells", "sell ", "invest", "investment", "finance", "financing",
    "joint venture", "partners", "partnership", "launches", "enters",
    "expands", "project", "wind farm", "turbine", "settlement", "dispute",
    "中标", "获得", "签署", "合同", "订单", "供货", "开发", "建设", "投运",
    "安装", "收购", "出售", "投资", "融资", "合资", "进入", "项目",
]

POLICY_AUTHORITY_TERMS = [
    "government", "ministry", "minister", "regulator", "authority", "commission",
    "parliament", "cabinet", "court", "agency", "department", "state council",
    "政府", "能源部", "环境部", "监管机构", "委员会", "议会", "内阁", "法院",
]

ANALYSIS_ONLY_HINTS = [
    "opinion", "interview", "takeaways", "explainer", "podcast", "webinar",
    "rank for investors", "what investors need to know", "five ways to",
]


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def load_rules() -> dict:
    rules = dict(DEFAULT_RULES)
    external = load_json(RULES_FILE, {})
    if isinstance(external, dict):
        for key, value in external.items():
            if isinstance(value, list):
                rules[key] = value
    return rules


RULES = load_rules()
COMPETITORS = [clean(x) for x in RULES["competitors"] if clean(x)]
WIND_TERMS = [clean(x).lower() for x in RULES["wind_terms"] if clean(x)]
NEGATIVE_ENERGY_TERMS = [clean(x).lower() for x in RULES["negative_energy_terms"] if clean(x)]
LOW_VALUE_TERMS = [clean(x).lower() for x in RULES["low_value_terms"] if clean(x)]


def contains(text: str, term: str) -> bool:
    text = text.lower()
    term = term.lower()
    if len(term.strip()) <= 3 and term.strip().isalnum():
        return re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)", text) is not None
    return term in text


def contains_any(text: str, terms: list[str]) -> bool:
    return any(contains(text, t) for t in terms)


def count_terms(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if contains(text, t))


def strong_wind_title(title: str) -> bool:
    t = clean(title).lower()
    if not t:
        return False
    if re.search(
        r"\b(?:"
        r"wind(?:\s+(?:power|energy|farm|farms|project|projects|market|markets|industry|sector|turbine|turbines))?"
        r"|windfarm|offshore(?:\s+wind)?|onshore(?:\s+wind)?|turbines?|repowering"
        r")\b",
        t,
        re.I,
    ):
        return True
    local = [x for x in WIND_TERMS if x not in {
        "wind", "wind power", "wind energy", "wind farm", "windfarm",
        "offshore wind", "onshore wind", "turbine", "turbines", "repowering",
    }]
    return any(x in t for x in local)


def wind_relevance(item: dict) -> tuple[int, list[str]]:
    title = clean(item.get("title"))
    zh = clean(item.get("translated_title"))
    excerpt = clean(item.get("excerpt"))[:1800]
    headline = f"{title} {zh}"
    full = f"{headline} {excerpt}"
    score = 0
    reasons: list[str] = []

    if strong_wind_title(title):
        score += 8
        reasons.append("标题明确风电")
    elif strong_wind_title(zh):
        score += 7
        reasons.append("中文标题明确风电")
    else:
        body_hits = count_terms(excerpt.lower(), WIND_TERMS)
        if body_hits >= 2:
            score += 4
            reasons.append("正文明确涉及风电")
        elif body_hits == 1:
            score += 2

    title_low = title.lower()
    negatives = [x for x in NEGATIVE_ENERGY_TERMS if contains(title_low, x)]
    if negatives and not strong_wind_title(title):
        score -= 10
        reasons.append("标题以其他能源为主")
    elif len(negatives) >= 2:
        score -= 3

    if contains_any(title_low, LOW_VALUE_TERMS):
        score -= 7
        reasons.append("低价值内容")
    if contains_any(title_low, ANALYSIS_ONLY_HINTS):
        score -= 2

    return score, reasons


def max_capacity_mw(text: str) -> float:
    values = []
    for number, unit in CAPACITY_RE.findall(text):
        try:
            value = float(number.replace(",", "."))
        except ValueError:
            continue
        if unit.upper() == "GW":
            value *= 1000
        if 0 < value < 100000:
            values.append(value)
    return max(values, default=0.0)


def bad_title(title: str) -> bool:
    raw = clean(title).strip(" -–—|:_")
    low = raw.lower()
    if not low or len(low) < 10:
        return True
    if low in GENERIC_TITLES or DATE_ONLY_RE.fullmatch(low) or DOMAIN_TITLE_RE.fullmatch(low):
        return True
    if re.fullmatch(r"(?:projects?|news|latest|international news|wind power|onshore wind|offshore wind)", low):
        return True
    return False


def valid_url(url: str) -> bool:
    try:
        p = urlparse(clean(url))
        if p.scheme not in {"http", "https"} or not p.netloc:
            return False
        low = (p.path or "/").lower()
        if low in {"", "/"}:
            return False
        if any(x in low for x in ["/category/", "/tag/", "/search", "/author/", "/page/"]):
            return False
        return True
    except Exception:
        return False


def competitor_hits(text: str) -> list[str]:
    hits = []
    for company in COMPETITORS:
        if contains(text, company):
            hits.append(company)
    return hits


def classify(item: dict) -> tuple[list[str], int, list[str]]:
    title = clean(item.get("title"))
    zh = clean(item.get("translated_title"))
    excerpt = clean(item.get("excerpt"))[:1800]
    headline = f"{title} {zh}"
    full = f"{headline} {excerpt}"
    low_head = headline.lower()
    low_full = full.lower()

    tags: list[str] = []
    reasons: list[str] = []
    score = 0

    # 招标采购：标题命中最可靠；正文命中时需同时存在风电或明确采购动作。
    tender_head_hits = [x for x in TAG_PATTERNS["招标采购"] if contains(low_head, x)]
    tender_full_hits = [x for x in TAG_PATTERNS["招标采购"] if contains(low_full, x)]
    if tender_head_hits:
        tags.append("招标采购")
        score += 11
        reasons.append("招标/采购节点")
    elif tender_full_hits and (strong_wind_title(title) or strong_wind_title(zh)):
        tags.append("招标采购")
        score += 7
        reasons.append("正文含招标/采购")

    # 市场机制：电价、CfD、容量市场、证书、补贴、结算等。
    market_head = [x for x in TAG_PATTERNS["市场机制"] if contains(low_head, x)]
    market_full = [x for x in TAG_PATTERNS["市场机制"] if contains(low_full, x)]
    if market_head:
        tags.append("市场机制")
        score += 12
        reasons.append("电价/市场机制")
    elif market_full and contains_any(low_full, POLICY_AUTHORITY_TERMS):
        tags.append("市场机制")
        score += 8
        reasons.append("政策正文涉及市场机制")

    # 电网建设：送出、输电、变电站、海缆、HVDC、并网、grid code 等。
    grid_head = [x for x in TAG_PATTERNS["电网建设"] if contains(low_head, x)]
    grid_full = [x for x in TAG_PATTERNS["电网建设"] if contains(low_full, x)]
    if grid_head:
        tags.append("电网建设")
        score += 10
        reasons.append("电网/送出建设")
    elif grid_full and (strong_wind_title(title) or strong_wind_title(zh)):
        tags.append("电网建设")
        score += 7
        reasons.append("项目涉及电网/送出")

    # 宏观政策：目标、路线图、本地化、产业政策、环保政策等。
    macro_head = [x for x in TAG_PATTERNS["宏观政策"] if contains(low_head, x)]
    macro_full = [x for x in TAG_PATTERNS["宏观政策"] if contains(low_full, x)]
    if macro_head:
        tags.append("宏观政策")
        score += 12
        reasons.append("宏观/产业政策")
    elif macro_full and contains_any(low_full, POLICY_AUTHORITY_TERMS):
        tags.append("宏观政策")
        score += 8
        reasons.append("政策正文涉及风电")

    # 审批政策：EIA、planning consent、许可、监管批准、海床租赁等。
    approval_head = [x for x in TAG_PATTERNS["审批政策"] if contains(low_head, x)]
    approval_full = [x for x in TAG_PATTERNS["审批政策"] if contains(low_full, x)]
    if approval_head:
        tags.append("审批政策")
        score += 11
        reasons.append("审批/许可节点")
    elif approval_full and (strong_wind_title(title) or strong_wind_title(zh)):
        tags.append("审批政策")
        score += 7
        reasons.append("正文涉及审批/许可")

    # 竞对资讯：必须是已配置厂商 + 明确的项目/订单/投资/建设动作。
    companies = competitor_hits(full)
    actions = [x for x in COMPETITOR_ACTIONS if contains(low_full, x)]
    if companies and actions:
        tags.append("竞对资讯")
        score += 9
        reasons.append("竞对项目动态")
        item["_competitor_names"] = companies[:4]

    # 大体量项目若同时有竞对/招标/审批/电网节点，进一步提升优先级。
    cap = max_capacity_mw(full)
    if cap >= 1000:
        score += 6
        reasons.append("GW级项目")
    elif cap >= 500:
        score += 4
        reasons.append("500MW+项目")
    elif cap >= 100:
        score += 2

    # 多标签通常意味着战略相关性更高。
    if len(set(tags)) >= 2:
        score += 3

    # 分析/观点类只在有明确政策机制或竞对事件时保留。
    if contains_any(low_head, ANALYSIS_ONLY_HINTS):
        if not any(t in tags for t in ["市场机制", "宏观政策", "审批政策", "竞对资讯"]):
            score -= 7

    deduped = [t for t in TAG_ORDER if t in set(tags)]
    return deduped, score, reasons


def keep_item(item: dict) -> tuple[bool, dict, list[str]]:
    title = clean(item.get("title"))
    if bad_title(title):
        return False, item, ["标题无效/栏目标题"]
    if not valid_url(item.get("url", "")):
        return False, item, ["非新闻详情页链接"]

    wind_score, wind_reasons = wind_relevance(item)
    if wind_score < 6:
        return False, item, wind_reasons or ["风电相关性不足"]

    tags, strategic_score, reasons = classify(item)
    if not tags:
        return False, item, ["无六类监测标签", *wind_reasons]
    if strategic_score < 7:
        return False, item, ["战略相关性不足", *reasons]

    out = dict(item)
    out["tags"] = tags
    out["strategic_score"] = strategic_score
    out["monitor_reason"] = " · ".join(dict.fromkeys(reasons + wind_reasons[:1]))
    if item.get("_competitor_names"):
        out["competitors"] = item["_competitor_names"]
    else:
        companies = competitor_hits(f"{title} {clean(item.get('translated_title'))} {clean(item.get('excerpt'))[:1200]}")
        if companies:
            out["competitors"] = companies[:4]
    out.pop("_competitor_names", None)
    return True, out, []


def save_csv(news: list[dict]) -> None:
    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["国家/地区", "来源", "标签", "原标题", "中文标题", "发布日期", "原文链接"])
        for x in news:
            w.writerow([
                x.get("country", ""),
                x.get("source_name", ""),
                " / ".join(x.get("tags", [])),
                x.get("title", ""),
                x.get("translated_title", ""),
                x.get("published_at", ""),
                x.get("url", ""),
            ])


def main() -> None:
    news = load_json(NEWS_FILE, [])
    if not isinstance(news, list):
        news = []

    kept: list[dict] = []
    rejected: list[dict] = []
    tag_counts = Counter()

    for item in news:
        ok, cleaned, reasons = keep_item(item)
        if ok:
            kept.append(cleaned)
            tag_counts.update(cleaned.get("tags", []))
        else:
            rejected.append({
                "title": clean(item.get("title")),
                "source_name": clean(item.get("source_name")),
                "published_at": clean(item.get("published_at")),
                "url": clean(item.get("url")),
                "reasons": reasons[:3],
            })

    kept.sort(key=lambda x: (x.get("published_at", ""), x.get("fetched_at", "")), reverse=True)
    NEWS_FILE.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    save_csv(kept)

    statuses = load_json(STATUS_FILE, [])
    if isinstance(statuses, list):
        by_source = Counter(x.get("source_name", "") for x in kept)
        for row in statuses:
            row["strategic_kept_total"] = int(by_source.get(row.get("name", ""), 0))
        STATUS_FILE.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = load_json(META_FILE, {})
    if not isinstance(meta, dict):
        meta = {}
    meta.update({
        "strategic_filter_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "articles_before_strategic_filter": len(news),
        "articles_after_strategic_filter": len(kept),
        "articles_filtered_out": len(rejected),
        "strategic_tag_counts": dict(tag_counts),
        "filter_version": "4.6",
    })
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT_FILE.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "before": len(news),
                "kept": len(kept),
                "filtered_out": len(rejected),
                "tag_counts": dict(tag_counts),
                "rejected_examples": rejected[:80],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"strategic filter: before={len(news)} kept={len(kept)} "
        f"filtered={len(rejected)} tags={dict(tag_counts)}"
    )


if __name__ == "__main__":
    main()
