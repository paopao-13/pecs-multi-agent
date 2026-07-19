# PECS 本机执行总指南（无坑详细版）

> 本指南整合了 WebShop 真实环境运行、Key 吊销、依赖锁文件刷新三件事，并修正了原方案中所有已知坑点。
> 执行环境：**Windows + Git Bash**（不是 CMD/PowerShell）。仓库根目录：`D:/简历/pecs-multi-agent`（下文简称 `pecs-multi-agent`）。
> 已确认的本机事实：无 conda（用系统 Python 3.11 venv 替代）、`.env` 含有效 DeepSeek Key、WebShop 数据文件已就位。

---

## 0. 背景：WebShop 真实环境是怎么跑的（先搞清 A 和 B 的关系）

PECS 要在"真实 WebShop"上评测，实际有 **3 个独立进程**协作：

```
┌─────────────────────────┐    HTTP :8000 (reset/step/health)   ┌──────────────────────────┐
│  PECS 主程序（窗口 C）    │ ─────────────────────────────────▶ │  WebShop HTTP 桥（窗口 A） │
│  Python + langgraph      │ ◀───────────────────────────────── │  webshop_server.py        │
│  run_webshop.py          │   返回 observation / reward          │  包着 WebAgentTextEnv-v0   │
└─────────────────────────┘                                     └──────────────────────────┘
        │ 用 LLM 网关(DeepSeek)生成动作                                │ 用 rank_bm25 搜索商品
        ▼                                                          ▼
   tools/webshop.py (HTTP 客户端)                        web_agent_site 引擎 + BM25 搜索后端
```

**为什么拆成"桥"？** WebShop 原生依赖 gym/旧版库，和 PECS 主环境冲突。所以 WebShop 单独跑在一个 **venv（Python 3.11）** 里，只暴露 HTTP 接口；PECS 这边是纯 HTTP 客户端，零冲突。

**一句话**：你在两个 Git Bash 窗口分别跑"桥"和"PECS"，中间靠 `WEBSHOP_SERVER_URL=http://localhost:8000` 连起来。

---

## 1. 前置软件（一次性）

| 软件 | 用途 | 验证 |
|---|---|---|
| Git for Windows | 提供 Git Bash | `git --version` |
| Python 3.11（系统已装） | 建 WebShop 隔离 venv | `C:\Users\jx\AppData\Local\Programs\Python\Python311\python.exe --version` |
| 你常用的 Python 环境 | 跑 PECS 主程序 | `python --version` |

> **不需要装**：Java/JDK、Docker、torch、Miniconda（全用 venv 替代，更轻量）。
> 原方案写的是 conda py3.8，但你本机无 conda，已验证系统 Python 3.11 venv 同样可跑。

---

## 2. 克隆 WebShop 源码（已做过可跳过）

**窗口 A（Git Bash）**：
```bash
cd pecs-multi-agent
git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
cd webshop && git rev-parse --short HEAD && echo "CLONE_OK"
```
> `Connection was reset` → 等 10 秒重跑。

---

## 3. 建 WebShop 专用 venv + 装依赖

**窗口 A（Git Bash）**：
```bash
cd pecs-multi-agent/webshop
# 用系统 Python 3.11 建隔离 venv（本机无 conda，venv 同样可行）
"C:/Users/jx/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
source .venv/Scripts/activate
python --version      # 应显示 3.11.x

pip install gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm \
            rank_bm25 thefuzz scikit_learn spacy
python -m spacy download en_core_web_sm     # 小模型 ~12MB，必装（环境初始化必走 goal.get_goals）
pip install transformers==4.19.2            # 仅兼容导入；装不上先跳过，遇 import 报错再加
```
> **刻意不含** `pyserini`（已用纯 Python `rank_bm25` 替代）、`torch`（已惰性，运行时用不到）。
> 已在 venv 内，`pip` 指向 `.venv/Scripts/pip.exe`，不会污染系统 Python。

---

## 4. 应用补丁 + 起 HTTP 桥（窗口 A 一直开着别关）

**⚠️ 必须先 apply 补丁，再起桥**（否则 bm25 搜索后端不生效，桥会用原版 pyserini 直接崩）：

