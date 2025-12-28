# users/serializers.py
from rest_framework import serializers
from .models import CustomUser, Role, ROLE_STUDENT
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError


class RoleSerializer(serializers.ModelSerializer):
    """
    角色序列化器，用于在用户详情中展示角色信息。
    """

    class Meta:
        model = Role
        fields = ('id', 'name', 'description')
        read_only_fields = ('id', 'name', 'description')


class CustomUserSerializer(serializers.ModelSerializer):
    """
    用户模型通用序列化器，用于返回用户详情。
    """
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)
    role = RoleSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(),
        source='role',
        write_only=True,
        required=False,
        allow_null=True
    )

    class Meta:
        model = CustomUser
        fields = (
            'id', 'username', 'email', 'full_name',
            'total_violation_count', 'phone_number',
            'work_id', 'major', 'student_class', 'gender', 'gender_display',
            'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login',
            'role', 'role_id'
        )
        read_only_fields = (
            'total_violation_count', 'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login', 'gender_display', 'role'
        )


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    用户注册序列化器，包含密码验证。
    注册时默认分配 '学生' 角色，不允许用户自行选择角色。
    """
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = CustomUser
        fields = (
            'username', 'email', 'phone_number', 'password', 'password2',
            'full_name',
            'work_id', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'username': {'required': True},
            'email': {'required': False, 'allow_blank': True},  # allow_blank=True means empty string is allowed input
            'phone_number': {'required': False, 'allow_blank': True},
            'work_id': {'required': True},
            'full_name': {'required': False, 'allow_blank': True},
            'major': {'required': False, 'allow_blank': True},
            'student_class': {'required': False, 'allow_blank': True},
            'gender': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "两次输入的密码不一致。"})

        temp_user = CustomUser(username=attrs['username'])
        try:
            validate_password(attrs['password'], user=temp_user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})

        # === 核心修改：将空字符串转换为 None for null=True 字段 ===
        for field_name in ['email', 'phone_number', 'full_name', 'major', 'student_class']:
            if attrs.get(field_name) == '':
                attrs[field_name] = None
        # =========================================================

        # Now, perform uniqueness checks with `None` values correctly
        if attrs.get('work_id') and CustomUser.objects.filter(work_id=attrs['work_id']).exists():
            raise serializers.ValidationError({"work_id": "该工号/学号已被注册。"})

        # 使用 `is not None` 进行检查
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

        # Default to '学生' role
        # 使用 get_or_create 确保角色存在
        student_role, created = Role.objects.get_or_create(name=ROLE_STUDENT)
        validated_data['role'] = student_role

        user = CustomUser.objects.create_user(
            password=password,
            **validated_data
        )
        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用于更新用户个人资料的序列化器。
    用户可以更新除敏感信息外的字段。
    """

    class Meta:
        model = CustomUser
        fields = (
            'full_name',
            'email', 'phone_number', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'email': {'required': False, 'allow_blank': True},
            'phone_number': {'required': False, 'allow_blank': True},
            'major': {'required': False, 'allow_blank': True},
            'student_class': {'required': False, 'allow_blank': True},
            'gender': {'required': False, 'allow_blank': True},
            'full_name': {'required': False, 'allow_blank': True},
        }

    # Custom validate methods already handle converting '' to None for their respective fields
    def validate_email(self, value):
        if value == '':
            return None
        if CustomUser.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("该邮箱已被其他用户使用。")
        return value

    def validate_phone_number(self, value):
        if value == '':
            return None
        if CustomUser.objects.exclude(id=self.instance.id).filter(phone_number=value).exists():
            raise serializers.ValidationError("该手机号已被其他用户使用。")
        return value

    def validate_full_name(self, value):
        if value == '':
            return None
        return value

    def validate_major(self, value):
        if value == '':
            return None
        return value

    def validate_student_class(self, value):
        if value == '':
            return None
        return value


class AdminUserUpdateSerializer(UserProfileUpdateSerializer):
    """
    管理员用于更新用户资料和角色的序列化器。
    继承自 UserProfileUpdateSerializer，并添加 role 字段。
    """
    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta(UserProfileUpdateSerializer.Meta):
        fields = UserProfileUpdateSerializer.Meta.fields + ('role',)