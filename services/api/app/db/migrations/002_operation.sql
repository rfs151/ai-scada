-- P3：用户、告警、挂牌、审计

CREATE TABLE IF NOT EXISTS app_user (
    username   text PRIMARY KEY,
    name       text NOT NULL,
    role       text NOT NULL CHECK (role IN ('viewer', 'operator', 'supervisor', 'admin')),
    pw_hash    text NOT NULL,
    enabled    boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 告警：level 1 事故 / 2 异常 / 3 越限 / 4 变位 / 5 告知
CREATE TABLE IF NOT EXISTS alarm (
    id         bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL,
    level      smallint NOT NULL,
    kind       text NOT NULL,
    point_id   text,
    bay        text,
    device     text,
    text       text NOT NULL,
    value      double precision,
    suppressed boolean NOT NULL DEFAULT false,  -- 检修挂牌抑制
    ack_by     text,
    ack_ts     timestamptz
);
CREATE INDEX IF NOT EXISTS alarm_ts_idx ON alarm (ts DESC);
CREATE INDEX IF NOT EXISTS alarm_unacked_idx ON alarm (id) WHERE ack_ts IS NULL;

-- 挂牌：maint 检修 / noclose 禁止合闸 / ground 接地线（node 为挂接的连接节点）
CREATE TABLE IF NOT EXISTS tag (
    id         bigserial PRIMARY KEY,
    device     text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('maint', 'noclose', 'ground')),
    node       text,
    note       text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (device, kind)
);

-- 审计：登录、遥控每一步、挂牌、告警确认；只增不改
CREATE TABLE IF NOT EXISTS audit_log (
    id       bigserial PRIMARY KEY,
    ts       timestamptz NOT NULL DEFAULT now(),
    username text,
    role     text,
    action   text NOT NULL,
    target   text NOT NULL DEFAULT '',
    ok       boolean NOT NULL,
    detail   jsonb,
    ip       text
);
CREATE INDEX IF NOT EXISTS audit_ts_idx ON audit_log (ts DESC);
