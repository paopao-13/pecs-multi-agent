# 贡献指南

感谢您对本项目提交代码的兴趣，请遵循以下规范。

## 环境搭建

- Python 3.10+（使用了 `match/case` 语法和 `TypedDict` 类型）
- 建议使用 venv 虚拟环境

```bash
git clone https://github.com/paopao-13/pecs-multi-agent.git
cd pecs-multi-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
pip install -e ".[dev]"         # 安装 dev 依赖（pytest/black/isort/flake8）
```

## 代码风格

强制使用 `black` + `isort` 格式化，提交前请执行：

```bash
black .
isort .
```

仅检查不修改：

```bash
black --check .
isort --check-only .
```

flake8 仅检查致命语法错误（E9/F63/F7/F82），不校验代码风格：

```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

配置在 `pyproject.toml` 中，line-length = 120。

## Commit 规范

仅接受以下四种前缀，不符合格式的提交将被退回：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat:` | 新功能 | `feat: 为 Planner 添加启发式兜底` |
| `fix:` | 修复缺陷 | `fix: executor 变量名拼写错误` |
| `docs:` | 文档更新 | `docs: 更新 README 安装步骤` |
| `chore:` | 杂项（依赖更新、配置调整等） | `chore: 升级 langgraph 到 0.2.5` |

## PR 自检清单

提交 PR 前，请确认以下项均通过：

- [ ] 本地执行 `pytest tests/ -v` 无报错
- [ ] `black --check .` 和 `isort --check-only .` 通过
- [ ] 新增工具必须通过 `tools/python_repl.py` 的 AST 静态安全检查
- [ ] 修改配置项（`config.py`）时同步更新 `.env.example`
- [ ] commit message 符合上述规范

## 测试说明

执行单元测试：

```bash
pytest tests/ -v
```

执行 GAIA Level 1 评测（会调用 API，消耗较多 Token，请注意成本）：

```bash
python -m benchmarks.gaia_eval --level 1 --tasks 10
```

> 注意：全量执行 28 题 GAIA Level 1 会消耗大量 API Token，建议先用 `--tasks 3` 验证效果，确认无误后再扩大范围。

如果未配置 `DEEPSEEK_API_KEY`，依赖 API 的测试用例将自动跳过，不会报错。
