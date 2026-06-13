#!/usr/bin/env python3
"""
长桥 CLI 数据读取全面测试脚本

基于 Longbridge Terminal CLI (https://github.com/longbridge/longbridge-terminal)
测试所有只读数据接口的 CLI 调用情况。

前置条件：
  1. 已安装 longbridge CLI 并已登录: longbridge auth login
  2. 已验证 Token 状态: longbridge check

用法：
  python tests/test_longbridge_cli.py                    # 运行所有测试
  python tests/test_longbridge_cli.py --group quotes      # 只测试行情相关
  python tests/test_longbridge_cli.py --group fundamentals # 只测试基本面相关
  python tests/test_longbridge_cli.py --verbose           # 详细输出
  python tests/test_longbridge_cli.py --adapter-only      # 只测适配器相关接口(大数据量)
  python tests/test_longbridge_cli.py --save-responses api_responses.json  # 录制返回值
  python tests/test_longbridge_cli.py --print-keys        # 打印每条响应的字段名
"""

import json
import os
import subprocess
import sys
import time
import argparse
from dataclasses import dataclass
from typing import Optional

# ──────────────────────────────────────────────────────────────
# 测试标的
# ──────────────────────────────────────────────────────────────

SYMBOLS = {
    "HK": "700.HK",        # 腾讯
    "US": "AAPL.US",       # 苹果
    "CN": "600519.SH",     # 贵州茅台
    "SG": "D05.SG",        # 星展银行
}

MARKETS = {
    "HK": "HK",
    "US": "US",
    "CN": "CN",
    "SG": "SG",
}

# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────


def run_cli(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """调用长桥 CLI 并返回结果"""
    cmd = ["longbridge"] + list(args) + ["--format", "json"]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout
    )


def parse_result(result: subprocess.CompletedProcess) -> Optional[dict | list]:
    """解析 CLI 返回的 JSON"""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


@dataclass
class TestResult:
    name: str
    command: str
    passed: bool
    message: str
    data_sample: Optional[str] = None
    stderr: Optional[str] = None
    is_optional: bool = False


