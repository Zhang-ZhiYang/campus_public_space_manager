# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import CustomUser, Role # 导入 Role 模型

# 注册 Role 模型
@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)
    ordering = ('name',)

@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin):
    list_display = (
        'username', 'email', 'phone_number', 'student_id', 'role', 'major', 'student_class', 'gender', # 显示 role 字段
        'total_violation_count',
        'is_active', 'is_staff', 'is_superuser', 'last_login'
    )
    search_fields = (
        'username', 'email', 'phone_number', 'student_id', 'major', 'student_class', 'role__name', # 可以按角色名称搜索
        'first_name', 'last_name'
    )
    list_filter = (
        'is_active', 'is_staff', 'is_superuser', 'gender', 'major', 'student_class',
        'total_violation_count', 'date_joined', 'role' # 可以按角色过滤
    )

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('个人信息', {'fields': (
            'first_name', 'last_name', 'email', 'phone_number',
            'student_id', 'major', 'student_class', 'gender', 'role' # 在这里添加 role 字段
        )}),
        ('权限', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('重要日期', {'fields': ('last_login', 'date_joined')}),
        ('违约信息', {'fields': ('total_violation_count',)}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('额外信息', {'fields': (
            'phone_number', 'student_id', 'major', 'student_class', 'gender', 'role', # 添加 role 字段
            'total_violation_count'
        )}),
    )

    ordering = ('username',)