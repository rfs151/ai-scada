-- 测点模型（由 config/station.yaml 生成，api 启动时同步）
CREATE TABLE IF NOT EXISTS measurement (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    cat         text NOT NULL,
    ioa         integer NOT NULL UNIQUE,
    bay         text NOT NULL,
    ref         text NOT NULL,
    meas        text NOT NULL,
    unit        text NOT NULL DEFAULT '',
    deadband    double precision NOT NULL DEFAULT 0,
    limits      jsonb,
    double      boolean NOT NULL DEFAULT false,
    target      text,
    decimals    integer NOT NULL DEFAULT 2,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 遥测 / 电度历史（变化存储：RTU 死区 + 前置变化检测）
CREATE TABLE IF NOT EXISTS analog_hist (
    ts        timestamptz NOT NULL,
    point_id  text NOT NULL,
    value     double precision NOT NULL,
    quality   smallint NOT NULL DEFAULT 0
);

SELECT create_hypertable('analog_hist', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS analog_hist_point_ts ON analog_hist (point_id, ts DESC);

-- 3 天前的数据块按测点分段压缩，通常可压缩到 1/10 以下；原始数据保留 30 天
ALTER TABLE analog_hist SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'point_id',
    timescaledb.compress_orderby = 'ts DESC'
);

SELECT add_compression_policy('analog_hist', INTERVAL '3 days', if_not_exists => TRUE);

SELECT add_retention_policy('analog_hist', INTERVAL '30 days', if_not_exists => TRUE);

-- 1 分钟连续聚合：长时间跨度的趋势查询走聚合，避免扫描原始数据
CREATE MATERIALIZED VIEW IF NOT EXISTS analog_1m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket,
       point_id,
       avg(value)       AS avg,
       min(value)       AS min,
       max(value)       AS max,
       last(value, ts)  AS last
FROM analog_hist
GROUP BY bucket, point_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('analog_1m',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists     => TRUE);

-- 遥信变位事件（SOE）：dev_ts 为 RTU 时标（毫秒），recv_ts 为主站接收时间；
-- 事件顺序以 dev_ts 为准（站内各装置同步对时），总召唤发现的不一致没有 dev_ts
CREATE TABLE IF NOT EXISTS yx_event (
    id         bigserial PRIMARY KEY,
    dev_ts     timestamptz,
    recv_ts    timestamptz NOT NULL,
    point_id   text NOT NULL,
    value      smallint NOT NULL,
    old_value  smallint,
    cot        smallint NOT NULL
);

CREATE INDEX IF NOT EXISTS yx_event_order ON yx_event ((coalesce(dev_ts, recv_ts)) DESC);

CREATE INDEX IF NOT EXISTS yx_event_point ON yx_event (point_id, recv_ts DESC);
