# 贡献指南

感谢你有兴趣给这个项目提交代码，下面是几条规矩，照着来就行。

## 环境搭建

- Python 3.10+（低了跑不起来，用了 `match/case` 和 `TypedDict`）
- 建议用 venv 虚拟环境

```bash
git clone https://github.com/paopao-13/langgraph-multi-agent.git
cd langgraph-multi-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
pip install -e ".[dev]"         # 安装 dev 依赖（pytest/black/isort/flake8）
```

## 代码风格

强制用 `black` + `isort`，没得商量。提交前自己跑一遍：

```bash
black .
isort .
```

只检查不修改：

```bash
black --check .
isort --check-only .
```

flake8 只查致命语法错误（E9/F63/F7/F82），不纠结代码风格：

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

配置在 `pyproject.toml` 里，line-length = 120。

## Commit 规范

只认以下四种前缀，其他格式一律打回：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat:` | 新功能 | `feat: 给 Planner 加了启发式兜底` |
| `fix:` | 修 bug | `fix: executor 变量名拼错了` |
| `docs:` | 改文档 | `docs: 更新 README 安装步骤` |
| `chore:` | 杂项（依赖更新、配置调整等） | `chore: 升级 langgraph 到 0.2.5` |

## PR 自检清单

提 PR 之前，确认以下都过了：

- [ ] 本地跑通 `pytest tests/ -v`，没有报错
- [ ] `black --check .` 和 `isort --check-only .` 通过
- [ ] 新增的工具必须过 `tools/python_repl.py` 里的 AST 静态安全检查（不能被沙箱拦截）
- [ ] 如果改了配置项（`config.py`），同步更新 `.env.example`
- [ ] commit message 符合上面的规范

## 测试说明

跑单元测试：

```bash
pytest tests/ -v
```

跑 GAIA Level 1 评测（**会调 API，烧钱，注意**）：

```bash
python run_gaia.py --level 1 --tasks 10
```

> 警告：全量跑 28 题 GAIA Level 1 会消耗大量 API Token，先用 `--tasks 3` 跑几题看看效果，确认没问题再扩大范围。别问我怎么知道的。

如果没配 `OPENAI_API_KEY`，需要 API Key 的测试用例会自动 skip，不会报错。
