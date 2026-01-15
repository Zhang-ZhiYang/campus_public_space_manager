# spaces/management/commands/test_amenity_cache.py
import logging
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from spaces.service.amenity_service import AmenityService
from spaces.models import Amenity
from core.service.cache import CacheService
from time import sleep

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Tests Amenity caching mechanism: hit, miss, and invalidation.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting Amenity Cache Test..."))

        # ===============================================
        # 0. 准备：获取用户、实例化 Service
        # ===============================================
        admin_user = get_user_model().objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = get_user_model().objects.create_superuser('testadmin', 'admin@example.com', 'adminpassword123')
            self.stdout.write(self.style.WARNING("Created a new superuser: testadmin"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Using existing superuser: {admin_user.username}"))

        amenity_service = AmenityService()

        # ===============================================
        # 1. 清理并创建初始 Amenity 数据
        # ===============================================
        self.stdout.write("\n--- 1. 清理并创建初始 Amenity 数据 ---")
        Amenity.objects.all().delete() # 清理所有旧的设施类型

        # !!! 修复点1: 确保列表缓存清理时明确指定 custom_postfix
        CacheService.invalidate_list_cache(key_prefix='spaces:amenity:list_all', custom_postfix='list_all')
        self.stdout.write(self.style.SUCCESS("已清空旧的 Amenity 数据和相关列表缓存。"))

        amenity1_data = {'name': 'Projector', 'description': 'High-resolution projector', 'is_bookable_individually': False}
        amenity2_data = {'name': 'Whiteboard', 'description': 'Standard whiteboard with markers', 'is_bookable_individually': True}

        res1 = amenity_service.create_amenity(admin_user, amenity1_data)
        # !!! 修复点2: 使用 .success 属性
        amenity1_pk = res1.data['id'] if res1.success else None
        self.stdout.write(self.style.SUCCESS(f"创建 Projector: {res1.message} (PK: {amenity1_pk})"))

        res2 = amenity_service.create_amenity(admin_user, amenity2_data)
        # !!! 修复点3: 使用 .success 属性
        amenity2_pk = res2.data['id'] if res2.success else None
        self.stdout.write(self.style.SUCCESS(f"创建 Whiteboard: {res2.message} (PK: {amenity2_pk})"))

        self.stdout.write(self.style.NOTICE("\n--- 检查 Celery Worker 日志，确认 create_amenity 后触发了 invalidate_amenity_cache 任务 ---"))
        self.stdout.write(self.style.NOTICE("等待 Celery 任务执行完成 (3秒)..."))
        sleep(3) # 给 Celery worker 一点时间来处理任务

        # ===============================================
        # 2. 第一次获取 Amenity 详情 (期望 Cache MISS)
        # ===============================================
        self.stdout.write("\n--- 2. 第一次获取 Amenity 详情 (期望 Cache MISS) ---")
        res = amenity_service.get_amenity_by_id(admin_user, amenity1_pk)
        self.stdout.write(f"获取 Projector (PK:{amenity1_pk}): {res.message}. Name: {res.data['name'] if res.success else 'N/A'}")
        self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache MISS"))
        sleep(1)

        # ===============================================
        # 3. 第二次获取 Amenity 详情 (期望 Cache HIT)
        # ===============================================
        self.stdout.write("\n--- 3. 第二次获取 Amenity 详情 (期望 Cache HIT) ---")
        res = amenity_service.get_amenity_by_id(admin_user, amenity1_pk)
        self.stdout.write(f"获取 Projector (PK:{amenity1_pk}): {res.message}. Name: {res.data['name'] if res.success else 'N/A'}")
        self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache HIT"))
        sleep(1)

        # ===============================================
        # 4. 第一次获取所有 Amenity 列表 (期望 Cache MISS)
        # ===============================================
        self.stdout.write("\n--- 4. 第一次获取所有 Amenity 列表 (期望 Cache MISS) ---")
        res = amenity_service.get_all_amenities(admin_user)
        # !!! 修复点4: 使用 .success 属性
        self.stdout.write(f"获取所有设施列表: {res.message}, 数量: {len(res.data) if res.success else 0}")
        self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache MISS"))
        sleep(1)

        # ===============================================
        # 5. 第二次获取所有 Amenity 列表 (期望 Cache HIT)
        # ===============================================
        self.stdout.write("\n--- 5. 第二次获取所有 Amenity 列表 (期望 Cache HIT) ---")
        res = amenity_service.get_all_amenities(admin_user)
        # !!! 修复点5: 使用 .success 属性
        self.stdout.write(f"获取所有设施列表: {res.message}, 数量: {len(res.data) if res.success else 0}")
        self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache HIT"))
        sleep(1)

        # ===============================================
        # 6. 更新 Amenity (期望触发缓存失效)
        # ===============================================
        self.stdout.write("\n--- 6. 更新 Amenity (期望触发缓存失效) ---")
        update_data = {'name': 'Projector V2'}
        res = amenity_service.update_amenity(admin_user, amenity1_pk, update_data)
        # !!! 修复点6: 使用 .success 属性
        if res.success:
            self.stdout.write(self.style.SUCCESS(f"更新 Projector (PK:{amenity1_pk}): {res.message}. 新名称: {res.data['name']}"))
            self.stdout.write(self.style.NOTICE("\n--- 检查 Celery Worker 日志，确认 update_amenity 后再次触发 invalidate_amenity_cache 任务 ---"))
            self.stdout.write(self.style.NOTICE("等待 Celery 任务执行完成 (3秒)..."))
            sleep(3) # 等待 Celery 任务处理失效

            self.stdout.write("\n--- 7. 再次获取更新后的 Amenity 详情 (期望 Cache MISS，因为已失效) ---")
            res = amenity_service.get_amenity_by_id(admin_user, amenity1_pk)
            # !!! 修复点7: 使用 .success 属性
            self.stdout.write(f"获取 Projector (PK:{amenity1_pk}): {res.message}. 名称: {res.data['name'] if res.success else 'N/A'}")
            self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache MISS"))
            sleep(1)

            self.stdout.write("\n--- 8. 再次获取所有 Amenity 列表 (期望 Cache MISS，因为已失效) ---")
            res = amenity_service.get_all_amenities(admin_user)
            # !!! 修复点8: 使用 .success 属性
            self.stdout.write(f"获取所有设施列表: {res.message}, 数量: {len(res.data) if res.success else 0}, 其中一个名称是: {res.data[0]['name'] if res.success and len(res.data) > 0 else 'N/A'}")
            self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache MISS"))
            sleep(1)
        else:
            self.stdout.write(self.style.ERROR(f"更新失败，跳过后续缓存失效测试。错误: {res.message}"))
            raise CommandError("Amenity update failed during test.")

        # ===============================================
        # 9. 删除 Amenity (期望触发缓存失效)
        # ===============================================
        self.stdout.write("\n--- 9. 删除 Amenity (期望触发缓存失效) ---")
        res = amenity_service.delete_amenity(admin_user, amenity2_pk)
        # !!! 修复点9: 使用 .success 属性
        if res.success:
            self.stdout.write(self.style.SUCCESS(f"删除 Whiteboard (PK:{amenity2_pk}): {res.message}"))
            self.stdout.write(self.style.NOTICE("\n--- 检查 Celery Worker 日志，确认 delete_amenity 后再次触发 invalidate_amenity_cache 任务 ---"))
            self.stdout.write(self.style.NOTICE("等待 Celery 任务执行完成 (3秒)..."))
            sleep(3) # 等待 Celery 任务处理失效

            self.stdout.write("\n--- 10. 再次获取所有 Amenity 列表 (期望 Cache MISS，因为已失效) ---")
            res = amenity_service.get_all_amenities(admin_user)

            # !!! 修复点10: 使用 .success 属性
            if res.success and len(res.data) > 0:
                self.stdout.write(f"获取所有设施列表: {res.message}, 数量: {len(res.data)}")
                self.stdout.write(f"剩下设施的名称: {[a['name'] for a in res.data]}")
            else:
                 self.stdout.write(f"列表中没有设施或获取失败: {res.message}")
            self.stdout.write(self.style.NOTICE("检查 Celery Worker 日志: 应该看到 Cache MISS"))
            sleep(1)
        else:
            self.stdout.write(self.style.ERROR(f"删除失败，跳过后续缓存失效测试。错误: {res.message}"))
            raise CommandError("Amenity delete failed during test.")

        self.stdout.write(self.style.SUCCESS("\n--- Amenity Cache Test Complete ---"))
        self.stdout.write(self.style.NOTICE("请结合 Celery Worker 终端的日志输出进行最终判断。"))