# your_app/management/commands/setup_initial_permissions.py (最终修订版 - 移除 SpaceManager 对用户禁用/豁免/违约点数的非查看权限)
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from guardian.shortcuts import assign_perm
from users.models import CustomUser
from spaces.models import (
    Space, SpaceType, Amenity, BookableAmenity,
    SPACE_MANAGEMENT_PERMISSIONS,
    BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS
)
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

        # --- 1. Create Groups ---
        sys_admin_group, created_sa = Group.objects.get_or_create(name='系统管理员')
        space_manager_group, created_sm = Group.objects.get_or_create(name='空间管理员')
        teacher_group, created_t = Group.objects.get_or_create(name='教师')
        student_group, created_stu = Group.objects.get_or_create(name='学生')

        if created_sa: self.stdout.write(self.style.SUCCESS(f'Created group: {sys_admin_group.name}'))
        if created_sm: self.stdout.write(self.style.SUCCESS(f'Created group: {space_manager_group.name}'))
        if created_t: self.stdout.write(self.style.SUCCESS(f'Created group: {teacher_group.name}'))
        if created_stu: self.stdout.write(self.style.SUCCESS(f'Created group: {student_group.name}'))

        # --- 2. Assign Global/Model-Level Permissions to Groups ---
        self.stdout.write(self.style.HTTP_INFO('Assigning permissions to groups...'))

        # --- 系统管理员 (SysAdmin) 组权限 ---
        all_models_and_perms_for_sysadmin = {
            Space: ["can_view_space", "can_create_space", "can_edit_space_info", "can_change_space_status",
                    "can_configure_booking_rules", "can_assign_space_manager", "can_manage_permitted_groups",
                    "can_add_space_amenity", "can_delete_space",
                    "can_view_space_bookings", "can_approve_space_bookings", "can_checkin_space_bookings",
                    "can_cancel_space_bookings", "can_book_this_space", "can_book_amenities_in_space"
                    ],

            SpaceType: ["can_view_spacetype", "can_create_spacetype", "can_edit_spacetype", "can_delete_spacetype"],
            Amenity: ["can_view_amenity", "can_create_amenity", "can_edit_amenity", "can_delete_amenity"],
            BookableAmenity: ["can_view_bookable_amenity", "can_edit_bookable_amenity_quantity",
                              "can_change_bookable_amenity_status", "can_delete_bookable_amenity"],

            Booking: ["can_view_all_bookings", "can_create_booking", "can_approve_any_booking",
                      "can_check_in_any_booking",
                      "can_cancel_any_booking", "can_edit_any_booking_notes", "can_delete_any_booking",
                      "can_mark_no_show_and_create_violation"],

            Violation: ["can_view_all_violations", "can_create_violation_record", "can_edit_violation_record",
                        "can_delete_violation_record", "can_resolve_violation_record"],

            DailyBookingLimit: ["can_view_daily_booking_limits", "can_manage_daily_booking_limits"],
            SpaceTypeBanPolicy: ["can_view_ban_policies", "can_manage_ban_policies"],
            UserSpaceTypeBan: ["can_view_user_bans", "can_manage_user_bans"],
            UserSpaceTypeExemption: ["can_view_user_exemptions", "can_manage_user_exemptions"],
            UserPenaltyPointsPerSpaceType: ["can_view_penalty_points"],
        }

        # 添加 SysAdmin 的所有权限
        for model_class, custom_perms in all_models_and_perms_for_sysadmin.items():
            ct = ContentType.objects.get_for_model(model_class)
            default_perms = Permission.objects.filter(content_type=ct,
                                                      codename__in=[f"add_{ct.model}", f"change_{ct.model}",
                                                                    f"delete_{ct.model}", f"view_{ct.model}"])
            sys_admin_group.permissions.add(*default_perms)

            for perm_codename in custom_perms:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    sys_admin_group.permissions.add(perm)
                    self.stdout.write(
                        self.style.NOTICE(f' - Assigned {ct.app_label}.{perm_codename} to {sys_admin_group.name}'))
                except Permission.DoesNotExist:
                    logger.warning(
                        f"WARNING: Permission '{perm_codename}' for {model_class.__name__} not found. Did you run makemigrations?")
                    self.stdout.write(self.style.ERROR(
                        f"WARNING: Permission '{ct.app_label}.{perm_codename}' for model '{model_class.__name__}' not found for SysAdmin group. "
                        "Please ensure it's defined in models.py and 'makemigrations'/'migrate' have been run properly."))

        # --- 空间管理员 (SpaceMan) 组权限 ---
        # 严格控制模型级权限，只赋予查看和必要的对象级操作权限的前提
        space_manager_model_level_perms = {
            Space: SPACE_MANAGEMENT_PERMISSIONS,
            SpaceType: ["can_view_spacetype"],
            Amenity: ["can_view_amenity"],
            BookableAmenity: BOOKABLE_AMENITY_MANAGEMENT_PERMISSIONS,

            Booking: [],  # 空间管理员不应拥有 Booking 的全局 'add', 'change', 'delete', 'view_all' 权限。
                          # 仅依赖 'view_booking' (由循环分配) 和对象级权限来操作。
            Violation: ["can_create_violation_record", "can_resolve_violation_record"],
            DailyBookingLimit: ["can_view_daily_booking_limits"],
            SpaceTypeBanPolicy: ["can_view_ban_policies"],
            UserSpaceTypeBan: ["can_view_user_bans"],  # <-- 修正：只保留查看权限
            UserSpaceTypeExemption: ["can_view_user_exemptions"],  # <-- 修正：只保留查看权限
            UserPenaltyPointsPerSpaceType: ["can_view_penalty_points"],
        }

        all_models_for_spaceman = [CustomUser, Space, SpaceType, Amenity, BookableAmenity, Booking, Violation,
                                  DailyBookingLimit, SpaceTypeBanPolicy, UserSpaceTypeBan,
                                  UserSpaceTypeExemption, UserPenaltyPointsPerSpaceType]

        for model_class in all_models_for_spaceman:
            ct = ContentType.objects.get_for_model(model_class, for_concrete_model=False)

            # 默认分配 Django 的 view_XXX 权限，以便在 Admin 界面中显示模块
            try:
                default_view_perm = Permission.objects.get(codename=f'view_{ct.model}', content_type=ct)
                space_manager_group.permissions.add(default_view_perm)
                self.stdout.write(self.style.NOTICE(
                    f' - Assigned default {ct.app_label}.view_{ct.model} to {space_manager_group.name}'))
            except Permission.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f"WARNING: Default Django 'view_{ct.model}' permission for model '{model_class.__name__}' ({ct.app_label}) not found for SpaceManager group. "
                    "Please ensure makemigrations/migrate have been run and app is properly registered."))
                logger.warning(
                    f"Default Django 'view_{ct.model}' permission for {model_class.__name__} not found for SpaceManager group.")

            custom_perms_for_model = space_manager_model_level_perms.get(model_class, [])
            for perm_codename in custom_perms_for_model:
                try:
                    perm = Permission.objects.get(codename=perm_codename, content_type=ct)
                    space_manager_group.permissions.add(perm)
                    self.stdout.write(
                        self.style.NOTICE(f' - Assigned {ct.app_label}.{perm_codename} to {space_manager_group.name}'))
                except Permission.DoesNotExist:
                    self.stdout.write(self.style.ERROR(
                        f"WARNING: Custom Permission '{ct.app_label}.{perm_codename}' for model '{model_class.__name__}' not found for SpaceManager group. "
                        "Please check models.py definition and makemigrations/migrate status."))
                    logger.warning(
                        f"Permission '{perm_codename}' for {model_class.__name__} not found for SpaceManager group.")

        # --- 教师 (Teacher) 和 学生 (Student) 组权限 ---
        try:
            space_ct = ContentType.objects.get_for_model(Space)
            amenity_ct = ContentType.objects.get_for_model(Amenity)
            spacetype_ct = ContentType.objects.get_for_model(SpaceType)
            bookable_amenity_ct = ContentType.objects.get_for_model(BookableAmenity)

            can_book_this_space_perm = Permission.objects.get(codename='can_book_this_space', content_type=space_ct)
            can_book_amenities_in_space_perm = Permission.objects.get(codename='can_book_amenities_in_space', content_type=space_ct)

            can_view_space_perm = Permission.objects.get(codename='can_view_space', content_type=space_ct)
            can_view_spacetype_perm = Permission.objects.get(codename='can_view_spacetype', content_type=spacetype_ct)
            can_view_amenity_perm = Permission.objects.get(codename='can_view_amenity', content_type=amenity_ct)
            can_view_bookable_amenity_perm = Permission.objects.get(codename='can_view_bookable_amenity', content_type=bookable_amenity_ct)

            view_booking_perm = Permission.objects.get(codename='view_booking', content_type=ContentType.objects.get_for_model(Booking))

            for group in [teacher_group, student_group]:
                group.permissions.add(
                    can_book_this_space_perm,
                    can_book_amenities_in_space_perm,
                    can_view_space_perm,
                    can_view_spacetype_perm,
                    can_view_amenity_perm,
                    can_view_bookable_amenity_perm,
                    view_booking_perm,
                )
                self.stdout.write(self.style.NOTICE(f' - Assigned booking and view permissions to group: {group.name}'))
        except Permission.DoesNotExist as e:
            self.stdout.write(self.style.ERROR(
                f"WARNING: Missing a permission for Teacher/Student groups: {e} (This might be expected during initial setup due to conditional app loading or new perms)."))
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