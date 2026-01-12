# spaces/serializers.py
from rest_framework import serializers
import datetime  # 导入 datetime 模块，用于处理 TimeField 和 DurationField
from django.contrib.auth import get_user_model  # 导入 get_user_model
from django.contrib.auth.models import Group

# 从 bookings.api.serializers 导入，避免在 core 层次创建新的文件，保持一致性
from bookings.api.serializers import UserSerializerMinimal

from spaces.models import Amenity, Space, SpaceType, BookableAmenity

# from core.utils.constants import MSG_BAD_REQUEST # 暂时注释，如果需要可以取消

# 获取 CustomUser 模型
CustomUser = get_user_model()


class SpaceTypeSerializerMinimal(serializers.ModelSerializer):
    space_type_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = SpaceType
        fields = ('space_type_id', 'name')
        read_only_fields = ('space_type_id', 'name')


class AmenitySerializer(serializers.ModelSerializer):
    """
    设施序列化器，用于在Space详情中嵌套显示和设施的CRUD操作。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']
        read_only_fields = ['id']


class BookableAmenitySerializer(serializers.ModelSerializer):
    """
    可预订设施实例的序列化器，用于 Space 详情页的嵌套显示。
    """
    amenity = AmenitySerializer(read_only=True)

    class Meta:
        model = BookableAmenity
        fields = ['id', 'amenity', 'quantity', 'is_bookable', 'is_active']
        read_only_fields = ['id']


class SpaceBaseSerializer(serializers.ModelSerializer):
    """
    空间基础序列化器，包含所有字段，并新增了**有效预订规则**的计算字段。
    """
    space_type = SpaceTypeSerializerMinimal(read_only=True)
    managed_by = UserSerializerMinimal(read_only=True)
    bookable_amenities = BookableAmenitySerializer(many=True, read_only=True)
    permitted_groups_display = serializers.SerializerMethodField()

    # ====== 新增的有效预订规则字段 ======
    effective_requires_approval = serializers.SerializerMethodField()
    effective_available_start_time = serializers.SerializerMethodField()
    effective_available_end_time = serializers.SerializerMethodField()
    effective_min_booking_duration = serializers.SerializerMethodField()
    effective_max_booking_duration = serializers.SerializerMethodField()
    effective_buffer_time_minutes = serializers.SerializerMethodField()

    # ====================================

    class Meta:
        model = Space
        fields = '__all__'  # 使用 __all__ 会包含所有模型字段及 SerializerMethodField
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_permitted_groups_display(self, obj: Space) -> str:
        """
        根据 permitted_groups 和 space_type.is_basic_infrastructure 综合判断显示文本。
        """
        if obj.permitted_groups.exists():
            return ", ".join([group.name for group in obj.permitted_groups.all()])

        # 如果没有指定用户组，且是基础型基础设施，则认为是“所有人”可预订
        if obj.space_type and obj.space_type.is_basic_infrastructure:
            return "所有人"  # 意味着所有认证用户默认可预订/访问

        # 如果没有指定用户组，且非基础型基础设施，则表示仅限管理员/空间管理者访问
        return "无特定限制 (需权限)"  # 非管理员用户可能需要特定权限才能访问

    # --- 有效预订规则的 SerializerMethodField 实现 ---

    def get_effective_requires_approval(self, obj: Space) -> bool:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值，最后默认 False
        if obj.requires_approval is not None:
            return obj.requires_approval
        if obj.space_type and obj.space_type.default_requires_approval is not None:
            return obj.space_type.default_requires_approval
        return False  # 默认不需审批

    def get_effective_available_start_time(self, obj: Space) -> str | None:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值
        time_obj = obj.available_start_time or \
                   (obj.space_type.default_available_start_time if obj.space_type else None)
        return time_obj.strftime('%H:%M:%S') if time_obj else None

    def get_effective_available_end_time(self, obj: Space) -> str | None:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值
        time_obj = obj.available_end_time or \
                   (obj.space_type.default_available_end_time if obj.space_type else None)
        return time_obj.strftime('%H:%M:%S') if time_obj else None

    def get_effective_min_booking_duration(self, obj: Space) -> str | None:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值
        duration_obj = obj.min_booking_duration or \
                       (obj.space_type.default_min_booking_duration if obj.space_type else None)
        return str(duration_obj) if duration_obj else None

    def get_effective_max_booking_duration(self, obj: Space) -> str | None:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值
        duration_obj = obj.max_booking_duration or \
                       (obj.space_type.default_max_booking_duration if obj.space_type else None)
        return str(duration_obj) if duration_obj else None

    def get_effective_buffer_time_minutes(self, obj: Space) -> int | None:
        # 如果空间本身设置了值，则使用空间的值，否则使用空间类型默认值，最后默认 0
        if obj.buffer_time_minutes is not None:
            return obj.buffer_time_minutes
        if obj.space_type and obj.space_type.default_buffer_time_minutes is not None:
            return obj.space_type.default_buffer_time_minutes
        return 0  # 默认没有缓冲时间


class SpaceListSerializer(SpaceBaseSerializer):
    """
    空间列表序列化器。
    它继承了 SpaceBaseSerializer，因此会自动包含所有模型字段及 `effective_` 字段。
    这里的 Meta.fields 应该明确列出你想在列表视图中展示的字段。
    """

    class Meta(SpaceBaseSerializer.Meta):
        # 显式列出列表视图所需的字段，包括所有 effective_ 字段
        fields = [
            'id', 'name', 'location', 'capacity', 'image', 'description',
            'is_active', 'is_bookable', 'is_container',
            'space_type', 'managed_by',
            'permitted_groups_display',
            'effective_requires_approval',
            'effective_available_start_time',
            'effective_available_end_time',
            'effective_min_booking_duration',
            'effective_max_booking_duration',
            'effective_buffer_time_minutes',
            'bookable_amenities',  # 通常在列表只显示少量信息，但根据你提供的json这里保留
        ]
        # read_only_fields 应保持为只读的，但这些计算字段本身都是只读的，故不对这里做修改
        # 它们在 SpaceBaseSerializer.Meta 中已经从 read_only_fields 中被移除了，因此这里不需要重复
        # 如果需要更严格的控制，可以在此添加新的 read_only_fields


class SpaceCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间创建和更新序列化器。
    """
    space_type_id = serializers.PrimaryKeyRelatedField(
        queryset=SpaceType.objects.all(), source='space_type', write_only=True, required=False, allow_null=True,
        help_text="空间类型的ID，例如：1"
    )
    parent_space_id = serializers.PrimaryKeyRelatedField(
        queryset=Space.objects.all(), source='parent_space', write_only=True, required=False, allow_null=True,
        help_text="父级空间的ID，例如：2"
    )
    managed_by_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(), source='managed_by', write_only=True, required=False, allow_null=True,
        help_text="主要管理人员的ID，例如：3"
    )

    amenity_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="以整数列表形式传入设施ID, 例如: [1, 2, 3]"
    )

    permitted_groups = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), many=True,
        required=False,
        help_text="可预订用户组的ID列表，例如: [1, 2]"
    )

    class Meta:
        model = Space
        fields = [
            'id', 'name', 'location', 'description', 'capacity',
            'is_bookable', 'is_active', 'is_container', 'requires_approval', 'image',
            'available_start_time', 'available_end_time',
            'min_booking_duration', 'max_booking_duration', 'buffer_time_minutes',
            'space_type_id', 'parent_space_id', 'managed_by_id', 'permitted_groups',
            'amenity_ids'
        ]
        read_only_fields = ('id',)
        extra_kwargs = {
            'min_booking_duration': {'allow_null': True},
            'max_booking_duration': {'allow_null': True},
            'buffer_time_minutes': {'allow_null': True},
            'image': {'required': False, 'allow_null': True}
        }

    def validate(self, data):
        """
        序列化器层面的自定义验证。
        """
        instance = self.instance

        # 1. 时间字段一致性验证
        start_time_new = data.get('available_start_time', instance.available_start_time if instance else None)
        end_time_new = data.get('available_end_time', instance.available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'available_end_time': '每日最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_time_range'
            )

        # 2. is_active, is_bookable, is_container 业务规则验证
        is_active_new = data.get('is_active', instance.is_active if instance else True)
        is_bookable_new = data.get('is_bookable', instance.is_bookable if instance else True)
        is_container_new = data.get('is_container', instance.is_container if instance else False)

        if not is_active_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '不活跃的空间不能设置为可预订。'},
                code='inactive_space_not_bookable'
            )

        if is_container_new and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': '容器空间通常不直接预订，请设置 is_bookable 为 False。'},
                code='container_space_not_bookable'
            )

        # 3. SpaceType.default_is_bookable 与 Space.is_bookable 的一致性
        space_type_new = data.get('space_type', instance.space_type if instance else None)
        if space_type_new and not space_type_new.default_is_bookable and is_bookable_new:
            raise serializers.ValidationError(
                {'is_bookable': f"所属空间类型 '{space_type_new.name}' 默认不可预订，此空间不能设置为可预订。"},
                code='space_type_not_bookable_conflict'
            )

        # 4. 父级空间不能是自身
        parent_space_new = data.get('parent_space', instance.parent_space if instance else None)
        if parent_space_new and instance and parent_space_new == instance:
            raise serializers.ValidationError(
                {'parent_space_id': '空间不能将自身设置为父级空间。'},
                code='self_parent_space'
            )

        # 5. 确保 is_bookable_individually 为 False 的 Amenity 不会尝试单独预订
        amenity_ids = data.get('amenity_ids', None)
        # 在更新时，如果 amenity_ids 没有传入，则不进行验证
        if amenity_ids is not None:
            non_bookable_amenities = Amenity.objects.filter(id__in=amenity_ids, is_bookable_individually=False)
            if non_bookable_amenities.exists():
                names = ", ".join([a.name for a in non_bookable_amenities])
                raise serializers.ValidationError(
                    {'amenity_ids': f'以下设施类型不可单独预订，不能作为可预订设施实例添加到空间中: {names}'},
                    code='non_bookable_amenity_added'
                )

        return data

    def create(self, validated_data):
        amenity_ids = validated_data.pop('amenity_ids', [])
        permitted_groups = validated_data.pop('permitted_groups', [])

        instance = super().create(validated_data)
        instance.permitted_groups.set(permitted_groups)

        # 处理 amenity_ids，确保生成 BookableAmenity 实例
        for amenity_id in amenity_ids:
            try:
                amenity = Amenity.objects.get(id=amenity_id)
                # 默认数量为1，活跃和可预订状态与 amenities.is_bookable_individually 关联 (虽然模型save会处理)
                BookableAmenity.objects.create(space=instance, amenity=amenity, quantity=1,
                                               is_bookable=amenity.is_bookable_individually,  # 初始设置，模型保存时会再次校验
                                               is_active=True)
            except Amenity.DoesNotExist:
                # 可以在这里记录警告，或者抛出 ValidationError，取决于业务需求
                pass  # 忽略不存在的 amenity_id，或者在此处抛出错误

        return instance

    def update(self, instance, validated_data):
        amenity_ids = validated_data.pop('amenity_ids', None)
        permitted_groups = validated_data.pop('permitted_groups', None)

        instance = super().update(instance, validated_data)

        if permitted_groups is not None:
            instance.permitted_groups.set(permitted_groups)

        # 处理 amenity_ids 的更新逻辑（添加或移除 BookableAmenity）
        if amenity_ids is not None:
            current_amenity_ids = set(instance.bookable_amenities.values_list('amenity__id', flat=True))
            new_amenity_ids = set(amenity_ids)

            # 需要删除的设施实例
            amenities_to_remove = current_amenity_ids - new_amenity_ids
            instance.bookable_amenities.filter(amenity__id__in=amenities_to_remove).delete()

            # 需要添加的设施实例
            amenities_to_add = new_amenity_ids - current_amenity_ids
            for amenity_id in amenities_to_add:
                try:
                    amenity = Amenity.objects.get(id=amenity_id)
                    BookableAmenity.objects.create(space=instance, amenity=amenity, quantity=1,
                                                   is_bookable=amenity.is_bookable_individually,
                                                   is_active=True)
                except Amenity.DoesNotExist:
                    pass  # 忽略不存在的 amenity_id

        return instance


