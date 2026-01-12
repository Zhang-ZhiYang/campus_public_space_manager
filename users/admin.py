# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group  # 导入 Group
from django.db.models import Q

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
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                form.base_fields[field_name].required = False
            return form

        is_requesting_superuser = request.user.is_superuser
        is_requesting_system_admin = getattr(request.user, 'is_system_admin',
                                             False)  # 如果is_system_admin是True，那么它也可能是is_superuser

        is_editing_superuser = obj and obj.is_superuser

        # --- 新增的逻辑：系统管理员不能编辑超级管理员 ---
        # 如果当前用户不是超级管理员，且正在编辑一个超级管理员，则完全禁用表单
        if is_editing_superuser and not is_requesting_superuser:
            messages.warning(request, _("您没有权限编辑超级用户。此表单已被禁用。"))
            for field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs['disabled'] = True
                form.base_fields[field_name].required = False
            return form
        # --- 结束新增逻辑 ---

        # 针对系统管理员 (非超级管理员) 的限制
        if is_requesting_system_admin and not is_requesting_superuser:  # 明确排除超级管理员
            if obj is not None:  # 编辑现有用户
                # 禁用安全敏感字段
                for field_name in ['is_superuser', 'user_permissions']:
                    if field_name in form.base_fields:
                        form.base_fields[field_name].widget.attrs['disabled'] = True
                        form.base_fields[field_name].required = False

                # 系统管理员不能修改其他系统管理员的组
                # 除非正在修改的是当前系统管理员自己
                if obj.is_system_admin and obj.pk != request.user.pk:
                    if 'groups' in form.base_fields:
                        form.base_fields['groups'].widget.attrs['disabled'] = True
                        messages.warning(request, _("您不能修改其他系统管理员的组。"))

        # 针对所有非超级管理员，其 is_superuser 字段都应该不可见或禁用
        if not is_requesting_superuser and 'is_superuser' in form.base_fields:
            form.base_fields['is_superuser'].widget.attrs['disabled'] = True
            form.base_fields['is_superuser'].required = False

        return form

    def save_model(self, request, obj, form, change):
        # ⚠️ 确保请求用户已认证
        if not request.user.is_authenticated:
            messages.error(request, _("您没有权限执行此操作，请先登录。"))
            return  # 未认证用户不应保存任何内容

        is_requesting_superuser = request.user.is_superuser
        # 明确区分是系统管理员但不是超级管理员的情况
        is_requesting_only_system_admin = getattr(request.user, 'is_system_admin',
                                                  False) and not is_requesting_superuser

        is_editing_superuser = obj and obj.is_superuser
        is_editing_system_admin = obj and getattr(obj, 'is_system_admin', False)

        # --- 新增的逻辑：系统管理员不能修改超级管理员 ---
        # 如果当前用户不是超级管理员，且正在编辑一个超级管理员 (再次检查，以防 get_form 失效)
        if is_editing_superuser and not is_requesting_superuser:
            messages.error(request, _("您没有权限修改超级用户。"))
            return
        # --- 结束新增逻辑 ---

        # 在调用 super().save() 之前，捕获原始的 is_superuser 和 groups 状态
        old_is_superuser = obj.is_superuser  # obj.is_superuser 可能在 get_form 逻辑中被修改, 这里获取真实旧状态
        old_groups_ids = set(obj.groups.values_list('pk', flat=True)) if obj.pk else set()

        # 允许 CustomUser.save() 首先执行，确保 obj.pk 有值，且 AbstractUser 的基础字段被保存
        # 这里会触发 CustomUser.save 方法中的 is_staff 逻辑
        # IMPORTANT: 如果form中is_superuser是禁用状态，则form.cleaned_data中不会有is_superuser，
        # obj.is_superuser会保持原样，这避免了误改。
        super().save_model(request, obj, form, change)

        # 针对 系统管理员 (非超级管理员) 的权限校验：关于 is_superuser 和 groups 的修改
        if is_requesting_only_system_admin:
            # 1. 尝试修改 is_superuser 字段
            # 由于 get_form 中已经禁用了 is_superuser，这里理论上不会有 form.changed_data['is_superuser']
            # 但作为额外的安全检查，如果通过某种方式绕过了，应该回滚。
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
                    # 规则：系统管理员不能将其他用户添加到 '系统管理员' 组
                    if sys_admin_group_pk not in old_groups_ids and sys_admin_group_pk in new_groups_ids:
                        if (obj.pk is None or obj.pk != request.user.pk):  # 不是自己 (或新建用户)
                            messages.error(request, _("系统管理员不能将其他用户添加到'系统管理员'组。已回滚。"))
                            obj.groups.set(list(old_groups_ids))  # 回滚到旧的组
                            # 重新触发 save 以更新 CustomUser 的 is_staff 属性
                            obj.save()
                            return

                    # 检查是否尝试从 '系统管理员' 组移除 (保护其他系统管理员)
                    # 规则：系统管理员不能移除其他系统管理员的 '系统管理员' 组
                    if sys_admin_group_pk in old_groups_ids and sys_admin_group_pk not in new_groups_ids:
                        if (obj.pk is None or obj.pk != request.user.pk):  # 不是自己
                            messages.error(request, _("您不能移除其他系统管理员的'系统管理员'组。已回滚。"))
                            obj.groups.set(list(old_groups_ids))
                            # 重新触发 save 以更新 CustomUser 的 is_staff 属性
                            obj.save()
                            return

        # 确保保存后刷新对象，以便后续信号或操作能获取到最新的 is_staff 值
        # 注意：CustomUser.save() 已经在适当的时候处理了 is_staff 的更新，这里可用于确保状态一致性
        obj.refresh_from_db()

    # 权限方法：对 CustomUser 模型的管理，我们使用基于 Group 的属性来控制权限
    def has_module_permission(self, request):
        """只有能登录后台的管理员和系统管理员（包括超级用户和属于系统管理员组的用户）才能看到用户模块"""
        if not request.user.is_authenticated:
            return False
        # 任何被 is_staff 或 is_system_admin 标记的用户都可以看到用户模块
        return request.user.is_staff or getattr(request.user, 'is_system_admin', False)

    def has_view_permission(self, request, obj=None):
        """系统管理员和空间管理员可以查看用户"""
        if not request.user.is_authenticated:
            return False
        # 超级用户和系统管理员可以查看所有用户
        if request.user.is_superuser or getattr(request.user, 'is_system_admin', False):
            return True
        # 空间管理员可以查看所有用户 (仅限查看列表，不能修改)
        if getattr(request.user, 'is_space_manager', False):
            return True
        return False

    def has_add_permission(self, request):
        """只有系统管理员 (包括超级用户) 可以添加用户"""
        if not request.user.is_authenticated:
            return False
        return request.user.is_superuser or getattr(request.user, 'is_system_admin', False)

    def has_change_permission(self, request, obj=None):
        user = request.user
        if not user.is_authenticated:
            return False

        if user.is_superuser:  # 超级管理员可以修改所有用户
            return True

        # 对于系统管理员 (非超级管理员)
        if getattr(user, 'is_system_admin', False) and not user.is_superuser:
            if obj is None:  # 表示在用户列表页，判断是否允许进入修改页 (只要有能修改的用户，就可以)
                # 系统管理员可以修改所有非超级用户、非其他系统管理员的用户
                # 所以只要存在一个这样的目标用户，就返回True
                return CustomUser.objects.filter(~Q(is_superuser=True), ~Q(pk=user.pk), is_active=True) \
                    .exclude(groups__name='系统管理员').exists()

            # 编辑特定用户
            if obj and obj.is_superuser:  # 不能修改超级用户
                return False
            # 系统管理员不能修改其他系统管理员用户 (除非是自己)
            elif obj and getattr(obj, 'is_system_admin', False) and obj.pk != user.pk:
                return False
            return True  # 可以修改非超级用户、非其他系统管理员的用户

        return False  # 空间管理员及以下用户不允许修改其他用户

    def has_delete_permission(self, request, obj=None):
        user = request.user
        if not user.is_authenticated:
            return False

        if user.is_superuser:  # 超级管理员可以删除所有用户
            return True

        # 对于系统管理员 (非超级管理员)
        if getattr(user, 'is_system_admin', False) and not user.is_superuser:
            if obj is None:  # 在用户列表页，判断是否允许批量删除 (只要有能删除的用户，就可以)
                return CustomUser.objects.filter(~Q(is_superuser=True), ~Q(pk=user.pk), is_active=True) \
                    .exclude(groups__name='系统管理员').exists()

            # 删除特定用户
            if obj and obj.is_superuser:  # 不能删除超级用户
                return False
            # 系统管理员不能删除其他系统管理员用户 (除非是自己)
            elif obj and getattr(obj, 'is_system_admin', False) and obj.pk != user.pk:
                return False
            return True  # 可以删除非超级用户、非其他系统管理员的用户

        return False