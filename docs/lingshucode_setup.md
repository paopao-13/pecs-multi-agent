# LLM 网关接入教程

> 适用场景：项目需要接入一个兼容 OpenAI 格式的 LLM API 网关作为模型提供方。
> 本文基于实测（已验证网关连通、模型可调、返回标准 OpenAI 格式）。

---

## 0. 它是什么

一个 **LLM API 网关（New API 类）**，提供与 OpenAI 完全兼容的接口：

- ✅ 替换 `base_url` 即可调用多种大模型（GLM、DeepSeek、Kimi、MiniMax 等），接口与 OpenAI 一致。
- ❌ 它**不能**部署 WebShop 的 Docker 容器、不能当云主机用——仅用于模型推理。
- 提供的长字符串是**该网关的 API Key**，不是服务器密码。

**用途**：拿该 Key 调 LLM 跑 PECS 框架本身。WebShop 真实环境的部署需另租云服务器或本机装 Docker，不在本教程范围。

---

## 1. 它提供什么

- **Base URL**：`<你的网关 Base URL，形如 https://gateway.example.com/v1>`
- **鉴权**：`Authorization: Bearer <你的_API_KEY>`
- **格式**：标准 OpenAI `/v1/chat/completions`（项目用 `langchain_openai.ChatOpenAI`，直接兼容）

> 调用前确认该网关实际支持的模型列表；仅填写其支持的具体模型名，调用未注册模型会报 `model_not_found`。

---

## 2. 在 PECS 项目里怎么用（改 3 行配置）

项目用 `ChatOpenAI` 接模型，配置来自 `.env` 的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。直接改这三行：

```ini
# .env
LLM_API_KEY=<你的_API_KEY>
LLM_BASE_URL=<你的网关 Base URL>
LLM_MODEL=<网关支持的模型名>
```

改完直接跑原有评测脚本即可，无需改代码：

```bash
python run_resumable.py gaia_l1_029
```

---

## 3. 注意事项（实测）

### 3.1 思考模型会先输出推理过程
部分模型先输出 `reasoning_content`（思考过程），再输出 `content`（正式答案）。
- ✅ 标准 `ChatOpenAI` 只读取 `content`，最终答案能正常拿到。
- ⚠️ 思考会消耗额外 token，大规模评测前留意额度。

### 3.2 给足 max_tokens
实测部分模型在 `max_tokens` 过小时，`content` 会被切断成 `None`（思考未结束即被截断）。项目 `config.py` 的 `DEFAULT_TOKEN_BUDGET` 通常足够大；单独调小测试时记得给够（≥200）。

### 3.3 监控额度
网关按 token 计费，可在控制台查看余额/用量。跑大规模评测前先小批量试跑确认成本与正确性。

---

## 4. WebShop 真实环境部署

LLM 网关提供不了能跑 Docker 的云主机。真实 WebShop 需要：

| 方案 | 说明 | 参考文档 |
|---|---|---|
| 本机 Docker Desktop | 本机开启虚拟化，最省心 | `docs/docker_install_windows.md` + `scripts/run_real_webshop.bat` |
| 租用云服务器 | 轻量应用服务器装 Docker 起 WebShop，本机 PECS 远程连 | `docs/webshop_real_env_setup.md` + `docker-compose.yml` |

部署好 WebShop 容器后，PECS 通过环境变量 `WEBSHOP_SERVER_URL=http://<地址>:3000` 自动从「本地玩具」切到「真实交互」（脚手架：`tools/webshop_env.py` + `tools/webshop.py` 的 `webshop_interact`）。
