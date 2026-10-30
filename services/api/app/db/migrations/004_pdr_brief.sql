-- 事故简报：首次查看时生成并缓存；source = template（规则模板）/ llm（大模型撰写）
ALTER TABLE pdr ADD COLUMN IF NOT EXISTS brief jsonb;
