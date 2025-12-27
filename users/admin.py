# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser

@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    """
    自定义 UserAdmin，以便在 Django Admin 中更好地管理 CustomUser 模型。
    """
    list_display = (
        'username', 'email', 'phone_number', 'student_id', 'major', 'student_class', 'gender', # 新增字段
        'total_violation_count',
        'is_active', 'is_staff', 'is_superuser', 'last_login'
    )
    search_fields = (
        'username', 'email', 'phone_number', 'student_id', 'major', 'student_class', # 新增搜索字段
        'first_name', 'last_name'
    )
    list_filter = (
        'is_active', 'is_staff', 'is_superuser', 'gender', 'major', 'student_class', # 新增过滤字段
        'total_violation_count', 'date_joined'
    )

    # 定义用户详情页的字段显示和布局
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('个人信息', {'fields': (
            'first_name', 'last_name', 'email', 'phone_number',
            'student_id', 'major', 'student_class', 'gender' # 新增字段
        )}),
        ('权限', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('重要日期', {'fields': ('last_login', 'date_joined')}),
        ('违约信息', {'fields': ('total_violation_count',)}),
    )

    # 针对添加新用户的表单字段 (如果使用默认的 CustomUserCreationForm，还需要修改该表单)
    # 对于 BaseUserAdmin，可以通过 add_fieldsets 来修改添加用户页面的布局
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('额外信息', {'fields': (
            'phone_number', 'student_id', 'major', 'student_class', 'gender', # 新增字段
            'total_violation_count'
        )}),
    )

    ordering = ('username',)