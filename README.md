# 全球金融数据看板（Global Finance Dashboard）

一个**纯本地运行**的全球金融行情聚合看板：一个 Python 文件负责抓数据，一个 HTML 页面负责展示。
无需数据库、无需 Node、无需任何账号或 API Key，双击即可启动。

所有行情均来自**公开、真实的数据源**，页面上每个板块、每个二级分类、每张 K 线图都**逐项标注了数据来源并附原始链接**，可点击跳转上游核对。

- 详细文档：[项目说明](docs/项目说明.md) ｜ [使用指南](docs/使用指南.md) ｜ [数据源说明](docs/数据源说明.md)

---

## ✨ 功能一览（9 大板块）

| # | 板块 | 规模 | 实时 | 分时 | 日K | 月K | 数据源 |
|---|------|------|:---:|:---:|:---:|:---:|--------|
| 1 | 汇率行情 | 191 个货币对（13 个币种分组） | ✅ | ✅ | ✅ | ✅ | 新浪财经 |
| 2 | 世界股市行情 | 中国 10（内地 6 + 中国香港 3 + 中国台湾 1）+ 亚太 7 + 欧洲 9 + 美洲 6 | ✅ | ✅ | ✅ | ✅ | 新浪 / 腾讯 / 金投网 |
| 3 | 美股三大指数与指数期货 | 3 指数 + 3 期货（CME 连续） | ✅ | ✅ | ✅ | ✅ | 金投网 / 新浪 |
| 4 | 虚拟币行情 | 12 个主流币种 | ✅ | ✅ | ✅ | ✅ | 新浪 / Gate.io |
| 5 | 国际期货行情 | 18 个品种（5 大类） | ✅ | ✅ | ✅ | ✅ | 新浪财经 |
| 6 | 人民币汇率指数 | CFETS / BIS / SDR | ✅ | — | — | — | 中国货币网 |
| 7 | 世界各国国债收益率 | 美 13 + 中 9 + 主要国家 35（每张卡片标注所属国家/地区） | ✅ | ✅ | ✅ | ✅ | 新浪财经 |
| 8 | 美国国债数据 | 8400+ 条逐日余额（1993 至今） | 走势图（3 类持有结构）+ 环比 / 月比 / 年比对比 + 表格分页（10/20/100 条）+ 全量 Excel 下载 | — | — | — | 美国财政部 |
| 9 | 各国央行利率 | 28 个国家 / 地区 | 表格 + 利率水平对比图（横向柱状图） | — | — | — | 金投网 |

**通用能力**
- **两级折叠**：板块标题栏（圆形箭头按钮）与板块内每个二级分类均可折叠 / 展开，状态分别记忆，页面不会过长
- 板块拖动排序 · 顶部 9 个板块导航按钮（每行 4 个）
- 点击卡片内联展开图表：分时 / 日K / 月K（MA5/MA20 + 缩放条），汇率另支持正 / 反向
- 美国国债支持**数据类型切换**（国债总额 / 公众持有 / 政府内部持有）与**时间范围切换**（近 3 月 ~ 近 10 年）
- 浅色 / 深色主题 · 30 秒自动刷新 · 涨红跌绿（A股配色习惯）

---

## 🚀 快速开始

