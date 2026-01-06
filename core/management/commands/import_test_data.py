# core/management/commands/import_test_data.py (修正版本)

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from guardian.shortcuts import assign_perm

import random
from datetime import timedelta

# Try to import necessary models. Use mock if not available (though for test data, they should be loaded)
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
        UserSpaceTypeExemption
    )
except ImportError:
    SpaceTypeBanPolicy = None
    UserPenaltyPointsPerSpaceType = None
    UserSpaceTypeBan = None
    UserSpaceTypeExemption = None
    print("Warning: 'bookings' app models not available. Data import for booking policies will be skipped.")

CustomUser = get_user_model()


class Command(BaseCommand):
    help = 'Imports essential test data including users, spaces, amenities, and booking policies.'

    def _create_user_with_roles(self, username, password, email, is_staff=False, is_superuser=False,
                                # 移除 is_system_admin 和 is_space_manager 参数，因为它们将通过组来管理
                                first_name='', last_name=''):
        user, created = CustomUser.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'is_staff': is_staff,
                'is_superuser': is_superuser,
                'is_active': True,
                'first_name': first_name,
                'last_name': last_name,
                # 移除此处对 is_system_admin 和 is_space_manager 的直接赋值
            }
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"  Created user: {username}"))
        else:
            self.stdout.write(self.style.WARNING(f"  User '{username}' already exists. Skipping creation."))
            # 对于已存在的用户，可以直接更新内置的 is_staff 和 is_superuser 标志
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            user.save()  # 保存以应用 is_staff/is_superuser 的更改
            # 自定义角色（如 is_system_admin, is_space_manager）是派生属性，不能直接赋值。
            # 它们的管理通过用户组完成，在 handle 方法中会处理。
        return user

        # core/management/commands/import_test_data.py

        # ... (前面的导入和 _create_user_with_roles 保持不变) ...

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Starting test data import...'))

        # --- 1. 创建 Users and Groups ---
        self.stdout.write(self.style.HTTP_INFO('\n1. Creating Users and Groups...'))

        # 创建 '空间管理员' 组
        space_manager_group, created = Group.objects.get_or_create(name='空间管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '空间管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '空间管理员' already exists."))

        # 创建 '系统管理员' 组
        system_admin_group, created = Group.objects.get_or_create(name='系统管理员')
        if created:
            self.stdout.write(self.style.SUCCESS("  Created group: '系统管理员'"))
        else:
            self.stdout.write(self.style.WARNING("  Group '系统管理员' already exists."))

        # 创建特定用户
        superuser = self._create_user_with_roles('admin', 'admin123', 'admin@example.com',
                                                 is_staff=True, is_superuser=True, first_name='Super',
                                                 last_name='User')
        sysadmin = self._create_user_with_roles('sysadmin', 'sysadmin123', 'sysadmin@example.com',
                                                is_staff=True, first_name='System',
                                                last_name='Admin')
        space_manager1 = self._create_user_with_roles('spaceman1', 'spaceman123', 'spaceman1@example.com',
                                                      is_staff=True, first_name='Space',
                                                      last_name='Manager One')
        space_manager2 = self._create_user_with_roles('spaceman2', 'spaceman234', 'spaceman2@example.com',
                                                      is_staff=True, first_name='Space',
                                                      last_name='Manager Two')
        space_manager3 = self._create_user_with_roles('spaceman3', 'spaceman345', 'spaceman3@example.com',
                                                      is_staff=True, first_name='Space',
                                                      last_name='Manager Three')
        regular_user1 = self._create_user_with_roles('user1', 'user123', 'user1@example.com',
                                                     first_name='Regular', last_name='User One')
        regular_user2 = self._create_user_with_roles('user2', 'user234', 'user2@example.com',
                                                     first_name='Regular', last_name='User Two')
        banned_user = self._create_user_with_roles('banneduser', 'banned123', 'banned@example.com',
                                                   first_name='Banned', last_name='User')

        # 将 sysadmin 用户添加到 '系统管理员' 组
        if not sysadmin.groups.filter(name=system_admin_group.name).exists():
            sysadmin.groups.add(system_admin_group)
            self.stdout.write(self.style.SUCCESS(f"  Added {sysadmin.username} to '系统管理员' group."))
        else:
            self.stdout.write(self.style.WARNING(f"  {sysadmin.username} is already in '系统管理员' group."))
        # IMPORTANT: Force save sysadmin to re-evaluate is_staff after group is added
        sysadmin.save()
        self.stdout.write(
            self.style.SUCCESS(f"  Ensured {sysadmin.username}'s is_staff reflects group membership."))

        # 将空间管理员用户添加到 '空间管理员' 组
        for sm in [space_manager1, space_manager2, space_manager3]:
            if not sm.groups.filter(name=space_manager_group.name).exists():
                sm.groups.add(space_manager_group)
                self.stdout.write(self.style.SUCCESS(f"  Added {sm.username} to '空间管理员' group."))
            else:
                self.stdout.write(self.style.WARNING(f"  {sm.username} is already in '空间管理员' group."))
            # IMPORTANT: Force save space manager to re-evaluate is_staff after group is added
            sm.save()
            self.stdout.write(self.style.SUCCESS(f"  Ensured {sm.username}'s is_staff reflects group membership."))

    # ... (以下部分保持不变) ...

        # --- 2. 创建 SpaceTypes, Amenities, Spaces & BookableAmenities ---
        # 以下部分保持不变，但为了完整性再次包含
        if Space and SpaceType and Amenity and BookableAmenity:
            self.stdout.write(self.style.HTTP_INFO('\n2. Creating SpaceTypes, Amenities, Spaces, BookableAmenities...'))

            space_types_data = [
                {'name': 'Lecture Hall', 'is_container_type': False, 'default_is_bookable': True},
                {'name': 'Meeting Room', 'is_container_type': False, 'default_is_bookable': True},
                {'name': 'Lab', 'is_container_type': False, 'default_is_bookable': True,
                 'default_requires_approval': True},
                {'name': 'Study Zone', 'is_container_type': False, 'default_is_bookable': True},
                {'name': 'Sports Field', 'is_container_type': False, 'default_is_bookable': True},
                {'name': 'Office', 'is_container_type': False, 'is_basic_infrastructure': True,
                 'default_is_bookable': False},
            ]
            space_types = {}
            for data in space_types_data:
                st, created = SpaceType.objects.get_or_create(
                    name=data['name'],
                    defaults={k: v for k, v in data.items() if k != 'name'}
                )
                space_types[st.name] = st
                self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Existing'} SpaceType: {st.name}"))

            amenities_data = [
                {'name': 'Projector', 'description': '高清投影仪'},
                {'name': 'Whiteboard', 'description': '交互式白板'},
                {'name': 'Video Conferencing', 'description': '视频会议设备'},
                {'name': 'Water Dispenser', 'description': '饮水机'},
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
            parent_space1, created = Space.objects.get_or_create(
                name='Main Building - Floor 1',
                defaults={'location': 'Central Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager1}
            )
            # 确保即使空间已存在，权限也能分配，以保证幂等性
            assign_perm('spaces.can_manage_space_details', space_manager1, parent_space1)
            assign_perm('spaces.can_manage_space_bookings', space_manager1, parent_space1)
            assign_perm('spaces.can_manage_space_amenities', space_manager1, parent_space1)
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space1.name}"))

            parent_space2, created = Space.objects.get_or_create(
                name='Innovation Hub',
                defaults={'location': 'East Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager2}
            )
            assign_perm('spaces.can_manage_space_details', space_manager2, parent_space2)
            assign_perm('spaces.can_manage_space_bookings', space_manager2, parent_space2)
            assign_perm('spaces.can_manage_space_amenities', space_manager2, parent_space2)
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space2.name}"))

            parent_space3, created = Space.objects.get_or_create(
                name='Outdoor Facilities',
                defaults={'location': 'West Campus', 'is_container': True, 'space_type': space_types['Office'],
                          'managed_by': space_manager3}
            )
            assign_perm('spaces.can_manage_space_details', space_manager3, parent_space3)
            assign_perm('spaces.can_manage_space_bookings', space_manager3, parent_space3)
            assign_perm('spaces.can_manage_space_amenities', space_manager3, parent_space3)
            self.stdout.write(
                self.style.SUCCESS(f"  {'Created' if created else 'Existing'} Parent Space: {parent_space3.name}"))

            # Child Bookable Spaces
            spaces_data = [
                {'name': 'Room 101', 'location': 'Floor 1', 'space_type': space_types['Meeting Room'],
                 'parent_space': parent_space1, 'capacity': 10, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager1, 'amenities': [('Projector', 1), ('Whiteboard', 1)]},  # 关联设施
                {'name': 'Lecture Hall A', 'location': 'Floor 1', 'space_type': space_types['Lecture Hall'],
                 'parent_space': parent_space1, 'capacity': 100, 'is_bookable': True, 'requires_approval': True,
                 'managed_by': space_manager1, 'amenities': [('Projector', 1), ('Video Conferencing', 1)]},
                {'name': 'Lab 3B', 'location': 'Research Wing', 'space_type': space_types['Lab'],
                 'parent_space': parent_space2, 'capacity': 20, 'is_bookable': True, 'requires_approval': True,
                 'managed_by': space_manager2, 'amenities': [('Water Dispenser', 1), ('Internet Access', 1)]},
                {'name': 'Meeting Pod 5', 'location': 'Collaborative Zone', 'space_type': space_types['Study Zone'],
                 'parent_space': parent_space2, 'capacity': 4, 'is_bookable': True, 'requires_approval': False,
                 'managed_by': space_manager2, 'amenities': []},  # 无设施
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
                s_amenities = s_data.pop('amenities')  # 提取设施数据
                space, created = Space.objects.get_or_create(
                    name=s_data['name'],
                    defaults={k: v for k, v in s_data.items() if k != 'name'}
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(f"  Created Bookable Space: {space.name}"))
                else:
                    self.stdout.write(self.style.WARNING(f"  Space '{space.name}' already exists. Skipping creation."))

                # 始终分配空间和可预订设施的权限，即使它们已存在。
                # 这确保了权限的幂等性。
                assign_perm('spaces.can_manage_space_details', space.managed_by, space)
                assign_perm('spaces.can_manage_space_bookings', space.managed_by, space)
                assign_perm('spaces.can_manage_space_amenities', space.managed_by, space)

                # Add/Update Bookable Amenities
                for amenity_name, quantity in s_amenities:
                    book_am, am_created = BookableAmenity.objects.get_or_create(
                        space=space,
                        amenity=amenities[amenity_name],
                        defaults={'quantity': quantity, 'is_bookable': True}
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f"    {'Created' if am_created else 'Existing'} Bookable Amenity: {amenity_name} for {space.name}"))
                    assign_perm('spaces.can_manage_bookable_amenity', space.managed_by, book_am)

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

            # User that triggers a ban
            penalty_banned, created = UserPenaltyPointsPerSpaceType.objects.get_or_create(
                user=banned_user,
                space_type=space_types['Meeting Room'],
                defaults={'current_penalty_points': 4, 'last_violation_at': timezone.now()}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Penalty for {banned_user.username}: {penalty_banned.current_penalty_points} points in {space_types['Meeting Room'].name} (should trigger ban)"))

            # User Bans
            user_ban1, created = UserSpaceTypeBan.objects.get_or_create(
                user=banned_user,
                space_type=space_types['Meeting Room'],
                start_date=timezone.now(),
                end_date=(timezone.now() + timedelta(days=7)).date(),  # 确保是 date 对象，如果模型字段是 DateField
                defaults={'reason': 'Triggered by Meeting Room policy',
                          'ban_policy_applied': policy1, 'issued_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} User Ban for {user_ban1.user.username}: {user_ban1.reason}"))

            user_ban2, created = UserSpaceTypeBan.objects.get_or_create(
                user=regular_user2,
                space_type=None,  # 全局禁用
                start_date=timezone.now(),
                end_date=(timezone.now() + timedelta(days=90)).date(),  # 确保是 date 对象
                defaults={'reason': 'Manual Global Ban',
                          'ban_policy_applied': global_policy, 'issued_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} Global User Ban for {user_ban2.user.username}: {user_ban2.reason}"))

            # User Exemptions
            exemption1, created = UserSpaceTypeExemption.objects.get_or_create(
                user=regular_user1,
                space_type=space_types['Lab'],
                start_date=timezone.now(),
                end_date=(timezone.now() + timedelta(days=30)).date(),  # 确保是 date 对象
                defaults={'exemption_reason': 'Research Project',
                          'granted_by': sysadmin}
            )
            self.stdout.write(self.style.SUCCESS(
                f"  {'Created' if created else 'Existing'} User Exemption for {exemption1.user.username}: {exemption1.exemption_reason}"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nSkipping creation of Booking Policies and User Ban/Penalty Data due to missing models."))

        self.stdout.write(self.style.HTTP_INFO('\nTest data import complete!'))