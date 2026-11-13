"""Configuration owned by this plugin; no MaiBot global changes are required."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    config_version: str = Field(default="1.0.0", description="配置版本")


class NetworkSection(PluginConfigBase):
    __ui_label__ = "PRTS 请求"

    timeout_seconds: float = Field(default=12.0, ge=3, le=20, description="单次官方 API 请求超时（秒）")
    cache_ttl_seconds: int = Field(default=300, ge=0, le=3600, description="成功结果的内存缓存时间（秒）")


class ResultSection(PluginConfigBase):
    __ui_label__ = "结果"

    max_chars: int = Field(default=10000, ge=2000, le=18000, description="工具正文最大字符数，较长条目可按章节查询")
    search_limit: int = Field(default=5, ge=1, le=10, description="搜索结果数量")
    recruit_limit: int = Field(default=30, ge=1, le=50, description="公招结果数量上限")


class CommandSection(PluginConfigBase):
    __ui_label__ = "指令"

    require_at_in_groups: bool = Field(default=True, description="群聊指令要求真正 @机器人；不限制正常对话中的模型工具调用")


class PRTSConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    network: NetworkSection = Field(default_factory=NetworkSection)
    result: ResultSection = Field(default_factory=ResultSection)
    commands: CommandSection = Field(default_factory=CommandSection)
