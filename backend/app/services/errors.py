"""服务层自定义异常。"""

from __future__ import annotations


class ServiceError(Exception):
    """服务层错误基类，携带用户可读信息与 HTTP 状态码。"""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(ServiceError):
    status_code = 404


class NotEnoughContentError(ServiceError):
    status_code = 409
