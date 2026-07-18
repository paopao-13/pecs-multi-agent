# WebShop 线路 A 极简上手（conda 原生，不用 Docker）

> 适用：你本机 Windows，想试真实 WebShop 环境。失败随时退回（见末尾「换回去」）。
> 前置：已装 Git、JDK 8/11、conda；先用 `python scripts/webshop_preflight.py` 自检。
>
> ⚠️ 重要心态：线路 A 只是把**真实考场**搭起来，让你能诚实测量 WebShop。
> 「考场搭好」≠「PECS 达标 +18pp」。达标与否要等 PECS 真上去跑过才知道。

---

## 1. 克隆 + 建独立环境

```bash
git clone https://github.com/princeton-nlp/webshop.git webshop
cd webshop
conda create -n webshop python=3.8.13 -y
conda activate webshop
```

## 2. 装依赖 + 数据 + 索引（约 10-20 分钟，需联网）

```bash
./setup.sh -d small      # small=1000 商品；-d all=全量（很慢、很吃盘）
```

- 若提示命令不存在 / 参数不对：不同 fork 的 data 下载命令略有差异，按仓库 README 的 data 步骤来。
- 卡在 `gdown` 下数据（网络/cookie）：手动从 Google Drive 下 `items_ins_v2.json` / `items_shuffle.json` 放 `data/` 后重跑。

## 3. 起 HTTP 桥（PECS 连这个，端口 :8000）

```bash
pip install flask
cp /你的PECS项目路径/tools/webshop_server.py ./
python webshop_server.py --port 8000 --num-products 1000
```

另开终端验证桥已就绪：

```bash
curl http://localhost:8000/health     # 应返回 {"status":"ok"}
```

> 启动日志若报 `env init failed`，是 WebShop 版本构造参数差异——脚本已内置多种 id/签名兜底；
> 仍不行就把报错贴给我，我调 `tools/webshop_server.py` 的 `_make_env`。

## 4. PECS 接真实环境

在 **PECS 项目目录**另开终端（激活你的 PECS 环境）：

```bash
set WEBSHOP_SERVER_URL=http://localhost:8000
python run_resumable.py webshop_001
```

不设 `WEBSHOP_SERVER_URL` 时 PECS 自动用本地 8 商品 mock；设了就走真实环境。**切换只靠这一个变量。**

---

## 换回去（"不行就退回"）

只需**清空该环境变量**，PECS 立刻回本地 mock，无需停 WebShop 服务、不改任何代码：

```bash
set WEBSHOP_SERVER_URL=
python run_resumable.py webshop_001    # 走本地 mock，不再依赖 WebShop 服务
```

想彻底停掉真实环境：关掉第 3 步的 `python webshop_server.py` 进程即可（conda 环境留着下次直接复用）。

---

## 已知最可能卡的点

| 现象 | 原因 | 处理 |
|---|---|---|
| `pyserini` 初始化失败 / JDK 相关报错 | Java 没装或版本不兼容 | 先过 preflight；装 JDK 8/11 并加入 PATH |
| `env init failed: ... gym.make ...` | WebShop 版本构造参数差异 | 看启动日志，贴报错给我调 `_make_env` |
| 连不上 `localhost:8000` | 桥没起 / 端口错 | 确认第 3 步进程在跑；PECS 连的是 **:8000**，不是官方 :3000 |
| `gdown` 下数据失败 | 网络/cookie | 手动从 Google Drive 下数据放 `data/` |
| 磁盘爆满 | small 也要 ~12GB | 换盘或清理 |

---

## 验证"真环境"确实生效

`webshop_001` 跑起来后，观察日志里是否出现 `WebShop 交互完成（共 N 步, 奖励=...）`
（来自 `tools/webshop.py` 的 `webshop_interact`）——有这个就说明走的是真实环境，不是 mock。
奖励分 ≥ 阈值（默认 0.5，严格口径改 `benchmarks/webshop_eval.py` 的 `REAL_REWARD_THRESHOLD=1.0`）算成功。
