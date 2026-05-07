# signal_monitor — 信号监控 SOP

信号监控场景：对**固定资产池**逐资产跑公式 → 判触发 → 冷静期去重 → 推送触发列表。

---

## 概念

| 术语 | 说明 |
|------|------|
| 触发判定 | `trigger_formula` 末日值 ≥ 0.5 为触发 |
| 冷静期 | `cooldown_days` 内同一 ticker 不重复推（设为 0 则每次都推） |
| 结果键 | `triggered[]` / `cooled_down[]` / `all_stocks[]` / `anomalies[]` |
| 公式占位符 | 第一条 `signal.formulas` 表达式中必须含 `{ASSETS}`，运行时自动替换为资产池名称列表 |

---

## 内置信号 presets

| 信号名 | preset 参数 | 触发条件 |
|--------|-------------|----------|
| R4A 短线抄底 | `dip_2sigma` | 收盘价 < MA20 − 2×STD20 |
| R4B 中线稳健 | `dip_2sigma_60d` | 收盘价 < MA60 − 2×STD60 |
| 20日突破 | `breakout_20d` | 收盘价 > 近20日最高 |
| MACD金叉 | `macd_cross` | DIF 上穿 DEA |

---

## job.json 格式（signal_monitor）

```json
{
  "task_type": "signal_monitor",
  "name": "我的抄底监控",
  "description": "收盘价 < MA20 - 2*STD20 时触发",
  "asset_source": {
    "type": "excel",
    "path": "assets.xlsx",
    "disabled_tickers": []
  },
  "signal": {
    "category": {"direction": "buy", "label": "抄底"},
    "formulas": [
      {"name": "资产池收盘价", "expression": "资产池构造({ASSETS}) * \"全市场每日收盘价\""},
      {"name": "MA20",  "expression": "平均(\"资产池收盘价\", 20)"},
      {"name": "下轨",  "expression": "\"MA20\" - 2 * 标准差(\"资产池收盘价\", 20)"},
      {"name": "信号",  "expression": "\"资产池收盘价\" < \"下轨\""}
    ],
    "trigger_formula": "信号",
    "display_fields": ["资产池收盘价", "MA20", "下轨"],
    "lookback_days": 60
  },
  "cooldown_days": 7,
  "notification": {
    "wecom": {
      "enabled": true,
      "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
    },
    "push_when": "triggered_only"
  },
  "schedule": {
    "cron": "*/15 9-15 * * 1-5"
  },
  "report": {
    "report_mode": "incremental"
  }
}
```

---

## 资产池 SOP ⚠️

### 三种 asset_source.type 的选择规则

| 场景 | 推荐类型 | 原因 |
|------|----------|------|
| 用户提供了股票列表、要长期维护 | **`excel`（默认）** | 用户可直接在 Excel 里增删，无需改 job.json |
| 用户偏好文本/版控管理 | `csv` | 纯文本，可用记事本/git 管理 |
| 临时测试/dry-run | `inline` | 不需要持久化 |
| 用户明确说"不需要文件" | `inline` | 尊重用户意图 |
| 股票极少（≤5只）且一次性 | `inline` | 不值得建文件 |

### Agent 默认规则（强制）

> ⚠️ **只要用户提供了股票列表用于定时任务，必须生成 `assets.xlsx` 文件，配置 `type=excel`，不得默认 inline。**
> inline 仅用于：用户明确指定、临时测试、≤5只一次性场景。

### 创建带文件资产池的完整步骤

```bash
# 1. 建 job 骨架
python scripts/cli.py add <id> --task-type signal_monitor --from preset:dip_2sigma

# 2. 生成 assets.xlsx（Python 脚本）
python -c "
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
assets = [('贵州茅台', 'SH600519'), ('宁德时代', 'SZ300750')]  # 替换为实际列表
for company, ticker in assets:
    ws.append([company, ticker])
wb.save('jobs/<id>/assets.xlsx')
"

# 3. 配置资产池类型（从 inline 切换到 excel）
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx

# 4. 后续配置
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/15 9-15 * * 1-5"
python scripts/cli.py set <id> report.report_mode incremental
python scripts/cli.py validate <id>
# 立即试跑：在对话里让 agent 按 docs/agent-runtime-flow.md 走 13 步
python scripts/cli.py apply-schedule <id>
```

### assets.xlsx 格式说明

- **两列，无表头**
- 第一列：公司名（中文，与 quant-buddy assets_db 一致）
- 第二列：股票代码（格式：`SH600519` / `SZ300750` / `HK00700` / `AAPL.O`）

```
贵州茅台   SH600519
宁德时代   SZ300750
腾讯控股   HK00700
```

### CSV 格式（备选）

若使用 `type=csv`，路径配置为 `asset_source.path: assets.csv`，文件格式同 xlsx（两列无表头，逗号分隔）：

```
贵州茅台,SH600519
宁德时代,SZ300750
```

### 迁移现有 inline 资产池 → 文件

```bash
# 1. 查看当前 inline 资产列表
python scripts/cli.py show <id>
# 从输出的 asset_source.assets 字段拿到列表

# 2. 生成 assets.xlsx（用上面的 Python 代码片段）

# 3. 切换配置
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> asset_source.assets []

# 4. 校验
python scripts/cli.py validate <id>
```

---

## signal_monitor 创建完整流程

### 场景一：用已知 preset 快速建（推荐）

```bash
python scripts/cli.py add <id> --task-type signal_monitor --from preset:dip_2sigma
# 生成 assets.xlsx（见资产池 SOP）
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py validate <id>
python scripts/cli.py apply-schedule <id>
```

### 场景二：自定义公式（没有 preset）

```bash
# 1. 建空骨架
python scripts/cli.py add <id> --task-type signal_monitor --scaffold

# 2. 调 quant-buddy-skill 生成 signal.formulas（对话层完成）

# 3. 写入公式
python scripts/cli.py set <id> signal.formulas '[{"name":"公式1","expression":"..."},...]'
python scripts/cli.py set <id> signal.trigger_formula "信号"
python scripts/cli.py set <id> signal.display_fields '["字段1","字段2"]'

# 4. 生成 assets.xlsx + 配置资产池（见资产池 SOP）

# 5. 后续同场景一步骤 4-8
```

---

## analysis_hook（可选）

开启后，agent 在 13 步流的步骤 8 会对每个 triggered ticker 做 WebSearch 归因，并按 `framework_doc` 指定的 markdown 框架组织结论。

```json
"analysis_hook": {
  "enabled": true,
  "framework_doc": "framework.md"
}
```

- `framework_doc`：相对 `jobs/<id>/` 的 markdown 路径，默认 `framework.md`。文件内容定义归因小节（如"基本面 / 消息面 / 资金面 / 结论"）。
- agent 会把该框架的小节标题塞进推送报告的「触发归因」章节，每个 ticker 一次。
- 不需要 `websearch_template` 或 `output_sections` — agent 会按 framework.md 的结构自适应。

---

## 资产参考库

资产信息来自 quant-buddy-skill 内置库（用于确认公司名与代码对应关系）：

```
quant-buddy-skill/presets/assets_db/
├── stock_a.yaml   # A 股（沪深京）
├── stock_hk.yaml  # 港股
└── stock_us.yaml  # 美股（.O=NASDAQ / .N=NYSE / .A=AMEX）
```
