"""GAIA 官方数据集本地镜像下载器（绕开 huggingface_hub 缓存机制）

为什么需要这个脚本
------------------
GAIA (gaia-benchmark/GAIA) 是 HuggingFace 上的 **门控（gated）** 数据集，
本项目的 `datasets/gaia_official_dataset.py` 原本用 `snapshot_download()`
整仓拉取（119 个文件）。实测在受限环境下这条路有两个硬伤：

1. **镜像丢鉴权**：`HF_ENDPOINT=https://hf-mirror.com` 对 `/resolve/` 一律
   308 跳回 `huggingface.co`，而 HTTP 客户端在跨域重定向时会剥掉
   `Authorization` 头，门控文件必然 401。只有直连 `huggingface.co`
   （302 → 预签名的 CDN 地址）才拿得到数据。
2. **缓存机制触发批量删除保护**：`snapshot_download` 对每个文件都会创建
   `.locks/`、`tmp_*`、`*.incomplete` 并在完成后删除，119 个文件累计的
   删除次数会触发宿主环境的批量删除保护（阈值 50/轮）而被中断。

因此改为**直接用 HTTP 下载到项目内目录**（纯写入，零删除），再让
`GAIAOfficialDataset` 通过 `PEC_GAIA_LOCAL_DIR` 读取该目录，评测链路
不再依赖 HF 缓存机制，可复现、可离线、CI 友好。

用法
----
  # 默认：Level 1 validation（53 题，含 11 个附件）
  python scripts/download_gaia.py

  # 全部 validation（level 1/2/3 的元数据 parquet + 全部附件）
  python scripts/download_gaia.py --all-levels

  # 同时下载 test split（用于提交 leaderboard，答案私有）
  python scripts/download_gaia.py --include-test

  # 自定义目标目录
  python scripts/download_gaia.py --dest data/gaia

Token 读取顺序：命令行 --token > 环境变量 HF_TOKEN > 项目根 .env 的 HF_TOKEN。
下载完成后打印落盘清单与校验（大小比对 HuggingFace tree API）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

REPO_ID = "gaia-benchmark/GAIA"
# 必须直连：镜像对 /resolve/ 会 308 跳回 hf.co 并在重定向中丢失 Authorization 头
DEFAULT_ENDPOINT = "https://huggingface.co"


def _load_dotenv_token(project_root: str) -> Optional[str]:
    """从 .env 读取 HF_TOKEN（不引入额外依赖，手写极简解析）"""
    p = os.path.join(project_root, ".env")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "HF_TOKEN":
                return v.strip().strip('"').strip("'")
    return None


def list_repo_files(endpoint: str, token: str, repo_id: str = REPO_ID) -> List[Dict]:
    """调用 tree API 列出仓库全部文件（含 LFS 的真实 size）"""
    import requests

    url = f"{endpoint}/api/datasets/{repo_id}/tree/main?recursive=true"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(
            f"列出仓库文件失败: HTTP {r.status_code} {r.text[:200]}\n"
            f"请确认 token 有效且已获得 GAIA 访问许可: {endpoint}/datasets/{repo_id}"
        )
    return [e for e in r.json() if e.get("type") == "file"]


def download_one(endpoint: str, token: str, repo_id: str, path: str,
                 dest_root: str, expect_size: Optional[int]) -> str:
    """下载单个文件到 dest_root/path，返回 'skip' | 'ok'"""
    import requests

    local_path = os.path.join(dest_root, path.replace("/", os.sep))
    if expect_size is not None and os.path.exists(local_path):
        if os.path.getsize(local_path) == expect_size:
            return "skip"

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    url = f"{endpoint}/datasets/{repo_id}/resolve/main/{path}"
    with requests.get(url, headers={"Authorization": f"Bearer {token}"},
                      stream=True, timeout=120) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {path}: {r.text[:200]}")
        # 先写临时名再改名（同目录 rename，不产生删除）
        tmp = local_path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
    if expect_size is not None and os.path.getsize(tmp) != expect_size:
        raise RuntimeError(
            f"大小校验失败 {path}: 期望 {expect_size} 实际 {os.path.getsize(tmp)}"
        )
    os.replace(tmp, local_path)
    return "ok"


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="GAIA 官方数据集本地镜像下载器")
    parser.add_argument("--dest", default=os.path.join(project_root, "data", "gaia"),
                        help="目标目录（默认 data/gaia，已加入 .gitignore）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--token", default=None)
    parser.add_argument("--all-levels", action="store_true",
                        help="下载 validation 全部 level 的 parquet 与附件（默认只下 level1 需要的）")
    parser.add_argument("--include-test", action="store_true",
                        help="同时下载 2023/test（答案私有，仅用于提交 leaderboard）")
    args = parser.parse_args()

    token = args.token or os.getenv("HF_TOKEN") or _load_dotenv_token(project_root)
    if not token:
        print("错误：未找到 HF_TOKEN（--token / 环境变量 / .env 均无）", file=sys.stderr)
        return 2

    endpoint = args.endpoint.rstrip("/")
    print(f"endpoint : {endpoint}")
    print(f"dest     : {args.dest}")

    files = list_repo_files(endpoint, token)
    print(f"仓库文件数: {len(files)}")

    selected: List[Dict] = []
    for e in files:
        p = e["path"]
        if p in (".gitattributes", "README.md"):
            selected.append(e)
            continue
        if p.startswith("2023/validation/"):
            if p.startswith("2023/validation/metadata"):
                # parquet：默认只要 level1（53 题），--all-levels 全要
                if args.all_levels or "level1" in p:
                    selected.append(e)
            else:
                # 附件：默认全下（小文件，且 level1 的 11 个混在其中）
                selected.append(e)
            continue
        if args.include_test and p.startswith("2023/test/"):
            if p.startswith("2023/test/metadata"):
                if args.all_levels or "level1" in p:
                    selected.append(e)
            else:
                selected.append(e)

    print(f"选中文件数: {len(selected)}")
    total_bytes = sum(e.get("size") or 0 for e in selected)
    print(f"预计大小  : {total_bytes / 1024 / 1024:.1f} MB")

    ok = skip = 0
    t0 = time.time()
    for i, e in enumerate(selected, 1):
        p = e["path"]
        try:
            res = download_one(endpoint, token, REPO_ID, p, args.dest, e.get("size"))
        except Exception as ex:
            print(f"  [{i}/{len(selected)}] 失败 {p}: {type(ex).__name__}: {str(ex)[:160]}")
            # 单文件失败重试一次
            try:
                res = download_one(endpoint, token, REPO_ID, p, args.dest, e.get("size"))
            except Exception as ex2:
                print(f"     重试仍失败 {p}: {str(ex2)[:160]}")
                return 1
        if res == "skip":
            skip += 1
        else:
            ok += 1
            print(f"  [{i}/{len(selected)}] ok {p}")

    print(f"\n完成：新增/更新 {ok} 个，跳过（已存在且大小一致）{skip} 个，"
          f"耗时 {time.time() - t0:.1f}s")

    # ---- 落盘后的关键自检：数据目录会不会被路径守卫拒掉？----
    # 黑名单含 C:\Users，而 Windows 上数据（含 HF 默认缓存 ~/.cache）默认就在
    # C:\Users 下，还常带 "." 前缀目录（.cache）命中「隐藏文件」规则。
    # 不自检的话表现为：附件题全部静默 0 分，而工具层返回的是「错误：禁止访问…」。
    try:
        sys.path.insert(0, project_root)
        from tools.path_guard import is_forbidden_path  # noqa: E402

        probe = next((e["path"] for e in selected
                      if e["path"].startswith("2023/") and not e["path"].endswith(".parquet")), None)
        if probe is None:
            probe = selected[0]["path"]
        abs_probe = os.path.join(args.dest, probe.replace("/", os.sep))
        reason = is_forbidden_path(abs_probe)
        if reason:
            print(f"\n⚠️ 警告：该目录被路径守卫拦截（{reason}），附件将无法正常解析。\n"
                  f"   请在 .env 设置：PEC_DATA_ALLOW_DIR={args.dest}\n"
                  f"   或将镜像目录放到非 C:\\Users / 非隐藏目录下再重跑。\n"
                  f"   自检样本：{abs_probe}")
        else:
            print(f"路径守卫自检：通过（{probe} 可正常解析）")
    except Exception as e:  # 自检失败不应让下载失败
        print(f"路径守卫自检跳过：{type(e).__name__}: {e}")

    # 落盘清单
    manifest = {
        "repo_id": REPO_ID,
        "endpoint": endpoint,
        "dest": args.dest,
        "files": [{"path": e["path"], "size": e.get("size")} for e in selected],
    }
    mpath = os.path.join(args.dest, "manifest.json")
    os.makedirs(args.dest, exist_ok=True)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"清单已写入: {mpath}")
    print(f"\n下一步：设置 PEC_GAIA_LOCAL_DIR={args.dest} 后再跑 GAIA 评测")
    return 0


if __name__ == "__main__":
    sys.exit(main())
