"""AliasRegistry 单元测试

对齐 tasks.md T014: 验证 resolve()、get_alias()、get_aliases_by_category()、
get_aliases_by_runtime_group()、list_all()、未知 alias 降级到 "main"、运行时 group 透传。
"""

from octoagent.provider.alias import AliasConfig, AliasRegistry


class TestAliasRegistry:
    """AliasRegistry 核心功能测试"""

    def test_default_aliases_count(self):
        """默认注册 6 个 MVP alias"""
        registry = AliasRegistry()
        assert len(registry.list_all()) == 6

    def test_default_alias_names(self):
        """默认 alias 名称完整"""
        registry = AliasRegistry()
        names = {a.name for a in registry.list_all()}
        expected = {"router", "extractor", "summarizer", "planner", "executor", "fallback"}
        assert names == expected

    def test_resolve_semantic_alias(self):
        """语义 alias 解析为运行时 group"""
        registry = AliasRegistry()
        # cheap 组
        assert registry.resolve("router") == "cheap"
        assert registry.resolve("extractor") == "cheap"
        assert registry.resolve("summarizer") == "cheap"
        # main 组
        assert registry.resolve("planner") == "main"
        assert registry.resolve("executor") == "main"
        # fallback 组
        assert registry.resolve("fallback") == "fallback"

    def test_resolve_runtime_group_passthrough(self):
        """已知运行时 group 名称直接透传"""
        registry = AliasRegistry()
        assert registry.resolve("cheap") == "cheap"
        assert registry.resolve("main") == "main"
        assert registry.resolve("fallback") == "fallback"

    def test_resolve_unknown_alias_fallback_to_main(self):
        """未知 alias 降级到 'main'"""
        registry = AliasRegistry()
        assert registry.resolve("unknown_alias") == "main"
        assert registry.resolve("nonexistent") == "main"

    def test_get_alias_exists(self):
        """按名称查询已注册 alias"""
        registry = AliasRegistry()
        config = registry.get_alias("planner")
        assert config is not None
        assert config.name == "planner"
        assert config.category == "main"
        assert config.runtime_group == "main"

    def test_get_alias_not_exists(self):
        """查询不存在的 alias 返回 None"""
        registry = AliasRegistry()
        assert registry.get_alias("nonexistent") is None

    def test_get_aliases_by_category_cheap(self):
        """按 category 查询 cheap 组"""
        registry = AliasRegistry()
        aliases = registry.get_aliases_by_category("cheap")
        names = {a.name for a in aliases}
        assert names == {"router", "extractor", "summarizer"}

    def test_get_aliases_by_category_main(self):
        """按 category 查询 main 组"""
        registry = AliasRegistry()
        aliases = registry.get_aliases_by_category("main")
        names = {a.name for a in aliases}
        assert names == {"planner", "executor"}

    def test_get_aliases_by_category_empty(self):
        """查询不存在的 category 返回空列表"""
        registry = AliasRegistry()
        aliases = registry.get_aliases_by_category("nonexistent")
        assert aliases == []

    def test_get_aliases_by_runtime_group(self):
        """按运行时 group 查询"""
        registry = AliasRegistry()
        aliases = registry.get_aliases_by_runtime_group("cheap")
        names = {a.name for a in aliases}
        assert names == {"router", "extractor", "summarizer"}

    def test_list_all_sorted(self):
        """list_all() 按 name 排序"""
        registry = AliasRegistry()
        aliases = registry.list_all()
        names = [a.name for a in aliases]
        assert names == sorted(names)

    def test_custom_aliases(self):
        """自定义 alias 列表"""
        custom = [
            AliasConfig(name="custom1", category="main", runtime_group="main"),
            AliasConfig(name="custom2", category="cheap", runtime_group="cheap"),
        ]
        registry = AliasRegistry(aliases=custom)
        assert len(registry.list_all()) == 2
        assert registry.resolve("custom1") == "main"
        assert registry.resolve("custom2") == "cheap"

    def test_no_duplicate_names(self):
        """同一 name 不重复注册"""
        registry = AliasRegistry()
        names = [a.name for a in registry.list_all()]
        assert len(names) == len(set(names))
