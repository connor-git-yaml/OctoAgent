"""Skill 领域异常定义。"""


class SkillError(Exception):
    """Skill 领域基类异常。"""


class SkillRegistrationError(SkillError):
    """Skill 注册错误（重复 skill_id 或 manifest 非法）。"""


class SkillNotFoundError(SkillError):
    """Skill 查询失败（未注册）。"""


class SkillInputError(SkillError):
    """输入模型校验失败。"""


class SkillRepeatError(SkillError):
    """可重试的模型输出错误。"""


class SkillAuthError(SkillError):
    """Provider 凭证失效（HTTP 401/403 且自动刷新已失败），不可重试。

    与 SkillRepeatError 的区别：凭证链断裂只能由用户重新授权解决，
    任何层级的自动重试都只会反复打同一个失效凭证。
    """


class SkillValidationError(SkillError):
    """输出模型校验失败。"""


class SkillToolExecutionError(SkillError):
    """工具执行错误。"""


class SkillLoopDetectedError(SkillError):
    """循环检测触发。"""
