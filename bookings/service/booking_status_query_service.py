# bookings/service/booking_status_query_service.py
import logging
import uuid
from typing import Dict, Any, Union, Optional

from core.service.base import BaseService
from core.service.service_result import ServiceResult
from core.utils.exceptions import NotFoundException, ForbiddenException, BadRequestException, ServiceException

from users.models import CustomUser
from bookings.models import Booking as BookingModel # 使用别名，避免与 Service 类名冲突

logger = logging.getLogger(__name__)

class BookingStatusQueryService(BaseService):
    """
    提供查询预订当前状态的公共服务。
    允许通过 booking_id 或 request_uuid 查询。
    """
    _dao_map = {
        'booking_dao': 'booking',
    }

    def __init__(self):
        super().__init__()
        self.booking_dao = self._get_dao_instance('booking')

    def get_booking_status_info(self, user: CustomUser, track_id: Union[int, uuid.UUID]) -> ServiceResult[Dict[str, Any]]:
        """
        根据 Booking ID 或 Request UUID 查询预订的当前状态信息。

        :param user: 当前请求的用户。
        :param track_id: 可以是 Booking ID (int) 或 Request UUID (uuid.UUID)。
        :return: ServiceResult，成功时 data 包含预订的状态信息简化字典。
        """
        try:
            booking: Optional[BookingModel] = None
            if isinstance(track_id, int):
                booking = self.booking_dao.get_booking_by_id(track_id)
            elif isinstance(track_id, uuid.UUID) or (isinstance(track_id, str) and len(track_id) == 36):
                # 如果是字符串形式的 UUID，尝试转换
                try:
                    uuid_obj = uuid.UUID(str(track_id))
                    booking = self.booking_dao.get_booking_by_request_uuid(uuid_obj)
                except ValueError:
                    raise BadRequestException(detail="track_id 格式无效，既不是有效的 Booking ID 也不是有效的 UUID。", code="invalid_track_id_format")
            else:
                raise BadRequestException(detail="track_id 类型无效，必须是 int (Booking ID) 或 uuid.UUID/str (Request UUID)。", code="invalid_track_id_type")

            if not booking:
                raise NotFoundException(detail="找不到匹配的预订记录。", code="booking_not_found")

            # 权限检查：只有预订用户本人、或相关空间的管理员、或系统管理员可以查看
            if booking.user.pk != user.pk and \
               not user.is_system_admin and \
               not (user.is_space_manager and booking.related_space and booking.related_space.managed_by == user):
                raise ForbiddenException(detail="您没有权限查看此预订记录的状态。", code="unauthorized_to_view_status")

            # 返回简化的状态信息字典
            status_info = {
                'id': booking.pk,
                'request_uuid': str(booking.request_uuid),
                'processing_status': booking.processing_status,
                'processing_status_display': booking.get_processing_status_display(),
                'status': booking.status,
                'status_display': booking.get_status_display(),
                'admin_notes': booking.admin_notes,
                'created_at': booking.created_at.isoformat(),
                'updated_at': booking.updated_at.isoformat(),
            }
            return ServiceResult.success_result(data=status_info, message="成功获取预订状态。")

        except ServiceException as e: # 捕获自定义 ServiceException
             return self._handle_exception(e)
        except NotFoundException as e:
            return self._handle_exception(e)
        except ForbiddenException as e:
            return self._handle_exception(e)
        except BadRequestException as e:
            return self._handle_exception(e)
        except Exception as e:
            logger.exception(f"查询预订状态失败 (User: {user.pk}, Track ID: {track_id}): {e}")
            return self._handle_exception(e, default_message="查询预订状态失败。")