class CLITester:
    """长桥 CLI 测试器"""

    def __init__(self, verbose: bool = False, print_keys: bool = False):
        self.results: list[TestResult] = []
        self.verbose = verbose
        self.print_keys = print_keys
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        # 录制容器: 存储每个接口的完整返回值
        self.recorded: list[dict] = []

    def _describe_keys(self, data) -> str:
        """提取数据的第一层 key 信息"""
        if isinstance(data, list):
            if data and isinstance(data[0], dict):
                return f"list[{len(data)}], first_item_keys: {list(data[0].keys())}"
            elif data:
                return f"list[{len(data)}], first_item type={type(data[0]).__name__}"
            else:
                return "list[0] (empty)"
        elif isinstance(data, dict):
            return f"dict, keys: {list(data.keys())}"
        else:
            return f"type={type(data).__name__}"

    def _test(self, name: str, *args, required: bool = True) -> TestResult:
        """执行单个 CLI 测试"""
        cmd_str = " ".join(["longbridge"] + list(args))
        is_optional = not required
        self.total += 1

        try:
            result = run_cli(*args)
        except subprocess.TimeoutExpired:
            r = TestResult(name, cmd_str, False, "超时 (>30s)")
            self.failed += 1
            self._log(r)
            self.recorded.append({
                "name": name, "command": cmd_str,
                "exit_code": -1, "error": "超时",
                "data": None, "stderr": "", "raw_stdout": "",
            })
            return r
        except Exception as e:
            r = TestResult(name, cmd_str, False, f"执行异常: {e}")
            self.failed += 1
            self._log(r)
            self._record_error(name, cmd_str, str(e), "", None)
            return r

        stderr_text = result.stderr.strip()[:500] if result.stderr else ""

        if result.returncode != 0:
            error_msg = stderr_text if stderr_text else "未知错误 (无 stderr 输出)"
            r = TestResult(name, cmd_str, False, f"退出码={result.returncode}", stderr=error_msg, is_optional=is_optional)
            if required:
                self.failed += 1
            else:
                self.skipped += 1
            self._log(r)
            self._record_error(name, cmd_str, f"exit={result.returncode}", stderr_text, result)
            return r

        data = parse_result(result)
        if data is None:
            r = TestResult(name, cmd_str, False, "无法解析 JSON 输出", stderr=result.stdout[:300], is_optional=is_optional)
            if required:
                self.failed += 1
            else:
                self.skipped += 1
            self._log(r)
            self._record_error(name, cmd_str, "JSON解析失败", result.stdout[:500], result)
            return r

        # 构建成功消息
        if isinstance(data, list):
            msg = f"返回 {len(data)} 条记录"
            sample = json.dumps(data[0], ensure_ascii=False, indent=2) if data else "空列表"
        elif isinstance(data, dict):
            msg = f"返回 dict, 键数={len(data)}"
            # 截取前 3 个 key
            keys = list(data.keys())[:3]
            sample = json.dumps({k: data[k] for k in keys}, ensure_ascii=False, indent=2)
        else:
            msg = f"返回 {type(data).__name__}"
            sample = str(data)[:300]

        r = TestResult(name, cmd_str, True, msg, sample)
        self.passed += 1

        # --print-keys 输出
        if self.print_keys:
            print(f"  🔑 [{name}] {self._describe_keys(data)}")

        # 录制成功响应
        self.recorded.append({
            "name": name,
            "command": cmd_str,
            "exit_code": 0,
            "data": data,
            "stderr": stderr_text,
        })

        self._log(r)
        return r

    def _record_error(self, name: str, cmd_str: str, error: str,
                      stderr: str, result) -> None:
        """录制失败的响应"""
        raw_stdout = ""
        if result and hasattr(result, 'stdout'):
            raw_stdout = (result.stdout or "")[:5000]
        self.recorded.append({
            "name": name,
            "command": cmd_str,
            "exit_code": result.returncode if result else -1,
            "error": error,
            "data": None,
            "stderr": stderr,
            "raw_stdout": raw_stdout,
        })

    def save_responses(self, filepath: str) -> None:
        """将录制的所有响应写入 JSON 文件"""
        output_dir = os.path.dirname(filepath)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        payload = {
            "test_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_tests": len(self.recorded),
            "passed": self.passed,
            "failed": self.failed,
            "results": self.recorded,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n📁 API 返回值已录制到: {filepath}")

    def _log(self, r: TestResult):
        if r.passed:
            if self.verbose:
                print(f"  ✅ [{r.name}] {r.message}")
                if r.data_sample:
                    for line in r.data_sample.split("\n")[:8]:
                        print(f"       {line}")
            else:
                # 非 verbose 模式：显示进度点
                print(".", end="", flush=True)
        elif r.is_optional:
            # 跳过（非必需命令不可用或数据为空）
            print(f"\n  ⏭️  [{r.name}] {r.message} (非必需命令，已跳过)")
            print(f"     → 命令: {r.command}")
            if r.stderr:
                print(f"     → 输出: {r.stderr[:200]}")
        else:
            # 真正的失败，永远显示详情
            print(f"\n  ❌ [{r.name}] {r.message}")
            print(f"     → 命令: {r.command}")
            if r.stderr:
                print(f"     → 输出: {r.stderr[:300]}")

    def print_summary(self):
        print("\n\n" + "=" * 60)
        print(f"📊 测试结果汇总: 总计 {self.total} | ✅ 通过 {self.passed} | ❌ 失败 {self.failed} | ⏭️ 跳过 {self.skipped}")
        print("=" * 60)
        if self.failed == 0:
            return

        print("\n❌ 失败项详情:")
        print("-" * 60)
        for i, r in enumerate(self.results, 1):
            if not r.passed:
                label = "⏭️ 跳过" if r.is_optional else "❌ 失败"
                print(f"  {i}. [{r.name}] {label}")
                print(f"     命令: {r.command}")
                print(f"     原因: {r.message}")
                if r.stderr:
                    print(f"     输出: {r.stderr[:300]}")
                print()

    def rate_limit_wait(self):
        """OpenAPI 限流: 每秒最多 10 次调用"""
        time.sleep(0.15)


# ──────────────────────────────────────────────────────────────
# 测试分组
# ──────────────────────────────────────────────────────────────


def test_diagnostics(t: CLITester):
    """诊断与连接检查"""
    print("\n🔍 一、诊断与连接检查")

    t._test("诊断-check", "check")
    t._test("认证状态", "auth", "status")


def test_quotes(t: CLITester):
    """实时行情数据"""
    print("\n📈 二、实时行情数据")

    # --- 基础报价 ---
    t._test("实时报价(单只)", "quote", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("实时报价(多只)", "quote", SYMBOLS["HK"], SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("静态信息(单只)", "static", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("静态信息(多只)", "static", SYMBOLS["HK"], SYMBOLS["US"])
    t.rate_limit_wait()

    # --- 深度行情 ---
    t._test("Level2深度行情(美股)", "depth", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("Level2深度行情(港股)", "depth", SYMBOLS["HK"])
    t.rate_limit_wait()

    # --- 逐笔成交 ---
    t._test("最近成交记录(美股)", "trades", SYMBOLS["US"], "--count", "10")
    t.rate_limit_wait()
    t._test("最近成交记录(港股)", "trades", SYMBOLS["HK"], "--count", "10")
    t.rate_limit_wait()

    # --- 分时数据 ---
    t._test("分时数据(美股)", "intraday", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("分时数据(港股)", "intraday", SYMBOLS["HK"])
    t.rate_limit_wait()

    # --- 资金流向 ---
    t._test("资金分布(美股)", "capital", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("资金流向时序(美股)", "capital", SYMBOLS["US"], "--flow")
    t.rate_limit_wait()

    # --- 港股经纪队列 ---
    t._test("经纪队列(港股)", "brokers", SYMBOLS["HK"])


def test_klines_and_indicators(t: CLITester):
    """K线与计算指标"""
    print("\n📊 三、K线与计算指标")

    # --- K线 ---
    t._test("日K线(美股)", "kline", SYMBOLS["US"], "--period", "day", "--count", "10")
    t.rate_limit_wait()
    t._test("日K线(港股)", "kline", SYMBOLS["HK"], "--period", "day", "--count", "10")
    t.rate_limit_wait()
    t._test("周K线(美股)", "kline", SYMBOLS["US"], "--period", "week", "--count", "10")
    t.rate_limit_wait()
    t._test("月K线(港股)", "kline", SYMBOLS["HK"], "--period", "month", "--count", "10")
    t.rate_limit_wait()
    t._test("前复权K线(美股)", "kline", SYMBOLS["US"], "--adjust", "forward", "--count", "5")
    t.rate_limit_wait()
    t._test("历史K线(美股)", "kline", "history", SYMBOLS["US"], "--start", "2025-01-01")
    t.rate_limit_wait()

    # --- 计算指标 ---
    t._test("计算指标(PE/PB/EPS-美股)", "calc-index", SYMBOLS["US"], "--fields", "pe,pb,eps")
    t.rate_limit_wait()
    t._test("计算指标(PE/PB/EPS-港股)", "calc-index", SYMBOLS["HK"], "--fields", "pe,pb,eps,turnover_rate")


def test_market_and_index(t: CLITester):
    """市场与指数数据"""
    print("\n🏢 四、市场与指数数据")

    # --- 指数成分股 ---
    t._test("标普500成分股(前20)", "constituent", ".SPX.US", "--limit", "20")
    t.rate_limit_wait()
    t._test("恒生指数成分股(前20)", "constituent", "HSI.HK", "--limit", "20")
    t.rate_limit_wait()

    # --- 市场情绪 ---
    t._test("市场情绪温度(美股)", "market-temp", "US")
    t.rate_limit_wait()
    t._test("市场情绪温度(港股)", "market-temp", "HK")
    t.rate_limit_wait()

    # --- 交易时段 ---
    t._test("交易时段(全市场)", "trading", "session")
    t.rate_limit_wait()
    t._test("交易日历(港股)", "trading", "days", "HK")
    t.rate_limit_wait()

    # --- 热搜排名 ---
    t._test("热门股票排名(美股)", "rank", "--key", "ib_hot_all-us", "--count", "10")
    t.rate_limit_wait()
    t._test("异动股票", "top-movers", "--market", "US", "--count", "10")
    t.rate_limit_wait()

    # --- 汇率 ---
    t._test("汇率查询", "exchange-rate")
    t.rate_limit_wait()

    # --- 证券列表 ---
    t._test("证券列表(港股)", "security-list", "HK")


def test_fundamentals(t: CLITester):
    """基本面数据"""
    print("\n📋 五、基本面数据")

    # --- 财务报表 (API-heavy, use longer waits) ---
    t._test("利润表(美股)", "financial-report", SYMBOLS["US"], "--kind", "IS")
    time.sleep(0.3)
    t._test("资产负债表(美股)", "financial-report", SYMBOLS["US"], "--kind", "BS")
    time.sleep(0.3)
    t._test("现金流量表(美股)", "financial-report", SYMBOLS["US"], "--kind", "CF")
    time.sleep(0.3)
    t._test("最新财报摘要(美股)", "financial-report", SYMBOLS["US"], "--latest")
    time.sleep(0.3)
    t._test("利润表(港股)", "financial-report", SYMBOLS["HK"], "--kind", "IS")
    t.rate_limit_wait()

    # --- 详细财报 (v3) ---
    t._test("详细财报(美股)", "financial-statement", SYMBOLS["US"], "--kind", "ALL", "--report", "af")
    t.rate_limit_wait()

    # --- 估值数据 ---
    t._test("估值快照(美股-PE)", "valuation", SYMBOLS["US"], "--indicator", "pe")
    t.rate_limit_wait()
    t._test("估值快照(港股-PB)", "valuation", SYMBOLS["HK"], "--indicator", "pb")
    t.rate_limit_wait()
    t._test("估值历史(美股-PE 5年)", "valuation", SYMBOLS["US"], "--history", "--indicator", "pe", "--range", "5")
    t.rate_limit_wait()
    t._test("行业估值排名(美股)", "valuation-rank", SYMBOLS["US"])
    t.rate_limit_wait()

    # --- 一致预期 ---
    t._test("一致预期(美股)", "consensus", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("EPS预测(美股)", "forecast-eps", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("分析师预测(美股)", "analyst-estimates", SYMBOLS["US"], required=False)
    t.rate_limit_wait()

    # --- 业务分部 ---
    t._test("业务分部(美股)", "business-segments", SYMBOLS["US"])


def test_institutions_and_holders(t: CLITester):
    """机构评级与股东"""
    print("\n🏦 六、机构评级与股东")

    # --- 机构评级 ---
    t._test("机构评级(美股)", "institution-rating", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("机构评级历史(美股)", "institution-rating", SYMBOLS["US"], "--history")
    t.rate_limit_wait()
    t._test("机构评级详情(美股)", "institution-rating", "detail", SYMBOLS["US"])

    # --- 股东 ---
    t.rate_limit_wait()
    t._test("基金持仓(美股)", "fund-holder", SYMBOLS["US"], "--count", "10")
    t.rate_limit_wait()
    t._test("机构股东(美股)", "shareholder", SYMBOLS["US"], "--count", "10")

    # --- 分红 ---
    t.rate_limit_wait()
    t._test("分红记录(美股)", "dividend", SYMBOLS["US"])

    # --- 内部人交易 ---
    t.rate_limit_wait()
    t._test("内部人交易(美股)", "insider-trades", SYMBOLS["US"], "--count", "10")

    # --- 公司行动 ---
    t.rate_limit_wait()
    t._test("公司行动(港股)", "corp-action", SYMBOLS["HK"])


def test_comparison_and_industry(t: CLITester):
    """对比与行业分析"""
    print("\n🔄 七、对比与行业分析")

    # --- 多股对比 ---
    t._test("多股估值对比(自动同行业)", "compare", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("多股估值对比(指定股票)", "compare", SYMBOLS["HK"], "9988.HK", "3690.HK", "--currency", "HKD")
    t.rate_limit_wait()

    # --- 行业排名 ---
    t._test("行业排名(美股涨跌幅)", "industry-rank", "--market", "US", "--indicator", "leading-gainer", "--limit", "10")
    t.rate_limit_wait()

    # --- 做空数据 ---
    t._test("做空持仓(美股)", "short-positions", SYMBOLS["US"], "--count", "10")
    t.rate_limit_wait()
    t._test("做空交易量(美股)", "short-trades", SYMBOLS["US"], "--count", "10")
    t.rate_limit_wait()
    t._test("做空持仓(港股)", "short-positions", SYMBOLS["HK"], "--count", "10")


def test_news_and_search(t: CLITester):
    """资讯与搜索"""
    print("\n📰 八、资讯与搜索")

    # --- 资讯 ---
    t._test("股票资讯(美股)", "news", SYMBOLS["US"], "--count", "5")
    t.rate_limit_wait()
    t._test("监管文件(美股)", "filing", SYMBOLS["US"], "list", "--count", "5", required=False)
    t.rate_limit_wait()

    # --- 搜索 (部分 CLI 版本可能不支持) ---
    t._test("搜索股票", "search", "Apple", "--tab", "market", "--count", "5", required=False)
    t.rate_limit_wait()
    t._test("热搜关键词", "search-hot", required=False)


def test_finance_calendar(t: CLITester):
    """财经日历"""
    print("\n📅 九、财经日历")

    t._test("财报发布日期", "finance-calendar", "report", "--symbol", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("分红日期", "finance-calendar", "dividend", "--symbol", SYMBOLS["US"])
    t.rate_limit_wait()
    t._test("宏观事件", "finance-calendar", "macrodata", "--star", "3")
    t.rate_limit_wait()
    t._test("市场休市日(港股)", "finance-calendar", "closed", "--market", "HK")


def test_screener(t: CLITester):
    """股票筛选器"""
    print("\n🔬 十、股票筛选器")

    t._test("筛选指标列表", "screener", "indicators")
    t.rate_limit_wait()
    t._test("推荐策略列表", "screener", "strategies")


# ══════════════════════════════════════════════════════
#  适配器专用测试（大数据量，用于检查字段匹配）
# ══════════════════════════════════════════════════════

def test_adapter_interfaces(t: CLITester):
    """
    适配器接口专项测试：用足够的数据量采样，覆盖所有适配器依赖的 CLI 接口。
    用于录制返回值 → 对照检查 longbridge_adapter.py 的字段假设。
    """
    print("\n🔌 适配器接口专项测试 (大数据量采样)")

    # ── 1. K线 (kline_to_dataframe) ──
    # 拉取足够多的日线数据确保有完整字段
    t._test("ADP-K线(美股日线x100)", "kline", SYMBOLS["US"],
            "--period", "day", "--count", "100")
    t.rate_limit_wait()
    t._test("ADP-K线(港股日线x100)", "kline", SYMBOLS["HK"],
            "--period", "day", "--count", "100")
    t.rate_limit_wait()
    t._test("ADP-K线(前复权美股x100)", "kline", SYMBOLS["US"],
            "--period", "day", "--count", "100", "--adjust", "forward")
    t.rate_limit_wait()

    # ── 2. 计算指标 (calc_index_to_valuation_df) ──
    # 请求全量字段
    t._test("ADP-计算指标(美股全字段)", "calc-index", SYMBOLS["US"],
            "--fields", "pe,pb,eps,turnover_rate,ps,dividend_yield,market_cap,float_market_cap")
    t.rate_limit_wait()
    t._test("ADP-计算指标(港股全字段)", "calc-index", SYMBOLS["HK"],
            "--fields", "pe,pb,eps,turnover_rate,ps,dividend_yield,market_cap,float_market_cap")
    t.rate_limit_wait()

    # ── 3. 利润表 IS (income_to_dataframe) ──
    t._test("ADP-利润表(美股)", "financial-report", SYMBOLS["US"], "--kind", "IS")
    time.sleep(0.3)
    t._test("ADP-利润表(港股)", "financial-report", SYMBOLS["HK"], "--kind", "IS")
    time.sleep(0.3)

    # ── 4. 资产负债表 BS (balance_to_dataframe) ──
    t._test("ADP-资产负债表(美股)", "financial-report", SYMBOLS["US"], "--kind", "BS")
    time.sleep(0.3)
    t._test("ADP-资产负债表(港股)", "financial-report", SYMBOLS["HK"], "--kind", "BS")
    time.sleep(0.3)

    # ── 5. 现金流量表 CF (cashflow_to_dataframe) ──
    t._test("ADP-现金流量表(美股)", "financial-report", SYMBOLS["US"], "--kind", "CF")
    time.sleep(0.3)
    t._test("ADP-现金流量表(港股)", "financial-report", SYMBOLS["HK"], "--kind", "CF")
    time.sleep(0.3)

    # ── 6. 最新财报摘要 --latest (financial_report_to_fina_indicator) ──
    t._test("ADP-最新财报摘要(美股)", "financial-report", SYMBOLS["US"], "--latest")
    time.sleep(0.3)
    t._test("ADP-最新财报摘要(港股)", "financial-report", SYMBOLS["HK"], "--latest")
    time.sleep(0.3)

    # ── 7. 估值历史 (fetch_pe_percentile / valuation_history) ──
    t._test("ADP-估值历史PE(美股5年)", "valuation", SYMBOLS["US"],
            "--history", "--indicator", "pe", "--range", "5")
    t.rate_limit_wait()
    t._test("ADP-估值历史PB(港股5年)", "valuation", SYMBOLS["HK"],
            "--history", "--indicator", "pb", "--range", "5")
    t.rate_limit_wait()

    # ── 8. 估值快照 (valuation_snapshot) ──
    t._test("ADP-估值快照PE(美股)", "valuation", SYMBOLS["US"], "--indicator", "pe")
    t.rate_limit_wait()
    t._test("ADP-估值快照PB(港股)", "valuation", SYMBOLS["HK"], "--indicator", "pb")


# ──────────────────────────────────────────────────────────────
# 分组注册
# ──────────────────────────────────────────────────────────────

TEST_GROUPS = {
    "diagnostics":      ("诊断与连接",      test_diagnostics),
    "quotes":           ("实时行情",        test_quotes),
    "klines":           ("K线与指标",       test_klines_and_indicators),
    "market":           ("市场与指数",      test_market_and_index),
    "fundamentals":     ("基本面数据",      test_fundamentals),
    "institutions":     ("机构评级与股东",  test_institutions_and_holders),
    "comparison":       ("对比与行业分析",  test_comparison_and_industry),
    "news":             ("资讯与搜索",      test_news_and_search),
    "calendar":         ("财经日历",        test_finance_calendar),
    "screener":         ("股票筛选器",      test_screener),
    "adapter":          ("适配器专项测试",  test_adapter_interfaces),
}


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="长桥 CLI 数据读取全面测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
可用分组: all, diagnostics, quotes, klines, market, fundamentals,
          institutions, comparison, news, calendar, screener, adapter

示例:
  %(prog)s                          # 运行所有测试
  %(prog)s --group quotes           # 只测试实时行情
  %(prog)s --group fundamentals     # 只测试基本面
  %(prog)s --group quotes,klines    # 测试多个分组
  %(prog)s --adapter-only           # 只测适配器相关接口(大数据量)
  %(prog)s --verbose                # 显示详细输出
  %(prog)s --print-keys             # 打印每条响应的字段名
  %(prog)s --save-responses api_responses.json  # 录制返回值
  %(prog)s --list                   # 列出所有分组
        """,
    )
    parser.add_argument(
        "--group", "-g",
        default="all",
        help="指定测试分组 (逗号分隔多个分组, 默认: all)",
    )
    parser.add_argument(
        "--adapter-only",
        action="store_true",
        help="仅测试适配器相关接口 (等同于 --group adapter, 含大数据量)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细输出 (包括返回数据样本)",
    )
    parser.add_argument(
        "--print-keys",
        action="store_true",
        help="打印每条成功响应的第一层字段名",
    )
    parser.add_argument(
        "--save-responses",
        default="",
        metavar="FILEPATH",
        help="将所有 CLI 返回值录制到指定 JSON 文件",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有可用测试分组",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="排除指定分组 (逗号分隔)",
    )

    args = parser.parse_args()

    if args.list:
        print("可用测试分组:")
        for key, (desc, _) in TEST_GROUPS.items():
            print(f"  {key:20s} - {desc}")
        return

    # adapter-only 快捷方式
    if args.adapter_only:
        selected = ["adapter"]
    elif args.group == "all":
        selected = list(TEST_GROUPS.keys())
    else:
        selected = [g.strip() for g in args.group.split(",") if g.strip()]

    # 排除分组
    if args.exclude:
        exclude = [g.strip() for g in args.exclude.split(",") if g.strip()]
        selected = [g for g in selected if g not in exclude]

    # 验证分组
    invalid = [g for g in selected if g not in TEST_GROUPS]
    if invalid:
        print(f"❌ 无效分组: {invalid}")
        print(f"可用分组: {list(TEST_GROUPS.keys())}")
        sys.exit(1)

    # 先检查 CLI 可用性
    print("🔍 检查长桥 CLI 可用性...")
    try:
        check = run_cli("check")
        if check.returncode != 0:
            print("❌ 长桥 CLI 不可用或未登录，请先执行: longbridge auth login")
            sys.exit(1)
        data = parse_result(check)
        if data:
            print(f"   ✅ Token 有效")
            if isinstance(data, dict):
                for k, v in data.items():
                    print(f"      {k}: {v}")
    except FileNotFoundError:
        print("❌ 未找到 longbridge 命令，请先安装长桥 Terminal CLI")
        print("   brew install --cask longbridge/tap/longbridge-terminal")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("❌ 长桥 CLI 连接超时")
        sys.exit(1)

    print(f"\n📋 将运行以下测试分组: {', '.join(selected)}")
    print(f"   测试标的: HK={SYMBOLS['HK']}, US={SYMBOLS['US']}, CN={SYMBOLS['CN']}, SG={SYMBOLS['SG']}")

    tester = CLITester(verbose=args.verbose, print_keys=args.print_keys)

    # 运行测试
    for group_key in selected:
        _, test_func = TEST_GROUPS[group_key]
        test_func(tester)
        if len(selected) > 1:
            time.sleep(0.5)  # 分组间稍作停顿

    tester.print_summary()

    # 保存录制结果
    if args.save_responses:
        tester.save_responses(args.save_responses)

    if tester.failed == 0 and tester.total == tester.passed:
        print("\n🎉 所有测试通过！长桥 CLI 数据接口工作正常。")
    elif tester.failed > 0:
        print(f"\n⚠️  有 {tester.failed} 个测试失败，请检查上述错误信息。")


if __name__ == "__main__":
    main()
