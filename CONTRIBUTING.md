# 贡献指南

> 这项目目前是我一个人瞎折腾的，如果你也想玩一下，欢迎。

## 怎么贡献

1. Fork 仓库
2. 新建分支：`git checkout -b feat/你的功能名` 或 `fix/bug描述`
3. 改代码，写测试（如果有的话）
4. 提交 PR

## 提交规范

用 Conventional Commits，但不用太死板：

- `feat:` 新功能
- `fix:` 修 bug
- `refactor:` 重构（没改功能）
- `docs:` 改文档
- `test:` 加测试

示例：
```
feat: 给 Planner 加个启发式兜底
fix: 草，刚才的变量名写错了
refactor: 把 budget 计算抽出来，之前的代码太丑了
```

## 注意

- 改核心逻辑之前先在 `benchmarks/` 跑一下评测，确保没把准确率搞崩
- 改 `python_repl.py` 要谨慎，AST 检查别整出漏洞
- 目前没 CI，所以 PR 里最好说明你测了哪些东西
