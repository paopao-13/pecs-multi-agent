"""API Key 鉴权与租户归属校验。

设计要点（为什么这么做）：

1. **默认关闭**：未设置 PECS_API_KEYS 时鉴权完全不生效（tenant 返回 "-"），
   保证本地开发、既有评测与全部既有测试的行为逐字不变。安全能力必须是
   "显式开启"的，否则会在 CI/离线环境制造大面积假失败。

2. **凭据只从环境变量读取**：格式 "key1:tenant_a,key2:tenant_b"。
   禁止硬编码、不写进日志、不进版本库（.env 已被 .gitignore 忽略）。

3. **越权访问返回 404 而非 403**：403 会确认"资源存在但你不该看"，
   等于泄露了 thread_id 的有效性；404 不泄露存在性。

4. **thread 归属靠命名约定** "<tenant>-<uuid>"：避免引入额外存储与查询，
   是这个项目当前阶段最省的做法（代价：客户端需遵守约定，已在 README 说明）。

已知边界（面试要能讲出来）：
  - 常量时间比较用的是 dict 查找，单机小表够用；密钥量级上千时应改为
    哈希索引 + hmac.compare_digest，避免按表长泄漏时序信息。
  - 这是"传输层鉴权"，不含配额、不含审计、不含密钥轮换；三者见后续规划。
"""
import os

from fastapi import HTTPException, Request

# 凭据表：{"api_key": "tenant_id"}
_KEY_TABLE: dict = {}
for _pair in os.getenv("PECS_API_KEYS", "").split(","):
    if ":" in _pair:
        _key, _tenant = _pair.split(":", 1)
        _key = _key.strip()
        _tenant = _tenant.strip()
        if _key and _tenant:
            _KEY_TABLE[_key] = _tenant

# 未配置即关闭鉴权（本地开发 / CI / 评测不受影响）
AUTH_ENABLED = bool(_KEY_TABLE)

ANONYMOUS_TENANT = "-"


def require_api_key(request: Request) -> str:
    """FastAPI 依赖：校验 X-API-Key，返回 tenant_id。

    未启用鉴权时返回 ANONYMOUS_TENANT（"-"），调用方无需分支处理。
    """
    if not AUTH_ENABLED:
        return ANONYMOUS_TENANT

    tenant = _KEY_TABLE.get(request.headers.get("X-API-Key", ""))
    if tenant is None:
        raise HTTPException(status_code=401, detail="缺失或无效的 X-API-Key")
    return tenant


def assert_thread_owner(thread_id: str, tenant: str) -> None:
    """校验 thread_id 归属当前租户；越权返回 404（不泄露资源是否存在）。

    thread_id 约定为 "<tenant>-<任意后缀>"。未启用鉴权时直接放行。
    """
    if not AUTH_ENABLED:
        return
    if not thread_id.startswith(f"{tenant}-"):
        raise HTTPException(status_code=404, detail="未找到该任务")


def require_metrics_key(request: Request) -> str:
    """FastAPI 依赖：/metrics 与 /metrics/prom 的鉴权。

    取舍：指标是系统级数据（请求计数、P95、token 总量），不含租户隔离的
    业务内容，因此允许**任何有效 Key** 拉取——Prometheus 专用 Key 与业务
    Key 均可，租户排查自己调用时也能看。只挡匿名访问（调用量属于运营数据，
    能反推业务体量）。/health 系列保持无鉴权（K8s probe 不注入 Key）。
    """
    if not AUTH_ENABLED:
        return ANONYMOUS_TENANT
    return require_api_key(request)
