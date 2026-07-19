# 给 AI 助手执行的提示词（复制下方代码块内容）

> 把下面 ```` ```text ```` 块里的全部内容复制给另一个 AI（具备本机终端执行能力的助手），
> 它会照着在用户的 Windows + Git Bash 环境里逐步执行，并在每步汇报结果/报错。

---

```text
你是一个具备本机终端执行能力的 AI 助手。请在用户的 Windows 机器上，使用 Git Bash 环境，严格按照以下步骤帮我完成 PECS 项目的本机执行任务。仓库根目录是 D:/简历/pecs-multi-agent（Git Bash 中写作 /d/简历/pecs-multi-agent）。

【环境约束】
- 只使用 Git Bash（MSYS2）执行命令，不要用 CMD 或 PowerShell 语法（如不要用 `&`、不要用 `dir`，用 `ls`/`cd`）。
- 用户本机【没有 conda】，WebShop 相关步骤必须用系统 Python 3.11 建 venv：路径 "C:/Users/jx/AppData/Local/Programs/Python/Python311/python.exe"
- 用户本机 .env 已含有效 DeepSeek Key（LLM_API_KEY / LLM_BASE_URL=https://api.deepseek.com/v1 / LLM_MODEL=deepseek-chat），WebShop 数据文件已就位（webshop/data/ 下三个 json），无需重新下载数据。
- 每条命令执行后，请汇报输出；若报错，先尝试本文档故障速查表的快修，仍不行就停下来把完整报错贴给我，不要盲目重试。

【任务总览】
任务1：跑通 WebShop 真实环境评测（产出 results/webshop_run.json, mode:"real"）
任务2：提示用户去 lingshucode 控制台吊销泄漏的旧 Key（这是人工操作，你只需给出明确指引并确认用户完成，不代执行）
任务3：刷新 requirements-lock.txt 并推送

【执行步骤】

步骤 1（窗口A）：克隆 WebShop 源码（如已存在 webshop/ 目录则跳过）
  cd /d/简历/pecs-multi-agent
  ls webshop/ >/dev/null 2>&1 && echo "WEBSOP_EXISTS_SKIP" || git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
  成功标志：出现 webshop/ 目录。

步骤 2（窗口A）：建 venv + 装依赖
  cd /d/简历/pecs-multi-agent/webshop
  "C:/Users/jx/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
  source .venv/Scripts/activate
  python --version   # 应显示 3.11.x
  pip install gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm rank_bm25 thefuzz scikit_learn spacy
  python -m spacy download en_core_web_sm
  pip install transformers==4.19.2
  成功标志：spacy 模型下载完、无 ImportError。

步骤 3（窗口A，一直开着）：应用补丁 + 起桥
  cd /d/简历/pecs-multi-agent
  bash webshop_patches/apply.sh     # 必须执行，否则 bm25 不生效
  cd webshop
  source .venv/Scripts/activate
  python webshop_server.py --port 8000 --num-products 1000
  成功标志：打印 "Running on http://0.0.0.0:8000"。
  注意：此窗口不要关闭。另开一个新 Git Bash 窗口执行步骤4验证。

步骤 4（窗口B）：验证桥活着
  curl http://localhost:8000/health
  期望输出：{"status":"ok"}。若不一致，回报错误。

步骤 5（窗口C，PECS 主环境）：先 mock 验证链路
  cd /d/简历/pecs-multi-agent
  python run_webshop.py
  成功标志：能正常打印三组对比（PECS / ReAct-light / ReAct），不报错。这步不设 WEBSHOP_SERVER_URL，走本地 8 商品 mock。

步骤 6（窗口C）：接真实环境跑完整评测
  cd /d/简历/pecs-multi-agent
  export WEBSHOP_SERVER_URL=http://localhost:8000
  python run_webshop.py
  成功标志：日志出现 "WebShop 交互完成（共 N 步, 奖励=X.XXX）"，且生成 results/webshop_run.json（mode:"real"）。
  注意：此步会多次调用 DeepSeek API，耗时较长（可能 10~30 分钟），请耐心等待不要中断。

步骤 7（窗口C）：提交真实结果
  cd /d/简历/pecs-multi-agent
  git add results/webshop_run.json
  git commit -m "eval: 更新 WebShop 真实环境评测结果"
  git push origin main
  成功标志：push 成功（或提示 up-to-date）。

步骤 8（任务2，人工指引）：吊销 lingshucode 泄漏 Key
  不要代执行，向用户输出以下指引并等待确认：
  "请打开 lingshucode 网关控制台（你当初申请 Key 的地方），找到 API Keys 页面，把历史泄漏的那个 Key 标记为 Revoke/禁用/删除，并确认调用返回 401/403。注意：你 .env 里的 DeepSeek Key (sk-e67c...) 没泄漏，不要动它。完成后告诉我 '已吊销'。"
  收到用户确认"已吊销"后再继续。

步骤 9（任务3）：刷新 lock 文件
  cd /d/简历/pecs-multi-agent
  pip freeze > requirements-lock.txt
  git add requirements-lock.txt
  git commit -m "chore: 刷新 requirements-lock.txt 精确复现依赖"
  git push origin main
  成功标志：push 成功。

【故障速查】
- git clone Connection was reset → 等10秒重试
- conda 不存在 → 已改用 venv，见步骤2
- spacy download 慢 → 多试两次，或 export HF_ENDPOINT=https://hf-mirror.com 后重试
- 桥 ImportError → 回步骤2重装依赖
- curl localhost:8000 无响应 → 桥没起，回步骤3
- PECS "连接被拒绝" → 确认 WEBSHOP_SERVER_URL=http://localhost:8000（不是:3000）
- 日志一直 mock → 窗口C 执行 echo $WEBSHOP_SERVER_URL 应为 http://localhost:8000

【完成后】汇报：webshop_run.json 是否生成(mode:real)、Key 是否确认吊销、lock 是否推送。并列出任何未解决的问题。
```
