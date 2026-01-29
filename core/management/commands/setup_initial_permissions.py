# your_app/management/commands/setup_initial_permissions.py (最终修订版)

from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from guardian.shortcuts import assign_perm, remove_perm
from users.models import CustomUser
from spaces.models import (
    Space, SpaceType, Amenity, BookableAmenity,
)
from bookings.models import (
    Booking, Violation, DailyBookingLimit, SpaceTypeBanPolicy, UserSpaceTypeBan,
    UserSpaceTypeExemption, UserPenaltyPointsPerSpaceType
)
from check_in.models import CheckInRecord  # <-- 新增导入 CheckInRecord

import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sets up initial Django Groups and assigns default global/model-level permissions.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Setting up initial groups and permissions...'))

        # --- 1. Create Groups ---
        sys_admin_group, created_sa = Group.objects.get_or_create(name='系统管理员')
        space_manager_group, created_sm = Group.objects.get_or_create(name='空间管理员')
        check_in_staff_group, created_cis = Group.objects.get_or_create(name='签到员')
        teacher_group, created_t = Group.objects.get_or_create(name='教师')
        student_group, created_stu = Group.objects.get_or_create(name='学生')

        if created_sa: self.stdout.write(self.style.SUCCESS(f'Created group: {sys_admin_group.name}'))
        if created_sm: self.stdout.write(self.style.SUCCESS(f'Created group: {space_manager_group.name}'))
        if created_cis: self.stdout.write(self.style.SUCCESS(f'Created group: {check_in_staff_group.name}'))
        if created_t: self.stdout.write(self.style.SUCCESS(f'Created group: {teacher_group.name}'))
        if created_stu: self.stdout.write(self.style.SUCCESS(f'Created group: {student_group.name}'))

        # --- 2. Assign Global/Model-Level Permissions to Groups ---
        self.stdout.write(self.style.HTTP_INFO('Assigning permissions to groups...'))

        # 定义需要处理的所有模型
        all_relevant_models = [CustomUser, Space, SpaceType, Amenity, BookableAmenity, Booking, Violation,
                               DailyBookingLimit, SpaceTypeBanPolicy, UserSpaceTypeBan,
                               UserSpaceTypeExemption, UserPenaltyPointsPerSpaceType, CheckInRecord]

        # --- 系统管理员 (SysAdmin) 组权限 (维持完全控制，所有CRUD和自定义权限) ---
        for model_class in all_relevant_models:
            ct = ContentType.objects.get_for_model(model_class)

            # Fetch all default Django CRUD and View permissions and assign them
            default_perms_codenames = [f"add_{ct.model}", f"change_{ct.model}", f"delete_{ct.model}",
                                       f"view_{ct.model}"]
            default_perms = Permission.objects.filter(content_type=ct, codename__in=default_perms_codenames)
            sys_admin_group.permissions.add(*default_perms)

            # Assign all custom permissions to SysAdmin
            custom_perms_map = {
                CustomUser: [],
                Space: ["can_assign_space_manager", "can_delete_space", "can_create_space",
                        "can_view_space", "can_edit_space_info", "can_change_space_status",
                        "can_configure_booking_rules", "can_manage_permitted_groups", "can_add_space_amenity",
                        "can_book_this_space", "can_book_amenities_in_space",
                        "can_view_space_bookings", "can_approve_space_bookings", "can_checkin_space_bookings",
                        "can_cancel_space_bookings", "can_mark_no_show_and_create_violation"
                        ],
                BookableAmenity: ["can_delete_bookable_amenity", "can_edit_bookable_amenity_quantity",
                                  "can_change_bookable_amenity_status", "can_view_bookable_amenity"],
                SpaceType: ["can_view_spacetype", "can_create_spacetype", "can_edit_spacetype", "can_delete_spacetype"],
                Amenity: ["can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity"],
                Booking: ["can_view_all_bookings", "can_create_booking", "can_approve_any_booking",
                          "can_check_in_any_booking", "can_cancel_any_booking", "can_edit_any_booking_notes",
                          "can_delete_any_booking", "can_mark_no_show_and_create_violation"],
                Violation: ["can_view_all_violations", "can_create_violation_record", "can_edit_violation_record",
                            "can_delete_violation_record", "can_resolve_violation_record"],
                DailyBookingLimit: ["can_view_daily_booking_limits", "can_manage_daily_booking_limits"],
                SpaceTypeBanPolicy: ["can_view_ban_policies", "can_manage_ban_policies"],
                UserSpaceTypeBan: ["can_view_user_bans", "can_manage_user_bans"],
                UserSpaceTypeExemption: ["can_view_user_exemptions", "can_manage_user_exemptions"],
                UserPenaltyPointsPerSpaceType: ["can_view_penalty_points", "can_manage_penalty_points"],
                CheckInRecord: ["can_view_checkinrecord", "can_add_checkinrecord", "can_change_checkinrecord",
                                "can_delete_checkinrecord"],
            }
            custom_perms = custom_perms_map.get(model_class, [])

            for perm_codename in custom_perms:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    sys_admin_group.permissions.add(perm)
                except Permission.DoesNotExist:
                    logger.warning(
                        f"WARNING: Permission '{ct.app_label}.{perm_codename}' for model '{model_class.__name__}' not found for SysAdmin group. Did you run makemigrations?")

        # --- 空间管理员 (SpaceManager) 组权限 ---
        for model_class in all_relevant_models:
            ct = ContentType.objects.get_for_model(model_class)
            # 移除所有默认 Django CRUD 和 View 权限 (防止旧迁移遗留)
            all_default_perms_to_remove_codenames = [f"add_{ct.model}", f"change_{ct.model}", f"delete_{ct.model}",
                                                     f"view_{ct.model}"]
            default_perms_to_remove = Permission.objects.filter(content_type=ct,
                                                                codename__in=all_default_perms_to_remove_codenames)
            if default_perms_to_remove.exists():
                space_manager_group.permissions.remove(*default_perms_to_remove)

            # 定义需要从空间管理员中移除的自定义权限 (防止意外赋予)
            custom_perms_to_remove_codenames = [
                "can_view_space", "can_edit_space_info", "can_change_space_status", "can_configure_booking_rules",
                "can_manage_permitted_groups", "can_add_space_amenity", "can_view_space_bookings",
                "can_approve_space_bookings", "can_checkin_space_bookings", "can_cancel_space_bookings",
                "can_book_this_space", "can_book_amenities_in_space",
                "can_view_bookable_amenity", "can_edit_bookable_amenity_quantity",
                "can_change_bookable_amenity_status", "can_delete_bookable_amenity",
                "can_view_spacetype", "can_create_spacetype", "can_edit_spacetype", "can_delete_spacetype",
                "can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity",
                "can_view_all_bookings", "can_create_booking", "can_approve_any_booking",
                "can_check_in_any_booking", "can_cancel_any_booking", "can_edit_any_booking_notes",
                "can_delete_any_booking", "can_mark_no_show_and_create_violation",
                "can_view_all_violations", "can_create_violation_record", "can_edit_violation_record",
                "can_delete_violation_record", "can_resolve_violation_record",
                "can_view_daily_booking_limits", "can_manage_daily_booking_limits",
                "can_view_ban_policies", "can_manage_ban_policies",
                "can_view_user_bans", "can_manage_user_bans",
                "can_view_user_exemptions", "can_manage_user_exemptions",
                "can_view_penalty_points", "can_manage_penalty_points",
                "can_view_checkinrecord", "can_add_checkinrecord", "can_change_checkinrecord",
                "can_delete_checkinrecord"  # <-- 从 SpaceManager 移除所有 CheckInRecord 权限
            ]

            perms_to_remove_from_group_explicitly = Permission.objects.filter(
                content_type__app_label__in=['spaces', 'bookings', 'check_in'],
                codename__in=custom_perms_to_remove_codenames
            )
            if perms_to_remove_from_group_explicitly.exists():
                space_manager_group.permissions.remove(*perms_to_remove_from_group_explicitly)

        # 重新分配 SpaceManager 所需的**真正的模型级权限** (仅保留非对象级的通用权限)
        space_manager_custom_perms_to_assign_map = {
            Space: ["can_create_space"],
            Violation: ["can_create_violation_record", "can_resolve_violation_record"],
            Booking: ["can_approve_any_booking", "can_cancel_any_booking", "can_mark_no_show_and_create_violation"],
            SpaceTypeBanPolicy: ["can_manage_ban_policies"],
            UserSpaceTypeBan: ["can_manage_user_bans"],
            UserSpaceTypeExemption: ["can_manage_user_exemptions"],
            SpaceType: ["can_view_spacetype"],
            Amenity: ["can_view_amenity"],
            BookableAmenity: [],
            UserPenaltyPointsPerSpaceType: [],
            CustomUser: [],
            DailyBookingLimit: [],
            CheckInRecord: [],  # <-- SpaceManager 不再拥有 CheckInRecord 的任何权限
        }

        for model_class, perms_to_assign in space_manager_custom_perms_to_assign_map.items():
            ct = ContentType.objects.get_for_model(model_class)
            for perm_codename in perms_to_assign:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    if not space_manager_group.permissions.filter(pk=perm.pk).exists():
                        space_manager_group.permissions.add(perm)
                except Permission.DoesNotExist:
                    self.stdout.write(self.style.ERROR(
                        f"WARNING: Permission '{ct.app_label}.{perm_codename}' for model '{model_class.__name__}' not found for SpaceManager group. Make sure it's defined and migrations are run."))

        # 显式地为需要让 SpaceManager 看见的 Admin 模块分配 Django 默认的 `view_xxx` 权限
        models_for_spaceman_to_view_modules = [
            (Space, 'spaces'), (BookableAmenity, 'spaces'), (SpaceType, 'spaces'), (Amenity, 'spaces'),
            (Booking, 'bookings'), (Violation, 'bookings'), (UserSpaceTypeBan, 'bookings'),
            (UserSpaceTypeExemption, 'bookings'), (UserPenaltyPointsPerSpaceType, 'bookings'),
            (SpaceTypeBanPolicy, 'bookings'),
        ]

        for model, app_label in models_for_spaceman_to_view_modules:
            ct = ContentType.objects.get_for_model(model)
            try:
                view_perm = Permission.objects.get(content_type=ct, codename=f'view_{ct.model}')
                if not space_manager_group.permissions.filter(pk=view_perm.pk).exists():
                    space_manager_group.permissions.add(view_perm)
            except Permission.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f"WARNING: Default Django 'view_{ct.model}' permission for model '{model.__name__}' not found. Did you run makemigrations?"))

        # --- 签到员 (CheckInStaff) 组权限 ---
        for model_class in all_relevant_models:
            ct = ContentType.objects.get_for_model(model_class)
            # 移除所有不属于签到员的默认 Django CRUD 和 View 权限
            all_default_perms_to_remove_codenames = [f"add_{ct.model}", f"change_{ct.model}", f"delete_{ct.model}",
                                                     f"view_{ct.model}"]
            default_perms_to_remove = Permission.objects.filter(content_type=ct,
                                                                codename__in=all_default_perms_to_remove_codenames)
            if default_perms_to_remove.exists():
                check_in_staff_group.permissions.remove(*default_perms_to_remove)

            # 移除所有自定义权限，避免旧数据干扰
            custom_perms_to_remove_codenames = [
                "can_view_space", "can_edit_space_info", "can_change_space_status", "can_configure_booking_rules",
                "can_manage_permitted_groups", "can_add_space_amenity", "can_view_space_bookings",
                "can_approve_space_bookings", "can_checkin_space_bookings", "can_cancel_space_bookings",
                "can_book_this_space", "can_book_amenities_in_space",
                "can_view_bookable_amenity", "can_edit_bookable_amenity_quantity",
                "can_change_bookable_amenity_status", "can_delete_bookable_amenity",
                "can_view_spacetype", "can_create_spacetype", "can_edit_spacetype", "can_delete_spacetype",
                "can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity",
                "can_view_all_bookings", "can_create_booking", "can_approve_any_booking",
                "can_check_in_any_booking", "can_cancel_any_booking", "can_edit_any_booking_notes",
                "can_delete_any_booking", "can_mark_no_show_and_create_violation",
                "can_view_all_violations", "can_create_violation_record", "can_edit_violation_record",
                "can_delete_violation_record", "can_resolve_violation_record",
                "can_view_daily_booking_limits", "can_manage_daily_booking_limits",
                "can_view_ban_policies", "can_manage_ban_policies",
                "can_view_user_bans", "can_manage_user_bans",
                "can_view_user_exemptions", "can_manage_user_exemptions",
                "can_view_penalty_points", "can_manage_penalty_points",
                "can_view_checkinrecord", "can_add_checkinrecord", "can_change_checkinrecord",
                "can_delete_checkinrecord"
            ]

            perms_to_remove_from_group_explicitly = Permission.objects.filter(
                content_type__app_label__in=['spaces', 'bookings', 'check_in'],
                codename__in=custom_perms_to_remove_codenames
            )
            if perms_to_remove_from_group_explicitly.exists():
                check_in_staff_group.permissions.remove(*perms_to_remove_from_group_explicitly)

        # 重新分配 '签到员' 所需的模型级权限
        check_in_staff_custom_perms_to_assign_map = {
            Booking: ["can_view_all_bookings", "can_check_in_any_booking"],
            CheckInRecord: ["can_view_checkinrecord", "can_add_checkinrecord", "can_change_checkinrecord",
                            "can_delete_checkinrecord"],
        }

        for model_class, perms_to_assign in check_in_staff_custom_perms_to_assign_map.items():
            ct = ContentType.objects.get_for_model(model_class)
            for perm_codename in perms_to_assign:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    if not check_in_staff_group.permissions.filter(pk=perm.pk).exists():
                        check_in_staff_group.permissions.add(perm)
                        self.stdout.write(self.style.NOTICE(
                            f' - Assigned CheckInStaff-specific model-level {ct.app_label}.{perm_codename} to {check_in_staff_group.name}'))
                except Permission.DoesNotExist:
                    self.stdout.write(self.style.ERROR(
                        f"WARNING: Permission '{ct.app_label}.{perm_codename}' for model '{model_class.__name__}' not found for CheckInStaff group. Make sure it's defined in models.py and migrations are run."))

        # 显式地为需要让 CheckInStaff 看见的 Admin 模块分配 Django 默认的 `view_xxx` 权限
        models_for_checkinstaff_to_view_modules = [
            (Booking, 'bookings'),
            (CheckInRecord, 'check_in'),
        ]

        for model, app_label in models_for_checkinstaff_to_view_modules:
            ct = ContentType.objects.get_for_model(model)
            try:
                view_perm = Permission.objects.get(content_type=ct, codename=f'view_{ct.model}')
                if not check_in_staff_group.permissions.filter(pk=view_perm.pk).exists():
                    check_in_staff_group.permissions.add(view_perm)
                    self.stdout.write(self.style.SUCCESS(
                        f' - Assigned Django default view_ permission ({app_label}.view_{ct.model}) to {check_in_staff_group.name} for module visibility.'))
            except Permission.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f"WARNING: Default Django 'view_{ct.model}' permission for model '{model.__name__}' not found. Did you run makemigrations?"))

        # --- 教师 (Teacher) 和 学生 (Student) 组权限 (使用 Django 默认的 view_xxx 权限) ---
        try:
            space_ct = ContentType.objects.get_for_model(Space)
            amenity_ct = ContentType.objects.get_for_model(Amenity)
            spacetype_ct = ContentType.objects.get_for_model(SpaceType)
            bookable_amenity_ct = ContentType.objects.get_for_model(BookableAmenity)
            booking_ct = ContentType.objects.get_for_model(Booking)

            # 确保这些权限在 roles.py 或者对应的 models.py 中有定义
            can_book_this_space_perm = Permission.objects.get(codename='can_book_this_space', content_type=space_ct)
            can_book_amenities_in_space_perm = Permission.objects.get(codename='can_book_amenities_in_space',
                                                                      content_type=space_ct)

            view_space_perm = Permission.objects.get(codename='view_space', content_type=space_ct)
            view_spacetype_perm = Permission.objects.get(codename='view_spacetype', content_type=spacetype_ct)
            view_amenity_perm = Permission.objects.get(codename='view_amenity', content_type=amenity_ct)
            view_bookable_amenity_perm = Permission.objects.get(codename='view_bookable_amenity',
                                                                content_type=bookable_amenity_ct)
            view_booking_perm = Permission.objects.get(codename='view_booking',
                                                       content_type=booking_ct)

            for group in [teacher_group, student_group]:
                group.permissions.add(
                    can_book_this_space_perm,
                    can_book_amenities_in_space_perm,
                    view_space_perm,
                    view_spacetype_perm,
                    view_amenity_perm,
                    view_bookable_amenity_perm,
                    view_booking_perm,
                )
                self.stdout.write(self.style.NOTICE(f' - Assigned booking and view permissions to group: {group.name}'))
        except Permission.DoesNotExist as e:
            self.stdout.write(self.style.ERROR(
                f"WARNING: Missing a permission for Teacher/Student groups: {e} (Please ensure all necessary models.py permissions are created by running 'makemigrations' and 'migrate')."))
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