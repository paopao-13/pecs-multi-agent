# 本机执行清单（照抄版）

> 目的：把"WebShop 真实环境实跑 + 吊销泄漏 Key + 刷新 lock 文件"三件事，压缩成一份复制粘贴就能跑的清单。
> 所有命令在 **Git Bash**（不是 CMD）里执行。仓库根目录 = `pecs-multi-agent`。
> 你本机已确认：`.env` 含有效 DeepSeek Key、数据文件已就位、无 conda（用 venv 替代）。

---

## 任务 1：WebShop 真实环境实跑（产出真实 +pp 证据）

### 1-A. 克隆 WebShop 源码（已做过可跳过）
```bash
cd pecs-multi-agent
git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
cd webshop && git rev-parse --short HEAD && echo "CLONE_OK"
```
> 若 `Connection was reset`：等 10 秒重试。

### 1-B. 建 venv + 装依赖
```bash
cd pecs-multi-agent/webshop
"C:/Users/jx/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
source .venv/Scripts/activate
pip install gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm rank_bm25 thefuzz scikit_learn spacy
python -m spacy download en_core_web_sm
pip install transformers==4.19.2
```
> `spacy + en_core_web_sm` 必须装（环境初始化必走）。`transformers` 装不上先跳过，遇 import 报错再加。

### 1-C. 应用补丁 + 起桥（窗口 A 一直开着）
```bash
cd pecs-multi-agent
bash webshop_patches/apply.sh
cd webshop
source .venv/Scripts/activate
python webshop_server.py --port 8000 --num-products 1000
```
> 看到 `Running on http://0.0.0.0:8000` 即成功。另开窗口验证：`curl http://localhost:8000/health` 期望 `{"status":"ok"}`。

### 1-D. 先 mock 验证链路（窗口 C，PECS 环境）
```bash
cd pecs-multi-agent
# 故意不设 WEBSHOP_SERVER_URL → 走本地 8 商品 mock，秒回，确认代码链通
python run_webshop.py
```
> 能正常打印三组对比即链路 OK。再接真环境。

### 1-E. 接真实环境跑完整评测（窗口 C）
```bash
cd pecs-multi-agent
export WEBSHOP_SERVER_URL=http://localhost:8000
# .env 已含 LLM_API_KEY/BASE_URL/MODEL，若未生效再 export
python run_webshop.py
```
> 日志出现 `WebShop 交互完成（共 N 步, 奖励=X.XXX）` 即真实环境。结果存 `results/webshop_run.json`。
> 三组对比：PECS 完整规则层 / ReAct-light / ReAct 纯 LLM，证明 +pp 来自"打破 search 循环"。

### 1-F. 把真实结果提交（可选，但建议）
```bash
cd pecs-multi-agent
git add results/webshop_run.json
git commit -m "eval: 更新 WebShop 真实环境评测结果"
git push origin main
```

---

## 任务 2：吊销泄漏的 lingshucode Key

> 你 GitHub 历史里清掉的那个明文 Key 是 **lingshucode 网关**的，必须去它控制台吊销（仓库清了但 Key 本身仍有效）。
> 你 `.env` 里现在的是 **DeepSeek Key**（`sk-e67c...`），没泄漏，保留不用动。

1. 打开 lingshucode 控制台（原 `lingshucode.com` 网关，你当时申请 Key 的地方）。
2. 找到 API Keys / 密钥管理页面。
3. 把历史记录里那个泄漏的 Key 标记为 **Revoke / 禁用 / 删除**。
4. 确认该 Key 调用接口返回 401/403（吊销成功）。
> 若已不记得控制台入口，直接换一个新 Key 并废弃旧的即可——核心目标是让旧 Key 失效。

---

## 任务 3：刷新 requirements-lock.txt（精确复现用）

```bash
cd pecs-multi-agent
# 在你跑 PECS 主程序的 Python 环境里（非 webshop venv）
pip freeze > requirements-lock.txt
git add requirements-lock.txt
git commit -m "chore: 刷新 requirements-lock.txt 精确复现依赖"
git push origin main
```
> README 顶部已引导"精确复现用 `pip install -r requirements-lock.txt`"。lock 文件应反映你本机实际装的全量依赖。

---

## 完成后自检

- [ ] `results/webshop_run.json` 存在且含 `mode: "real"`
- [ ] 泄漏的 lingshucode Key 已吊销（控制台确认失效）
- [ ] `requirements-lock.txt` 已更新并推送
- [ ] GitHub main 同步最新
