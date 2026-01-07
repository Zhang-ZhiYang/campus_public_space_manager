# bookings/tests/test_api.py
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.models import Group, Permission
from guardian.shortcuts import assign_perm, remove_perm # 用于对象级权限

from users.models import CustomUser
from spaces.models import SpaceType, Space, Amenity, BookableAmenity
from bookings.models import Booking

class BookingAPITests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        # 创建用户
        cls.admin_user = CustomUser.objects.create_superuser(
            username='admin', email='admin@example.com', password='password'
        )
        cls.amenity_booker_user = CustomUser.objects.create_user(
            username='amenity_booker', email='amenity@example.com', password='password'
        )
        cls.regular_user = CustomUser.objects.create_user(
            username='regular_user', email='regular@example.com', password='password'
        )
        cls.restricted_user = CustomUser.objects.create_user(
            username='restricted_user', email='restricted@example.com', password='password'
        )

        # 创建空间类型
        cls.space_type_lecture_hall = SpaceType.objects.create(name='Lecture Hall')
        cls.space_type_office = SpaceType.objects.create(name='Office')

        # 创建空间A：一个普通的报告厅，可以被分配权限
        cls.space_a = Space.objects.create(
            name='Lecture Hall A', location='Building A, Room 101',
            space_type=cls.space_type_lecture_hall,
            is_bookable=True, is_active=True,
            capacity=100, requires_approval=False,
            min_booking_duration=timedelta(minutes=30),
            max_booking_duration=timedelta(hours=4),
            available_start_time=timezone.now().time(),
            available_end_time=(timezone.now() + timedelta(hours=8)).time(),
        )
        # 创建一个不可预订的空间
        cls.space_non_bookable = Space.objects.create(
            name='Storage Room', location='Basement',
            space_type=cls.space_type_office,
            is_bookable=False, is_active=True,
            capacity=5, requires_approval=False,
        )

        # 创建设施类型
        cls.amenity_projector = Amenity.objects.create(
            name='Projector', is_bookable_individually=True
        )
        cls.amenity_microphone = Amenity.objects.create(
            name='Microphone', is_bookable_individually=False # 不可单独预订的设施
        )

        # 创建Space A下的可预订设施实例
        cls.bookable_amenity_proj_a = BookableAmenity.objects.create(
            space=cls.space_a, amenity=cls.amenity_projector,
            quantity=2, is_bookable=True, is_active=True
        )
        # 创建Space A下不可预订的设施实例
        cls.bookable_amenity_mic_a_non_bookable = BookableAmenity.objects.create(
            space=cls.space_a, amenity=cls.amenity_microphone,
            quantity=1, is_bookable=False, is_active=True # 即使类型为不可单独预订，实例也可以手动设置为不可预订
        )

        # 获取权限对象
        cls.perm_book_space = Permission.objects.get(codename='can_book_this_space', content_type__app_label='spaces')
        cls.perm_book_amenities_in_space = Permission.objects.get(codename='can_book_amenities_in_space', content_type__app_label='spaces')

        # 创建用户组：AmenityBookers 和 RestrictedGroup
        cls.group_amenity_bookers, _ = Group.objects.get_or_create(name='设施预订员')
        cls.group_restricted, _ = Group.objects.get_or_create(name='限制用户组')

        # 将 restricted_user 加入 RestrictedGroup
        cls.restricted_user.groups.add(cls.group_restricted)
        # 将 RestrictedGroup 绑定到 space_a 的 restricted_groups 字段上
        cls.space_a.restricted_groups.add(cls.group_restricted)
        cls.space_a.save() # 确保 ManyToMany 关系保存

        # =============================================================
        # 核心权限配置：赋予 Amenity Booker 预订 Space A 中设施的权限，但不能预订 Space A 本身
        # =============================================================
        assign_perm(cls.perm_book_amenities_in_space, cls.amenity_booker_user, cls.space_a)
        # 不给 cls.amenity_booker_user can_book_this_space 权限

        cls.booking_create_url = reverse('bookings_api:booking-create')

    def _create_booking_data(self, target_type='space', target_id=None, days_from_now=1, duration_hours=1):
        start_time = timezone.now() + timedelta(days=days_from_now)
        end_time = start_time + timedelta(hours=duration_hours)
        data = {
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'purpose': 'Test booking',
            'booked_quantity': 1,
        }
        if target_type == 'space':
            data['space_id'] = target_id if target_id else self.space_a.id
        elif target_type == 'amenity':
            data['bookable_amenity_id'] = target_id if target_id else self.bookable_amenity_proj_a.id
        return data

    def test_admin_can_book_space(self):
        """管理员可以预订空间。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='space', target_id=self.space_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertIn('booking_id', response.data)

    def test_admin_can_book_amenity(self):
        """管理员可以预订设施。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='amenity', target_id=self.bookable_amenity_proj_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertIn('booking_id', response.data)

    def test_amenity_booker_can_book_amenity_in_space(self):
        """设施预订员可以预订 Space A 中的设施。"""
        self.client.force_authenticate(user=self.amenity_booker_user)
        data = self._create_booking_data(target_type='amenity', target_id=self.bookable_amenity_proj_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertIn('booking_id', response.data)
        book = Booking.objects.first()
        self.assertEqual(book.bookable_amenity, self.bookable_amenity_proj_a)
        self.assertEqual(book.user, self.amenity_booker_user)

    def test_amenity_booker_cannot_book_space_directly(self):
        """设施预订员不能直接预订 Space A (核心测试)。"""
        self.client.force_authenticate(user=self.amenity_booker_user)
        data = self._create_booking_data(target_type='space', target_id=self.space_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("没有权限预订空间", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0) # 确保没有创建预订

    def test_regular_user_cannot_book_space(self):
        """普通用户不能预订空间。"""
        self.client.force_authenticate(user=self.regular_user)
        data = self._create_booking_data(target_type='space', target_id=self.space_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("没有权限预订空间", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_regular_user_cannot_book_amenity(self):
        """普通用户不能预订设施。"""
        self.client.force_authenticate(user=self.regular_user)
        data = self._create_booking_data(target_type='amenity', target_id=self.bookable_amenity_proj_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("没有权限预订空间", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_non_existent_space(self):
        """预订不存在的空间。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='space', target_id=99999)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("空间不存在", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_non_existent_amenity(self):
        """预订不存在的设施。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='amenity', target_id=99999)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("可预订设施实例不存在", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_non_bookable_space(self):
        """预订不可预订的空间。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='space', target_id=self.space_non_bookable.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不可预订或未启用", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_non_bookable_amenity(self):
        """预订不可预订的设施。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data(target_type='amenity', target_id=self.bookable_amenity_mic_a_non_bookable.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("不可预订或未启用", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_restricted_space_by_group(self):
        """用户属于受限组，不能预订空间。"""
        self.client.force_authenticate(user=self.restricted_user)
        data = self._create_booking_data(target_type='space', target_id=self.space_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("用户组被限制预订空间", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_restricted_amenity_by_group(self):
        """用户属于受限组，不能预订该空间下的设施。"""
        self.client.force_authenticate(user=self.restricted_user)
        data = self._create_booking_data(target_type='amenity', target_id=self.bookable_amenity_proj_a.id)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("用户组被限制预订空间", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    def test_no_target_specified(self):
        """未指定预订目标。"""
        self.client.force_authenticate(user=self.admin_user)
        data = self._create_booking_data() # 不设置 target_type 或 target_id
        data.pop('space_id', None)
        data.pop('bookable_amenity_id', None)
        response = self.client.post(self.booking_create_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("预订必须指定一个空间ID或设施ID", response.data['detail'])
        self.assertEqual(Booking.objects.count(), 0)

    # TODO: 可以继续添加更多测试，例如：
    # - 预订时间冲突 (由 Booking.clean() 内部逻辑处理)
    # - 预订数量超出设施容量
    # - 禁用策略生效 (Booking.clean() 的一部分)
    # - 未认证用户尝试预订