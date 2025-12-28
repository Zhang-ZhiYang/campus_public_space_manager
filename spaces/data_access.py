# spaces/data_access.py
from typing import List, Optional, Dict, Any
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.core.exceptions import ValidationError  # 导入Django的ValidationError

from spaces.models import Amenity, Space
from core.utils.exceptions import NotFoundException, ConflictException, CustomAPIException, BadRequestException


class AmenityDataAccess:
    """
    负责设施（Amenity）模型的数据库交互。
    """

    @staticmethod
    def get_amenity_by_id(amenity_id: int) -> Amenity:
        """根据ID获取设施"""
        try:
            return Amenity.objects.get(id=amenity_id)
        except Amenity.DoesNotExist:
            raise NotFoundException(detail=f"设施 ID {amenity_id} 未找到。")

    @staticmethod
    def list_amenities(**filters) -> QuerySet[Amenity]:
        """列出所有设施，可带过滤条件"""
        return Amenity.objects.filter(**filters).order_by('name')

    @staticmethod
    def create_amenity(name: str, description: str = '') -> Amenity:
        """创建新设施"""
        try:
            return Amenity.objects.create(name=name, description=description)
        except IntegrityError:  # 捕获唯一性约束错误
            raise ConflictException(detail=f"设施 '{name}' 已存在。")
        except Exception as e:
            raise CustomAPIException(detail=f"创建设施失败: {str(e)}")

    @staticmethod
    def update_amenity(amenity: Amenity, name: Optional[str] = None, description: Optional[str] = None) -> Amenity:
        """更新设施信息"""
        if name is not None:
            amenity.name = name
        if description is not None:
            amenity.description = description
        try:
            amenity.save()  # 调用 save 会触发模型的 clean 方法
            return amenity
        except IntegrityError:
            raise ConflictException(detail=f"设施名称 '{name}' 已存在。")
        except ValidationError as e:  # 捕获模型 clean() 或字段验证的错误
            raise BadRequestException(
                detail=f"数据验证失败: {e.message_dict if hasattr(e, 'message_dict') else str(e)}")
        except Exception as e:
            raise CustomAPIException(detail=f"更新设施失败: {str(e)}")

    @staticmethod
    def delete_amenity(amenity: Amenity) -> None:
        """删除设施"""
        try:
            amenity.delete()
        except IntegrityError:
            raise ConflictException(detail=f"设施 '{amenity.name}' 正在被某些空间使用，无法删除。")
        except Exception as e:
            raise CustomAPIException(detail=f"删除设施失败: {str(e)}")


class SpaceDataAccess:
    """
    负责空间（Space）模型的数据库交互。
    """

    @staticmethod
    def get_space_by_id(space_id: int) -> Space:
        """根据ID获取空间，并预加载设施"""
        try:
            return Space.objects.prefetch_related('amenities').get(id=space_id)
        except Space.DoesNotExist:
            raise NotFoundException(detail=f"空间 ID {space_id} 未找到。")

    @staticmethod
    def list_spaces(**filters) -> QuerySet[Space]:
        """列出所有空间，可带过滤条件，并预加载设施"""
        return Space.objects.filter(**filters).prefetch_related('amenities').order_by('name')

    @staticmethod
    def create_space(data: Dict[str, Any], amenity_ids: List[int]) -> Space:
        """创建新空间并关联设施"""
        try:
            with transaction.atomic():
                space = Space.objects.create(**data)  # 创建空间时会触发模型save方法及其中的clean()
                if amenity_ids:
                    # 批量获取设施，检查是否存在缺失ID
                    amenities = Amenity.objects.filter(id__in=amenity_ids)
                    if len(amenity_ids) != amenities.count():
                        found_ids = set(amenity.id for amenity in amenities)
                        missing_ids = set(amenity_ids) - found_ids
                        raise NotFoundException(detail=f"部分设施ID未找到: {list(missing_ids)}")
                    space.amenities.set(amenities)
                return space
        except IntegrityError:  # 捕获唯一性约束错误 (如名称重复)
            raise ConflictException(detail=f"空间名称 '{data.get('name', '')}' 已存在。")
        except ValidationError as e:  # 捕获模型 clean() 或字段验证的错误
            raise BadRequestException(
                detail=f"数据验证失败: {e.message_dict if hasattr(e, 'message_dict') else str(e)}")
        except Exception as e:
            raise CustomAPIException(detail=f"创建空间失败: {str(e)}")

    @staticmethod
    def update_space(space: Space, data: Dict[str, Any], amenity_ids: Optional[List[int]] = None) -> Space:
        """更新空间信息及关联设施"""
        try:
            with transaction.atomic():
                for attr, value in data.items():
                    setattr(space, attr, value)

                # space.save() 调用会触发模型的 clean() 和 full_clean()
                # 并且在_save()中已经处理了 is_active => is_bookable 的逻辑
                space.save()

                if amenity_ids is not None:  # 仅当 amenity_ids 明确提供时才更新关联设施
                    amenities = Amenity.objects.filter(id__in=amenity_ids)
                    if len(amenity_ids) != amenities.count():
                        found_ids = set(amenity.id for amenity in amenities)
                        missing_ids = set(amenity_ids) - found_ids
                        raise NotFoundException(detail=f"部分设施ID未找到: {list(missing_ids)}")
                    space.amenities.set(amenities)
            return space
        except IntegrityError:
            name_to_check = data.get('name', space.name)
            raise ConflictException(detail=f"空间名称 '{name_to_check}' 已存在。")
        except ValidationError as e:  # 捕获模型 clean() 或字段验证的错误
            raise BadRequestException(
                detail=f"数据验证失败: {e.message_dict if hasattr(e, 'message_dict') else str(e)}")
        except Exception as e:
            raise CustomAPIException(detail=f"更新空间失败: {str(e)}")

    @staticmethod
    def delete_space(space: Space) -> None:
        """删除空间"""
        try:
            space.delete()
        except IntegrityError:  # Can happen if other models (e.g., Booking) have a protected ForeignKey
            raise ConflictException(detail="无法删除空间，它被其他记录引用（如预订记录）。")
        except Exception as e:
            raise CustomAPIException(detail=f"删除空间失败: {str(e)}")