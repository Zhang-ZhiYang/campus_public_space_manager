# core/__init__.py
# 这确保了 Celery 应用在 Django 启动时被加载
from .celery import app as celery_app
import pymysql
pymysql.install_as_MySQLdb()