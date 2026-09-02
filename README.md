# 🧳 旅行助手智能体（LangChain + FastAPI + Streamlit）

一个「旅行规划 Agent」完整骨架：用户输入 **目的地 + 日期 + 偏好**，后端以 **LangGraph 状态机** 依次执行
**天气查询 → 景点搜索（偏好过滤）→ 酒店推荐 → 每日路线规划（驾车/步行/公交可切换）→ 预算汇总**，
Web 界面分模块展示结果并支持 **一键导出 Markdown 旅行计划**。

> 定位说明：本仓库为「后端核心 + 前端骨架」的可运行版本，默认**无需任何 API Key 即可完整体验**
> （演示/免费数据源），接入真实数据或 LLM 只需在界面/`.env` 填入对应 Key。

---

## ✨ 特性

| 能力 | 说明 |
| --- | --- |
| LangGraph 六步工作流 | 地理编码 → 天气 → POI → 酒店 → 行程 → 预算，节点可独立降级/失败重试 |
| 多 LLM 供应商 | OpenAI / Anthropic / Azure OpenAI / 智谱 / 百度千帆，界面按次配置，Key 不落盘 |
| 多数据源 | `demo`（离线内置）· `openstreetmap`（免费真实）· `amap`（高德，中文最佳）· `auto` 自动选择 |
| 出行方式切换 | 驾车 / 步行 / 公交地铁（公交需高德 Key，否则按估算并标注） |
| 偏好过滤 | 历史文化 / 博物馆 / 自然风光 / 美食 / 购物 / 亲子 / 休闲 / 地标 / 夜生活 |
| 预算可调 | 酒店档位、餐饮预算、交通预算均可手动覆盖，自动输出每日明细与总额 |
| Markdown 导出 | 结果顶部「下载 Markdown」，也可调用 `/api/export/markdown` |
| 结果缓存 | 相同「数据源+城市+日期+偏好」1 小时内直接命中缓存，避免重复计费调用 |

## 📁 目录结构

```
travel-assistant/
├── backend/
│   ├── main.py                 # FastAPI 入口（/api/generate 等）
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── graph.py                # LangGraph 六步工作流（核心）
│   ├── llm_factory.py          # 多供应商 LLM 工厂
│   ├── markdown.py             # 旅行计划 → Markdown 渲染
│   ├── cache.py                # 短期结果缓存（cachetools）
│   ├── config.py               # .env 配置
│   └── providers/              # 数据访问层（天气/POI/酒店/路线/地理编码）
│       ├── demo.py             #   内置演示城市（北京/上海/成都/西安）
│       ├── weather.py          #   Open-Meteo → OpenWeatherMap → 估算
│       ├── poi.py              #   高德/Overpass/演示 景点+酒店
│       ├── route.py            #   高德 v5 / OSRM / 直线估算
│       └── geocode.py          #   城市 → 坐标
├── frontend/
│   └── app.py                  # Streamlit 界面（输入/配置/结果/导出）
├── requirements.txt
├── Dockerfile                  # 前后端同一镜像，不同启动命令
├── docker-compose.yml          # backend + frontend 一键启动
└── .env.example                # 复制为 .env 使用
```

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env          # Windows: copy .env.example .env
docker compose up --build
```

- 后端 API：<http://localhost:8000>（文档 <http://localhost:8000/docs>）
- 前端界面：<http://localhost:8501>

### 方式二：本地运行

要求 Python 3.10 ~ 3.12。

```bash
pip install -r requirements.txt

# 终端 1：后端
uvicorn backend.main:app --reload --port 8000

