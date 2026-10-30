"""数据库：连接池、迁移、模型同步。

迁移为按文件名顺序执行的 SQL 文件，记录在 schema_migrations；多实例启动时用 advisory lock 串行化。
语句逐条执行而不包在事务里：TimescaleDB 的连续聚合不能在事务块中创建。
"""

import json
import logging
import re
from pathlib import Path

import asyncpg

from scadalib.model import Station

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_LOCK_ID = 7_104_2404


async def create_pool(dsn: str) -> asyncpg.Pool:
    # min_size=0：数据库暂不可用时服务仍能启动，健康检查如实报告
    return await asyncpg.create_pool(dsn, min_size=0, max_size=8, command_timeout=30)


def split_sql(script: str) -> list[str]:
    """按语句末尾的分号切分（迁移脚本中不含字符串内分号）。"""
    no_comments = re.sub(r"--[^\n]*", "", script)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


async def migrate(pool: asyncpg.Pool) -> list[str]:
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
            )
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.name in done:
                    continue
                for stmt in split_sql(f.read_text(encoding="utf-8")):
                    await conn.execute(stmt)
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", f.name)
                applied.append(f.name)
                log.info("migration applied: %s", f.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
    return applied


async def sync_model(pool: asyncpg.Pool, st: Station) -> int:
    """把点表同步到 measurement 表（新增/更新/删除）。"""
    rows = [
        (p.id, p.name, str(p.cat), p.ioa, p.bay, p.ref, p.meas, p.unit, p.deadband,
         json.dumps(p.limits.as_dict()) if p.limits else None, p.double, p.target, p.decimals)
        for p in st.points
    ]  # fmt: skip
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("CREATE TEMP TABLE m_new (LIKE measurement INCLUDING DEFAULTS) ON COMMIT DROP")
        await conn.copy_records_to_table(
            "m_new",
            records=rows,
            columns=["id", "name", "cat", "ioa", "bay", "ref", "meas", "unit", "deadband", "limits", "double",
                     "target", "decimals"],
        )  # fmt: skip
        await conn.execute("DELETE FROM measurement WHERE id NOT IN (SELECT id FROM m_new)")
        # IOA 有唯一约束：先清掉将被其他点占用的地址，避免交换地址时冲突
        await conn.execute("DELETE FROM measurement m USING m_new n WHERE m.ioa = n.ioa AND m.id <> n.id")
        await conn.execute(
            """
            INSERT INTO measurement (id, name, cat, ioa, bay, ref, meas, unit, deadband, limits, double, target, decimals)
            SELECT id, name, cat, ioa, bay, ref, meas, unit, deadband, limits, double, target, decimals FROM m_new
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, cat = EXCLUDED.cat, ioa = EXCLUDED.ioa, bay = EXCLUDED.bay, ref = EXCLUDED.ref,
                meas = EXCLUDED.meas, unit = EXCLUDED.unit, deadband = EXCLUDED.deadband, limits = EXCLUDED.limits,
                double = EXCLUDED.double, target = EXCLUDED.target, decimals = EXCLUDED.decimals, updated_at = now()
            """
        )
    return len(rows)
