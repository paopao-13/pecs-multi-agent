#!/usr/bin/env python3
"""
WebShop 线路 A 前置体检（纯标准库，任意 Python 3 可跑）

在动手装 conda 环境 / clone / 构建索引之前先跑它，30 秒自检机器是否够格。
避免在 Java 没装、磁盘不够、网络不通的情况下闷头跑 20 分钟白忙。

用法：
  python scripts/webshop_preflight.py
  python scripts/webshop_preflight.py --path D:/webshop   # 指定打算放 WebShop 的盘

退出码：0 = 前置基本就绪，可继续；1 = 有阻断项，先解决再试。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys


def run(cmd: str, timeout: int = 15):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "ignore") + (p.stderr or b"").decode("utf-8", "ignore")
        return p.returncode, out.strip()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def section(title: str):
    print("\n=== " + title + " ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--path",
        default=os.getcwd(),
        help="打算存放 WebShop 的目录（用于检查磁盘空间）",
    )
    args = ap.parse_args()
    path = os.path.abspath(args.path)
    all_ok = True

    # ---- Python --------------------------------------------------------
    section("Python")
    py_ok = sys.version_info >= (3, 8)
    all_ok &= py_ok
    print(f"[{'PASS' if py_ok else 'FAIL'}] Python >= 3.8: {sys.version.split()[0]}")
    if not py_ok:
        print("        -> WebShop 需 3.8.x；bridge 会装在独立 conda 环境，本机 base 无所谓")

    # ---- Git -----------------------------------------------------------
    section("Git")
    rc, out = run("git --version")
    git_ok = rc == 0
    all_ok &= git_ok
    print(f"[{'PASS' if git_ok else 'FAIL'}] git: {out.splitlines()[0] if out else '未找到'}")
    if not git_ok:
        print("        -> 装 Git for Windows: https://git-scm.com/download/win")

    # ---- Java (pyserini 必需) -----------------------------------------
    section("Java (pyserini 必需)")
    rc, out = run("java -version")
    if rc == 0:
        ver_line = next((l for l in out.splitlines() if "version" in l), out)
        m = re.search(r'"(\d+)\.(\d+)(?:\.(\d+))?', ver_line)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            major = b if a == 1 else a  # Java 8 显示为 "1.8"
        else:
            major = 0
        if major == 0:
            print(f"[PASS] Java 已安装: {ver_line.strip()}")
        elif major >= 17:
            print(f"[WARN] Java 版本较新 ({ver_line.strip()})")
            print("        -> pyserini/旧依赖在 Java 17+ 偶有兼容问题，建议 JDK 8 或 11")
        else:
            print(f"[PASS] Java: {ver_line.strip()}")
        java_ok = True
    else:
        java_ok = False
        all_ok = False
        print("[FAIL] Java 未安装：pyserini 索引必须 Java")
        print("        -> 装 JDK 8/11 并加入 PATH: https://adoptium.net/")
    all_ok &= java_ok

    # ---- 网络 ----------------------------------------------------------
    section("网络 (clone + 下载商品/指令数据)")
    net_ok = True
    for host in ("github.com", "raw.githubusercontent.com"):
        try:
            socket.create_connection((host, 443), timeout=8)
            print(f"[PASS] 可达 {host}:443")
        except Exception as e:  # noqa: BLE001
            net_ok = False
            all_ok = False
            print(f"[FAIL] 不可达 {host}: {e}")
            print("        -> 检查代理/防火墙；WebShop 数据需从 GitHub 下载")
    all_ok &= net_ok

    # ---- 磁盘 ----------------------------------------------------------
    section("磁盘空间 (small 数据集约需 10-12GB, 全量 >30GB)")
    try:
        total, used, free = shutil.disk_usage(path)
        free_gb = free / (1024 ** 3)
        print(f"[INFO] {path} 可用 {free_gb:.1f} GB")
        if free_gb < 12:
            all_ok = False
            print("[FAIL] 磁盘 < 12GB：small 数据集也要 ~12GB")
            print("        -> 清理空间或换盘")
        else:
            print(f"[PASS] 磁盘空间充足 ({free_gb:.1f} GB)")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 无法读取磁盘信息: {e}")

    # ---- conda (可选) --------------------------------------------------
    section("conda (可选)")
    rc, out = run("conda --version")
    if rc == 0:
        print(f"[PASS] {out.splitlines()[0] if out else 'conda 可用'}")
    else:
        print("[WARN] 未检测到 conda（用于建独立 3.8 环境）；可改用 venv 或 Docker 路线")

    # ---- 结论 ----------------------------------------------------------
    print("\n========================================")
    if all_ok:
        print("结论: 前置项基本就绪，可以开始路线 A（conda 原生）。")
        print("下一步: 照 docs/QUICKSTART_webshop_routeA.md 第 1 步 clone + 建环境。")
        sys.exit(0)
    else:
        print("结论: 有阻断项（见上方 FAIL），先解决再跑路线 A。")
        print("若暂时不想折腾 WebShop 真实环境，PECS 不设 WEBSHOP_SERVER_URL 即自动退回本地 mock。")
        sys.exit(1)


if __name__ == "__main__":
    main()
