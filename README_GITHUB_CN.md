# 风电法规政策与战略情报监测 — 最终部署包操作说明

**不需要购买服务器、注册域名，也不需要让 Mac 始终开机。** 网站继续使用现有 GitHub 仓库 `fengcy85/wind-monitor` 和 GitHub Pages 公网地址：<https://fengcy85.github.io/wind-monitor/>。

## 一、这次完整包包含什么

- 原有 `scrape.py`、`sources.json`、`strategic_filter.py`、`weekly_focus.py`：保留海外行业新闻监测及历史数据。
- **新法规主流程** `policy_scrape.py`、`policy_crawler.py`、`policy_logic.py`：按来源目录、HTML/RSS/PDF、JS 备用浏览器、条款筛选、去重、影响初筛及评分运行。
- `policy_sources.json`：**121 条原附件网址，含国内 100 条、海外及国际 21 条**，按具体来源保留官网原始链接。登记成功不等于当场抓取成功。
- `regulatory_dimensions.json`：附件中的 **18 个法规维度，网页使用完整中文名称**。
- `policy_assessments.json`：手动核验 D/B/C/A/U 证据的可选录入文件，缺少证据时不会虚构评分。
- `site/`：法规条款、影响与行动建议、行业新闻、来源抓取诊断的静态网站；已有 `site/data/news.json` 保留。
- `.github/workflows/update.yml`：自动执行测试 → 法规抓取 → 行业抓取 → 更新数据 → 发布网站。
- `tests/`：离线可重复测试。
- `prompts/`：根据提供的法规抓取、排序、影响分析文件转成规则说明供核验。

## 二、Mac 上覆盖现有 GitHub 仓库（最稳妥：GitHub Desktop）

**先备份已有仓库数据。不要删除已有 GitHub 仓库。**

1. 下载本 ZIP 并解压，打开其中的 `wind-monitor-main` 文件夹。
2. 安装并打开 [GitHub Desktop](https://desktop.github.com/)，登录与你当前仓库关联的 GitHub 账号。
3. 在 GitHub Desktop 中使用 `File → Clone Repository`，选择 `fengcy85/wind-monitor` 克隆到 Mac。
4. 在 GitHub Desktop 菜单 `Repository → Show in Finder`，打开克隆的仓库目录。
5. 从新解压的 **`wind-monitor-main` 文件夹内部**，将文件及文件夹复制进克隆的仓库 **根目录**，允许同名文件替换。**不要把外层 `wind-monitor-main` 再套一层！**
6. Mac 的 `.github` 是隐藏目录。Finder 可按 `Command + Shift + .` 显示；确保 `.github/workflows/update.yml` 真正进入克隆仓库。
7. 返回 GitHub Desktop，检查 Changes 是否含 `policy_crawler.py`、`policy_logic.py`、`policy_scrape.py`、`site/static/app.js`、`.github/workflows/update.yml` 等文件；输入提交说明，点 **Commit to main**，然后点 **Push origin**。
8. 打开 <https://github.com/fengcy85/wind-monitor/actions> → `Update wind policy intelligence and deploy` → `Run workflow` → 选择 `main` → `Run workflow`。
9. 若 GitHub Pages 还未选择 Actions 发布，进入 GitHub 仓库 `Settings → Pages → Build and deployment → Source`，选择 **GitHub Actions**。
10. 等 `update-and-deploy` 全部显示绿色成功后，打开 <https://fengcy85.github.io/wind-monitor/>。若页面缓存旧版可强制刷新。

## 三、定时安排与费用

- 已配置 **北京时间每天约 08:13** 触发（UTC `00:13`），避开整点请求拥堵。实际触发时间可能因 GitHub 排队或公开仓库停用而延迟。可以随时在 Actions 中手动执行。
- 在公开仓库里使用 GitHub Pages / GitHub Actions 的 GitHub 免费账户方案时可按平台当前政策运行，但资源用量、平台排队及计划额度以 GitHub 官方规则为准。
- 公开仓库如果长期无活动，GitHub 可能自动停止定时任务；请检查 Actions 是否仍启用。
- 无需填写付费 AI API 密钥。法规影响是**本地规则辅助初筛**，不是模型自动完成法律核验；完整企业专属评分需要补齐业务证据或接入受控分析服务。

## 四、海外网站适配与失败说明

每条法规渠道都分配 `html_pdf` / `browser_html_pdf` / `search_portal` 三种模式之一，并保留英文等多语言关键词；实际抓取按站点自动尝试：

**官网栏目 → HTML 解析 → 官网公开 RSS / GET 搜索表单 → robots.txt 声明的 sitemap → JS 公开页浏览器回退（需要时） → 正文与文本 PDF → 条款级筛选**。

- `search_portal` 代表检索型政府法规数据库。部分站只在输入特定搜索条件后才返回结果；未取得官网 API/RSS 或有效的公共检索表单时会显示“检索接口待适配”，**不会谎报已覆盖**。
- 官网返回 403 / 429、禁止 robots、要求登录或验证码时，系统不绕过访问限制；如文本 PDF 扫描不可读取，会记录“可能需要 OCR”。
- 海外网站（如 EUR-Lex）可提供注册制接口或用户自定义 RSS；没有授权信息时无法承诺持续完整抓取。详见 `海外渠道适配说明.md`。
- 首页能访问仅代表 `http_success=true`；只有有明确法律文本证据的条款才进入候选记录。
- 某个网站本轮失败，已存法规记录不会被清空；网站上查看“信息源与抓取诊断”。

## 五、法规内容如何显示？

- 一条候选条款展示：国家地区、原文名称、18 维度中文标签、条款编号/原文证据、产品设计影响、商业模式影响、应对建议及官方跳转网址。
- 征求意见稿 / 通用法规与风电关联仍需证明的条款，进入“政策观察与待核实线索”，不会当成强制法规发布。
- 候选条款先按 **初筛审阅顺序** 排列。只有当真实证据完整确认 D/B/C/A/U 五维各 0—5 分、法律效力、公司适用性后才启用正式 `6D+5B+4C+3A+2U` 分值与 P0—P4；强制 P0 升级保留原始分。
- 这并不等于已经配备会独立完成法律分析的 AI；无模型 API、公司内部产品与目标市场数据时，不应自动编造具体法定阈值或业务后果。

## 六、查看失败原因

GitHub Actions 中打开最新一次 `update-and-deploy`：

- `Crawl policy and standards sources`：本轮政策抓取及各网站的状态汇总；
- `Update existing industry news`：原行业新闻抓取，失败不会阻止法规数据发布，但会显示警告；
- 网页的 **“信息源与抓取诊断”**：逐渠道的 `status`、`failure_details`、抓取页数、发现链接数、候选条款数和使用的适配方式；
- GitHub 仓库的 `site/data/policy_source_status.json`：机器可读的详细诊断清单。

出现 `search_adapter_required` 时，下一步需要补充该机构公开的真实栏目/API/RSS（如需账号授权则需由你依法提供），而不是一味延长超时时间。

## 七、本地测试（可选）

在包含这些文件的文件夹内执行：

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 -m http.server --directory site 8000
```

打开 <http://localhost:8000/>。注意：浏览器打开本地页面**不会自动执行抓取**，需要单独运行 `python3 policy_scrape.py`；正式线上每日执行由 GitHub Actions 负责。

**特别提醒：** 公网仓库不要提交公司内部机型方案、价格、未公开市场策略、目标市场明细或授权凭据。源码与页面公开可见。
