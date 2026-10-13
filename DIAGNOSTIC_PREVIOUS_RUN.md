# 已公开的上一轮抓取异常记录（不是本补丁运行结果）

本报告依据 2026-10-13 GitHub 公开仓库中的 `site/data/policy_source_status.json` 状态快照，以及对若干公开站点的访问验证整理。**由于执行环境无法直接联网运行 GitHub Actions，本补丁没有证据证明以下站点已经修好，不能把下面的状态写成“成功”。**

| 来源 | 原入口 | 上次错误类型 | 本次补丁可采取的处理 |
| --- | --- | --- | --- |
| 天津发展改革委 | https://fzgg.tj.gov.cn/zwgk_47325/zcfg_47338/zcwjx/fgwj/ | HTTP 403 | 记录禁止访问；若提供官方开放栏目/接口可配置替代入口，不绕过限制 |
| 天津工业和信息化局 | https://gyxxh.tj.gov.cn/ZWGK4147/ZCWJ6355/wjwj/ | HTTP 403 | 同上；外部检查仍观察到 403 |
| 河北省人民政府 | https://www.hebei.gov.cn/columns/49f13cc2-db03-4d0c-b4fe-2f3f659d3b6e/index.html | 连接失败 | 超时/连接错误单独归类；复核官方栏目路径 |
| 河北省发改委 | https://hbdrc.hebei.gov.cn/jhlm/jhzcfg/index.html | 连接失败 | 同上；外部检查亦发生超时 |
| 山西省人民政府 | https://www.shanxi.gov.cn/zcwjk/swwj/ | 连接失败 | 同上 |
| 山西省能源局 | https://nyj.shanxi.gov.cn/zfxxgk/fdzdgknr/sjwj/fdzdgknr_11759.html | 连接失败 | 同上 |
| 山西省工信厅 | https://gxt.shanxi.gov.cn/zcwj/wjfb/ | 连接失败 | 同上 |
| 山西省发改委 | https://fgw.shanxi.gov.cn/sxfgwzwgk/sxsfgwxxgk/xxgkml/zfxxgkxgwj/ | 连接失败 | 同上 |
| 内蒙古自治区人民政府 | https://www.nmg.gov.cn/zwgk/zfxxgk/zfxxgkml/?gk=3&cid=3696 | HTTP 403 | 标记访问受限，待核验可用官方开放数据渠道 |

## 无法直接用 HTML 栏目抓取的代表性入口

- 国务院政策文件库与部门文件检索：动态/查询型入口（本补丁开始尝试读取页面和公共浏览器渲染，不再直接全部跳过；具体查询接口仍要逐站验证）
- 全国标准信息公共服务平台与国家标准全文公开平台：标准检索接口，不能靠抓首页来证明标准变更已经被覆盖
- IEA 政策数据库：公开页面可显示列表，但它同时包含大量非风电历史政策；本补丁尝试目录链接、详情内容、发布日期核验及时间窗口过滤
- 单篇文章与 PDF 链接：只能作为具体文件来源，不能充当持续更新目录

## 运行后验收要求

首次 `policy_days=14` 的 GitHub Actions 完成后，查看 `site/data/policy_source_status.json`：

- `ok_with_results`：确认了近期有具体文件，发布日已从元数据/列表/页面取得。
- `ok_no_recent`：栏目读取正常，但在本次窗口内没有确认到符合条件的法规。
- `ok_no_candidates`：只确认网页可读，没有证据证明政策链接被抓取完整。
- `needs_adapter`：动态搜索或不合适入口，不能计入已完成适配。
- `blocked`、`timeout`、`connection_error`、`detail_error`：真正的失败，应继续逐站排查。

**只有后续运行结果与原文抽样核对均通过，才能说某网站已完成适配。**
