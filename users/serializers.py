# users/serializers.py
from rest_framework import serializers
from .models import CustomUser  # 确保在这里导入 CustomUser 模型
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.models import Group
from core.utils.exceptions import ConflictException


# ====================================================================
# CustomUserSerializer - 主要展示字段
# ====================================================================
class CustomUserSerializer(serializers.ModelSerializer):
    """
    用户模型通用序列化器，用于返回用户详情。
    根据请求，精简了返回的字段，移除了冗余的角色判断布尔值和get_full_name。
    """
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)

    # groups 字段依然保留，它是判断用户角色的主要依据
    groups = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field='name'
    )

    # 显式声明的字段，并标记为 read_only=True，确保它们从模型实例中正确获取值。
    # allow_null=True 用于在模型字段为 null 时，JSON 也输出 null 而不是空字符串。
    email = serializers.EmailField(read_only=True, allow_null=True)
    name = serializers.CharField(read_only=True, allow_null=True)
    phone_number = serializers.CharField(read_only=True, allow_null=True)
    work_id = serializers.CharField(read_only=True, allow_null=True)
    major = serializers.CharField(read_only=True, allow_null=True)
    student_class = serializers.CharField(read_only=True, allow_null=True)
    gender = serializers.CharField(read_only=True, allow_null=True)

    # 移除了 is_system_admin, is_space_manager, is_teacher, is_student, is_staff_member, get_full_name
    # 它们不再作为顶级字段直接出现在 CustomUserSerializer 的输出中。
    # 如果需要在其他地方（如 JWT Payload）包含这些信息，那里的序列化器应单独处理。

    class Meta:
        model = CustomUser
        fields = (
            'id', 'username', 'email', 'name',
            'phone_number', 'work_id', 'major', 'student_class', 'gender', 'gender_display',
            'is_active', 'date_joined', 'last_login', 'groups','is_staff'
            # 移除了 'is_staff', 'is_superuser' (Django 内部权限标志，通常前端不需要直接显示)
            # 移除了所有 'is_system_admin', 'is_space_manager', 'is_teacher', 'is_student', 'is_staff_member'
            # 移除了 'get_full_name'
        )
        # read_only_fields 列表也相应调整，移除上述已删除的字段
        read_only_fields = (
            'id', 'username', 'is_active',
            'date_joined', 'last_login', 'gender_display', 'groups','is_staff'
        )


