# 版本管理规范

> pecs-multi-agent 采用语义化版本控制（Semantic Versioning），通过 Git Tag 与 GitHub Release 管理版本。
> 各版本的**详细变更记录以 [`CHANGELOG.md`](../../CHANGELOG.md) 为准**，本文只描述规则与流程。

## 1. 版本号规则

遵循 [SemVer 2.0.0](https://semver.org/lang/zh-CN/) 规范：

```
MAJOR.MINOR.PATCH
```

| 版本段 | 递增条件 | 示例 |
|--------|----------|------|
| MAJOR | 不兼容的 API 修改 | 0.6.1 → 1.0.0 |
| MINOR | 向下兼容的功能新增 | 0.6.1 → 0.7.0 |
| PATCH | 向下兼容的缺陷修复 | 0.6.1 → 0.6.2 |

`0.x.y` 阶段视为快速迭代期：MINOR 可包含较大范围的功能与结构调整，直到对外契约稳定后发布 `1.0.0`。

## 2. 版本历程

| 版本 | 日期 | 主要内容 |
|------|------|----------|
| v0.1.0 | 2026-07-09 | 四角色（Planner/Executor/Critic/Synthesizer）+ LangGraph + AST 沙箱 + Token 预算三级降级 |
| v0.2.0 | 2026-07-12 | 工程化补充（LICENSE/CONTRIBUTING/CI/pyproject）+ 仓库更名 `pecs-multi-agent` |
| v0.3.0 | 2026-07-19 | 仓库结构整理（脚本入 `scripts/`、阶段文档入 `docs/archive/`）+ 全仓密钥审计与 git 历史重写 |
| v0.4.0 | 2026-07-20 | 生产级 FastAPI 服务；GAIA 官方 L1 validation 53 题接入；WebShop 真实环境评测；async/HOL 修复 |
| v0.4.1 | 2026-07-20 | LLM 调用独立线程池，与默认 executor 解耦 |
| v0.4.2 | 2026-07-20 | `/metrics` 按 endpoint 分桶延迟 + 真实 token 计量 |
| v0.5.0 | 2026-07-20 | 启动自检 + `/metrics/prom` Prometheus 多进程指标 |
| v0.6.0 | 2026-07-20 | 全体限流（令牌桶）+ 故障注入 / 混沌工程 |
| v0.6.1 | 2026-09-10 | 工具层容错（超时/熔断/幂等/权限白名单）+ 成本归因 + 内容管线与回放 + 依赖故障显式化 + 跨平台路径校验 |

**当前版本：v0.6.1**（详见 [`CHANGELOG.md`](../../CHANGELOG.md)）

## 3. Git Tag 管理

```bash
# 创建附注标签（annotated tag）
git tag -a v0.6.1 -m "v0.6.1 - P0 生产化加固（依赖故障显式化 / 工具层容错 / 成本可观测）"

# 推送标签到远程
git push origin v0.6.1

# 查看所有标签 / 某个版本详情
git tag -l
git show v0.6.1
```

标签**只在对应版本的内容已合入 `main` 后**创建，避免出现指向未合并提交的标签。

## 4. GitHub Release 流程

1. 确认待发布内容已全部合入 `main`，且 `main` 的 CI 为绿。
2. 在 `CHANGELOG.md` 中补全该版本条目（`## [x.y.z] - YYYY-MM-DD`）。
3. 创建并推送附注标签（见 §3）。
4. 创建 Release（命令行方式，等价于网页端 Draft a new release）：

   ```bash
   gh release create v0.6.1 \
     --title "v0.6.1 - P0 生产化加固" \
     --notes-file <release-notes.md>   # 内容取自 CHANGELOG 对应章节
   ```

5. 可在网页端补充附件（架构图、指标对比图）后发布。

## 5. CHANGELOG 维护

每次发版前更新仓库根目录的 `CHANGELOG.md`，格式参考 [Keep a Changelog](https://keepachangelog.com/)；
分类词固定使用：`Added` / `Changed` / `Fixed` / `Removed` / `Security` / `Deprecated`。

```markdown
## [0.7.0] - 2026-10-01

### Added
- 新增 XX 能力

### Changed
- XX 行为调整

### Fixed
- 修复 XX 问题
```

## 6. 分支管理（个人项目简化版）

| 分支 | 用途 | 命名规则 |
|------|------|----------|
| `main` | 稳定发布分支（受保护，CI 必须为绿） | 固定 |
| `feat/*` | 功能开发分支 | `feat/p0-production-enhance` |
| `fix/*` | 缺陷修复分支 | `fix/sandbox-timeout` |

**简化策略：** 小改动可直接提交到 `main`；范围较大的迭代开 `feat/*` 分支并经 Pull Request 合并，
由 PR 触发 CI（`.github/workflows/ci.yml` 仅在 `pull_request` 与 `push: main` 时运行）。