**窗口 A（venv 已 activate）**：
```bash
cd pecs-multi-agent
bash webshop_patches/apply.sh          # 把 3 个补丁覆盖进 webshop/web_agent_site/...
cd webshop
source .venv/Scripts/activate
python webshop_server.py --port 8000 --num-products 1000
```
> 看到 `Running on http://0.0.0.0:8000` 即成功。（即使打印 `WARN: env init failed`，服务也起了，请求时才真正建环境。）

**窗口 B（另开 Git Bash）验证桥活着**：
```bash
curl http://localhost:8000/health
# 期望：{"status":"ok"}
```

---

## 5. PECS 跑 WebShop 评测（窗口 C）

**先 mock 验证链路（不设变量 → 走本地 8 商品 mock，秒回，确认代码链通）**：
```bash
cd pecs-multi-agent
python run_webshop.py
```
> 能正常打印三组对比即链路 OK。再接真环境。

**接真实环境跑完整评测**：
```bash
cd pecs-multi-agent
export WEBSHOP_SERVER_URL=http://localhost:8000
# .env 已含 LLM_API_KEY/BASE_URL/MODEL（DeepSeek），若未生效再 export
python run_webshop.py
```
> 日志出现 `WebShop 交互完成（共 N 步, 奖励=X.XXX）` 即真实环境。结果存 `results/webshop_run.json`。
> 三组对比：PECS 完整规则层 / ReAct-light / ReAct 纯 LLM，证明 +pp 来自"打破 search 循环"。

**把真实结果提交（建议）**：
```bash
cd pecs-multi-agent
git add results/webshop_run.json
git commit -m "eval: 更新 WebShop 真实环境评测结果"
git push origin main
```

---

## 6. 吊销泄漏的 lingshucode Key（任务 2）

> 你 GitHub 历史里清掉的明文 Key 是 **lingshucode 网关**的。仓库清了但 Key 本身仍有效，必须去控制台吊销。
> 你 `.env` 现在的 `sk-e67c...` 是 **DeepSeek Key**，没泄漏，**保留不动**。

1. 打开 lingshucode 网关控制台（你当时申请 Key 的地方）。
2. 找到 API Keys / 密钥管理页面。
3. 把泄漏的旧 Key 标记为 **Revoke / 禁用 / 删除**。
4. 确认该 Key 调用接口返回 401/403（吊销成功）。
> 若找不到控制台入口，直接换发新 Key 并废弃旧的——核心让旧 Key 失效。

---

## 7. 刷新 requirements-lock.txt（任务 3）

```bash
cd pecs-multi-agent
# 在你跑 PECS 主程序的 Python 环境里（非 webshop venv）
pip freeze > requirements-lock.txt
git add requirements-lock.txt
git commit -m "chore: 刷新 requirements-lock.txt 精确复现依赖"
git push origin main
```
> README 顶部已引导"精确复现用 `pip install -r requirements-lock.txt`"。

---

## 8. 故障速查表

| 现象 | 原因 | 快修 |
|---|---|---|
| `git clone` Connection was reset | 网络抖动 | 等 10 秒重试 |
| `conda` 命令不存在 | 本机无 conda | 用第 3 节 venv 方案 |
| `python -m spacy download` 慢 | CDN 偶发 | 多试两次，或设 `HF_ENDPOINT=https://hf-mirror.com` 后重试 |
| 桥起不来 / ImportError | 依赖没装全 | 回第 3 节重装，看缺哪个包 |
| `curl localhost:8000` 无响应 | 桥没起 | 回第 4 节确认进程在跑 |
| PECS 报"连接被拒绝" | 端口/变量错 | 确认 `WEBSHOP_SERVER_URL=http://localhost:8000`（不是 :3000） |
| 日志一直 mock 不接真环境 | 忘了设变量 | 窗口 C `echo $WEBSHOP_SERVER_URL` 应为 `http://localhost:8000` |

---

## 9. 完成后自检

- [ ] `results/webshop_run.json` 存在且含 `mode: "real"`
- [ ] 泄漏的 lingshucode Key 已吊销（控制台确认失效）
- [ ] `requirements-lock.txt` 已更新并推送
- [ ] GitHub main 同步最新
