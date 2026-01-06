# core/service/service_result.py
from typing import Any, List, Optional, Tuple, TypeVar, Generic

T = TypeVar('T') # 定义一个类型变量

class ServiceResult(Generic[T]):
    """
    统一的 Service 层操作结果封装类。
    用于替代传统的多值返回或直接抛出异常来表示业务结果。
    """
    def __init__(self, success: bool, data: Optional[T] = None,
                 message: str = "", warnings: Optional[List[str]] = None,
                 errors: Optional[List[str]] = None,
                 error_code: Optional[str] = None,
                 status_code: Optional[int] = None):
        self.success = success
        self.data = data
        self.message = message
        self.warnings = warnings if warnings is not None else []
        self.errors = errors if errors is not None else []
        self.error_code = error_code # 对应 CustomAPIException 的 code
        self.status_code = status_code # 对应 CustomAPIException 的 status_code

    @classmethod
    def success_result(cls, data: T = None, message: str = "操作成功",
                       warnings: Optional[List[str]] = None) -> 'ServiceResult[T]':
        """创建一个成功的 ServiceResult 实例。"""
        return cls(success=True, data=data, message=message, warnings=warnings)

    @classmethod
    def error_result(cls, message: str = "操作失败", errors: Optional[List[str]] = None,
                     error_code: str = "service_error", status_code: int = 500) -> 'ServiceResult[T]':
        """创建一个失败的 ServiceResult 实例。"""
        # 注意: 泛型类型T在这里是None，因为是错误结果
        return cls(success=False, data=None, message=message, errors=errors,
                   error_code=error_code, status_code=status_code)

    def __bool__(self):
        """Allows ServiceResult instances to be used in boolean contexts."""
        return self.success

    def __repr__(self):
        return (f"ServiceResult(success={self.success}, message='{self.message[:50]}...', "
                f"data_type={type(self.data).__name__ if self.data else 'None'}, "
                f"errors={self.errors}, warnings={self.warnings})")

    # 辅助方法，用于在 ServiceResult 失败时，能够方便地转换为 CustomAPIException
    def to_exception(self) -> Exception:
        if self.success:
            return ValueError("Cannot convert a successful ServiceResult to an exception.")

        from core.utils.exceptions import CustomAPIException # 局部导入以避免循环依赖

        # 如果有多个错误，将它们合并为一个字符串或列表
        detail = self.message
        if self.errors:
            if len(self.errors) == 1:
                detail = self.errors[0]
            else:
                detail = f"{self.message}: {'; '.join(self.errors)}"

        return CustomAPIException(
            detail=detail,
            code=self.error_code,
            status_code=self.status_code
        )