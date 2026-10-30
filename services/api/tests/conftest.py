import os

# 单元测试不启动后台任务（迁移、历史库写入、推送中心），也不依赖真实数据库/Redis
os.environ.setdefault("ENABLE_WORKERS", "false")
