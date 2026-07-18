# 灵梳 Code（lingshucode.com）LLM 网关接入教程

> 适用场景：你拿到了 lingshucode.com 的 API Key，想把它接入 PECS 项目当作 LLM 提供方。
> 本文基于实测（已验证网关连通、模型可调、返回标准 OpenAI 格式）。

---

## 0. 先说清楚它到底是什么（非常重要，避免走弯路）

**lingshucode.com 是一个「LLM API 网关」（New API），不是云服务器 / 算力平台。**

- ✅ 它干的事：你替换 `base_url`，就能通过它调用各种大模型（GLM、DeepSeek、Kimi、MiniMax 等），接口与 OpenAI 完全兼容。
- ❌ 它**不能**部署 WebShop 的 Docker 容器、不能当云主机用。
- 你给的那个长字符串（48 位）是**这个网关的 API Key**，不是服务器密码。

**所以这两个东西的用途是**：拿这个 Key 去调 LLM 模型，用来跑 PECS 框架本身（替代/补充你之前直接用的 DeepSeek key）。**WebShop 真实环境的「云端部署」还需要另租一台真正的云服务器（或本机装 Docker）**——这点后面第 4 节讲。

---

## 1. 它提供什么

- **Base URL**：`https://www.lingshucode.com/v1`
- **鉴权**：`Authorization: Bearer <你的_API_KEY>`
- **格式**：标准 OpenAI `/v1/chat/completions`（你的项目用 `langchain_openai.ChatOpenAI`，直接兼容）
- **实测可用模型**（共 9 个，均为国产主流模型）：
  - `glm-5.2` / `glm-5.2-vision`
  - `deepseek-v4-pro` / `deepseek-v4-pro-vision` / `deepseek-v4-flash` / `deepseek-v4-flash-vision`
  - `kimi-k2.7` / `kimi-k2.6`
  - `minimax-m3`

> ⚠️ 它**没有** `gpt-3.5-turbo` / `claude` 等，调用这些会报 `model_not_found`。只用上面 9 个。

---

## 2. 在 PECS 项目里怎么用（改 3 行配置即可）

你的项目用 `ChatOpenAI` 接模型，配置来自 `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。直接改这三行：

```ini
# .env
LLM_API_KEY=<你的_API_KEY>
LLM_BASE_URL=https://www.lingshucode.com/v1
LLM_MODEL=deepseek-v4-flash
```

> **推荐模型**：`deepseek-v4-flash` —— 实测 `content` 正常返回答案、速度快、便宜。
> 想要更强推理用 `glm-5.2`（见第 3 节注意事项）。

改完直接跑原来的评测脚本即可，无需改任何代码：

```bash
python run_resumable.py gaia_l1_029
```

---

## 3. 注意事项（实测血泪，必看）

### 3.1 这些模型大多是「思考模型」
`glm-5.2`、`deepseek-v4-flash`、`kimi-k2.6` 都会先输出 `reasoning_content`（思考过程），再输出 `content`（正式答案）。
- ✅ 标准 `ChatOpenAI` 只读取 `content`，所以**最终答案能正常拿到**，不用担心。
- ⚠️ **但思考会消耗额外 token**，成本比非思考模型高。跑大规模评测前留意额度。

### 3.2 `glm-5.2` 要给足 max_tokens
实测 `glm-5.2` 在 `max_tokens` 太小（如 20）时，`content` 会被切断成 `None`（思考没结束就被截断）。
- 项目 `config.py` 的 `DEFAULT_TOKEN_BUDGET` 通常足够大（几千），正常调用没问题。
- 若你单独调小 `max_tokens` 测试，记得给够（≥200）。

### 3.3 监控额度
网关按 token 计费。可在 lingshucode.com 控制台查看余额/用量。跑 GAIA 全量前先小批量试跑几题确认成本和正确性。

---

## 4. WebShop 真实环境的云端部署，这个网关帮不了

你之前说「切云端路线部署 WebShop」，但 lingshucode 是 LLM 网关，**提供不了能跑 Docker 云主机**。真实 WebShop 需要：

| 方案 | 说明 | 参考文档 |
|---|---|---|
| 本机 Docker Desktop | 你本机 Win10/11 + 开虚拟化，最省心 | `docs/docker_install_windows.md` + `scripts/run_real_webshop.bat` |
| 租用云服务器 | 腾讯云/阿里云轻量应用服务器（¥30-60/月），上面装 Docker 起 WebShop，本机 PECS 远程连 | 见下一节 |

> 部署好 WebShop 容器后，PECS 通过环境变量 `WEBSHOP_SERVER_URL=http://<地址>:3000` 自动从「本地玩具」切到「真实交互」（代码脚手架已写好：`tools/webshop_env.py` + `tools/webshop.py` 的 `webshop_interact`）。

---

## 5. 下一步建议

1. **现在就能做**：按第 2 节把 `.env` 改成 lingshucode，跑 1-2 道题验证模型效果（比 DeepSeek 直连可能更稳/更便宜）。
2. **WebShop 真达标**：另租一台云服务器（或本机装 Docker），照 `docs/webshop_real_env_setup.md` + `docker-compose.yml` 部署，再用 `WEBSHOP_SERVER_URL` 接真实环境。

需要我帮你做哪一步？比如：
- 把 `.env` 直接改成 lingshucode（你确认用哪个模型）并跑回归验证；
- 或写一份「云服务器（腾讯云/阿里云）租赁 + Docker 部署 WebShop」的傻瓜教程。
