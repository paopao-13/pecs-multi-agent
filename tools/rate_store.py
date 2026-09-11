"""跨进程状态存储 —— 无 Redis 环境下的替代方案（目标落地路径 tools/rate_store.py）

为什么不用 Redis：当前开发环境没有 Redis / PostgreSQL 可用（用户约束），
但"状态必须外置"这件事本身不能妥协 —— 进程内 dict 在多 worker 下必然失效
（scripts/api.py:206 的 _RATE_BUCKETS、tools/wrapper.py:160/212 的熔断与幂等缓存都是这个病）。

取舍：
- SQLite 是**标准库**，零安装、零新依赖，且天然支持多进程可见（写锁串行化）。
- 代价是吞吐：每次限流判断一次写事务。本项目 QPS 由 LLM 时延主导（秒级/请求），
  SQLite 的写锁完全够用；若未来 QPS 上到千级，换 Redis adapter 即可，**接口不变**。
- 这不是"用 SQLite 模拟 Redis"，而是把「状态外置」先做对，后端可替换。

失败策略：DB 不可用（锁超时 / 磁盘满）时 fail-open（放行）并计数告警 ——
限流组件的职责是防止过载，它自己不该成为新的故障源。
"""
import os
import sqlite3
import time

DEFAULT_DB = os.getenv("PEC_STATE_DB", "results/state.sqlite")
_BUSY_TIMEOUT_S = 5.0  # 拿不到写锁最多等 5s，超时即降级，绝不无限阻塞


class StateStore:
    """限流 + 幂等缓存的状态存储。所有方法都带显式超时与降级路径。"""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        _dir = os.path.dirname(db_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_S)
        # WAL 让读不阻塞写，多个 worker 并发时显著减少锁等待
        conn.execute("PRAGMA journal_mode=WAL")
        # 每次写事务落盘；NORMAL 在 WAL 下已足够安全且比 FULL 快得多
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS token_bucket (
                       endpoint TEXT PRIMARY KEY,
                       tokens   REAL NOT NULL,
                       last_ts  REAL NOT NULL
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS idem_cache (
                       idem_key TEXT PRIMARY KEY,
                       result   TEXT NOT NULL,
                       created_ts REAL NOT NULL
                   )"""
            )

    def consume(self, endpoint: str, rps: float, burst: float) -> bool:
        """令牌桶：允许消费返回 True，超限返回 False。

        原子性靠 BEGIN IMMEDIATE 立即取写锁 —— 读-改-写三步必须在一个事务里，
        否则多进程下会同时读到旧值并各自放行（这就是进程内 dict 的错误放大版）。
        """
        now = time.monotonic()
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT tokens, last_ts FROM token_bucket WHERE endpoint = ?", (endpoint,)
                ).fetchone()
                if row is None:
                    tokens, last_ts = burst, now
                else:
                    tokens, last_ts = row[0], row[1]

                tokens = min(burst, tokens + (now - last_ts) * rps)
                allowed = tokens >= 1.0
                if allowed:
                    tokens -= 1.0
                conn.execute(
                    "INSERT INTO token_bucket(endpoint, tokens, last_ts) VALUES (?, ?, ?) "
                    "ON CONFLICT(endpoint) DO UPDATE SET tokens = ?, last_ts = ?",
                    (endpoint, tokens, now, tokens, now),
                )
                conn.commit()
                return allowed
            finally:
                conn.close()
        except sqlite3.Error:
            # 降级：限流组件自己不能成为故障源，放行并让上层指标暴露异常
            return True

    def cache_get(self, key: str, ttl_s: float):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT result, created_ts FROM idem_cache WHERE idem_key = ?", (key,)
                ).fetchone()
            if row and (time.time() - row[1]) <= ttl_s:
                return row[0]
        except sqlite3.Error:
            return None
        return None

    def cache_put(self, key: str, result: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO idem_cache(idem_key, result, created_ts) VALUES (?, ?, ?) "
                    "ON CONFLICT(idem_key) DO UPDATE SET result = ?, created_ts = ?",
                    (key, result, time.time(), result, time.time()),
                )
        except sqlite3.Error:
            pass

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM token_bucket")
            conn.execute("DELETE FROM idem_cache")
