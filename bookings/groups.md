SysAdmin (系统管理员):
角色识别： is_superuser 或 CustomUser 属于 "系统管理员" 用户组。
权限分配：
系统管理员应该拥有所有全局权限 (例如 bookings.can_view_all_bookings, bookings.can_approve_any_booking, bookings.can_create_violation 等)。这些权限可以直接通过将 "系统管理员" 组分配给 Django Permission 对象来授予。
对于所有模型，系统管理员通常也应具有所有对象级权限。django-guardian 默认情况下不会自动为 superuser 提供所有对象级权限，你需要在检查时：
在 Service 层进行权限检查时，如果 user.is_superuser 或 user.is_system_admin 为 True，则直接绕过对象级权限检查。
或者，可以通过代码为 is_system_admin 组的成员分配对所有 现有 对象的相应对象级权限，但这可能消耗大量资源且不灵活。
推荐做法： 在 Service 层执行对象级 user.has_perm() 检查时，始终优先检查 user.is_superuser 或 user.is_system_admin。如果为真，则直接授予权限。这已经在你的代码中有所体现 (if user.is_superuser or user.is_system_admin: return True)。
SysAdmin 应该有权限创建、更新、删除 SpaceType, Amenity, DailyBookingLimit, SpaceTypeBanPolicy 等系统级配置。这些操作在你的 Views 中已使用 @is_system_admin_required 拦截，并由 Service 层执行简单 CRUD，符合要求。
SpaceMan (空间管理员):
角色识别： CustomUser 属于 "空间管理员" 用户组。
权限分配：
全局权限： space_man 通常不应拥有所有全局 can_approve_any_booking 权限。他们应该只拥有对其管理的空间的预订审批权限。
但是，你的 BookingService.update_booking_status 方法中，if new_status in ['APPROVED', 'REJECTED'] and not (user.has_perm('bookings.can_approve_booking') or (...)) 这里的 bookings.can_approve_booking 是一个全局权限。这可能需要重新思考。如果 space_man 可以审批所有预订，那么这个全局权限是对的。如果只能审批自己管理的空间的预订，那么 user.has_perm('bookings.can_approve_booking') 应该移除，只保留 (target_space and user.has_perm('spaces.can_manage_space_bookings', target_space))。
同样的道理也适用于 can_check_in_booking。
对象级权限： 这是 space_man 的核心。
自动分配（post_save 信号）： 当 space_man 创建一个空间时，自动为其分配新的细粒度权限：can_edit_space_info、can_change_space_status、can_configure_booking_rules、can_add_space_amenity、can_view_space_bookings、can_approve_space_bookings、can_checkin_space_bookings、can_cancel_space_bookings 等。
手动分配/委托管理（高权限用户进行）： 系统管理员可以为 space_man 分配对任意现有空间的这些细粒度对象级权限。
管理 Amenity Types, Space Types： 通常 space_man 不直接创建/修改/删除全局的 AmenityType 和 SpaceType。这些操作应该只由 sys_admin 负责。你的 Views 已通过 @is_system_admin_required 确保了这一点，这很好。