# ====================================================================
# UserRegistrationSerializer - 用户注册 (**无需修改，保持不变**)
# ====================================================================
class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    用户注册序列化器，包含密码验证。
    """
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = CustomUser
        fields = (
            'username', 'email', 'name', 'phone_number', 'work_id',
            'major', 'student_class', 'gender',
            'password', 'password2',
        )
        extra_kwargs = {
            'username': {'required': True},
            'email': {'required': False, 'allow_blank': True},
            'name': {'required': False, 'allow_blank': True},
            'phone_number': {'required': False, 'allow_blank': True},
            'work_id': {'required': True},
            'major': {'required': False, 'allow_blank': True},
            'student_class': {'required': False, 'allow_blank': True},
            'gender': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "两次输入的密码不一致。"})

        temp_user_for_validation = CustomUser(username=attrs['username'])
        try:
            validate_password(attrs['password'], user=temp_user_for_validation)
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})

        for field_name in ['email', 'name', 'phone_number', 'major', 'student_class']:
            if attrs.get(field_name) == '':
                attrs[field_name] = None

        if attrs.get('work_id') and CustomUser.objects.filter(work_id=attrs['work_id']).exists():
            raise serializers.ValidationError({"work_id": "该工号/学号已被注册。"})
        email = attrs.get('email')
        if email is not None and CustomUser.objects.filter(email=email).exists():
            raise serializers.ValidationError({"email": "该邮箱已被注册。"})
        phone_number = attrs.get('phone_number')
        if phone_number is not None and CustomUser.objects.filter(phone_number=phone_number).exists():
            raise serializers.ValidationError({"phone_number": "该手机号已被注册。"})

        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data.pop('password2')
        user = CustomUser.objects.create_user(
            password=password,
            **validated_data
        )
        student_group, created = Group.objects.get_or_create(name='学生')
        user.groups.add(student_group)
        user.save()
        return user


# ====================================================================
# UserProfileUpdateSerializer (**无需修改，保持不变**)
# ====================================================================
class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用于更新用户个人资料的序列化器。
    """

    class Meta:
        model = CustomUser
        fields = (
            'name', 'email', 'phone_number', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'name': {'required': False, 'allow_blank': True},
            'email': {'required': False, 'allow_blank': True},
            'phone_number': {'required': False, 'allow_blank': True},
            'major': {'required': False, 'allow_blank': True},
            'student_class': {'required': False, 'allow_blank': True},
            'gender': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        for field_name in ['name', 'email', 'phone_number', 'major', 'student_class']:
            if attrs.get(field_name) == '':
                attrs[field_name] = None
        return attrs

    def validate_email(self, value):
        if value is not None and CustomUser.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("该邮箱已被其他用户使用。")
        return value

    def validate_phone_number(self, value):
        if value is not None and CustomUser.objects.exclude(pk=self.instance.pk).filter(phone_number=value).exists():
            raise serializers.ValidationError("该手机号已被其他用户使用。")
        return value


# ====================================================================
# AdminUserUpdateSerializer (**无需修改，保持不变**)
# ====================================================================
class AdminUserUpdateSerializer(UserProfileUpdateSerializer):
    """
    管理员用于更新用户资料和组（角色）的序列化器。
    """
    groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        many=True,
        required=False
    )

    class Meta(UserProfileUpdateSerializer.Meta):
        fields = UserProfileUpdateSerializer.Meta.fields + (
            'groups', 'is_active', 'is_staff', 'is_superuser'
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)

        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("认证失败或请求用户未登录。", code='authentication_required')

        target_user = self.instance

        is_requesting_superuser = request.user.is_superuser
        is_requesting_system_admin = getattr(request.user, 'is_system_admin', False)

        is_editing_superuser = target_user and target_user.is_superuser
        is_editing_system_admin = target_user and getattr(target_user, 'is_system_admin', False)

        if is_editing_superuser and not is_requesting_superuser:
            raise serializers.ValidationError({"detail": "您没有权限修改超级用户。"}, code='forbidden_to_edit_superuser')

        if is_requesting_system_admin and not is_requesting_superuser:
            if target_user and target_user.pk != request.user.pk:
                if is_editing_system_admin:
                    raise serializers.ValidationError({"detail": "系统管理员不能修改其他系统管理员用户。"},
                                                      code='forbidden_to_edit_other_system_admin')

            if 'is_superuser' in attrs and attrs['is_superuser'] != (
            target_user.is_superuser if target_user else False):
                raise serializers.ValidationError({"is_superuser": "您没有权限更改用户的超级用户状态。"},
                                                  code='forbidden_to_change_superuser_status')

            if 'groups' in attrs:
                old_groups_ids = set(target_user.groups.values_list('pk', flat=True)) if target_user else set()
                new_groups_ids = set([g.pk for g in attrs['groups']])
                sys_admin_group = Group.objects.filter(name='系统管理员').first()

                if sys_admin_group:
                    sys_admin_group_pk = sys_admin_group.pk

                    if sys_admin_group_pk not in old_groups_ids and sys_admin_group_pk in new_groups_ids:
                        if target_user.pk != request.user.pk:
                            raise serializers.ValidationError(
                                {"groups": "系统管理员不能将其他用户添加到'系统管理员'组。"},
                                code='forbidden_to_add_system_admin_group_to_others')

                    if sys_admin_group_pk in old_groups_ids and sys_admin_group_pk not in new_groups_ids:
                        if target_user.pk != request.user.pk:
                            raise serializers.ValidationError(
                                {"groups": "系统管理员不能移除其他系统管理员的'系统管理员'组。"},
                                code='forbidden_to_remove_system_admin_group_from_others')
        return attrs

    def update(self, instance, validated_data):
        groups_data = validated_data.pop('groups', None)
        instance = super().update(instance, validated_data)
        if groups_data is not None:
            instance.groups.set(groups_data)
            instance.save()
        return instance