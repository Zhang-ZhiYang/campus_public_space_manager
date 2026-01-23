# notifications/tasks.py
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from .models import Notification
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_task(self, notification_id):
    """
    异步发送邮件的任务。
    如果失败，会自动重试 3 次，每次间隔 60 秒。
    """
    try:
        # 获取通知记录
        notification = Notification.objects.get(id=notification_id)

        logger.info(f"Starting to send email (ID: {notification_id}) to {notification.recipient_email}")

        # 执行发送 (Django 封装的 SMTP 发送)
        send_mail(
            subject=notification.subject,
            message=notification.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[notification.recipient_email],
            fail_silently=False,  # 报错时抛出异常以便捕获
        )

        # 发送成功，更新状态
        notification.status = Notification.Status.SUCCESS
        notification.sent_at = timezone.now()
        notification.error_log = None  # 清空之前的错误（如果有）
        notification.save()

        logger.info(f"Email sent successfully (ID: {notification_id})")
        return f"Email sent to {notification.recipient_email}"

    except Notification.DoesNotExist:
        logger.error(f"Notification ID {notification_id} not found.")
        return "Notification not found"

    except Exception as e:
        # 发送失败，更新状态和错误日志
        error_msg = str(e)
        logger.error(f"Failed to send email (ID: {notification_id}): {error_msg}")

        # 即使在重试中，也先记录下当前的错误
        try:
            notification = Notification.objects.get(id=notification_id)
            notification.status = Notification.Status.FAILED
            notification.error_log = error_msg
            notification.save()
        except:
            pass

        # 触发 Celery 的自动重试机制
        raise self.retry(exc=e)