# 终端 2：前端
BACKEND_URL=http://127.0.0.1:8000 streamlit run frontend/app.py
# Windows (cmd)： set BACKEND_URL=http://127.0.0.1:8000 && streamlit run frontend/app.py
```

### 三分钟上手（零 Key 体验）

1. 打开 <http://localhost:8501>；
2. 数据源保持「自动」；目的地选 `北京`（或上海/成都/西安以获得完整演示数据），日期选未来 2~3 天；
3. 偏好勾选「历史文化 + 美食」，出行方式选「驾车」；
4. 点击 **生成旅行计划** → 查看天气/景点/酒店/每日行程/预算，点击 **下载 Markdown**。

> 说明：任意城市都能用 OpenStreetMap 免费源获取真实景点，但评分/门票/简介等富信息
> 仅内置的 4 个演示城市提供（真实数据源会明确标注「估算」或留空）。

## 🔑 配置详解

### 数据源（三档任选）

| 数据源 | Key | 覆盖 | 说明 |
| --- | --- | --- | --- |
| `demo` | 无 | 天气/景点/酒店/路线/预算 | 内置北京、上海、成都、西安 curated 数据，完全离线，结果最完整（评分/门票为演示值） |
| `openstreetmap` | 无 | 天气/POI/自驾与步行路线 | Open-Meteo + Overpass + OSRM，真实名称与坐标；无评分/门票，酒店价格为估算 |
| `amap` | `AMAP_KEY` | POI/酒店/驾车/步行/公交 | 高德地图 Web 服务，中文数据最佳；免费额度约 2000 次/天。申请：<https://console.amap.com/dev/key/app>（创建应用 → 添加 Key → 服务平台选「Web服务」） |
| `auto` | 按需 | — | 有高德 Key 走高德，否则走 openstreetmap，失败自动降级 demo（若目的地在内置库） |

天气层固定优先 **Open-Meteo**（免费、无 Key、实测可用）；填入 `OPENWEATHERMAP_KEY` 可作为第二备选；
实时源不可达或日期超预报范围时，用确定性估算兜底并给出 ⚠️ 提示。

> 坐标提醒：高德使用 GCJ-02、OSM/OSRM 使用 WGS-84，系统保证同一数据源内部自洽、不做跨源混用。

### LLM（可选增强）

任何 Key 都不填 → 规则引擎即可产出完整计划（分组聚类 + 最近邻排序 + 路线 API 段 + 预算公式）。
填写后 LLM 仅做**内容润色**（为缺失简介的 POI 补一句话、每日晚餐建议、整体出行贴士），
行程的结构仍由规则引擎保证与真实路线一致 —— 这样骨架版本稳定可离线，且不会让 LLM 产出假地名。

| 供应商 | model 默认值 | 说明 |
| --- | --- | --- |
| openai | `gpt-4o-mini` | Key：platform.openai.com |
| anthropic | `claude-3-5-haiku-latest` | Key：console.anthropic.com |
| azure_openai | 用 deployment 名 | 需另填 Endpoint + Deployment |
| zhipu | `glm-4-flash` | Key：open.bigmodel.cn；自动指向 OpenAI 兼容端点 |
| qianfan | `ernie-4.0-turbo-8k` | Key：console.bce.baidu.com/qianfan；自动指向 v2 兼容端点 |

Key 可放在 `.env`（`DEFAULT_LLM_*`），也可在 Streamlit 侧边栏按次输入 —— **仅随本次 `/api/generate`
请求传输，服务端不写入磁盘、不持久化**。

## 📡 REST API

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/generate` | POST | 完整工作流。请求体见 `backend/schemas.py::TravelRequest`；响应含 `meta/weather/pois/hotels/itinerary/budget/logs/markdown` |
| `/api/export/markdown` | POST | 传入 `/api/generate` 的结果 JSON，返回 `{ "markdown": "…" }` |
| `/api/providers` | GET | 可用的 LLM 供应商 / 数据源 / 偏好说明 |
| `/api/health` | GET | 健康检查（docker compose 依赖它做就绪探测） |

请求示例：

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "北京",
    "start_date": "2026-09-10",
    "end_date": "2026-09-12",
    "preferences": ["历史文化", "美食"],
    "travel_mode": "driving",
    "data_provider": "demo"
  }'
```

## 📝 示例输出（Markdown 摘录）

```markdown
# 北京 3 日旅行计划（2026-09-10 ~ 2026-09-12）

## 天气概览
- 2026-09-10：晴，22°C ~ 30°C，降水概率 10%
- 2026-09-11：多云，20°C ~ 28°C，降水概率 40%
- 2026-09-12：阵雨，18°C ~ 25°C，降水概率 75%（建议备伞、优先室内景点）

## 每日行程（自驾）
### Day 1（2026-09-10）
> 故宫博物院 → 景山公园 → 南锣鼓巷
- 上午：故宫博物院
- 下午：景山公园　[← 1.0 km / 20 分钟（估算）]
- 晚餐推荐：铜锅涮肉（北京当地人气选择）

## 预算汇总
| 项目 | 金额 | 说明 |
| --- | --- | --- |
| 酒店 | ¥1860 | 全季酒店（北京王府井店）620 元/晚 × 3 晚 |
| 总计 | ¥… | … |
```

## ⚠️ 已知限制与安全说明

- **价格均为估算**：公开地图/天气 API 不提供实时房价与门票价，酒店按「档位 × 城市基准」估算并标注；门票仅演示城市内置，真实数据源按免费/未知处理。预算请以实际预订为准。
- **公交路线**：免费 OSRM 不支持公交，无高德 Key 时公交段按直线估算并标注 `estimated`。
- **免费公共实例有速率限制**：OSRM/Overpass 建议适度使用；高德免费 Key 每天约 2000 次。
- **Key 安全**：Key 仅存在于 `.env` 或单次请求中；服务端不写日志不落盘。仍建议为 Key 配置 IP 白名单（高德控制台支持）。
- **缓存**：默认进程内 `cachetools.TTLCache`（1 小时），多副本部署时可替换为 Redis。

## 🛠 常见问题

- **高德 POI 搜不到/返回空**：确认创建的是「Web服务」类型 Key（不是 JS API Key），并已为应用开通相关服务。
- **Overpass/OSRM 超时**：公共实例可能偶发拥堵，系统会自动重试一次并降级为估算；可换成自建 OSRM 实例（修改 `providers/route.py::OSRM_BASE`）。
- **想加城市**：在 `backend/providers/demo.py::DEMO_CITIES` 增补条目（景点/酒店/美食/基准价）即可获得完整演示数据。
- **前端默认后端地址**：`docker compose` 下前端自动用 `http://backend:8000`；本地运行时通过环境变量 `BACKEND_URL` 指定。