# --------- Amenity Type Serializers ---------

class AmenityBaseSerializer(serializers.ModelSerializer):
    """
    设施类型（Amenity）的基础序列化器。
    """

    class Meta:
        model = Amenity
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class AmenityCreateUpdateSerializer(serializers.ModelSerializer):
    """
    设施类型创建和更新序列化器。
    """

    class Meta:
        model = Amenity
        fields = ['id', 'name', 'description', 'is_bookable_individually']
        read_only_fields = ['id']

    def validate(self, data):
        return data


# --------- Space Type Serializers ---------

class SpaceTypeBaseSerializer(serializers.ModelSerializer):
    """
    空间类型（SpaceType）的基础序列化器，包含所有字段。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'  # 字段已在模型中移除，此处无需特别处理
        read_only_fields = ('id', 'created_at', 'updated_at')


class SpaceTypeCreateUpdateSerializer(serializers.ModelSerializer):
    """
    空间类型创建和更新序列化器。
    """

    class Meta:
        model = SpaceType
        fields = '__all__'  # 字段已在模型中移除，此处无需特别处理
        read_only_fields = ('id',)
        extra_kwargs = {
            'default_available_start_time': {'allow_null': True},
            'default_available_end_time': {'allow_null': True},
            'default_min_booking_duration': {'allow_null': True},
            'default_max_booking_duration': {'allow_null': True},
            'default_buffer_time_minutes': {'allow_null': True},
        }

    def validate(self, data):
        instance = self.instance

        start_time_new = data.get('default_available_start_time',
                                  instance.default_available_start_time if instance else None)
        end_time_new = data.get('default_available_end_time', instance.default_available_end_time if instance else None)

        if start_time_new and end_time_new and start_time_new >= end_time_new:
            raise serializers.ValidationError(
                {'default_available_end_time': '默认最晚可预订时间必须晚于最早可预订时间。'},
                code='invalid_default_time_range'
            )
        return data