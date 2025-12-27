# users/serializers.py
from rest_framework import serializers
from .models import CustomUser, Role, ROLE_STUDENT # 导入 Role 模型和角色常量
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

class RoleSerializer(serializers.ModelSerializer):
    """
    角色序列化器，用于在用户详情中展示角色信息。
    """
    class Meta:
        model = Role
        fields = ('id', 'name', 'description')
        read_only_fields = ('id', 'name', 'description') # 角色通常由管理员独立管理，不通过用户接口修改

class CustomUserSerializer(serializers.ModelSerializer):
    """
    用户模型通用序列化器，用于返回用户详情。
    """
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)
    role = RoleSerializer(read_only=True) # 将 role 字段嵌入式地展示为 RoleSerializer
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), 
        source='role', 
        write_only=True, 
        required=False,
        allow_null=True # 允许在更新时设置为空
    ) # 用于更新时通过 ID 传递角色

    class Meta:
        model = CustomUser
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name',
            'total_violation_count', 'phone_number',
            'work_id', 'major', 'student_class', 'gender', 'gender_display',
            'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login',
            'role', 'role_id' # 添加 role 字段 (read-only) 和 role_id (write-only)
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
            'first_name', 'last_name',
            'work_id', 'major', 'student_class', 'gender'
            # 注册时不再包含 'role' 字段，将在 create 方法中默认分配
        )
        extra_kwargs = {
            'username': {'required': True},
            'email': {'required': True},
            'phone_number': {'required': True},
            'work_id': {'required': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})

        temp_user_data = {k: v for k, v in attrs.items() if k not in ['password', 'password2', 'email', 'username', 'gender', 'phone_number', 'work_id']}

        try:
            validate_password(attrs['password'], user=CustomUser(**temp_user_data))
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})

        if CustomUser.objects.filter(work_id=attrs.get('work_id')).exists():
            raise serializers.ValidationError({"work_id": "Student ID already exists."})
        if CustomUser.objects.filter(phone_number=attrs.get('phone_number')).exists():
            raise serializers.ValidationError({"phone_number": "Phone number already exists."})
        if CustomUser.objects.filter(email=attrs.get('email')).exists():
            raise serializers.ValidationError({"email": "Email already exists."})

        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data.pop('password2')

        username = validated_data.pop('username')
        email = validated_data.pop('email')

        # 默认分配 '学生' 角色
        student_role, created = Role.objects.get_or_create(name=ROLE_STUDENT)
        validated_data['role'] = student_role

        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=password,
            **validated_data
        )
        return user

class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用于更新用户个人资料的序列化器。
    用户可以更新除敏感信息外的字段。
    """
    # 用户一般不能自己修改角色，但管理员可以。这个序列化器是给普通用户更新自己的 Profile 用的。
    # 如果要为管理员提供一个更新用户角色的接口，则需要一个单独的 AdminUserUpdateSerializer
    class Meta:
        model = CustomUser
        fields = (
            'first_name', 'last_name', 'email', 'phone_number',
            'major', 'student_class', 'gender'
            # 允许用户更新的字段不包含 role
        )
        extra_kwargs = {
            'email': {'required': False},
            'phone_number': {'required': False},
            'major': {'required': False},
            'student_class': {'required': False},
            'gender': {'required': False},
        }

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This email is already in use by another account.")
        return value

    def validate_phone_number(self, value):
        if CustomUser.objects.filter(phone_number=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("This phone number is already in use by another account.")
        return value

# 新增：用于管理员更新用户信息的序列化器（包含角色修改）
class AdminUserUpdateSerializer(UserProfileUpdateSerializer):
    """
    管理员用于更新用户资料和角色的序列化器。
    继承自 UserProfileUpdateSerializer，并添加 role 字段。
    """
    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(),
        required=False, # 允许不更新角色
        allow_null=True # 允许设置角色为空
    )

    class Meta(UserProfileUpdateSerializer.Meta):
        fields = UserProfileUpdateSerializer.Meta.fields + ('role',)