"""
Longbridge CLI Python 封装

每个方法对应一条 longbridge CLI 命令。
通过 subprocess 调用 CLI，返回解析后的 JSON（dict/list）。
"""
import json
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger("newstock.data_fetcher.longbridge_client")


class LongbridgeClient:
    """长桥 CLI 客户端"""

    def __init__(self, timeout: int = 30, rate_limit_per_second: int = 10):
        self.timeout = timeout
        self._rate_limit = rate_limit_per_second
        self._last_call = 0.0

    def _rate_limit_wait(self) -> None:
        """OpenAPI 限流控制 (默认 10 req/s)"""
        elapsed = time.time() - self._last_call
        min_interval = 1.0 / self._rate_limit
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call = time.time()

    def _run(self, *args: str) -> dict | list:
        """执行 CLI 命令并返回解析后的 JSON"""
        self._rate_limit_wait()
        cmd = ["longbridge"] + list(args) + ["--format", "json"]
        logger.debug(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.timeout
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"CLI 错误 (exit={result.returncode}): {result.stderr.strip()[:500]}"
            )

        stdout = result.stdout.strip()
        if not stdout:
            return [] if "--history" in args else {}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}, stdout[:200]={stdout[:200]}")
            return [] if "--history" in args else {}

    # ── 诊断 ──
    def check(self) -> dict:
        """检查 CLI 连接和认证状态"""
        return self._run("check")

    # ── 实时行情 ──
    def quote(self, *symbols: str) -> dict | list:
        """实时报价"""
        return self._run("quote", *symbols)

    def static_info(self, *symbols: str) -> dict | list:
        """静态信息（名称、行业、市值等）"""
        return self._run("static", *symbols)

    # ── K线（替代 Tushare daily/daily_adj）──
    def kline_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "day",
        adjust: str = "forward",
    ) -> list[dict]:
        """
        历史 K 线（前复权）
        start_date / end_date: YYYY-MM-DD
        """
        return self._run(
            "kline", "history", symbol,
            "--start", start_date, "--end", end_date,
            "--period", period, "--adjust", adjust,
        )

    def kline_recent(
        self,
        symbol: str,
        count: int = 100,
        period: str = "day",
        adjust: str = "forward",
    ) -> list[dict]:
        """最近 N 根 K 线"""
        return self._run(
            "kline", symbol,
            "--period", period, "--count", str(count), "--adjust", adjust,
        )

    # ── 计算指标（替代 Tushare daily_basic / fina_indicator）──
    def calc_index(
        self, symbol: str, fields: str = "pe,pb,eps,turnover_rate"
    ) -> dict:
        """估值指标 PE/PB/EPS/换手率"""
        return self._run("calc-index", symbol, "--fields", fields)

    # ── 估值 ──
    def valuation_snapshot(self, symbol: str, indicator: str = "pe") -> dict:
        """估值快照"""
        return self._run("valuation", symbol, "--indicator", indicator)

    def valuation_history(
        self, symbol: str, indicator: str = "pe", range_years: int = 5
    ) -> list[dict]:
        """估值历史"""
        return self._run(
            "valuation", symbol, "--history",
            "--indicator", indicator, "--range", str(range_years),
        )

    # ── 财报（替代 Tushare income/balancesheet/cashflow）──
    def financial_report(
        self, symbol: str, kind: str = "IS"
    ) -> list[dict]:
        """
        财务报表
        kind: IS=利润表, BS=资产负债表, CF=现金流量表
        """
        return self._run("financial-report", symbol, "--kind", kind)

    def financial_report_latest(self, symbol: str) -> dict:
        """最新财报摘要"""
        return self._run("financial-report", symbol, "--latest")

    # ── 一致预期 / EPS 预测 ──
    def consensus(self, symbol: str) -> dict:
        """一致预期"""
        return self._run("consensus", symbol)

    def forecast_eps(self, symbol: str) -> list[dict]:
        """EPS 预测"""
        return self._run("forecast-eps", symbol)

    # ── 资金流向 ──
    def capital_flow(self, symbol: str) -> dict | list:
        """资金分布"""
        return self._run("capital", symbol)

    def capital_flow_history(self, symbol: str) -> list[dict]:
        """资金流向时序"""
        return self._run("capital", symbol, "--flow")

    # ── 股东 ──
    def shareholder(self, symbol: str, count: int = 10) -> list[dict]:
        """机构股东"""
        return self._run("shareholder", symbol, "--count", str(count))

    def fund_holder(self, symbol: str, count: int = 10) -> list[dict]:
        """基金持仓"""
        return self._run("fund-holder", symbol, "--count", str(count))

    # ── 分红 ──
    def dividend(self, symbol: str) -> list[dict]:
        """分红记录"""
        return self._run("dividend", symbol)

    # ── 资讯 ──
    def news(self, symbol: str, count: int = 10) -> list[dict]:
        """股票资讯"""
        return self._run("news", symbol, "--count", str(count))

    # ── 多股对比 ──
    def compare(self, *symbols: str, currency: str = "USD") -> list[dict]:
        """多股估值对比"""
        return self._run("compare", *symbols, "--currency", currency)

    # ── 公司行动 ──
    def corp_action(self, symbol: str) -> list[dict]:
        """公司行动 (拆合股、名称变更等)"""
        return self._run("corp-action", symbol)
