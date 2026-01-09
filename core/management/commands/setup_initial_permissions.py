# your_app/management/commands/setup_initial_permissions.py (最终修复版 - 修正权限分配逻辑)
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from guardian.shortcuts import assign_perm
from users.models import CustomUser
from spaces.models import Space, SpaceType, Amenity, BookableAmenity
from bookings.models import (
    Booking, Violation, DailyBookingLimit, SpaceTypeBanPolicy, UserSpaceTypeBan,
    UserSpaceTypeExemption, UserPenaltyPointsPerSpaceType
)
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sets up initial Django Groups and assigns default global/model-level permissions.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Setting up initial groups and permissions...'))

        sys_admin_group, created_sa = Group.objects.get_or_create(name='系统管理员')
        space_manager_group, created_sm = Group.objects.get_or_create(name='空间管理员')
        teacher_group, created_t = Group.objects.get_or_create(name='教师')
        student_group, created_stu = Group.objects.get_or_create(name='学生')

        if created_sa: self.stdout.write(self.style.SUCCESS(f'Created group: {sys_admin_group.name}'))
        if created_sm: self.stdout.write(self.style.SUCCESS(f'Created group: {space_manager_group.name}'))
        if created_t: self.stdout.write(self.style.SUCCESS(f'Created group: {teacher_group.name}'))
        if created_stu: self.stdout.write(self.style.SUCCESS(f'Created group: {student_group.name}'))

        self.stdout.write(self.style.HTTP_INFO('Assigning permissions to groups...'))

        # --- 系统管理员 (SysAdmin) 组权限 ---
        # 针对每个模型，列出 SysAdmin 应该拥有的自定义权限
        # Django 默认的 four-permissions (add, change, delete, view) 会在循环中自动添加
        sysadmin_custom_permissions = {
            Space: [
                "can_view_space", "can_create_space", "can_edit_space_info", "can_change_space_status",
                "can_configure_booking_rules", "can_assign_space_manager", "can_manage_permitted_groups",
                "can_add_space_amenity", "can_delete_space", "can_view_space_bookings",
                "can_approve_space_bookings", "can_checkin_space_bookings", "can_cancel_space_bookings",
                "can_book_this_space", "can_book_amenities_in_space"
            ],
            SpaceType: ["can_view_spacetype", "can_create_spacetype", "can_edit_spacetype", "can_delete_spacetype"],
            Amenity: ["can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity"],
            BookableAmenity: [  # <-- 修复：BookableAmenity 的权限现在分配给 BookableAmenity 模型
                "can_view_bookable_amenity", "can_edit_bookable_amenity_quantity",
                "can_change_bookable_amenity_status", "can_delete_bookable_amenity"
            ],
            Booking: [
                "can_view_all_bookings", "can_approve_any_booking", "can_check_in_any_booking",
                "can_cancel_any_booking", "can_edit_any_booking_notes", "can_delete_any_booking",
                "can_mark_no_show_and_create_violation"
            ],
            Violation: [
                "can_view_all_violations", "can_create_violation_record", "can_edit_violation_record",
                "can_delete_violation_record", "can_resolve_violation_record"
            ],
            DailyBookingLimit: ["can_view_daily_booking_limits", "can_manage_daily_booking_limits"],
            SpaceTypeBanPolicy: ["can_view_ban_policies", "can_manage_ban_policies"],
            UserSpaceTypeBan: ["can_view_user_bans", "can_manage_user_bans"],
            UserSpaceTypeExemption: ["can_view_user_exemptions", "can_manage_user_exemptions"],
            UserPenaltyPointsPerSpaceType: ["can_view_penalty_points"],  # <-- 修复：确保此权限在此处分配
        }

        for model_class, custom_perms in sysadmin_custom_permissions.items():
            ct = ContentType.objects.get_for_model(model_class)
            # 添加 Django 默认的 four-permissions (add, change, delete, view)
            default_perms_codenames = [f"add_{ct.model}", f"change_{ct.model}", f"delete_{ct.model}",
                                       f"view_{ct.model}"]
            default_perms = Permission.objects.filter(content_type=ct, codename__in=default_perms_codenames)
            sys_admin_group.permissions.add(*default_perms)
            self.stdout.write(self.style.NOTICE(
                f' - Assigned default {ct.app_label}.{ct.model} permissions to {sys_admin_group.name}'))  # 更改日志信息

            # 然后添加自定义权限
            for perm_codename in custom_perms:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    sys_admin_group.permissions.add(perm)
                    self.stdout.write(
                        self.style.NOTICE(f' - Assigned {ct.app_label}.{perm_codename} to {sys_admin_group.name}'))
                except Permission.DoesNotExist:
                    logger.warning(
                        f"Permission '{perm_codename}' for {model_class.__name__} not found. Did you run makemigrations?")

        # --- 空间管理员 (SpaceMan) 组权限 ---
        spaceman_custom_permissions = {
            Space: ["can_view_space", "can_create_space"],  # <-- 修复：确保此权限在此处分配
            SpaceType: ["can_view_spacetype"],
            Amenity: ["can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity"],
            Booking: ["can_view_all_bookings", "can_create_booking"],  # <-- 修复：确保此权限在此处分配
            Violation: ["can_view_all_violations", "can_create_violation_record"],
            DailyBookingLimit: ["can_view_daily_booking_limits"],
            SpaceTypeBanPolicy: ["can_view_ban_policies"],
            UserSpaceTypeBan: ["can_view_user_bans", "can_manage_user_bans"],
            UserSpaceTypeExemption: ["can_view_user_exemptions", "can_manage_user_exemptions"],
            UserPenaltyPointsPerSpaceType: ["can_view_penalty_points"],  # <-- 修复：确保此权限在此处分配
        }

        for model_class, perms_list in spaceman_custom_permissions.items():
            ct = ContentType.objects.get_for_model(model_class)
            for perm_codename in perms_list:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    space_manager_group.permissions.add(perm)
                    self.stdout.write(
                        self.style.NOTICE(f' - Assigned {ct.app_label}.{perm_codename} to {space_manager_group.name}'))
                except Permission.DoesNotExist:
                    logger.warning(
                        f"Permission '{perm_codename}' for {model_class.__name__} not found for space manager group.")

        # 针对 SpaceManager，显式添加 Django 默认的 view_space 权限
        try:
            space_ct = ContentType.objects.get_for_model(Space)
            view_space_perm = Permission.objects.get(codename='view_space', content_type=space_ct)  # 默认的 view_space 权限
            space_manager_group.permissions.add(view_space_perm)
            self.stdout.write(
                self.style.NOTICE(f' - Assigned spaces.view_space (default Django) to {space_manager_group.name}'))
        except Permission.DoesNotExist:
            logger.warning(f"Default Django permission 'view_space' for Space not found for space manager group.")

        # --- 教师 (Teacher) 和 学生 (Student) 组权限 ---
        try:
            space_ct = ContentType.objects.get_for_model(Space)  # 确保 space_ct 在这里定义
            book_this_space_perm = Permission.objects.get(codename='can_book_this_space', content_type=space_ct)
            book_amenities_in_space_perm = Permission.objects.get(codename='can_book_amenities_in_space',
                                                                  content_type=space_ct)

            view_amenity_perm = Permission.objects.get(codename='can_view_amenity',
                                                       content_type=ContentType.objects.get_for_model(Amenity))
            view_spacetype_perm = Permission.objects.get(codename='can_view_spacetype',
                                                         content_type=ContentType.objects.get_for_model(SpaceType))

            view_space_perm_for_users = Permission.objects.get(codename='can_view_space', content_type=space_ct)

            for group in [teacher_group, student_group]:
                group.permissions.add(book_this_space_perm, book_amenities_in_space_perm, view_amenity_perm,
                                      view_spacetype_perm, view_space_perm_for_users)
                self.stdout.write(self.style.NOTICE(f' - Assigned booking and view permissions to group: {group.name}'))
        except Permission.DoesNotExist as e:
            logger.warning(
                f"Missing a permission for Teacher/Student groups: {e} (This might be expected during initial setup.)")

        # --- 3. Update existing Superusers to join '系统管理员' group ---
        for user in CustomUser.objects.filter(is_superuser=True):
            if not user.groups.filter(name='系统管理员').exists():
                user.groups.add(sys_admin_group)
                self.stdout.write(self.style.SUCCESS(f"Superuser {user.username} added to '系统管理员' group."))

            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=['is_staff'])
                self.stdout.write(self.style.SUCCESS(f"Set is_staff=True for superuser {user.username}."))

        self.stdout.write(self.style.SUCCESS('Initial permission setup complete.'))