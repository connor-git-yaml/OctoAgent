"""第三方库语义钉住 guard family（F142 件1，agent-zero 范式）。

背景：OctoAgent 三次线上/bench 踩坑全是「依赖语义假设破裂」——
① anyio 4.12.1 asyncio backend TLS 读竞态（httpx.ReadError 空 message，bench
  ~30-50% 调用命中，被 FallbackManager 掩盖成 Echo 假成功）；
② APScheduler CronTrigger 星期从 Monday=0 计（与 Unix cron Monday=1 不同，
  数字 DOW 每周提醒错一天，Codex P1-1）；
③ piper synthesize→synthesize_wav API 错用（F110，hermetic Fake 符合 Protocol
  但掩盖真库 API 变化）。
三次事后回归全是 fake 钉自家调用点——**依赖升级破坏假设时本地 pytest 不会暴露**。

本目录的测试直接 import 真库、驱动真行为，把我们代码所依赖的库语义钉成常驻
回归：升级 anyio/httpx/APScheduler/piper 若破坏假设，全量 pytest 立即红，
而非等到线上竞态 / 每周提醒错一天 / 语音真机才发现。

覆盖清单（与 .specify/features/142-deterministic-guards/spec.md 件1 复核表对齐）：
- test_httpx_anyio_tls_read_semantics.py —— 真本地 TLS server（ephemeral 自签证书
  + 端口，零外网）+ 繁忙 event loop，钉「流中断异常 ∈ ProviderClient 瞬态重试
  family」+ 真栈端到端重试恢复
- test_apscheduler_cron_dow_semantics.py —— 真 CronTrigger.from_crontab 钉
  Monday=0 语义（cron_tools DP-3 拒数字 DOW 的根据）
- test_piper_api_semantics.py —— importorskip 门控的真 piper API 签名钉住
  （voice optional extra，未装则 SKIP；装了 piper 的环境自动激活）

显式略过：aiosqlite——全部 store 层测试经 create_store_group 走真 aiosqlite +
真 SQLite 文件（packages/core/src/octoagent/core/store/__init__.py 直接 import），
语义假设每天在集成层被真库验证，无需另立钉住测试。
"""
