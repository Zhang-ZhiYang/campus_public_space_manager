# notifications/models.py
from django.db import models
from django.utils.translation import gettext_lazy as _

class Notification(models.Model):
    class NotificationType(models.TextChoices):
        EMAIL = 'email', _('Email')
        SYSTEM = 'system', _('System Message')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        SUCCESS = 'success', _('Success')
        FAILED = 'failed', _('Failed')

    recipient_email = models.EmailField(_("Recipient Email"), help_text="接收人邮箱")
    subject = models.CharField(_("Subject"), max_length=255)
    message = models.TextField(_("Message Content"))
    notification_type = models.CharField(
        max_length=20, 
        choices=NotificationType.choices, 
        default=NotificationType.EMAIL
    )
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING
    )
    error_log = models.TextField(_("Error Log"), blank=True, null=True, help_text="发送失败时的错误信息")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject} -> {self.recipient_email}"