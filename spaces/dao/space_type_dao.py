from core.dao import BaseDAO
from spaces.models import SpaceType # 确保从 .models 导入 SpaceType 模型

class SpaceTypeDAO(BaseDAO):
    model = SpaceType
    # 如果需要，可以在这里添加SpaceType特有的查询优化或方法
    def get_queryset(self):
        return super().get_queryset().all()