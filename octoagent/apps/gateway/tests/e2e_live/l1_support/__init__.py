"""F140 L1 UI E2E 支撑件（非 pytest 测试；Playwright webServer 拉起）。

- ``scenario_brain``：prompt-marker 路由的脚本脑（L1 场景专用）
- ``serve_l1_gateway``：hermetic L1 gateway 启动器脚本

本目录不含 test_* 文件，pytest 不收集；e2e_live 目录本就被 CI
backend-deterministic job ``--ignore``。
"""
