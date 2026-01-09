# core/pagination.py
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class CustomPageNumberPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "success": True,
            "message": "成功获取列表。", # 可以让Service返回具体的message，此处为通用默认
            "status_code": self.request.successful_response_status, # 利用DRF请求上下文中的状态码
            "data": {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data
            }
        }, status=self.request.successful_response_status) # 确保HTTP状态码正确