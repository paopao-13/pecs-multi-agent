"""wrapper 熔断/幂等的跨进程共享存储（可选启用）。

与 tools/rate_store.py 的关系：
  rate_store.StateStore 只有限流令牌桶与幂等缓存两张表；本模块扩展它，
  增加熔断状态表 breaker_state。继承而非修改 StateStore——rate_store 是
  已验证通过的逻辑（4 进程限流共享实测），不做任何改动。

为什么熔断状态必须外置：
  tools/wrapper.py 的 _breaker_state 是进程内 dict。scripts/api.py 以
  多 worker 运行时各 worker 计数互不可见，"连续失败 N 次熔断"的阈值在
  N 个 worker 下实际要 N*N 次失败才会全部熔断——形同虚设。与限流同理，
  落 SQLite（标准库、多进程可见、写锁串行化）。

时间基准（重要）：
  breaker_state 用 time.time()（墙钟）而非 wrapper.py 进程内版本的
  time.monotonic()。原因：monotonic 的起点是每进程独立的，跨进程比较
  opened_at 无意义；墙钟在"熔断冷却 60s"这种秒级尺度上，NTP 校时
  跳变的风险可忽略（且冷却判断失效的后果只是提前/延后放行探测，安全）。

失败策略（与 rate_store 一致）：
  DB 不可用时熔断按"未熔断"处理（fail-open）——熔断器自己不能成为
  把整个服务打入不可用的新故障源；幂等读失败按未命中处理（重新执行，
  语义安全），写失败不缓存。
"""
import sqlite3
import time
from typing import Optional, Tuple

from tools.rate_store import StateStore, _BUSY_TIMEOUT_S


class SharedStateStore(StateStore):
    """在 StateStore 基础上增加熔断状态表。

    幂等缓存直接复用父类的 idem_cache 表与 cache_get/cache_put 方法——
    与限流共享同一个 SQLite 文件，一个 PEC_SHARED_STATE_DB 打通全部外置状态。
    """

    def _init_schema(self) -> None:
        super()._init_schema()  # 先建父类的 token_bucket / idem_cache
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS breaker_state (
                       action    TEXT PRIMARY KEY,
                       failures  INTEGER NOT NULL,
                       opened_ts REAL
                   )"""
            )

    # ---- 熔断状态（跨进程） ----

    def breaker_snapshot(self, action: str) -> Tuple[int, Optional[float]]:
        """读取 (连续失败次数, 打开时刻)。读失败按未熔断处理（fail-open）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT failures, opened_ts FROM breaker_state WHERE action = ?",
                    (action,),
                ).fetchone()
            if row:
                return int(row[0]), row[1]
        except sqlite3.Error:
            return 0, None
        return 0, None

    def breaker_record_failure(self, action: str, threshold: int) -> bool:
        """连续失败 +1；达阈值时记录打开时刻。返回是否（新近）达到熔断阈值。

        原子性靠 BEGIN IMMEDIATE：读-改-写必须在同一写事务内，否则多进程
        并发失败时计数互相覆盖，阈值永远凑不满（进程内 dict 病的 SQLite 版）。
        """
        now = time.time()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT failures FROM breaker_state WHERE action = ?", (action,)
                ).fetchone()
                failures = (int(row[0]) + 1) if row else 1
                opened_ts = now if failures >= threshold else None
                conn.execute(
                    "INSERT INTO breaker_state(action, failures, opened_ts) VALUES (?, ?, ?) "
                    "ON CONFLICT(action) DO UPDATE SET failures = ?, opened_ts = ?",
                    (action, failures, opened_ts, failures, opened_ts),
                )
                conn.commit()
                return failures >= threshold
            finally:
                conn.close()
        except sqlite3.Error:
            return False  # 记不上失败 → 熔断可能晚触发，服务仍可用

    def breaker_record_success(self, action: str) -> None:
        """一次成功即清除该工具的失败计数（半开探测成功 → 完全恢复）。"""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM breaker_state WHERE action = ?", (action,))
        except sqlite3.Error:
            pass

    def breaker_reset_all(self) -> None:
        """清空全部熔断状态（测试 / 运维手动复位）。只动本表，不碰限流与幂等。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM breaker_state")

    # ---- 幂等缓存（复用父类表，统一 TTL 语义） ----

    def idem_lookup(self, key: str, ttl_s: float) -> Optional[str]:
        """带 TTL 的幂等查询；过期视为未命中（不主动删，等下次写入覆盖）。"""
        return self.cache_get(key, ttl_s)

    def idem_store(self, key: str, result: str) -> None:
        self.cache_put(key, result)

    def idem_clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM idem_cache")
