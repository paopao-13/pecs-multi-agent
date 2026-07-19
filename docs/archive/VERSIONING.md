# 版本管理规范

> pecs-multi-agent 采用语义化版本控制（Semantic Versioning），通过 Git Tag 和 GitHub Release 管理版本。

## 1. 版本号规则

遵循 [SemVer 2.0.0](https://semver.org/lang/zh-CN/) 规范：

```
MAJOR.MINOR.PATCH
```

| 版本段 | 递增条件 | 示例 |
|--------|----------|------|
| MAJOR | 不兼容的 API 修改 | 1.0.0 → 2.0.0 |
| MINOR | 向下兼容的功能新增 | 1.0.0 → 1.1.0 |
| PATCH | 向下兼容的缺陷修复 | 1.0.0 → 1.0.1 |

## 2. 当前版本

**v1.0.0** — 首个正式版本

| 阶段 | 日期 | 内容 |
|------|------|------|
| v0.1.0 | 2026-07-03 | 基础架构搭建（PECS四角色 + LangGraph + AST沙箱） |
| v0.2.0 | 2026-07-05 | 评测模块（GAIA + ReAct基线 + 消融实验） |
| v0.3.0 | 2026-07-08 | P0/P1缺陷修复 + 22个工程模块 |
| v1.0.0 | 2026-07-10 | 文档完善 + Demo + 开源规范化 |

## 3. Git Tag 管理

```bash
# 创建版本标签
git tag -a v1.0.0 -m "v1.0.0 - PECS多智能体框架首个正式版"

# 推送标签到远程
git push origin v1.0.0

# 查看所有标签
git tag -l

# 查看某个版本的详情
git show v1.0.0
```

## 4. GitHub Release 流程

1. 确认所有代码已合并到 `main` 分支
2. 创建 Git Tag：`git tag -a v1.x.0 -m "版本说明"`
3. 推送 Tag：`git push origin v1.x.0`
4. 在 GitHub 仓库 → Releases → Draft a new release
5. 选择刚推送的 Tag
6. 填写 Release Notes（参考 CHANGELOG.md）
7. 上传附件（架构图、指标对比图）
8. 发布

## 5. CHANGELOG 维护

每次版本发布前更新 `CHANGELOG.md`，格式参考 [Keep a Changelog](https://keepachangelog.com/)：

```markdown
## [1.1.0] - 2026-07-15

### Added
- 新增多模型切换支持（GPT-4o/Claude）
- 新增 SSE 流式输出

### Changed
- Executor 改为异步执行

### Fixed
- 修复 simple 任务 Synthesizer 遗漏关键信息问题

### Deprecated
- 废弃 config.py 硬编码方式，统一使用 YAML 配置
```

## 6. 分支管理（个人项目简化版）

| 分支 | 用途 | 命名规则 |
|------|------|----------|
| `main` | 稳定发布分支 | 固定 |
| `dev` | 日常开发分支 | 固定 |
| `feature/*` | 功能开发分支 | `feature/async-llm` |
| `fix/*` | 缺陷修复分支 | `fix/sandbox-timeout` |

**个人项目简化策略：** 可直接在 `main` 分支开发，通过 commit message 区分类型（feat/fix/docs）。
