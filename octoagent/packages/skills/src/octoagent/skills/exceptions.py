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


class SkillValidationError(SkillError):
    """输出模型校验失败。"""


class SkillToolExecutionError(SkillError):
    """工具执行错误。"""


class SkillLoopDetectedError(SkillError):
    """循环检测触发。"""
