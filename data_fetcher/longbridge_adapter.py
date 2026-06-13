"""
Longbridge JSON → Tushare 兼容 DataFrame 适配器

关键目标：让 downstream（indicators/、snapshot/、storage/）感知不到数据源切换。
所有转换函数接收 Longbridge CLI 返回的 JSON，输出 pandas DataFrame 或 dict，
字段名、格式与原有 Tushare 输出保持一致。

实际 API 格式基于 tests/api_responses.json 录制文件验证。
"""
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger("newstock.data_fetcher.longbridge_adapter")


# ══════════════════════════════════════════════════════
#  K 线 → Tushare daily 兼容 DataFrame
# ══════════════════════════════════════════════════════

def kline_to_dataframe(
    kline_data: list[dict],
    ts_code: str = "",
) -> pd.DataFrame:
    """
    将 Longbridge K 线 JSON 转为 Tushare 兼容的日线 DataFrame。

    Longbridge CLI 实际输出格式:
      [{"time": "2026-06-10T04:00:00Z", "open": "290.740",
        "high": "294.750", "low": "287.380", "close": "291.580",
        "volume": "52793266", "turnover": "15384589118.000"}]

    输出 Tushare 兼容 DataFrame:
      columns: ts_code, trade_date, open, high, low, close, pre_close,
               change, pct_chg, vol, amount
      trade_date 格式: "20260612"
    """
    if not kline_data:
        return pd.DataFrame(
            columns=["ts_code", "trade_date", "open", "high", "low",
                     "close", "pre_close", "change", "pct_chg", "vol", "amount"]
        )

    df = pd.DataFrame(kline_data)

    # 时间字段：实际输出为 ISO 8601 字符串 "2026-06-10T04:00:00Z"
    if "time" in df.columns:
        df["trade_date"] = pd.to_datetime(df["time"], utc=True).dt.strftime("%Y%m%d")
    elif "timestamp" in df.columns:
        # 兼容旧版 epoch 秒整数格式
        df["trade_date"] = pd.to_datetime(df["timestamp"], unit="s",
                                          utc=True).dt.strftime("%Y%m%d")
    else:
        logger.warning("kline 数据缺少 time/timestamp 字段")
        return pd.DataFrame()

    # 列名映射 + 数值转换（CLI 输出为字符串，需要 pd.to_numeric）
    col_map = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "vol",
        "turnover": "amount",
    }
    for src, dst in col_map.items():
        if src in df.columns:
            df[dst] = pd.to_numeric(df[src], errors="coerce")

    # 确保必要的列存在
    required = ["trade_date", "open", "high", "low", "close", "vol"]
    for col in required:
        if col not in df.columns:
            df[col] = None

    # 计算辅助列（对齐 Tushare 格式）
    if "close" in df.columns:
        close_vals = df["close"].astype(float)
        df["pre_close"] = close_vals.shift(1)
        df["change"] = close_vals - close_vals.shift(1)
        df["pct_chg"] = (close_vals.pct_change() * 100).round(4)

    if "amount" not in df.columns:
        df["amount"] = None

    # ts_code
    df["ts_code"] = ts_code if ts_code else ""

    # 按 trade_date 排序，去重
    df = df.sort_values("trade_date").drop_duplicates(
        subset=["trade_date"], keep="last"
    ).reset_index(drop=True)

    # 输出列顺序对齐 Tushare
    out_cols = ["ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "change", "pct_chg", "vol", "amount"]

    return df[out_cols]


# ══════════════════════════════════════════════════════
#  估值指标 → daily_basic 兼容 DataFrame
# ══════════════════════════════════════════════════════

def calc_index_to_valuation_df(
    calc_data,
    trade_date: str,
    ts_code: str = "",
) -> pd.DataFrame:
    """
    将 Longbridge calc-index 输出转为 Tushare daily_basic 兼容格式。

    实际 CLI 输出:
      [{"dividend_yield": "0.36", "pb": "40.390", "pe": "35.090",
        "symbol": "AAPL.US", "turnover_rate": "0.090"}]
    — list 类型，当前只返回一条（最新）。

    输出: single-row DataFrame with daily_basic columns
    """
    # 如果返回的是 list，取第一个元素
    row_data = {}
    if isinstance(calc_data, list) and len(calc_data) > 0:
        row_data = calc_data[0]
    elif isinstance(calc_data, dict):
        row_data = calc_data
    else:
        return pd.DataFrame()

    row = {
        "ts_code": ts_code or row_data.get("symbol", ""),
        "trade_date": trade_date,
        "pe_ttm": _float(row_data.get("pe")),
        "pb": _float(row_data.get("pb")),
        "ps_ttm": _float(row_data.get("ps")),
        "dv_ttm": _float(row_data.get("dividend_yield")),
        "total_mv": _float(row_data.get("market_cap")),
        "circ_mv": _float(row_data.get("float_market_cap")),
        "turnover_rate": _float(row_data.get("turnover_rate")),
        "volume_ratio": None,
    }
    return pd.DataFrame([row])


# ══════════════════════════════════════════════════════
#  财务数据适配
# ══════════════════════════════════════════════════════

def _flatten_financial_report(
    report_data: dict,
    report_kind: str,
    field_mapping: dict[str, str],
) -> pd.DataFrame:
    """
    将 Longbridge financial-report 嵌套结构展平为 Tushare 兼容的二维表。

    实际 API 格式:
      {
        "list": {
          "IS": {                           # report_kind
            "indicators": [
              {
                "accounts": [
                  {
                    "field": "OperatingRevenue",     # 指标名
                    "name": "营业收入(USD)",          # 显示名
                    "values": [
                      {"fp_end": "1774670400",      # 账期结束时间(epoch秒)
                       "period": "Q2 2026",
                       "value": "254940000000.00",
                       "year": 2026,
                       "yoy": "0.1606"},
                      ...
                    ]
                  },
                  ...
                ]
              }
            ]
          }
        },
        "report": "2026.H1"                  # 报告期标签
      }

    输出: DataFrame with columns = list(field_mapping.values()) + ['end_date', 'year', 'period']
    """
    if not report_data or not isinstance(report_data, dict):
        return pd.DataFrame()

    # 提取 report section
    report_list = report_data.get("list", {})
    section = report_list.get(report_kind, {})
    if not section:
        logger.warning(f"financial-report 中未找到 {report_kind} section")
        return pd.DataFrame()

    indicators = section.get("indicators", [])
    if not indicators:
        return pd.DataFrame()

    # 收集所有时间点（fp_end），取所有 accounts 的 values 的并集
    all_periods = {}  # fp_end → {period, year}

    # 第一遍：收集所有时间点
    all_acct_data = {}  # field → {fp_end → value}
    for indicator_group in indicators:
        for acct in indicator_group.get("accounts", []):
            field = acct.get("field", "")
            if field not in field_mapping:
                continue
            acct_values = {}
            for v in acct.get("values", []):
                fp_end = v.get("fp_end", "")
                if fp_end:
                    acct_values[fp_end] = v.get("value")
                    if fp_end not in all_periods:
                        all_periods[fp_end] = {
                            "period": v.get("period", ""),
                            "year": v.get("year", ""),
                        }
            all_acct_data[field] = acct_values

    if not all_periods:
        return pd.DataFrame()

    # 构建行数据
    rows = []
    for fp_end in sorted(all_periods.keys()):
        row = {
            "end_date": _epoch_to_date_str(fp_end),
            "year": all_periods[fp_end]["year"],
            "period": all_periods[fp_end]["period"],
        }
        for src_field, dst_field in field_mapping.items():
            val = all_acct_data.get(src_field, {}).get(fp_end)
            row[dst_field] = _float(val)
        rows.append(row)

    return pd.DataFrame(rows)


def _epoch_to_date_str(epoch_str: str) -> str:
    """将 epoch 秒字符串转为 YYYYMMDD 格式"""
    try:
        ts = int(epoch_str)
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y%m%d")
    except (ValueError, TypeError):
        return epoch_str


# ── IS 利润表 field 映射 ──

_IS_FIELD_MAP = {
    "OperatingRevenue": "revenue",       # 营业收入
    "OperatingIncome":  "operate_profit", # 营业利润
    "NetProfit":        "n_income",       # 净利润
    "GrossMgn":         "gross_profit",   # 毛利率 (注意这是比率，不是金额)
    "EPS":              "basic_eps",      # 每股收益
    "ROE":              "roe",            # ROE
    "NetProfitMargin":  "net_profit_margin", # 净利率
}

# ── BS 资产负债表 field 映射 ──

_BS_FIELD_MAP = {
    "TotalAssets":    "total_assets",    # 总资产
    "TotalLiability": "total_liabs",     # 总负债
    "TotalEquity":    "total_hldr_eqy_exc_min_int", # 总权益 (可能不存在)
    "BPS":            "bps",             # 每股净资产
    "CashSTInvest":   "cash_st_invest",  # 现金及短期投资
    "Inventory":      "inventories",     # 存货
    "TotalReceiv":    "accounts_receiv", # 应收账款
    "NPPE":           "fix_assets",      # 固定资产净值
    "LTInvest":       "lt_invest",       # 长期投资
}

# ── CF 现金流量表 field 映射 ──

_CF_FIELD_MAP = {
    "NetOperateCashFlow": "n_cashflow_act",      # 经营活动现金流
    "NetInvestCashFlow":  "n_cashflow_inv_act",  # 投资活动现金流
    "NetFinanceCashFlow": "n_cashflow_fin_act",  # 筹资活动现金流
    "NetFreeCashFlow":    "free_cashflow",       # 自由现金流
    "CapEx":              "capex",               # 资本支出
}


def income_to_dataframe(report_data, ts_code: str = "") -> pd.DataFrame:
    """
    将 Longbridge 利润表 JSON 转为 Tushare income 兼容格式。

    输入: financial-report --kind IS 的原始返回 (dict)
    输出: DataFrame with Tushare income columns
    """
    if not report_data or not isinstance(report_data, dict):
        return pd.DataFrame()

    df = _flatten_financial_report(report_data, "IS", _IS_FIELD_MAP)
    if df.empty:
        return df

    df["ts_code"] = ts_code

    # Tushare 兼容列: ts_code, end_date, report_type, comp_type, ...
    # 保持所有已转换的列
    return df


def balance_to_dataframe(report_data, ts_code: str = "") -> pd.DataFrame:
    """
    Longbridge 资产负债表 → Tushare balancesheet 兼容格式

    输入: financial-report --kind BS 的原始返回 (dict)
    输出: DataFrame with Tushare balancesheet columns
    """
    if not report_data or not isinstance(report_data, dict):
        return pd.DataFrame()

    df = _flatten_financial_report(report_data, "BS", _BS_FIELD_MAP)
    if df.empty:
        return df

    df["ts_code"] = ts_code
    return df


def cashflow_to_dataframe(report_data, ts_code: str = "") -> pd.DataFrame:
    """
    Longbridge 现金流量表 → Tushare cashflow 兼容格式

    输入: financial-report --kind CF 的原始返回 (dict)
    输出: DataFrame with Tushare cashflow columns
    """
    if not report_data or not isinstance(report_data, dict):
        return pd.DataFrame()

    df = _flatten_financial_report(report_data, "CF", _CF_FIELD_MAP)
    if df.empty:
        return df

    df["ts_code"] = ts_code
    return df


def financial_report_to_fina_indicator(
    latest_data: dict,
    calc_index: Optional[dict] = None,
    ts_code: str = "",
    end_date: str = "",
) -> pd.DataFrame:
    """
    用 financial-report --latest + calc-index 构建 Tushare fina_indicator 兼容行。

    实际 --latest API 格式:
      {
        "currency": "USD",
        "indicators": [
          {"field_name": "operating_revenue", "indicator_name": "营业收入",
           "indicator_value": "254940000000.00", "yoy": "0.1606"},
          {"field_name": "net_profit", ...},
          {"field_name": "total_assets", ...},
          {"field_name": "total_debts", ...},
          {"field_name": "eps", ...},
          {"field_name": "bps", ...},
          ...
        ],
        "report": "2026.H1",
        "report_txt": "2026 财年中报"
      }

    替代 hk_fina_indicator / us_fina_indicator / fina_indicator。
    """
    # 构建 indicators map: field_name → indicator_value / yoy
    indicator_map = {}
    indicator_yoy_map = {}
    if latest_data and isinstance(latest_data, dict):
        for item in latest_data.get("indicators", []):
            fn = item.get("field_name", "")
            indicator_map[fn] = item.get("indicator_value")
            indicator_yoy_map[fn] = item.get("yoy")

    row = {
        "ts_code": ts_code,
        "end_date": end_date or latest_data.get("report", "") if isinstance(latest_data, dict) else "",
    }

    # ── 核心财务指标 ──
    revenue = _float(indicator_map.get("operating_revenue"))
    net_profit = _float(indicator_map.get("net_profit"))
    total_assets = _float(indicator_map.get("total_assets"))
    total_debts = _float(indicator_map.get("total_debts"))
    bps = _float(indicator_map.get("bps"))
    eps = _float(indicator_map.get("eps"))

    row["revenue"] = revenue
    row["n_income"] = net_profit
    row["total_assets"] = total_assets
    row["total_hldr_eqy_exc_min_int"] = None  # 没有 total_equity 字段
    row["bps"] = bps
    row["eps"] = eps

    # ── 计算 ROE ──
    # 如果不能从 total_assets - total_debts 推算 equity，尝试从 indicator_map 获取
    equity = _float(indicator_map.get("total_equity"))
    if equity is None and total_assets is not None and total_debts is not None:
        equity = total_assets - total_debts

    if net_profit is not None and equity and equity != 0:
        row["roe"] = round(net_profit / equity * 100, 2)

    # ── 计算 ROA ──
    if net_profit is not None and total_assets and total_assets != 0:
        row["roa"] = round(net_profit / total_assets * 100, 2)

    # ── 同比增速 ──
    revenue_yoy = indicator_yoy_map.get("operating_revenue")
    profit_yoy = indicator_yoy_map.get("net_profit")
    if revenue_yoy:
        row["revenue_yoy"] = _float(revenue_yoy)
    if profit_yoy:
        row["profit_yoy"] = _float(profit_yoy)

    # ── 净利率（从 --latest 直接获取）──
    net_margin = indicator_map.get("net_profit_margin")
    if net_margin is not None:
        row["netprofit_margin"] = _float(net_margin)

    # ── 资产负债率 ──
    if total_assets and total_debts and total_assets != 0:
        row["debt_to_assets"] = round(total_debts / total_assets * 100, 2)

    # ── calc-index 补充 ──
    if calc_index:
        ci = calc_index
        if isinstance(ci, list) and len(ci) > 0:
            ci = ci[0]
        if isinstance(ci, dict):
            row["pe_ttm"] = _float(ci.get("pe"))
            row["pb"] = _float(ci.get("pb"))
            row["dv_ttm"] = _float(ci.get("dividend_yield"))

    return pd.DataFrame([row])


# ══════════════════════════════════════════════════════
#  估值历史 → PE 分位计算辅助
# ══════════════════════════════════════════════════════

def extract_valuation_history_values(
    valuation_history_data,
    indicator: str = "pe",
) -> list[dict]:
    """
    从 valuation --history 的嵌套响应中提取时间序列值列表。

    实际 API 格式:
      {
        "metrics": {
          "pe": {
            "desc": "...",
            "high": "34.26",
            "list": [
              {"timestamp": "1624248000", "value": "28.70"},
              {"timestamp": "1624852800", "value": "25.91"},
              ...
            ]
          }
        },
        "range": "5Y"
      }

    输出: [{"value": "28.70", "timestamp": "1624248000"}, ...]
    """
    if not valuation_history_data or not isinstance(valuation_history_data, dict):
        return []

    metrics = valuation_history_data.get("metrics", {})
    ind_data = metrics.get(indicator, {})
    values_list = ind_data.get("list", [])

    return values_list


def extract_industry_median_from_valuation(
    valuation_history_data,
    indicator: str = "pe",
) -> Optional[float]:
    """
    从 valuation --history 的 desc 字段中解析行业估值中位数。

    实际 desc 格式 (中文):
      "当前市盈率 ... 行业中位数 <strong>8.84</strong>。"
    或:
      "目前PE ... 行业中位数 <strong>12.50</strong>。"

    返回: 行业中位数 (float)，解析失败返回 None
    """
    import re

    if not valuation_history_data or not isinstance(valuation_history_data, dict):
        return None

    metrics = valuation_history_data.get("metrics", {})
    ind_data = metrics.get(indicator, {})
    desc = ind_data.get("desc", "")

    if not desc:
        return None

    # 匹配 "行业中位数 <strong>8.84</strong>" 模式
    match = re.search(r"行业中位数\s*<strong>([\d.]+)</strong>", desc)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            return None
    return None


# ══════════════════════════════════════════════════════
#  符号转换
# ══════════════════════════════════════════════════════

def to_longbridge_symbol(code: str, market: str = "") -> str:
    """
    将系统内部格式转成 Longbridge CLI 格式。

    输入 (Tushare 兼容):  "00700.HK" / "105.AAPL" / "600519.SH"
    输出 (Longbridge):     "700.HK"  / "AAPL.US"   / "600519.SH"
    """
    code = code.strip().upper()

    # 已经符合长桥格式
    if code.endswith(".HK") or code.endswith(".US"):
        # 港股去前导零: 00700.HK → 700.HK
        if ".HK" in code:
            prefix = code.split(".HK")[0]
            symbol = prefix.lstrip("0") or "0"
            return f"{symbol}.HK"
        return code

    # SH/SZ/BJ → A 股格式
    if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"):
        return code

    # 纯数字 A 股代码 (6位)
    if code.isdigit() and len(code) == 6:
        if code.startswith("6") or code.startswith("68"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")) and len(code) == 6:
            return f"{code}.BJ"

    # 已经是 "AAPL.US" 格式
    return code


def from_longbridge_symbol(longbridge_code: str) -> str:
    """
    将 Longbridge 格式转成系统内部格式（Tushare 兼容）。
    主要用于缓存 key 保持一致。

    输入:  "700.HK"  / "AAPL.US" / "600519.SH"
    输出:  "700.HK"  / "AAPL.US" / "600519.SH"  (已兼容)
    """
    return longbridge_code.upper()


def convert_us_symbol(tushare_us_code: str) -> str:
    """
    Tushare 美股代码 → Longbridge 美股代码
    105.AAPL → AAPL.US
    106.TSM  → TSM.US
    """
    parts = tushare_us_code.split(".")
    if len(parts) >= 2:
        return f"{parts[-1]}.US"
    return f"{tushare_us_code}.US"


def convert_hk_symbol(tushare_hk_code: str) -> str:
    """
    Tushare 港股代码 → Longbridge 港股代码
    00700    → 700.HK
    00700.HK → 700.HK
    """
    code = tushare_hk_code.replace(".HK", "").replace(".hk", "")
    symbol = code.lstrip("0") or "0"
    return f"{symbol}.HK"


def convert_cn_symbol(tushare_cn_code: str) -> str:
    """
    Tushare A 股代码 → Longbridge A 股代码
    600519    → 600519.SH
    000651    → 000651.SZ
    300750    → 300750.SZ
    688111    → 688111.SH
    """
    code = tushare_cn_code.strip().upper()
    # 已有后缀
    if "." in code:
        return code
    if code.startswith("6") or code.startswith("68"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")) and len(code) == 6:
        return f"{code}.BJ"
    return code


# ══════════════════════════════════════════════════════
#  辅助
# ══════════════════════════════════════════════════════

def _float(val) -> Optional[float]:
    """安全转为 float"""
    if val is None:
        return None
    try:
        s = str(val).replace("%", "").replace(",", "")
        return float(s)
    except (ValueError, TypeError):
        return None
