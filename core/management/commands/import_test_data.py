# core/management/commands/import_test_data.py (修正版本)

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from guardian.shortcuts import assign_perm

import random
from datetime import timedelta, time

# Try to import necessary models.
try:
    from spaces.models import Space, SpaceType, Amenity, BookableAmenity
except ImportError:
    Space = None
    SpaceType = None
    Amenity = None
    BookableAmenity = None
    print("Warning: 'spaces' app models not available. Data import for spaces and amenities will be skipped.")

try:
    from bookings.models import (
        SpaceTypeBanPolicy,
        UserPenaltyPointsPerSpaceType,
        UserSpaceTypeBan,
        UserSpaceTypeExemption,
        DailyBookingLimit  # NEW IMPORT: 每日预订限制模型
    )
except ImportError:
    SpaceTypeBanPolicy = None
    UserPenaltyPointsPerSpaceType = None
    UserSpaceTypeBan = None
    UserSpaceTypeExemption = None
    DailyBookingLimit = None  # NEW ASSIGNMENT
    print("Warning: 'bookings' app models not available. Data import for booking policies will be skipped.")

CustomUser = get_user_model()


class Command(BaseCommand):
    help = 'Imports essential test data including users, spaces, amenities, and booking policies.'

    def _create_user_with_roles(self, username, password, email=None, is_staff=False, is_superuser=False,
                                full_name='', phone_number=None, work_id=None, major=None, student_class=None,
                                gender=None):
        """
        创建或更新自定义用户，并支持设置新的CustomUser字段。
        对于已存在用户，会更新其非密码字段。
        """
        defaults = {
            'email': email,
            'is_staff': is_staff,  # 初始设置，CustomUser.save()会根据组再次调整
            'is_superuser': is_superuser,
            'is_active': True,
            'name': full_name,
            'phone_number': phone_number,
            'work_id': work_id,
            'major': major,
            'student_class': student_class,
            'gender': gender,
        }

        # 清理空字符串和None，避免唯一性字段报错
        if not defaults.get('phone_number'): defaults['phone_number'] = None
        if not defaults.get('work_id'): defaults['work_id'] = None
        if not defaults.get('email'): defaults['email'] = None
        if not defaults.get('name'): defaults['name'] = username  # 如果姓名为空，默认使用用户名

        user, created = CustomUser.objects.get_or_create(
            username=username,
            defaults=defaults
        )

        message = ""
        if created:
            user.set_password(password)
            user.save()
            message = f"  Created user: {username} (Name: {full_name or username})"
        else:
            updated = False
            # 检查并更新关键字段
            for field in ['email', 'name', 'phone_number', 'work_id', 'major', 'student_class', 'gender']:
                if hasattr(user, field) and getattr(user, field) != defaults[field]:
                    setattr(user, field, defaults[field])
                    updated = True

            # 特殊处理is_superuser (不被CustomUser.save自动更新)
            if user.is_superuser != is_superuser:
                user.is_superuser = is_superuser
                updated = True

            # 不直接设置is_staff，而是让CustomUser的save方法或后续的组添加处理
            # 但如果期望是staff却不是，可以先设置为true，待CustomUser的save处理
            if is_staff and not user.is_staff:
                user.is_staff = True
                updated = True

            if updated:
                user.save()  # 调用save触发CustomUser.save()中的is_staff和组相关逻辑
                message += f"  User '{username}' already exists. Updated details."
            else:
                message += f"  User '{username}' already exists. No significant details updated."

            # 确保密码与期望一致（对于测试数据，重置密码通常是可以接受的）
            if not user.has_usable_password() or not user.check_password(password):
                user.set_password(password)
                user.save(update_fields=['password'])
                message += " Password ensured/reset."

        if created:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING(message))

        return user

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting test data import...'))

        # --- 1. 创建 Users and Groups ---
        self.stdout.write(self.style.HTTP_INFO('\n1. Creating Users and Groups...'))

        # 创建 '系统管理员' 组
        system_admin_group, created = Group.objects.get_or_create(name='系统管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '系统管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '系统管理员' already exists."))

        # 创建 '空间管理员' 组
        space_manager_group, created = Group.objects.get_or_create(name='空间管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '空间管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '空间管理员' already exists."))

        # NEW: 创建 '教师' 组
        teacher_group, created = Group.objects.get_or_create(name='教师')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '教师'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '教师' already exists."))

        # NEW: 创建 '学生' 组
        student_group, created = Group.objects.get_or_create(name='学生')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '学生'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '学生' already exists."))

        # NEW: 创建 '普通用户' 组 (用于每日预订限制等)
        general_user_group, created = Group.objects.get_or_create(name='普通用户')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '普通用户'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '普通用户' already exists."))

        # 创建特定用户 (使用新的 full_name, phone_number, work_id 等字段)
        superuser = self._create_user_with_roles(
            'admin', 'admin123', 'admin@example.com',
            is_staff=True, is_superuser=True, full_name='超级用户'
        )
        sysadmin = self._create_user_with_roles(
            'sysadmin', 'sysadmin123', 'sysadmin@example.com',
            is_staff=True, full_name='系统管理员', work_id='SYS001'
        )
        space_manager1 = self._create_user_with_roles(
            'spaceman1', 'spaceman123', 'spaceman1@example.com',
            is_staff=True, full_name='空间经理一', work_id='SM001', phone_number='13000000001'
        )
        space_manager2 = self._create_user_with_roles(
            'spaceman2', 'spaceman234', 'spaceman2@example.com',
            is_staff=True, full_name='空间经理二', work_id='SM002', phone_number='13000000002'
        )
        space_manager3 = self._create_user_with_roles(
            'spaceman3', 'spaceman345', 'spaceman3@example.com',
            is_staff=True, full_name='空间经理三', work_id='SM003', phone_number='13000000003'
        )
        regular_user1 = self._create_user_with_roles(
            'user1', 'user123', 'user1@example.com',
            full_name='普通用户一', phone_number='13812345678'
        )
        regular_user2 = self._create_user_with_roles(
            'user2', 'user234', 'user2@example.com',
            full_name='普通用户二', phone_number='13887654321'
        )
        banned_user = self._create_user_with_roles(
            'banneduser', 'banned123', 'banned@example.com',
            full_name='禁用用户', phone_number='13911112222'
        )
        # NEW: 学生用户
        student_user = self._create_user_with_roles(
            'student1', 'student123', 'student1@example.com',
            full_name='张三', phone_number='13700001111', work_id='20200101',
            major='计算机科学与技术', student_class='计科2001班', gender='M'
        )
        # NEW: 教师用户 (通常也是is_staff)
        teacher_user = self._create_user_with_roles(
            'teacher1', 'teacher123', 'teacher1@example.com',
            is_staff=True, full_name='李老师', phone_number='13622223333', work_id='T2023001',
            gender='F'
        )

        # 将用户添加到相应的组 & 触发CustomUser.save()以更新is_staff
        def add_user_to_group_and_save(user_obj, group_obj):
            if not user_obj.groups.filter(name=group_obj.name).exists():
                user_obj.groups.add(group_obj)
                self.stdout.write(self.style.SUCCESS(f"  Added {user_obj.username} to '{group_obj.name}' group."))
            else:
                self.stdout.write(self.style.WARNING(f"  {user_obj.username} is already in '{group_obj.name}' group."))
            user_obj.save()  # 触发CustomUser.save()更新is_staff

        add_user_to_group_and_save(sysadmin, system_admin_group)
        for sm in [space_manager1, space_manager2, space_manager3]:
            add_user_to_group_and_save(sm, space_manager_group)
        add_user_to_group_and_save(student_user, student_group)
        add_user_to_group_and_save(teacher_user, teacher_group)
        for u in [regular_user1, regular_user2, banned_user]:
            add_user_to_group_and_save(u, general_user_group)

        # 确保超级用户的 is_staff 也是正确的
        if not superuser.is_staff:  # Superuser is always staff
            superuser.is_staff = True
            superuser.save()

        # --- 2. 创建 SpaceTypes, Amenities, Spaces & BookableAmenities ---
        # 以下部分保持不变
        if Space and SpaceType and Amenity and BookableAmenity:
            self.stdout.write(self.style.HTTP_INFO('\n2. Creating SpaceTypes, Amenities, Spaces, BookableAmenities...'))

            space_types_data = [
                # 移除 'is_container_type' 字段
                {'name': 'Lecture Hall', 'default_is_bookable': True},
                {'name': 'Meeting Room', 'default_is_bookable': True},
                {'name': 'Lab', 'default_is_bookable': True,
                 'default_requires_approval': True},
                {'name': 'Study Zone', 'default_is_bookable': True},
                {'name': 'Sports Field', 'default_is_bookable': True},
                # 'Office' 这个类型也无 'is_container_type'，只保留 SpaceType 本身有的字段
                {'name': 'Office', 'is_basic_infrastructure': True,
                 'default_is_bookable': False},
            ]
            space_types = {}
            for data in space_types_data:
                # 显式提供default values for time fields
                defaults_with_time = {
                    'description': '',  # 确保description默认值
                    'default_available_start_time': time(8, 0),
                    'default_available_end_time': time(22, 0),
                    'default_min_booking_duration': timedelta(minutes=30),
                    'default_max_booking_duration': timedelta(hours=4),
                    'default_buffer_time_minutes': 0,
                    **{k: v for k, v in data.items() if k != 'name'}  # 覆盖默认值
                }

                st, created = SpaceType.objects.get_or_create(
                    name=data['name'],
                    defaults=defaults_with_time
                )
                space_types[st.name] = st
                self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Existing'} SpaceType: {st.name}"))

            amenities_data = [
                {'name': 'Projector', 'description': '高清投影仪'},
                {'name': 'Whiteboard', 'description': '交互式白板'},
                {'name': 'Video Conferencing', 'description': '视频会议设备'},
                {'name': 'Water Dispenser', 'description': '饮水机', 'is_bookable_individually': True},  # 部分设施可单独预订
                {'name': 'Internet Access', 'description': '高速网络接入'},
                {'name': 'Sports Equipment', 'description': '各类运动器材'},
            ]
            amenities = {}
            for data in amenities_data:
                am, created = Amenity.objects.get_or_create(
                    name=data['name'],
                    defaults={k: v for k, v in data.items() if k != 'name'}
                )
                amenities[am.name] = am
                self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Amenity: {am.name}"))

            # Parent Spaces (Container Spaces)
            # 注意：容器空间通常设置为is_bookable=False，space_type选择默认不可预订的类型
            parent_space1, created = Space.objects.get_or_create(
                name='Main Building - Floor 1',
                defaults={'location': 'Central Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager1, 'is_bookable': False}
            )
            # 确保即使空间已存在，权限也能分配，以保证幂等性 (此为信号处理，这里可省略，但保留以显式展示意图)
            # assign_perm('spaces.can_manage_space_details', space_manager1, parent_space1)
            # assign_perm('spaces.can_manage_space_bookings', space_manager1, parent_space1)
            # assign_perm('spaces.can_manage_space_amenities', space_manager1, parent_space1)
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space1.name}"))

            parent_space2, created = Space.objects.get_or_create(
                name='Innovation Hub',
                defaults={'location': 'East Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager2, 'is_bookable': False}
            )
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space2.name}"))

            parent_space3, created = Space.objects.get_or_create(
                name='Outdoor Facilities',
                defaults={'location': 'West Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager3, 'is_bookable': False}
            )
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space3.name}"))

            # Child Bookable Spaces
            spaces_data = [
                {'name': 'Room 101', 'location': 'Floor 1', 'space_type': space_types['Meeting Room'],
                 'parent_space': parent_space1, 'capacity': 10, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager1, 'amenities': [('Projector', 1), ('Whiteboard', 1)]},
                {'name': 'Lecture Hall A', 'location': 'Floor 1', 'space_type': space_types['Lecture Hall'],
                 'parent_space': parent_space1, 'capacity': 100, 'is_bookable': True, 'requires_approval': True,
                 'managed_by': space_manager1, 'amenities': [('Projector', 1), ('Video Conferencing', 1)]},
                {'name': 'Lab 3B', 'location': 'Research Wing', 'space_type': space_types['Lab'],
                 'parent_space': parent_space2, 'capacity': 20, 'is_bookable': True, 'requires_approval': True,
                 'managed_by': space_manager2, 'amenities': [('Water Dispenser', 1, True), ('Internet Access', 1)]},
                # Water Dispenser 为可单独预订
                {'name': 'Meeting Pod 5', 'location': 'Collaborative Zone', 'space_type': space_types['Study Zone'],
                 'parent_space': parent_space2, 'capacity': 4, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager2, 'amenities': []},
                {'name': 'Soccer Field 1', 'location': 'Sports Complex', 'space_type': space_types['Sports Field'],
                 'parent_space': parent_space3, 'capacity': 30, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager3, 'amenities': [('Sports Equipment', 10)]},
                {'name': 'Training Room', 'location': 'Ground Floor', 'space_type': space_types['Meeting Room'],
                 'parent_space': parent_space1, 'capacity': 25, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager1, 'amenities': [('Projector', 1), ('Whiteboard', 1)]},
                {'name': 'Group Study Room', 'location': 'Quiet Zone', 'space_type': space_types['Study Zone'],
                 'parent_space': parent_space2, 'capacity': 6, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager2, 'amenities': [('Internet Access', 1)]},
            ]

            for s_data in spaces_data:
                s_amenities_list = s_data.pop('amenities')  # 提取设施数据

                # 检查并设置 Space 的 available_start/end_time 为 time 对象
                if 'available_start_time' in s_data and isinstance(s_data['available_start_time'], str):
                    h, m = map(int, s_data['available_start_time'].split(':'))
                    s_data['available_start_time'] = time(h, m)
                if 'available_end_time' in s_data and isinstance(s_data['available_end_time'], str):
                    h, m = map(int, s_data['available_end_time'].split(':'))
                    s_data['available_end_time'] = time(h, m)

                space, created = Space.objects.get_or_create(
                    name=s_data['name'],
                    defaults={k: v for k, v in s_data.items() if k != 'name'}
                )
                self.stdout.write(
                    self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Bookable Space: {space.name}"))

                # Add/Update Bookable Amenities
                for amenity_item in s_amenities_list:
                    amenity_name = amenity_item[0]
                    quantity = amenity_item[1]
                    is_bookable_instance = amenity_item[2] if len(amenity_item) > 2 else amenities[
                        amenity_name].is_bookable_individually  # 如果未指定，则继承设施类型的默认值

                    book_am, am_created = BookableAmenity.objects.get_or_create(
                        space=space,
                        amenity=amenities[amenity_name],
                        defaults={'quantity': quantity, 'is_bookable': is_bookable_instance}
                    )
                    # 如果已存在，更新数量和是否可预订状态
                    if not am_created:
                        if book_am.quantity != quantity or book_am.is_bookable != is_bookable_instance:
                            book_am.quantity = quantity
                            book_am.is_bookable = is_bookable_instance
                            book_am.save()
                            self.stdout.write(self.style.WARNING(
                                f"    Updated Bookable Amenity: {amenity_name} for {space.name}"))
                        else:
                            self.stdout.write(self.style.WARNING(
                                f"    Existing Bookable Amenity: {amenity_name} for {space.name}. No changes needed."))
                    else:
                        self.stdout.write(self.style.SUCCESS(
                            f"    Created Bookable Amenity: {amenity_name} for {space.name}"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nSkipping creation of SpaceTypes, Amenities, Spaces, BookableAmenities due to missing models."))

        # --- 3. 创建 Booking Policies & User Ban/Penalty Data ---
        if SpaceTypeBanPolicy and UserPenaltyPointsPerSpaceType and UserSpaceTypeBan and UserSpaceTypeExemption:
            self.stdout.write(self.style.HTTP_INFO('\n3. Creating Booking Policies & User Ban/Penalty Data...'))

            # Ban Policies
            policy1, created = SpaceTypeBanPolicy.objects.get_or_create(
                space_type=space_types['Meeting Room'],
                threshold_points=3,
                ban_duration=timedelta(days=7),
                defaults={'priority': 10, 'is_active': True, 'description': '违约3点在会议室禁用7天'}
            )
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Ban Policy: {policy1.description}"))

            policy2, created = SpaceTypeBanPolicy.objects.get_or_create(
                space_type=space_types['Lab'],
                threshold_points=5,
                ban_duration=timedelta(days=30),
                defaults={'priority': 20, 'is_active': True, 'description': '违约5点在实验室禁用30天'}
            )
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Ban Policy: {policy2.description}"))

            global_policy, created = SpaceTypeBanPolicy.objects.get_or_create(
                space_type=None,  # Global policy
                threshold_points=10,
                ban_duration=timedelta(days=90),
                defaults={'priority': 30, 'is_active': True, 'description': '违约10点全局禁用90天'}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Global Ban Policy: {global_policy.description}"))

            # User Penalty Points
            penalty1, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=regular_user1,
                space_type=space_types['Meeting Room'],
                defaults={'current_penalty_points': 2, 'last_violation_at': timezone.now()}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Penalty for {regular_user1.username}: {penalty1.current_penalty_points} points in {space_types['Meeting Room'].name}"))

            # User that triggers a ban (banned_user will have 4 points, triggering 3-point policy for Meeting Room)
            penalty_banned, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=banned_user,
                space_type=space_types['Meeting Room'],
                defaults={'current_penalty_points': 4, 'last_violation_at': timezone.now()}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Penalty for {banned_user.username}: {penalty_banned.current_penalty_points} points in {space_types['Meeting Room'].name} (should trigger ban)"))

            # User Bans (日期不再使用.date()转换)
            user_ban1, created = UserSpaceTypeBan.objects.get_or_create(
                user=banned_user,
                space_type=space_types['Meeting Room'],
                defaults={'start_date': timezone.now(),
                          'end_date': timezone.now() + timedelta(days=7),
                          'reason': 'Triggered by Meeting Room policy',
                          'ban_policy_applied': policy1, 'issued_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} User Ban for {user_ban1.user.username}: {user_ban1.reason}"))

            user_ban2, created = UserSpaceTypeBan.objects.get_or_create(
                user=regular_user2,
                space_type=None,  # 全局禁用
                defaults={'start_date': timezone.now(),
                          'end_date': timezone.now() + timedelta(days=90),
                          'reason': 'Manual Global Ban',
                          'ban_policy_applied': global_policy, 'issued_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Global User Ban for {user_ban2.user.username}: {user_ban2.reason}"))

            # User Exemptions (日期不再使用.date()转换)
            exemption1, created = UserSpaceTypeExemption.objects.get_or_create(
                user=regular_user1,
                space_type=space_types['Lab'],
                defaults={'start_date': timezone.now(),
                          'end_date': timezone.now() + timedelta(days=30),
                          'exemption_reason': 'Research Project',
                          'granted_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} User Exemption for {exemption1.user.username}: {exemption1.exemption_reason}"))

            # 为学生用户添加一项豁免 (例如，免于所有违约点数的影响或缩短禁用期)
            exemption_student, created = UserSpaceTypeExemption.objects.get_or_create(
                user=student_user,
                space_type=space_types['Study Zone'],  # 豁免在自习区的一些规则
                defaults={'start_date': timezone.now(),
                          'end_date': timezone.now() + timedelta(days=180),
                          'exemption_reason': '优秀学生特批',
                          'granted_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} User Exemption for {exemption_student.user.username}: {exemption_student.exemption_reason}"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nSkipping creation of Booking Policies and User Ban/Penalty Data due to missing models."))

        # --- 4. 创建 Daily Booking Limits ---
        if DailyBookingLimit:
            self.stdout.write(self.style.HTTP_INFO('\n4. Creating Daily Booking Limits...'))

            # 为 '普通用户' 组设置每日预订限制
            daily_limit_general, created = DailyBookingLimit.objects.get_or_create(
                group=general_user_group,
                defaults={'max_bookings': 3, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Daily Booking Limit for '{general_user_group.name}': {daily_limit_general.max_bookings}"))

            # 为 '学生' 组设置每日预订限制
            daily_limit_student, created = DailyBookingLimit.objects.get_or_create(
                group=student_group,
                defaults={'max_bookings': 5, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Daily Booking Limit for '{student_group.name}': {daily_limit_student.max_bookings}"))

            # 为 '教师' 组设置每日预订限制 (可以适当放宽)
            daily_limit_teacher, created = DailyBookingLimit.objects.get_or_create(
                group=teacher_group,
                defaults={'max_bookings': 10, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Daily Booking Limit for '{teacher_group.name}': {daily_limit_teacher.max_bookings}"))

            # 系统管理员和空间管理员通常没有每日预订次数限制 (设置为0表示无限制)
            daily_limit_sysadmin, created = DailyBookingLimit.objects.get_or_create(
                group=system_admin_group,
                defaults={'max_bookings': 0, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Daily Booking Limit for '{system_admin_group.name}': {'无限制' if daily_limit_sysadmin.max_bookings == 0 else daily_limit_sysadmin.max_bookings}"))

            daily_limit_spaceman, created = DailyBookingLimit.objects.get_or_create(
                group=space_manager_group,
                defaults={'max_bookings': 0, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Daily Booking Limit for '{space_manager_group.name}': {'无限制' if daily_limit_spaceman.max_bookings == 0 else daily_limit_spaceman.max_bookings}"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nSkipping creation of Daily Booking Limits due to missing 'DailyBookingLimit' model."))

        self.stdout.write(self.style.HTTP_INFO('\nTest data import complete!'))