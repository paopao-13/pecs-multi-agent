#!/usr/bin/env bash
# 把 webshop_patches/ 里的三处补丁覆盖进 webshop/ 对应路径。
# 用法（在仓库根目录执行）：bash webshop_patches/apply.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCH_DIR="$ROOT/webshop_patches"
WS="$ROOT/webshop"

if [ ! -d "$WS/web_agent_site" ]; then
  echo "❌ 未找到 webshop/ 目录，请先: git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop"
  exit 1
fi

echo ">>> 复制补丁文件到 webshop/ ..."
cp "$PATCH_DIR/bm25_search.py"        "$WS/web_agent_site/engine/bm25_search.py"
cp "$PATCH_DIR/engine.py"             "$WS/web_agent_site/engine/engine.py"
cp "$PATCH_DIR/web_agent_text_env.py" "$WS/web_agent_site/envs/web_agent_text_env.py"
echo "✅ 补丁已应用。接下来按 docs/webshop_local_runbook.md 装依赖、下数据、起桥。"
