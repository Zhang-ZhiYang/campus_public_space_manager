# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group  # 导入 Group
from .models import CustomUser  # 导入 CustomUser 模型
from django.utils.translation import gettext_lazy as _
from django.contrib import messages  # 导入 messages 用于用户反馈


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (_('自定义信息'), {  # 使用 _("...") 进行国际化
            'fields': (
                'name',  # 新增 name 字段
                'phone_number', 'work_id', 'major',
                'student_class', 'gender',
            ),
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (_('自定义信息'), {  # 使用 _("...") 进行国际化
            'fields': (
                'name',  # 新增 name 字段
                'phone_number', 'work_id', 'major',
                'student_class', 'gender',
            ),
        }),
    )

    list_display = UserAdmin.list_display + ('name', 'get_full_name', 'phone_number', 'work_id', 'get_groups_display')
    list_filter = UserAdmin.list_filter + ('groups',)  # 调整为按 groups 过滤
    search_fields = ('username', 'name', 'first_name', 'last_name', 'phone_number', 'work_id',
                     'email')  # 调整 search_fields，包含 name
    ordering = ('username',)

    @admin.display(description='所属组')
    def get_groups_display(self, obj: CustomUser):  # 明确类型提示
        return ", ".join([g.name for g in obj.groups.all()])

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        # ⚠️ 确保请求用户已认证
        if not request.user.is_authenticated:
            # 未认证用户不应该能访问表单，因为 Admin 视图本身应该阻止
            # 但作为防御性措施，可以禁用所有字段
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                form.base_fields[field_name].required = False
            return form

        is_requesting_superuser = request.user.is_superuser
        is_requesting_system_admin = request.user.is_system_admin  # 判断是否是系统管理员 (包含超级管理员)

        is_editing_superuser = obj and obj.is_superuser

        # 如果当前用户不是超级管理员，且正在编辑一个超级管理员
        if is_editing_superuser and not is_requesting_superuser:
            messages.warning(request, _("您没有权限编辑超级用户。此表单已被禁用。"))
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                form.base_fields[field_name].required = False
            return form

        # 只有在编辑已有用户时才检查这些细粒度权限，新建用户不受此限制
        if is_requesting_system_admin and obj is not None:
            # 禁用安全敏感字段
            for field_name in ['is_superuser', 'user_permissions']:
                if field_name in form.base_fields:
                    form.base_fields[field_name].widget.attrs['disabled'] = True
                    form.base_fields[field_name].required = False

            if 'groups' in form.base_fields:
                # 如果正在编辑的用户是另一个系统管理员 (非当前用户)，则不能编辑其组
                if obj.is_system_admin and obj.pk != request.user.pk:
                    form.base_fields['groups'].widget.attrs['disabled'] = True
                    messages.warning(request, _("您不能修改其他系统管理员的组。"))

        return form

    def save_model(self, request, obj, form, change):
        # ⚠️ 确保请求用户已认证
        if not request.user.is_authenticated:
            messages.error(request, _("您没有权限执行此操作，请先登录。"))
            return  # 未认证用户不应保存任何内容

        is_requesting_superuser = request.user.is_superuser
        is_requesting_system_admin = request.user.is_system_admin  # 判断是否是系统管理员 (包含超级管理员)
        is_editing_superuser = obj and obj.is_superuser

        # 如果当前用户不是超级管理员，且正在编辑一个超级管理员 (再次检查，以防 get_form 失效)
        if is_editing_superuser and not is_requesting_superuser:
            messages.error(request, _("您没有权限修改超级用户。"))
            return

        # 在调用 super().save() 之前，捕获原始的 is_superuser 和 groups 状态
        old_is_superuser = obj.is_superuser
        old_groups_ids = set(obj.groups.values_list('pk', flat=True)) if obj.pk else set()

        # 允许 CustomUser.save() 首先执行，确保 obj.pk 有值，且 AbstractUser 的基础字段被保存
        super().save_model(request, obj, form, change)  # 这里会触发 CustomUser.save 方法中的 is_staff 逻辑

        # 权限校验：关于 is_superuser 和 groups 的修改
        if not is_requesting_superuser:  # 非超级管理员需要校验
            # 1. 尝试修改 is_superuser 字段
            if 'is_superuser' in form.changed_data and obj.is_superuser != old_is_superuser:
                messages.error(request, _("您没有权限修改用户的超级用户状态。已回滚。"))
                obj.is_superuser = old_is_superuser  # 回滚
                obj.save(update_fields=['is_superuser'])
                return  # 阻止进一步保存

            # 2. 尝试修改 groups 字段
            if 'groups' in form.changed_data:
                # 获取系统管理员 Group 实例
                sys_admin_group = Group.objects.filter(name='系统管理员').first()
                if sys_admin_group:
                    sys_admin_group_pk = sys_admin_group.pk
                    new_groups_ids = set(obj.groups.values_list('pk', flat=True))

                    # 检查是否尝试添加 '系统管理员' 组
                    if sys_admin_group_pk not in old_groups_ids and sys_admin_group_pk in new_groups_ids:
                        if not is_requesting_system_admin:  # 非系统管理员尝试添加系统管理员组
                            messages.error(request, _("您没有权限将用户添加到'系统管理员'组。已回滚。"))
                            obj.groups.set(list(old_groups_ids))  # 回滚到旧的组
                            return
                        # 如果请求者是系统管理员，但不允许将其他人设为系统管理员 (除了自己)
                        elif is_requesting_system_admin and (obj.pk is None or obj.pk != request.user.pk):
                            messages.error(request, _("系统管理员不能将其他用户添加到'系统管理员'组。已回滚。"))
                            obj.groups.set(list(old_groups_ids))
                            return

                    # 检查是否尝试从 '系统管理员' 组移除 (保护其他系统管理员)
                    if sys_admin_group_pk in old_groups_ids and sys_admin_group_pk not in new_groups_ids:
                        if (obj.pk is None or obj.pk != request.user.pk):  # 无法移除其他系统管理员的组
                            messages.error(request, _("您不能移除其他系统管理员的'系统管理员'组。已回滚。"))
                            obj.groups.set(list(old_groups_ids))
                            return

        # 确保保存后刷新对象，以便后续信号或操作能获取到最新的 is_staff 值
        obj.refresh_from_db()

    # 权限方法：对 CustomUser 模型的管理，我们使用基于 Group 的属性来控制权限
    def has_module_permission(self, request):
        """只有能登录后台的管理员和空间管理员才能看到用户模块"""
        # ⚠️ 必须先检查是否已认证
        if not request.user.is_authenticated:
            return False
        return request.user.is_staff and (request.user.is_system_admin or request.user.is_space_manager)

    def has_view_permission(self, request, obj=None):
        """系统管理员和空间管理员可以查看用户"""
        # ⚠️ 必须先检查是否已认证
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin or request.user.is_space_manager

    def has_add_permission(self, request):
        """只有系统管理员可以添加用户"""
        # ⚠️ 必须先检查是否已认证
        if not request.user.is_authenticated:
            return False
        return request.user.is_system_admin

    def has_change_permission(self, request, obj=None):
        user = request.user
        # ⚠️ 必须先检查是否已认证
        if not user.is_authenticated:
            return False

        if user.is_superuser:  # 超级管理员可以修改所有用户
            return True
        if user.is_system_admin:  # 系统管理员可以修改非超级管理员的用户
            if obj and obj.is_superuser:  # 不能修改超级用户
                return False
            # 系统管理员不能修改其他系统管理员用户 (除非是自己)
            elif obj and obj.is_system_admin and obj.pk != user.pk:
                return False
            return True
        return False  # 空间管理员不允许修改其他用户

    def has_delete_permission(self, request, obj=None):
        user = request.user
        # ⚠️ 必须先检查是否已认证
        if not user.is_authenticated:
            return False

        if user.is_superuser:  # 超级管理员可以删除所有用户
            return True
        if user.is_system_admin:  # 系统管理员可以删除非超级管理员的用户
            if obj and obj.is_superuser:  # 不能删除超级用户
                return False
            # 系统管理员不能删除其他系统管理员用户 (除非是自己)
            elif obj and obj.is_system_admin and obj.pk != user.pk:
                return False
            return True
        return False