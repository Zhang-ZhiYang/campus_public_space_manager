# core/utils/constants.py

# --- HTTP 状态码 (可根据需要扩展) ---
HTTP_200_OK = 200
HTTP_201_CREATED = 201
HTTP_204_NO_CONTENT = 204
HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_405_METHOD_NOT_ALLOWED = 405
HTTP_409_CONFLICT = 409 # 用于业务冲突，如预订冲突
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_503_SERVICE_UNAVAILABLE = 503

# --- 常用响应消息 ---
MSG_SUCCESS = "操作成功"
MSG_CREATED = "创建成功"
MSG_NO_CONTENT = "无内容"
MSG_BAD_REQUEST = "请求参数错误"
MSG_UNAUTHORIZED = "未经认证"
MSG_FORBIDDEN = "权限不足，您没有执行此操作的权限"
MSG_NOT_FOUND = "资源未找到"
MSG_INTERNAL_ERROR = "服务器内部错误"
MSG_VALIDATION_ERROR = "数据验证失败"
MSG_METHOD_NOT_ALLOWED = "请求方法不允许"
MSG_TOKEN_INVALID = "身份验证凭据不正确，提供的令牌无效或已过期"
MSG_SERVICE_UNAVAILABLE = "服务暂时不可用，请稍后再试"

# --- 业务错误消息 (示例，可根据实际业务扩展) ---
MSG_USER_BLACKLISTED = "您已被系统列入黑名单，无法执行此操作。"
MSG_BOOKING_CONFLICT = "预订时间段已被占用或与您现有预订冲突。"
MSG_SPACE_NOT_AVAILABLE = "该空间在所选时间段内不可用。"
MSG_BOOKING_LIMIT_EXCEEDED = "您已达到该空间或您个人预订频率上限。"
MSG_MISSING_REQUIRED_FIELD = "缺少必要的请求参数。"
MSG_INVALID_CREDENTIALS = "用户名或密码不正确。"

# --- 通用错误码 (可自定义) ---
CODE_SUCCESS = "success"
CODE_BAD_REQUEST = "bad_request"
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "permission_denied"
CODE_NOT_FOUND = "not_found"
CODE_CONFLICT = "conflict"
CODE_SERVER_ERROR = "server_error"
CODE_VALIDATION_ERROR = "validation_error"
CODE_USER_BLACKLISTED = "user_blacklisted"
CODE_BOOKING_CONFLICT = "booking_conflict"
# ... 更多业务码

# --- 其他常量 (示例) ---
DEFAULT_PAGE_SIZE = 10
MAX_USERNAME_LENGTH = 150
MAX_PHONE_NUMBER_LENGTH = 15

# 例: 用户全局黑名单默认持续天数
DEFAULT_GLOBAL_BLACKLIST_DAYS = 7
# 例: 违约次数上限触发全局黑名单
GLOBAL_BLACKLIST_VIOLATION_THRESHOLD = 3