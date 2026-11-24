# 实时数据分析与可视化应用平台

支撑实时数据聚合、多维度分析、可视化图表生成和数据应用发布的分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8019/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5199/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8019`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时聚合器 | `realtime_agg` | 聚合窗口 | 窗口名称、数据源、聚合维度 |
| 事件追踪器 | `event_tracker` | 事件定义 | 事件名称、事件属性、触发条件 |
| 仪表盘构建器 | `dashboard_builder` | 仪表盘 | 仪表盘名称、包含图表、刷新间隔 |
| 查询缓存层 | `query_cache` | 缓存配置 | 配置名称、关联数据集、缓存时长秒数 |
| 分群引擎 | `segment_engine` | 分群定义 | 分群名称、分群条件、所属人口特征 |
| 指标存储 | `metric_store` | 指标快照 | 指标名称、快照值、快照时间 |
| 下钻路径 | `drill_path` | 下钻规则 | 规则名称、起始指标、下钻维度 |
| 分析告警策略 | `alert_policy` | 告警规则 | 规则名称、监控指标、告警条件 |
| 数据报告 | `data_report` | 报告生成器 | 报告名称、包含指标集、报告周期 |
| 异常归因 | `anomaly_analysis` | 归因分析器 | 分析对象、异常时间点、归因维度 |
| 预测模型 | `forecast_model` | 预测任务 | 任务名称、预测指标、预测周期 |
| 分群对比 | `cohort_compare` | 对比报告 | 对比名称、分群A、分群B |
| 分析结果导出 | `data_export` | 导出任务 | 导出名称、查询条件、导出格式 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询历史 | `query_history` | 查询记录 | 查询时间、执行人、涉及数据集 |
| 报告快照 | `report_snapshot` | 快照记录 | 快照名称、所属报告、快照时间 |
| 数据API | `data_api` | 数据接口 | 接口名称、关联指标、请求方式 |
| 留存分析 | `retention_analysis` | 留存任务 | 任务名称、行为事件、留存天数 |
| 漏斗分析器 | `funnel_analyzer` | 漏斗定义 | 漏斗名称、步骤序列、转化窗口小时 |
| 多维数据集构建 | `cube_builder` | Cube定义 | Cube名称、事实表、维度组合 |
| 代跑委托签认 | `run_delegation` | 委托授权 | 委托方业务组、被委托执行人、指定数据集、可签额度 |

## 一次性受控签认

查询历史只有执行人和用量，业务组代跑费用凭委托落账的链路如下：

- 业务组在 `run_delegation` 模块签发一次性委托，绑定执行人、指定数据集和可签额度。
- `GET /api/signoff/usage` 按确证的运行日志计算代跑用量费用（返回行数 × 行费率），并标注已签认/待补办。
- `POST /api/signoff/sign`（`{delegation_id, run_id}`）由数据报告生成报销凭证：每份授权只能签
  一整笔运行，核销后余额作废；同一份委托同时被提交也只有一笔成功，另一笔进入
  `GET /api/signoff/reissue?business_group=…` 待补办清单。
- 凭证经 `GET /api/signoff/vouchers[/id]` 只读核对：写明本次扣的是哪笔运行、签认金额、
  作废余额、委托归属与实际代跑人；已签凭证不能被改签，撤销委托不影响其原值。

签认测试：`cd backend && python3 -m unittest discover -s tests -v`

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
