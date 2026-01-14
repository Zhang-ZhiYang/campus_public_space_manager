# core/celery.py
import os
from celery import Celery

# 设置 Django 的 settings 模块
# 这里的 'core.settings' 对应你的项目的主 settings 文件路径
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# 创建 Celery 实例
# 给你的 Celery 应用一个独特的名称
app = Celery('campus_public_space_manager')

# 从 Django settings 加载配置
# `namespace='CELERY'` 表示 Celery 相关设置都以 'CELERY_' 开头
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动从 INSTALLED_APPS 中的 'tasks.py' 文件发现任务
app.autodiscover_tasks()

# 示例任务 (可选，可以先删除)
@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')