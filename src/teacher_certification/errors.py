"""认证服务的领域错误类型。"""


class DomainError(Exception):
    """所有领域规则违例的基类。"""


class NotFoundError(DomainError):
    """引用的实体不存在。"""


class ValidationError(DomainError):
    """输入数据不满足结构约束。"""


class InvalidStateError(DomainError):
    """当前状态不允许执行该操作。"""


class FrameworkExpiredError(DomainError):
    """能力框架已过期，不能签发新结论。"""


class QualificationError(DomainError):
    """评委资质不满足要求。"""


class ConflictOfInterestError(DomainError):
    """存在利益冲突，评委须回避。"""


class DecisionError(DomainError):
    """认证决定违反决定规则。"""


class DuplicateCertificateError(DomainError):
    """候选人已持有有效证书，不得重复签发。"""


class DependencyError(DomainError):
    """补强步骤的前置依赖尚未完成。"""


class StepOverdueError(DomainError):
    """补强步骤已超过完成期限。"""
