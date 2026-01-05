# users/serializers.py
from rest_framework import serializers
from .models import CustomUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.models import Group  # 导入 Group


# ====================================================================
# RoleSerializer 已删除
# ====================================================================

class CustomUserSerializer(serializers.ModelSerializer):
    """
    用户模型通用序列化器，用于返回用户详情。
    移除了 role 相关字段，增加了 groups 字段以显示所属组。
    """
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)
    # 移除了 role 和 role_id 字段

    # 显示用户所属的所有组的名称
    groups = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field='name'
    )

    class Meta:
        model = CustomUser
        fields = (
            'id', 'username', 'email', 'get_full_name',  # 使用 property
            'phone_number',
            'work_id', 'major', 'student_class', 'gender', 'gender_display',
            'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login',
            'groups'  # 添加 groups 字段
        )
        read_only_fields = (
            'get_full_name', 'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login', 'gender_display', 'groups'
        )


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    用户注册序列化器，包含密码验证。
    注册时默认将用户添加到 '学生' Groups，不允许用户自行选择角色（组）。
    """
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = CustomUser
        fields = (
            'username', 'email', 'phone_number', 'password', 'password2',
            'first_name', 'last_name',  # 调整为使用 first_name/last_name 而不是 full_name
            'work_id', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'username': {'required': True},
            'email': {'required': False, 'allow_blank': True},
            'phone_number': {'required': False, 'allow_blank': True},
            'work_id': {'required': True},
            'first_name': {'required': False, 'allow_blank': True},
            'last_name': {'required': False, 'allow_blank': True},
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

        for field_name in ['email', 'phone_number', 'first_name', 'last_name', 'major', 'student_class']:
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

        # 默认将新注册用户添加到 '学生' Group
        student_group, created = Group.objects.get_or_create(name='学生')
        user.groups.add(student_group)

        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用于更新用户个人资料的序列化器。
    用户可以更新除敏感信息外的字段。
    """

    class Meta:
        model = CustomUser
        fields = (
            'first_name', 'last_name',  # 调整为使用 first_name/last_name
            'email', 'phone_number', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'email': {'required': False, 'allow_blank': True},
            'phone_number': {'required': False, 'allow_blank': True},
            'major': {'required': False, 'allow_blank': True},
            'student_class': {'required': False, 'allow_blank': True},
            'gender': {'required': False, 'allow_blank': True},
            'first_name': {'required': False, 'allow_blank': True},
            'last_name': {'required': False, 'allow_blank': True},
        }

    def validate_email(self, value):
        if value == '':
            return None
        if CustomUser.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("该邮箱已被其他用户使用。")
        return value

    def validate_phone_number(self, value):
        if value == '':
            return None
        if CustomUser.objects.exclude(pk=self.instance.pk).filter(phone_number=value).exists():
            raise serializers.ValidationError("该手机号已被其他用户使用。")
        return value

    def validate_first_name(self, value):
        if value == '':
            return None
        return value

    def validate_last_name(self, value):
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
    管理员用于更新用户资料和组（角色）的序列化器。
    继承自 UserProfileUpdateSerializer，并添加了 groups 字段。
    """
    # 允许管理员通过 ID 列表更新用户的组
    groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        many=True,  # 用户可以属于多个组
        required=False  # 更新时 groups 字段不是必需的
    )

    class Meta(UserProfileUpdateSerializer.Meta):
        fields = UserProfileUpdateSerializer.Meta.fields + (
        'groups', 'is_active', 'is_staff', 'is_superuser')  # 管理员可以修改 is_active, is_staff, is_superuser

    def update(self, instance, validated_data):
        # 先处理 groups，因为 groups 是 ManyToMany 字段，需要特殊处理
        groups_data = validated_data.pop('groups', None)

        # 调用父类的 update 方法处理其他字段
        instance = super().update(instance, validated_data)

        # 处理 groups
        if groups_data is not None:
            instance.groups.set(groups_data)  # set() 方法会删除旧的，添加新的

        return instance