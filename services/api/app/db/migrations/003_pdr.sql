-- 事故追忆（PDR, Post-Disturbance Review）：事故总 / 备自投动作时自动生成记录，
-- 回放时从历史库还原触发前 pre_s 秒至触发后 post_s 秒的全站遥测、遥信
CREATE TABLE IF NOT EXISTS pdr (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL,
    title       text NOT NULL,
    bay         text,
    pre_s       integer NOT NULL DEFAULT 30,
    post_s      integer NOT NULL DEFAULT 60,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pdr_ts ON pdr (ts DESC);
