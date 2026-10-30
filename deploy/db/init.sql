-- 仅在数据卷首次初始化时执行。业务表结构由 api 的迁移管理（P1 引入 Alembic）。
CREATE EXTENSION IF NOT EXISTS timescaledb;
