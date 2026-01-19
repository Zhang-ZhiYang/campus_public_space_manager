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
        DailyBookingLimit
    )
except ImportError:
    SpaceTypeBanPolicy = None
    UserPenaltyPointsPerSpaceType = None
    UserSpaceTypeBan = None
    UserSpaceTypeExemption = None
    DailyBookingLimit = None
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
            'is_staff': is_staff,
            'is_superuser': is_superuser,
            'is_active': True,
            'name': full_name,
            'phone_number': phone_number,
            'work_id': work_id,
            'major': major,
            'student_class': student_class,
            'gender': gender,
        }

        if not defaults.get('phone_number'): defaults['phone_number'] = None
        if not defaults.get('work_id'): defaults['work_id'] = None
        if not defaults.get('email'): defaults['email'] = None
        if not defaults.get('name'): defaults['name'] = username

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
            for field in ['email', 'name', 'phone_number', 'work_id', 'major', 'student_class', 'gender']:
                if hasattr(user, field) and getattr(user, field) != defaults[field]:
                    setattr(user, field, defaults[field])
                    updated = True

            if user.is_superuser != is_superuser:
                user.is_superuser = is_superuser
                updated = True

            if is_staff and not user.is_staff:
                user.is_staff = True
                updated = True

            if updated:
                user.save()
                message += f"  User '{username}' already exists. Updated details."
            else:
                message += f"  User '{username}' already exists. No significant details updated."

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

        system_admin_group, created = Group.objects.get_or_create(name='系统管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '系统管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '系统管理员' already exists."))

        space_manager_group, created = Group.objects.get_or_create(name='空间管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '空间管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '空间管理员' already exists."))

        teacher_group, created = Group.objects.get_or_create(name='教师')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '教师'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '教师' already exists."))

        student_group, created = Group.objects.get_or_create(name='学生')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '学生'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '学生' already exists."))

        general_user_group, created = Group.objects.get_or_create(name='普通用户')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '普通用户'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '普通用户' already exists."))

        # 创建特定用户
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
        student_user = self._create_user_with_roles(
            'student1', 'student123', 'student1@example.com',
            full_name='张三', phone_number='13700001111', work_id='20200101',
            major='计算机科学与技术', student_class='计科2001班', gender='M'
        )
        teacher_user = self._create_user_with_roles(
            'teacher1', 'teacher123', 'teacher1@example.com',
            is_staff=True, full_name='李老师', phone_number='13622223333', work_id='T2023001',
            gender='F'
        )

        def add_user_to_group_and_save(user_obj, group_obj):
            if not user_obj.groups.filter(name=group_obj.name).exists():
                user_obj.groups.add(group_obj)
                self.stdout.write(self.style.SUCCESS(f"  Added {user_obj.username} to '{group_obj.name}' group."))
            else:
                self.stdout.write(self.style.WARNING(f"  {user_obj.username} is already in '{group_obj.name}' group."))
            user_obj.save()

        add_user_to_group_and_save(sysadmin, system_admin_group)
        for sm in [space_manager1, space_manager2, space_manager3]:
            add_user_to_group_and_save(sm, space_manager_group)
        add_user_to_group_and_save(student_user, student_group)
        add_user_to_group_and_save(teacher_user, teacher_group)
        for u in [regular_user1, regular_user2, banned_user]:
            add_user_to_group_and_save(u, general_user_group)

        if not superuser.is_staff:
            superuser.is_staff = True
            superuser.save()

        # --- 2. 创建 SpaceTypes, Amenities, Spaces & BookableAmenities ---
        if Space and SpaceType and Amenity and BookableAmenity:
            self.stdout.write(self.style.HTTP_INFO('\n2. Creating SpaceTypes, Amenities, Spaces, BookableAmenities...'))

            space_types_data = [
                {'name': 'Lecture Hall', 'default_is_bookable': True},
                {'name': 'Meeting Room', 'default_is_bookable': True},
                {'name': 'Lab', 'default_is_bookable': True,
                 'default_requires_approval': True},
                {'name': 'Study Zone', 'default_is_bookable': True},
                {'name': 'Sports Field', 'default_is_bookable': True},
                {'name': 'Office', 'is_basic_infrastructure': True,
                 'default_is_bookable': False},
            ]
            space_types = {}
            for data in space_types_data:
                defaults_with_time = {
                    'description': '',
                    'default_available_start_time': time(8, 0),
                    'default_available_end_time': time(22, 0),
                    'default_min_booking_duration': timedelta(minutes=30),
                    'default_max_booking_duration': timedelta(hours=4),
                    'default_buffer_time_minutes': 0,
                    **{k: v for k, v in data.items() if k != 'name'}
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
                {'name': 'Water Dispenser', 'description': '饮水机', 'is_bookable_individually': True},
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

            parent_space1, created = Space.objects.get_or_create(
                name='Main Building - Floor 1',
                defaults={'location': 'Central Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager1, 'is_bookable': False}
            )
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
                s_amenities_list = s_data.pop('amenities')

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

                for amenity_item in s_amenities_list:
                    amenity_name = amenity_item[0]
                    quantity = amenity_item[1]
                    is_bookable_instance = amenity_item[2] if len(amenity_item) > 2 else amenities[
                        amenity_name].is_bookable_individually

                    book_am, am_created = BookableAmenity.objects.get_or_create(
                        space=space,
                        amenity=amenities[amenity_name],
                        defaults={'quantity': quantity, 'is_bookable': is_bookable_instance}
                    )
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
                space_type=None,
                threshold_points=10,
                ban_duration=timedelta(days=90),
                defaults={'priority': 30, 'is_active': True, 'description': '违约10点全局禁用90天'}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Global Ban Policy: {global_policy.description}"))

            penalty1, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=regular_user1,
                space_type=space_types['Meeting Room'],
                defaults={'current_penalty_points': 2, 'last_violation_at': timezone.now()}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Penalty for {regular_user1.username}: {penalty1.current_penalty_points} points in {space_types['Meeting Room'].name}"))

            penalty_banned, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=banned_user,
                space_type=space_types['Meeting Room'],
                defaults={'current_penalty_points': 4, 'last_violation_at': timezone.now()}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Penalty for {banned_user.username}: {penalty_banned.current_penalty_points} points in {space_types['Meeting Room'].name} (should trigger ban)"))

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
                space_type=None,
                defaults={'start_date': timezone.now(),
                          'end_date': timezone.now() + timedelta(days=90),
                          'reason': 'Manual Global Ban',
                          'ban_policy_applied': global_policy, 'issued_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Global User Ban for {user_ban2.user.username}: {user_ban2.reason}"))

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

            exemption_student, created = UserSpaceTypeExemption.objects.get_or_create(
                user=student_user,
                space_type=space_types['Study Zone'],
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

        # --- 4. 创建 Daily Booking Limits (重点修改部分) ---
        if DailyBookingLimit:
            self.stdout.write(self.style.HTTP_INFO('\n4. Creating Daily Booking Limits...'))

            # --- 全局每日预订限制 ---
            # 普通用户组：全局每日预订3次，优先级较低
            DailyBookingLimit.objects.get_or_create(
                group=general_user_group,
                space_type=None,  # 全局限制
                defaults={'max_bookings': 3, 'priority': 10, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  Daily Booking Limit for '{general_user_group.name}' (Global): 3 bookings/day (Priority:10)"))

            # 学生组：全局每日预订5次，优先级适中
            DailyBookingLimit.objects.get_or_create(
                group=student_group,
                space_type=None,  # 全局限制
                defaults={'max_bookings': 5, 'priority': 20, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  Daily Booking Limit for '{student_group.name}' (Global): 5 bookings/day (Priority:20)"))

            # 教师组：全局每日预订10次，优先级较高
            DailyBookingLimit.objects.get_or_create(
                group=teacher_group,
                space_type=None,  # 全局限制
                defaults={'max_bookings': 10, 'priority': 30, 'is_active': True}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  Daily Booking Limit for '{teacher_group.name}' (Global): 10 bookings/day (Priority:30)"))

            # 系统管理员和空间管理员：全局没有每日预订次数限制 (max_bookings=0 表示无限制)
            DailyBookingLimit.objects.get_or_create(
                group=system_admin_group,
                space_type=None,  # 全局限制
                defaults={'max_bookings': 0, 'priority': 100, 'is_active': True}  # 最高优先级
            )
            self.stdout.write(self.style.SUCCESS(
                f"  Daily Booking Limit for '{system_admin_group.name}' (Global): 无限制 (Priority:100)"))

            DailyBookingLimit.objects.get_or_create(
                group=space_manager_group,
                space_type=None,  # 全局限制
                defaults={'max_bookings': 0, 'priority': 90, 'is_active': True}  # 较高优先级
            )
            self.stdout.write(self.style.SUCCESS(
                f"  Daily Booking Limit for '{space_manager_group.name}' (Global): 无限制 (Priority:90)"))

            # --- 特定空间类型的每日预订限制 (需要 space_types 字典填充完毕) ---
            if space_types:
                # 为 '普通用户' 组在 'Meeting Room' 空间类型下设置更严格的限制
                # 优先级高于其全局限制 (10 > 5)
                DailyBookingLimit.objects.get_or_create(
                    group=general_user_group,
                    space_type=space_types['Meeting Room'],
                    defaults={'max_bookings': 1, 'priority': 15, 'is_active': True}  # 特定类型规则优先于全局规则
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  Daily Booking Limit for '{general_user_group.name}' ({space_types['Meeting Room'].name}): 1 booking/day (Priority:15)"))

                # 为 '学生' 组在 'Lab' 空间类型下设置更宽松的限制，例如实验室可能需要更多访问
                DailyBookingLimit.objects.get_or_create(
                    group=student_group,
                    space_type=space_types['Lab'],
                    defaults={'max_bookings': 7, 'priority': 25, 'is_active': True}  # 优先级高于其全局的学生限制
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  Daily Booking Limit for '{student_group.name}' ({space_types['Lab'].name}): 7 bookings/day (Priority:25)"))

                # 针对 '教师' 组在 'Lecture Hall' 类型的空间做另一个限制，例如可以预订更多次
                DailyBookingLimit.objects.get_or_create(
                    group=teacher_group,
                    space_type=space_types['Lecture Hall'],
                    defaults={'max_bookings': 20, 'priority': 35, 'is_active': True}  # 优先级高于其全局的教师限制
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  Daily Booking Limit for '{teacher_group.name}' ({space_types['Lecture Hall'].name}): 20 bookings/day (Priority:35)"))

        else:
            self.stdout.write(self.style.WARNING(
                "\nSkipping creation of Daily Booking Limits due to missing 'DailyBookingLimit' model."))

        self.stdout.write(self.style.HTTP_INFO('\nTest data import complete!'))