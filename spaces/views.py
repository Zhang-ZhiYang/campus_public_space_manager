# # spaces/views.py
# from rest_framework import viewsets, status
# from rest_framework.decorators import action
# from rest_framework.permissions import IsAuthenticated
#
# from core.utils.response import success_response, error_response
# from core.utils.exceptions import CustomAPIException
# from core.utils.constants import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT, MSG_SUCCESS, MSG_CREATED, \
#     MSG_NO_CONTENT, MSG_VALIDATION_ERROR, MSG_FORBIDDEN
#
# from spaces.models import Amenity, Space
# from spaces.serializers import (
#     AmenitySerializer, SpaceBaseSerializer, SpaceListSerializer,
#     SpaceCreateUpdateSerializer
# )
# from spaces.services import AmenityService, SpaceService
# # 导入自定义权限类，请确保它们在 users 应用中定义并可见
# from users.permissions import IsAdminOrSpaceManagerOrReadOnly, IsAdminOrSuperAdmin, IsAdminOrSpaceManager
#
#
# class AmenityViewSet(viewsets.ModelViewSet):
#     """
#     设施管理视图集。
#     - 认证用户可查看。
#     - 管理员/空间管理员可创建、更新、删除。
#     """
#     queryset = Amenity.objects.all()
#     serializer_class = AmenitySerializer
#     permission_classes = [IsAuthenticated, IsAdminOrSpaceManagerOrReadOnly]  # 权限设置：认证用户可读，管理员/空间管理员可写
#
#     def list(self, request, *args, **kwargs):
#         try:
#             filters = request.query_params.dict()
#             amenities = AmenityService.list_amenities(request.user, filters)
#             serializer = self.get_serializer(amenities, many=True)
#             return success_response(MSG_SUCCESS, data=serializer.data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             # 捕获其他未知错误，统一格式，便于调试
#             return error_response(message="列出设施失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def retrieve(self, request, pk=None, *args, **kwargs):
#         try:
#             amenity = AmenityService.get_amenity(request.user, pk)
#             serializer = self.get_serializer(amenity)
#             return success_response(MSG_SUCCESS, data=serializer.data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="获取设施详情失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def create(self, request, *args, **kwargs):
#         serializer = self.get_serializer(data=request.data)
#         # 序列化器验证失败时，DRF会自动抛出 ValidationError，被全局异常处理器捕获
#         serializer.is_valid(raise_exception=True)
#         try:
#             amenity = AmenityService.create_amenity(request.user, **serializer.validated_data)
#             return success_response(MSG_CREATED, data=self.get_serializer(amenity).data, status_code=HTTP_201_CREATED)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="创建设施失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def update(self, request, pk=None, *args, **kwargs):
#         # partial=True 允许部分更新 (PATCH 请求)
#         partial = kwargs.get('partial', False)
#         # 获取当前实例以便序列化器进行部分更新和字段验证
#         instance = AmenityService.get_amenity(request.user, pk)
#         serializer = self.get_serializer(instance, data=request.data, partial=partial)
#         serializer.is_valid(raise_exception=True)
#         try:
#             amenity = AmenityService.update_amenity(request.user, pk, serializer.validated_data)
#             return success_response(MSG_SUCCESS, data=self.get_serializer(amenity).data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="更新设施失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def destroy(self, request, pk=None, *args, **kwargs):
#         try:
#             AmenityService.delete_amenity(request.user, pk)
#             return success_response(MSG_NO_CONTENT, status_code=HTTP_204_NO_CONTENT)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="删除设施失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#
# class SpaceViewSet(viewsets.ModelViewSet):
#     """
#     空间管理视图集。
#     - 认证用户可查看。
#     - 管理员/空间管理员可创建、更新。
#     - 系统管理员/超级管理员可删除。
#     """
#     queryset = Space.objects.all()
#     # 默认权限：认证用户可读，管理员/空间管理员可创建/更新，删除权限可能更严格
#     permission_classes = [IsAuthenticated, IsAdminOrSpaceManagerOrReadOnly]
#
#     def get_serializer_class(self):
#         if self.action == 'list':
#             return SpaceListSerializer
#         elif self.action == 'retrieve':
#             return SpaceBaseSerializer  # 详情页显示完整信息
#         elif self.action in ['create', 'update', 'partial_update']:
#             return SpaceCreateUpdateSerializer  # 创建和更新使用此序列化器
#         return SpaceBaseSerializer
#
#     def list(self, request, *args, **kwargs):
#         # 过滤参数将传递到 service 层
#         filters = request.query_params.dict()
#         try:
#             spaces = SpaceService.list_spaces(request.user, filters)
#             serializer = self.get_serializer(spaces, many=True)
#             return success_response(MSG_SUCCESS, data=serializer.data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="列出空间失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def retrieve(self, request, pk=None, *args, **kwargs):
#         try:
#             space = SpaceService.get_space(request.user, pk)
#             serializer = self.get_serializer(space)
#             return success_response(MSG_SUCCESS, data=serializer.data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="获取空间详情失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def create(self, request, *args, **kwargs):
#         serializer = self.get_serializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         # 从验证过的数据中提取 amenity_ids，其余传给 service 层
#         amenity_ids = serializer.validated_data.pop('amenity_ids', [])
#
#         try:
#             space = SpaceService.create_space(request.user, serializer.validated_data, amenity_ids)
#             # 返回时使用 SpaceBaseSerializer 获取完整的空间详情
#             return success_response(MSG_CREATED, data=SpaceBaseSerializer(space).data, status_code=HTTP_201_CREATED)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="创建空间失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def update(self, request, pk=None, *args, **kwargs):
#         # partial=True 允许 PATCH 请求进行部分更新
#         partial = kwargs.get('partial', False)
#         # 获取当前实例以便序列化器进行部分更新和字段验证
#         instance = SpaceService.get_space(request.user, pk)
#         serializer = self.get_serializer(instance, data=request.data, partial=partial)
#         serializer.is_valid(raise_exception=True)
#
#         # 从验证过的数据中提取 amenity_ids。如果 PATCH 请求中未提供，则为 None
#         amenity_ids = serializer.validated_data.pop('amenity_ids', None)
#
#         try:
#             space = SpaceService.update_space(request.user, pk, serializer.validated_data, amenity_ids)
#             return success_response(MSG_SUCCESS, data=SpaceBaseSerializer(space).data)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="更新空间失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
#
#     def destroy(self, request, pk=None, *args, **kwargs):
#         # 删除权限通常比创建/更新更严格，所以这里单独验证权限
#         # IsAdminOrSuperAdmin 权限类应该单独指定，或者在 viewset 级别通过 get_permissions 方法处理
#         if not IsAdminOrSuperAdmin().has_permission(request, self):
#             return error_response(message=MSG_FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN)
#
#         try:
#             SpaceService.delete_space(request.user, pk)
#             return success_response(MSG_NO_CONTENT, status_code=HTTP_204_NO_CONTENT)
#         except CustomAPIException as e:
#             return error_response(message=e.default_detail, error=e.detail, status_code=e.status_code)
#         except Exception as e:
#             return error_response(message="删除空间失败", error=f"服务器内部错误: {str(e)}",
#                                   status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)