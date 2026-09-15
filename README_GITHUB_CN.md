# 海外风电动态监测 V4 — GitHub 全免费公网版

## 这版和 V3 最大的区别

V3 是“服务器一直运行”。

V4 改成：

```text
GitHub Actions 每天自动运行一次
        ↓
抓取 16 个海外网站
        ↓
保存最新新闻数据
        ↓
GitHub Pages 自动发布网页
        ↓
任何人通过公开网址查看
```

因此不需要购买服务器，也不需要购买域名。

最终网址通常类似：

```text
https://你的GitHub用户名.github.io/wind-monitor/
```

---

## 你需要准备

只需要一个 GitHub 免费账号。

推荐创建一个：

```text
Public
```

公开仓库。

项目监测的本身都是公开网站和公开新闻，因此这个版本就是按公开数据看板设计的。

---

## 第一次部署

### 1. 创建仓库

GitHub：

```text
+ → New repository
```

仓库名称建议：

```text
wind-monitor
```

选择：

```text
Public
```

创建。

### 2. 上传项目

解压 V4 ZIP。

把 `wind_monitor_v4_github` 文件夹里面的内容上传到仓库根目录。

最终仓库根目录应该直接看到：

```text
.github/
site/
scrape.py
sources.json
requirements.txt
README_GITHUB_CN.md
00_第一次使用_请看我.txt
```

#### Mac 看不到 `.github` 怎么办？

Finder 同时按：

```text
Command + Shift + .
```

即可显示隐藏文件夹。

`.github` 必须上传，否则 GitHub 不知道如何每天自动抓取。

### 3. 开启 GitHub Pages

仓库：

```text
Settings → Pages
```

在：

```text
Build and deployment
```

把 Source 设置成：

```text
GitHub Actions
```

### 4. 第一次运行

仓库：

```text
Actions
```

选择：

```text
Update wind news and deploy
```

点击：

```text
Run workflow
```

第一次运行会下载依赖和 Chromium，因此需要几分钟。

### 5. 打开网页

工作流变成绿色 ✓ 后：

```text
Settings → Pages
```

即可看到公开网址。

---

## 每天怎么自动更新

工作流文件：

```text
.github/workflows/update-and-deploy.yml
```

设置为：

```yaml
cron: "0 0 * * *"
```

GitHub 的 cron 使用 UTC。

所以大致对应：

```text
北京时间每天 08:00
```

定时任务不是秒级准点执行，GitHub 繁忙时可能出现排队延迟。

---

## 如果想立即抓取

V4 没有一直运行的后台服务器。

因此网页访客不能点击一个按钮来启动 Python 爬虫。

仓库管理员可以：

```text
GitHub → Actions
→ Update wind news and deploy
→ Run workflow
```

运行完成后网页自动更新。

---

## 页面现在显示什么

每条新闻包含：

- 国家/地区
- 来源
- 中文标题
- 原标题
- 发布日期
- 中文翻译（原文节选）
- 原文节选
- 查看完整原文

网页支持：

- 关键词搜索
- 国家/地区筛选
- 新闻来源筛选
- 7 天 / 30 天 / 90 天 / 1 年筛选
- CSV 导出
- 16 个来源状态监控

---

## 原文链接改进

V2/V3 中出现过栏目页或首页被当成“原文”的问题。

V4 会拒绝：

- 网站根首页
- 配置的栏目入口
- `/category/`
- `/tag/`
- `/search`
- `/author/`
- 登录页
- Feed 页面

只有通过文章详情页检查、日期检查和风电内容检查的 URL 才保存。

canonical URL 也会重新检查。

---

## 原文节选

系统读取公开文章详情页中的少量正文段落。

默认上限：

```text
约 900 个字符
```

然后尝试生成中文翻译。

这不是复制完整文章。

完整内容仍然通过：

```text
查看完整原文
```

进入来源网站。

Recharge 等付费媒体不会绕过订阅墙。

---

## 数据为什么不会每天消失

抓取完成后 GitHub Actions 会把下面的数据提交回你的仓库：

```text
site/data/news.json
site/data/source_status.json
site/data/meta.json
site/data/wind_news.csv
```

下一次运行会先读取旧数据，再合并新文章。

默认：

- 最长保留约 730 天
- 最多保留 1500 条新闻
- 按原文 URL 去重

---

## 16 个来源状态

网页底部有三种状态：

### 绿色：抓取成功

本轮找到了符合条件的风电动态。

### 灰色：暂无相关新闻

网站可以正常访问，但本轮没有发现符合监测规则的风电内容。

### 红色：抓取异常

页面访问、反爬、Cloudflare、网站改版或其他原因导致本轮失败。

这样不会把：

```text
今天没有新闻
```

误认为：

```text
抓取器坏了
```

---

## 一个需要知道的现实限制

GitHub Actions 是云端运行器。

个别网站可能对：

- 云数据中心 IP
- 自动化浏览器
- Cloudflare
- 访问频率

有限制。

所以“免费公网版”不能保证 16 个网站永久 100% 无维护。

V4 会把失败来源标红，后续可以针对单个来源修复，不影响整个网页。

---

## 项目主要文件

```text
scrape.py
```
每天运行的抓取程序。

```text
sources.json
```
16 个海外信息源配置。

```text
.github/workflows/update-and-deploy.yml
```
GitHub 自动运行和自动发布配置。

```text
site/
```
最终公开网页。

```text
site/data/news.json
```
新闻数据库（静态 JSON）。

```text
site/data/wind_news.csv
```
可下载 CSV。

---

## 修改运行时间

例如要北京时间每天 09:00：

北京时间 = UTC + 8，因此设置 UTC 01:00：

```yaml
- cron: "0 1 * * *"
```

修改：

```text
.github/workflows/update-and-deploy.yml
```

---

## 最终你需要记住的只有两个地方

### 看网页

```text
https://你的用户名.github.io/wind-monitor/
```

### 想立刻更新

```text
GitHub → wind-monitor → Actions
→ Update wind news and deploy → Run workflow
```
