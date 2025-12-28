# my_project/utils/admin_permissions.py

# 这个模块定义了用于 Django Admin 权限检查的辅助函数。
# 它直接利用 CustomUser 模型上已经定义好的 is_super_admin, is_admin, is_space_manager 属性。

def has_global_admin_privileges(user):
    """
    检查用户是否拥有全局系统管理权限。
    这包括超级管理员和系统管理员。
    适用于 SpaceTypeAdmin, AmenityAdmin 等全局配置模块。
    """
    return user.is_authenticated and (user.is_super_admin or user.is_admin)

def has_space_management_privileges(user):
    """
    检查用户是否拥有空间管理权限。
    这包括超级管理员、系统管理员以及空间管理员。
    适用于 SpaceAdmin, BookableAmenityInline。
    """
    return user.is_authenticated and (user.is_super_admin or user.is_admin or user.is_space_manager)

def has_booking_management_privileges(user):
    """
    检查用户是否拥有预订管理权限。
    这包括超级管理员、系统管理员以及空间管理员。
    适用于 BookingAdmin。
    """
    return user.is_authenticated and (user.is_super_admin or user.is_admin or user.is_space_manager)

def has_violation_management_privileges(user):
    """
    检查用户是否拥有违约记录管理权限。
    通常只有超级管理员和系统管理员才有权处理违约。
    适用于 ViolationAdmin。
    """
    return user.is_authenticated and (user.is_super_admin or user.is_admin)