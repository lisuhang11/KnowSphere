"""Celery 应用：KnowSphere 异步任务（Celery + Redis 队列）。

- broker/backend 均为 Redis（settings.redis_url）。
- 任务定义在 api/tasks.py，worker 启动命令：
    celery -A api.celery_app.celery worker -B --loglevel=info
  （-B 嵌入 beat，单实例场景够用；任务队列为 "documents"）
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from config.settings import settings

celery = Celery(
    "knowsphere",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["api.tasks"],
)

celery.conf.update(
    task_default_queue="documents",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # 避免项目根目录 schedule 文件与多实例/权限冲突（勿与 docker worker 共用同一路径）
    beat_schedule_filename="data/celerybeat-schedule",
    # 任务最多尝试 3 次（初次 + 2 次重试）
    task_default_retry_delay=5,
    task_acks_late=True,  # worker 崩溃后任务重新投递，配合 DB 状态守卫防重复副作用
    worker_prefetch_multiplier=1,
    beat_schedule={
        # housekeeping：定时回收处理超时的孤儿文档
        "housekeeping-recover-stale": {
            "task": "api.tasks.housekeeping_recover_stale",
            "schedule": crontab(minute="*/5"),
        },
        # 临时聊天附件过期清理（每 10 分钟）
        "cleanup-expired-temporary-attachments": {
            "task": "api.tasks.cleanup_expired_temporary_attachments",
            "schedule": crontab(minute="*/10"),
        },
    },
)
