# 风电法规监测 V5.2：覆盖率、发布日期、表格与两周回溯

这是 **GitHub 仓库增量替换补丁**，不是完整项目。不要删除已有的 `policy_sources.json`、`regulatory_dimensions.json`、`scrape.py`、`sources.json` 和原有行业动态页面；这些文件仍由仓库中的现有版本提供。

## 如何更新 GitHub

1. 将本压缩包解压，将 `policy_scrape.py`、`site/index.html`、`site/static/policy.js`、`site/static/policy-table.css`、`.github/workflows/update.yml` 按相同目录路径上传/替换到 `main` 分支。**`site/static/policy.js` 如不存在，直接新增。**
2. 打开仓库根目录的 `requirements.txt`，保留现有内容，直接将包内 `requirements.txt` 同名替换（保留现有依赖，并新增 `pypdf>=5.0,<7`）。
3. 在仓库点击 **Actions → Update wind policies and news and deploy → Run workflow**，选择 `policy_days=14`。该次运行会尝试检查所有配置来源，而不是上版默认每次只查 24 个。
4. 运行完成后查看 `site/data/policy_source_status.json` 或网站“政策及标准来源目录”，对 `blocked`、`timeout`、`connection_error`、`detail_error`、`needs_adapter` 单独跟进。动态页会先尝试 Playwright 公共网页渲染；仍不能拿到明确政策记录时，不假报抓取成功。
5. 后续计划任务 **北京时间每日 08:00** 以滚动 `2` 天为范围检索（每天执行，并靠原文链接去重以防遗漏晚发布的文件）。首次安装补丁的第一次抓取，即使由计划任务触发，也会先执行 `14` 天回溯一次。除非手动选择更长范围，之后是每天定时执行的两日滚动增量，历史库累计保留。

## 主要改变

- 法规逐条展示为 **一条主表格行**，点击“详情”后再看证据、法规维度、设计/商业模式影响。页面可按日期、地域、类型、维度、效力等筛选，默认最近两周、排除明显失效的历史法规。
- `published_at` 只来自页面 meta / JSON-LD / time 标签 / 正文明确的发布日期 / 来源列表日期。**不能从“成文日期”或“实施日期”假装是发布日期**。无法核实的文件被送入 `policy_candidates_review.json`，不混入近期结果。首次安装还会重新请求最多 30 条旧版缺发布日期的历史候选，修补能找到官方发布日期的旧记录。
- 支持普通 HTML 栏目、正常的国际政策列表、政府页面常见 HTML 日期、部分 JavaScript 渲染，以及可提取文字的 PDF。图片扫描 PDF 和需官方检索接口的站点必须进一步适配。
- 含权限拒绝的 HTTP 403、超时、连接失败、详情失败等分类及日志；不中断其它来源。**不能保证任何网络条件下所有站点 100% 成功**，尤其是官方限制访问、需账户或验证码、网站本身宕机及不公开检索接口的情况。
- 每次运行会把准确的 `policy_sources.json` 和 `regulatory_dimensions.json` 同步到 `site/data/`，确保页面加载配置与后台一致。

## 本地快速测试

在仓库根目录运行：

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python policy_scrape.py --days 14 --source-limit 3
```

`--source-limit 3` 仅用于小规模本地试跑，生产工作流使用 `0`（全部来源）。

### 为什么不能把“网站能够访问”直接算作成功？

政策数据库、国家标准检索平台、单篇 PDF 或单篇文章，不一定能列出持续更新的政策。对于这些情况只能标记为“待适配”“日期待核实”或“无近期候选”，而不是伪造政策记录。所有待复核文件会保留在 review JSON 中供继续逐站排查。