### 方式一：免安装 EXE（Windows 推荐）
1. 到 [Releases](https://github.com/chenzhuanxin/global-finance-dashboard/releases) 下载 `全球金融数据看板-vX.X.exe`
2. **双击即用**——自动启动数据服务并打开浏览器 `http://127.0.0.1:8770/`，关闭窗口即退出
3. 已在运行时再次双击，会直接打开浏览器（不会重复起服务）

> 无需安装 Python；首次启动解压自释放约需 2~3 秒。

### 方式二：源码运行
1. 安装 [Python 3.8+](https://www.python.org/downloads/)（安装时勾选 *Add to PATH*）
2. 安装依赖：
   ```
   pip install requests openpyxl
   ```
   > `openpyxl` 用于导出 Excel；未安装时自动降级为 CSV 下载。
3. **双击 `启动.bat`** —— 自动启动服务并打开浏览器 `http://127.0.0.1:8770/`

### macOS / Linux
```bash
pip install requests openpyxl
python3 server.py
# 浏览器打开 http://127.0.0.1:8770/
```

### 可选参数
| 环境变量 | 默认 | 说明 |
|---|---|---|
| `GFD_PORT` | `8770` | 服务端口 |

局域网内其他设备访问：将 `server.py` 中监听地址改为 `0.0.0.0`，然后用本机 IP 访问（详见[使用指南 · 部署](docs/使用指南.md#8-部署到局域网或服务器)）。

---

## 📡 数据源（全部实测可用）

| 用途 | 数据源 | 链接 |
|------|--------|------|
| 汇率实时行情（191 对） | 新浪财经 · 外汇行情 | <https://finance.sina.com.cn/money/forex/hq/USDCNY.shtml> |
| 汇率日K / 分时 | 新浪财经 · 外汇历史行情 | <https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php> |
| A股指数实时（6） | 新浪财经 · 沪深指数 | <https://finance.sina.com.cn/realstock/company/sh000001/nc.shtml> |
| A股 / 港股 K线与分时 | 腾讯证券 | <https://gu.qq.com/> |
| 港股指数实时（3，含恒生系列） | 新浪财经 · 港股 | <https://stock.finance.sina.com.cn/hkstock/quotes/HSI.html> |
| 亚太 / 欧洲 / 美洲 + 中国台湾股指（23） | 金投网 · 全球股指 | <https://quote.cngold.org/gp/hqgz.html> |
| 美股三大指数（3） | 金投网 · 全球股指 | <https://quote.cngold.org/gp/hqgz.html> |
| 美股指数期货（3） | 新浪财经 · 国际期货 | <https://finance.sina.com.cn/futures/quotes/GC.shtml> |
| 虚拟币实时（12） | 新浪财经 · 区块链行情 | <https://finance.sina.com.cn/blockchain/hq.shtml> |
| 虚拟币分时 / 日K / 月K | Gate.io · 现货 K 线（分时 = 5 分钟线合成当日走势） | <https://api.gateio.ws/api/v4/spot/candlesticks> |
| 国际期货实时 + 历史（18） | 新浪财经 · 国际期货 | <https://finance.sina.com.cn/futures/quotes/GC.shtml> |
| 人民币汇率指数 | 中国货币网 | <https://www.chinamoney.com.cn/chinese/bkrmbidx/> |
| 各国国债收益率实时 | 新浪财经 · 全球国债 | <https://stock.finance.sina.com.cn/forex/globalbd/us10yt.html> |
| 国债日K / 分时 | 新浪财经 · 债券行情 | <https://bond.finance.sina.com.cn/hq/gb/daily?symbol=us10yt> |
| 美国国债余额（逐日全量） | 美国财政部 · Debt to the Penny | <https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/debt-to-the-penny> |
| 各国央行利率 | 金投网 · 财经日历 | <http://calendar.cngold.org/rate.htm> |

> 以上 16 类数据源均于 2026-10-04 逐一实测（HTTP 200 + 返回真实行情）。
> 看板页脚也内置了同一份「数据源清单」，随版本同步更新。

---

## 📁 项目结构

```
global-finance-dashboard/
├── server.py               # 后端：抓取 + 缓存 + 本地 HTTP 服务（约 1350 行）
├── launcher.py             # EXE 启动器：起服务 + 自动打开浏览器
├── static/
│   ├── index.html          # 前端单页应用（约 1250 行，含全部样式与逻辑）
│   ├── logo.png            # 站点图标（浏览器标签页）
│   └── vendor/
│       └── echarts.min.js  # ECharts 5.5.0 本地化（离线可用）
├── assets/
│   └── logo.ico            # 应用图标（EXE 用）
├── 启动.bat                 # Windows 一键启动（源码方式）
├── README.md
└── docs/
    ├── 项目说明.md          # 架构 / 模块 / 数据源 / 接口文档 / 技术要点
    ├── 使用指南.md          # 安装 / 操作 / 交互 / FAQ / 二次开发
    └── 数据源说明.md        # 每个数据源的接口 / 参数 / 格式 / 反爬对策 / 缓存策略
```

---

## 🔌 接口一览

| 接口 | 说明 |
|------|------|
| `GET /api/fx` | 191 个货币对实时行情 |
| `GET /api/rmbidx` | 人民币三大汇率指数当前值 + 历史曲线 |
| `GET /api/bonds` | 美 / 中全期限 + 主要国家 10 年期国债收益率 |
| `GET /api/debt?page=1&size=20` | 美国国债逐日余额（分页）；附加每行 `dod/mom/yoy`（较上一交易日 / 上月同期 / 上年同期，含绝对值、百分比、参照日）、`series`（近 10 年走势）、`compare`（最新一期的三类对比） |
| `GET /api/debt/export` | 美国国债全量 Excel 下载（14 列，含三类对比列） |
| `GET /api/rates` | 28 国央行基准利率 |
| `GET /api/indices` | 中国（含中国香港恒生系列、中国台湾）/ 亚太 / 欧洲 / 美洲股指 |
| `GET /api/us` | 美股三大指数 + 指数期货 |
| `GET /api/crypto` | 虚拟币实时行情 |
| `GET /api/futures` | 国际期货实时行情 |
| `GET /api/kline?kind=&code=&type=` | 统一 K 线（kind: fx/bond/index/us/crypto/futures；type: min/day/month） |
| `GET /api/ping` | 健康检查 |

---

## ❓ 常见问题

- **某个品种显示「暂无数据」？** 上游可能休市 / 接口调整，稍后点顶栏「刷新」重试；看板会自动回退到最近一次成功缓存。
- **首次加载较慢？** 各源首次抓取需建立缓存（约 3–8 秒），之后命中缓存毫秒级返回。
- **Excel 下载下来是 CSV？** 说明未安装 `openpyxl`，`pip install openpyxl` 后重启即可。
- **汇率 K 线想看反向（如 CNY/USD）？** 展开图表面板后点「正/反向」按钮。

更多见 [使用指南 · FAQ](docs/使用指南.md#7-常见问题-faq)。

---

## ⚠️ 免责声明

本项目所有数据均通过**公开接口**实时抓取，版权归原始数据方所有；行情仅供学习与研究参考，**不构成任何投资建议**。请遵守数据源的访问条款，勿用于商业转售或高频抓取。

---

## 👤 作者

**设计：公歧子** ｜ 微信：`gongqizi0`

欢迎 Star / Fork / Issue。
