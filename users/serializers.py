from rest_framework import serializers
from .models import CustomUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError


class CustomUserSerializer(serializers.ModelSerializer):
    """
    用户模型通用序列化器，用于返回用户详情。
    """
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)

    class Meta:
        model = CustomUser
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name',
            'total_violation_count', 'phone_number',
            'student_id', 'major', 'student_class', 'gender', 'gender_display',
            'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login'
        )
        read_only_fields = (
            'total_violation_count', 'is_active', 'is_staff', 'is_superuser',
            'date_joined', 'last_login', 'gender_display'
        )


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    用户注册序列化器，包含密码验证。
    """
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = CustomUser
        fields = (
            'username', 'email', 'phone_number', 'password', 'password2',
            'first_name', 'last_name',
            'student_id', 'major', 'student_class', 'gender'
        )
        extra_kwargs = {
            'username': {'required': True},
            'email': {'required': True},
            'phone_number': {'required': True},
            'student_id': {'required': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})

        # 创建一个临时用户实例来验证密码强度，避免将 'password' 或 'password2' 传递给 CustomUser 构造函数。
        temp_user_data = {k: v for k, v in attrs.items() if
                          k not in ['password', 'password2', 'email', 'username', 'gender', 'phone_number',
                                    'student_id']}

        # 即使这里传入的 CustomUser 实例不完全， validate_password 也能检查密码本身的强度
        try:
            validate_password(attrs['password'], user=CustomUser(**temp_user_data))
        except DjangoValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})

        # 唯一性验证 (ModelSerializer 通常会处理 unique=True 的字段)
        # 这里手动添加是为了即时反馈和明确控制
        # `self.instance` 在 create 操作中不存在，因此不需要 `exclude(pk=self.instance.pk)`
        if CustomUser.objects.filter(student_id=attrs.get('student_id')).exists():
            raise serializers.ValidationError({"student_id": "Student ID already exists."})

        if CustomUser.objects.filter(phone_number=attrs.get('phone_number')).exists():
            raise serializers.ValidationError({"phone_number": "Phone number already exists."})

        if CustomUser.objects.filter(email=attrs.get('email')).exists():
            raise serializers.ValidationError({"email": "Email already exists."})

        return attrs

    def create(self, validated_data):
        # 1. 明确取出并移除 password 和 password2
        password = validated_data.pop('password')
        password2 = validated_data.pop('password2')  # 确保 password2 被移除

        # 2. 明确取出并移除 username 和 email，因为 create_user 期望它们作为具名参数
        username = validated_data.pop('username')
        email = validated_data.pop('email')

        # 3. 现在 validated_data 中只剩下 CustomUser 模型的额外字段
        #    这些字段可以安全地通过 **validated_data 传递给 create_user 的 **extra_fields
        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password=password,
            **validated_data
            # 传递所有剩余的字段 (first_name, last_name, phone_number, student_id, major, student_class, gender)
        )
        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    用于更新用户个人资料的序列化器。
    用户可以更新除敏感信息外的字段 (学号通常不可修改)。
    """

    class Meta:
        model = CustomUser
        fields = (
            'first_name', 'last_name', 'email', 'phone_number',
            'major', 'student_class', 'gender'
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