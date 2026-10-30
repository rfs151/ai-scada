import asyncpg

from app.auth.security import User, hash_password, verify_password

DEMO_USERS = [
    ("zhang", "张工", "operator"),
    ("li", "李工", "operator"),
    ("wang", "王工", "supervisor"),
    ("admin", "系统管理员", "admin"),
]


async def seed_demo_users(pool: asyncpg.Pool, password: str) -> None:
    """演示账号：不存在才创建，不覆盖已修改的口令。"""
    async with pool.acquire() as conn:
        for username, name, role in DEMO_USERS:
            await conn.execute(
                "INSERT INTO app_user (username, name, role, pw_hash) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (username) DO NOTHING",
                username, name, role, hash_password(password),
            )  # fmt: skip


async def authenticate(pool: asyncpg.Pool, username: str, password: str) -> User | None:
    row = await pool.fetchrow(
        "SELECT username, name, role, pw_hash FROM app_user WHERE username = $1 AND enabled", username
    )
    # 用户不存在时也做一次散列计算，避免通过响应时间探测账号是否存在
    stored = row["pw_hash"] if row else hash_password("timing-equalizer")
    if not verify_password(password, stored) or row is None:
        return None
    return User(row["username"], row["name"], row["role"])
