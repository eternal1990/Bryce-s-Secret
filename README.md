# Bryce-s-Secret

## BTC 10:00 ET 冲击检测脚本

脚本：`btc_1000_et_shock.py`

### 功能覆盖
- 按美东时区取最近 N 个完整自然日（默认 7 天）。
- 定义每天 `10:00:00 ET` 为事件锚点；若缺失则向后对齐第一根 1m bar，并标记 `aligned=false`。
- 计算 5m/15m/30m/60m 窗口的：
  - 收益（`ret`）
  - 下冲（`dd`）
  - 上冲（`ru`）
  - 成交量（`vol`）
  - 最低点出现分钟（`time_to_low`）
  - 最低点后反弹（`rebound`）
- 质量控制：若 T0 后 60 分钟缺失分钟数大于阈值（默认 3）则记为 `invalid_day`。
- 标签：`dump_5m` / `dump_15m` / `dump_rebound` / `fakeout`。
- 输出：
  - 日明细 CSV（约 7 行）
  - 汇总 JSON（均值/中位数/分位数/出现次数/低点集中度/成交量放大比）

---

## 用法 1：直接拉 Binance（默认）

```bash
python3 btc_1000_et_shock.py --days 7 --out-dir output
```

参数：
- `--symbol`：交易对，默认 `BTCUSDT`
- `--days`：完整自然日数量，默认 `7`
- `--max-missing-60m`：T0 后 60m 最大允许缺失分钟，默认 `3`
- `--out-dir`：输出目录，默认 `output`

---

## 用法 2：配合 CoinGecko MCP / Pro API（推荐你当前场景）

你提到有 CoinGecko Pro API，那么更稳妥做法是：

1. 用 CoinGecko MCP（或你自己的 Pro API 拉取脚本）先导出 **1m OHLCV CSV**。
2. 再让本脚本只做“10:00 ET 冲击分析”，不依赖 Binance 连通性。

### CSV 格式要求
CSV 至少包含以下列：

- `timestamp`（支持 Unix 秒 / Unix 毫秒 / ISO8601）
- `open`
- `high`
- `low`
- `close`
- `volume`

### 分析命令

```bash
python3 btc_1000_et_shock.py \
  --days 7 \
  --input-csv data/btc_1m_ohlcv.csv \
  --out-dir output
```

脚本会自动解析时区并按美东日期做事件锚点分析。

---

## CoinGecko MCP 配置提示（你有 Pro key）

你可以按 CoinGecko MCP 文档把 Pro key 配进 MCP server（通常通过环境变量如 `COINGECKO_PRO_API_KEY`），然后在 MCP 客户端里先取分钟级数据，落地到 CSV，再喂给本脚本。

文档：<https://docs.coingecko.com/docs/mcp-server>

> 说明：不同 MCP 客户端（Claude Desktop、Cursor、Codex CLI 等）配置位置不一样，但核心都是给 MCP server 注入 Pro API key。

---

## 运行环境说明
- 若你所在环境无法访问 Binance API，默认模式会失败；请改用 `--input-csv` 模式。
- `--input-csv` 模式适合你这种“已有 Pro 数据源/MCP”的场景。

## 对原方案可继续增强的点（建议）
1. **基线更细化**：除 `9:00–10:00` 外，可加 `同星期几/同小时`历史基线，避免把日内季节性误判成“10:00 特征”。
2. **稳健统计**：7 天样本极小，建议同时报告 `median + IQR`，并在结论中标注“探索性结果”。
3. **阈值敏感性**：在 `dd_5m` 阈值上跑一个网格（如 -0.6%~-1.4%），看 `dump_rebound` 占比是否稳定。
4. **方向拆分**：把事件日按 `09:00–10:00` 预趋势分组（涨/跌/横盘），检验“10:00 冲击”是否依赖前序状态。
5. **交易成本视角**：若后续用于策略，增加滑点和手续费后再看 60m 反转是否仍有优势。
