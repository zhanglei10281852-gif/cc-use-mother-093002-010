"""领域异常层级。"""


class DomainError(Exception):
    """所有领域错误的基类。"""


class NotFoundError(DomainError):
    """引用的实体不存在。"""


class ValidationError(DomainError):
    """输入或不变量校验失败。"""


class StateError(DomainError):
    """当前状态不允许执行该操作。"""


class ConcurrencyError(DomainError):
    """并发冲突，例如重复签发有效证书。"""
