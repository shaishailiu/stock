#!/usr/bin/env python3
"""
清空数据库脚本 - 删除数据库和分析报告，从头开始执行

将删除：
  - data/newstock.db        (SQLite 数据库)
  - data/processed/         (处理后数据)
  - reports/                (分析报告)

将保留：
  - data/raw/               (原始缓存 parquet，可复用)
"""

import os
import sys
import shutil
from pathlib import Path

# 项目根目录（脚本位于 scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

TO_CLEAR = [
    {
        "path": PROJECT_ROOT / "data" / "newstock.db",
        "desc": "SQLite 数据库",
    },
    {
        "path": PROJECT_ROOT / "data" / "processed",
        "desc": "处理后数据",
    },
    {
        "path": PROJECT_ROOT / "reports",
        "desc": "分析报告",
    },
]


def confirm(message: str) -> bool:
    """等待用户确认"""
    ans = input(f"{message} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def clear_item(item: dict) -> bool:
    """清理单个路径"""
    path = item["path"]
    desc = item["desc"]

    if not path.exists():
        print(f"  ⏭  [{desc}] 不存在，跳过: {path}")
        return False

    try:
        if path.is_file():
            path.unlink()
            print(f"  ✅ [{desc}] 已删除文件: {path}")
        elif path.is_dir():
            shutil.rmtree(path)
            print(f"  ✅ [{desc}] 已删除目录: {path}")
        return True
    except Exception as e:
        print(f"  ❌ [{desc}] 删除失败: {e}")
        return False


def main():
    print("=" * 60)
    print("  清空数据库脚本")
    print("=" * 60)
    print()
    print("将要清理以下内容:")
    print()

    existing = []
    for item in TO_CLEAR:
        path = item["path"]
        rel_path = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        status = "存在" if path.exists() else "不存在"
        marker = "  [存在]" if path.exists() else "  [无]"
        print(f"{marker}  {rel_path}  ({item['desc']})")
        if path.exists():
            existing.append(item)

    print()

    if not existing:
        print("没有需要清理的内容，所有数据路径都已清空。")
        return

    # 双重确认
    if not confirm("确认删除以上所有缓存和数据？此操作不可恢复！"):
        print("已取消。")
        return

    if not confirm("再次确认：真的要删除所有数据吗？"):
        print("已取消。")
        return

    print()
    print("开始清理...")
    print("-" * 40)

    deleted_count = 0
    for item in existing:
        if clear_item(item):
            deleted_count += 1

    print("-" * 40)
    print(f"清理完成：成功删除 {deleted_count}/{len(existing)} 项。")

    # 确保必要的目录结构存在
    print()
    print("重建必要的目录结构...")
    for d in ["data/processed", "reports"]:
        (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)
        print(f"  ✅ {d}/")

    print()
    print("=" * 60)
    print("数据库已清空！")
    print(f"现在可以运行: python pipelines/daily_prepare.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
