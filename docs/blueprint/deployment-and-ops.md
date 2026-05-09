# §12 运行与部署（Ops & Deployment）

> 本文件是 [blueprint.md](../blueprint.md) §12 的完整内容。

---

## 12. 运行与部署（Ops & Deployment）

> 本节覆盖从开发到生产的完整运维体系。设计原则对齐 Constitution：
> - **C1 Durability First** → 备份、恢复验证、优雅关闭
> - **C5 Least Privilege** → 容器安全加固、secrets 注入、网络隔离
> - **C6 Degrade Gracefully** → 分级故障策略、熔断、降级
> - **C8 Observability** → 健康检查、运维事件、告警通道

### 12.1 部署拓扑

#### 12.1.1 开发拓扑（单进程）

MVP 开发阶段采用单进程模式，降低调试复杂度：

- Gateway / Kernel / Worker 全部运行在同一 Python 进程内（FastAPI sub-app 或模块化路由）
- SQLite 文件直接读写本地 `./data/sqlite/`
- LiteLLM Proxy 单独容器运行（唯一外部依赖）
- 不需要 Docker 网络编排，本地 `localhost` 通信即可

```
[ 本地进程: Gateway + Kernel + Workers ]
          ↓ HTTP
[ Docker: litellm-proxy :4000 ]
          ↓
[ SQLite: ./data/sqlite/octoagent.db ]
[ Artifacts: ./data/artifacts/ ]
```

#### 12.1.2 生产拓扑（Docker Compose 多容器）

长期运行场景采用容器化部署，每个服务独立隔离：

```
                    ┌──────────────────────┐
                    │   reverse-proxy      │  :443 (HTTPS)
                    │   (caddy / nginx)    │
                    └──────┬───────────────┘
                           │
              ┌────────────┼────────────────┐
              ▼            ▼                ▼
        ┌──────────┐ ┌──────────┐   ┌─────────────┐
        │ gateway  │ │ kernel   │   │ worker-ops  │
        │ :9000    │ │ :9001    │   │ (内部端口)  │
        └──────────┘ └──────────┘   └─────────────┘
              │            │                │
              └────────────┼────────────────┘
                           ▼
                    ┌──────────────┐
                    │ litellm-proxy│  :4000 (内部)
                    └──────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     [ volume: ./data ]         [ Docker Socket ]
     sqlite / artifacts / vault   (JobRunner 沙箱)
```

服务清单：

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| reverse-proxy | caddy:2-alpine | 443, 80 | HTTPS 终止（Telegram webhook 要求）；自动 Let's Encrypt |
| octo-gateway | 自建 | 9000（内部） | 渠道适配 + SSE 转发 |
| octo-kernel | 自建 | 9001（内部） | Orchestrator + Policy + Event Store |
| octo-worker-* | 自建 | 无外部端口 | Worker 进程；MVP 可先内置在 kernel 中 |
| litellm-proxy | ghcr.io/berriai/litellm | 4000（内部） | 模型网关 |

#### 12.1.3 Docker-in-Docker 策略（执行沙箱 vs 部署容器）

系统存在**两层 Docker 使用**，必须明确区分：

- **部署层**：系统自身的容器化（docker-compose 管理）
- **执行层**：JobRunner 为 Worker 创建的沙箱容器（FR-EXEC-1/2）

**方案选择：Docker Socket 挂载（非 DinD）**

```yaml
# kernel / worker 容器挂载宿主 Docker socket
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

- 理由：DinD 复杂度高且有安全隐患；Socket 挂载是 Agent Zero / Dify 等项目验证过的方案
- 约束：JobRunner 创建的沙箱容器**必须**挂载到独立的 Docker network（`octo-sandbox-net`），与系统内部网络隔离
- 沙箱容器默认：`--network=octo-sandbox-net --read-only --cap-drop=ALL --memory=512m --cpus=1`

### 12.2 Docker Compose 参考配置

```yaml
# docker-compose.yml（生产参考）
version: "3.9"

x-common: &common
  restart: unless-stopped
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"

networks:
  octo-internal:        # 系统内部通信
    driver: bridge
  octo-sandbox-net:     # JobRunner 沙箱隔离网络
    driver: bridge
    internal: true       # 默认禁止外部访问

volumes:
  octo-data:            # sqlite + artifacts + vault

services:
  reverse-proxy:
    <<: *common
    image: caddy:2-alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
    networks:
      - octo-internal
    depends_on:
      gateway:
        condition: service_healthy

  litellm-proxy:
    <<: *common
    image: ghcr.io/berriai/litellm:main-latest
    env_file: .env.litellm
    volumes:
      - ./deploy/litellm-config.yaml:/app/config.yaml:ro
    networks:
      - octo-internal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M

  gateway:
    <<: *common
    build:
      context: .
      dockerfile: deploy/Dockerfile.gateway
    env_file: .env
    networks:
      - octo-internal
    depends_on:
      litellm-proxy:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    read_only: true
    user: "1000:1000"
    cap_drop:
      - ALL
    deploy:
      resources:
        limits:
          memory: 256M

  kernel:
    <<: *common
    build:
      context: .
      dockerfile: deploy/Dockerfile.kernel
    env_file: .env
    volumes:
      - octo-data:/app/data
      - /var/run/docker.sock:/var/run/docker.sock:ro   # JobRunner 沙箱
    networks:
      - octo-internal
      - octo-sandbox-net   # 管理沙箱容器
    depends_on:
      litellm-proxy:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9001/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    user: "1000:1000"
    deploy:
      resources:
        limits:
          memory: 1G
```

#### 12.2.1 Secrets 注入策略

- **绝不**将 secrets 硬编码在 docker-compose.yml 或镜像中
- 使用 `.env` 文件（`.gitignore` 保护）注入环境变量
- `.env` 文件分层：`.env`（通用）+ `.env.litellm`（LiteLLM 专用 API keys）
- 生产环境可升级为 Docker Secrets 或 HashiCorp Vault
- 对齐 Constitution C5：secrets 按 scope 分区，不进 LLM 上下文

```
# .env 示例（.gitignore 必须包含）
OCTO_DB_PATH=/app/data/sqlite/octoagent.db
OCTO_ARTIFACTS_DIR=/app/data/artifacts
OCTO_VAULT_DIR=/app/data/vault
TELEGRAM_BOT_TOKEN=ENV:...       # 由渠道插件读取
```

#### 12.2.2 服务启动顺序

严格依赖链（通过 `depends_on` + `condition: service_healthy` 保证）：

```
litellm-proxy（先启动，健康检查通过）
    → gateway + kernel（并行启动）
        → reverse-proxy（gateway 健康后启动）
```

Worker 进程的启动策略：
- MVP：Worker 内嵌在 kernel 进程中，无需独立启动
- M2+：Worker 作为独立容器，depends_on kernel 健康检查

### 12.3 健康检查与监控

#### 12.3.1 健康检查端点

每个服务必须暴露以下端点：

| 端点 | 用途 | 响应 |
|------|------|------|
| `GET /health` | Liveness — 进程是否存活 | `200 {"status": "ok"}` |
| `GET /ready` | Readiness — 能否接受请求（依赖就绪） | `200 {"status": "ready", "checks": {...}}` |

Readiness 检查内容（分级 level；响应字段为兼容沿用 `profile`）：

- `core`（默认，M0 必须）：`sqlite`、`artifacts_dir`、`disk_space_mb`
- `llm`（M1）：`core` + `litellm_proxy`
- `full`（M2+）：`llm` + memory/plugins 等扩展依赖
- 未启用组件返回 `skipped`，不应导致 profile 失败

```json
// GET /ready 响应示例
{
  "status": "ready",
  "profile": "core",
  "checks": {
    "sqlite": "ok",
    "litellm_proxy": "skipped",
    "disk_space_mb": 2048,
    "artifacts_dir": "ok"
  }
}
```

- Docker HEALTHCHECK 使用 `/health`（liveness）
- 反向代理使用 `/ready`（readiness）做上游健康判定
- 对齐 §9.6 插件 Manifest 中的 `healthcheck` 字段

#### 12.3.2 运维事件类型

对齐 Constitution C2（Everything is Event），系统运维操作必须生成事件：

```yaml
# 新增运维事件类型（扩展 §8.1 Event.type）
OpsEventTypes:
  - SYSTEM_STARTED         # 进程启动完成
  - SYSTEM_SHUTTING_DOWN   # 收到停止信号，开始优雅关闭
  - HEALTH_DEGRADED        # 某依赖不健康（如 litellm 不可达）
  - HEALTH_RECOVERED       # 依赖恢复
  - BACKUP_STARTED         # 备份开始
  - BACKUP_COMPLETED       # 备份完成
  - BACKUP_FAILED          # 备份失败
  - PLUGIN_DISABLED        # 插件被自动禁用
  - CONFIG_CHANGED         # 配置变更（对齐 FR-OPS-1）
```

#### 12.3.3 告警通道

故障事件必须主动通知 Owner（对齐 C7 User-in-Control + C8 Observability）：

- **首选**：通过 Telegram Bot 推送告警消息（复用已有渠道基础设施）
- **备选**：结构化日志输出（structlog JSON），由外部监控工具拾取
- 告警级别：`info`（备份完成）/ `warn`（依赖降级）/ `critical`（数据不一致/进程异常退出）
- 告警抑制：同类告警 5 分钟内不重复推送（防刷屏）

### 12.4 数据备份与恢复

#### 12.4.1 备份对象与策略

| 数据 | 方案 | 频率 | 保留策略 |
|------|------|------|---------|
| SQLite DB | `sqlite3 .backup` 在线快照 + WAL 归档 | 每日 + 重大操作前 | 7 天滚动 + 每月 1 份永久 |
| Artifacts | `rsync --checksum` 增量同步到 NAS | 每日 | 跟随关联 task 生命周期 |
| Vault | `gpg --symmetric` 加密后 rsync | 每日 | 30 天滚动 + 每月 1 份永久 |
| 配置文件 | Git 版本管理（deploy/ 目录） | 每次变更 | Git 历史 |
| Event Store | 随 SQLite DB 备份（events 表是核心） | 同 SQLite | 同 SQLite |

#### 12.4.2 SQLite 备份细节

- **在线备份**：使用 `sqlite3 .backup` API（不中断服务、保证一致性快照）
- **WAL 归档**：备份后执行 `PRAGMA wal_checkpoint(TRUNCATE)` 回收 WAL 文件
- **可选增强（M2+）**：引入 Litestream 做实时 WAL 流复制到 NAS/S3，RPO 趋近于零
- **备份命名**：`octoagent-{date}-{time}.db`，保留最近 7 天

#### 12.4.3 Vault 加密备份

- 加密方式：`gpg --symmetric --cipher-algo AES256`（对称加密，密码短语）
- 密钥管理：备份密码存储在 Owner 的密码管理器中（不与系统共存）
- 恢复时需要：备份文件 + 密码短语（两要素）

#### 12.4.4 备份自动化

- MVP：APScheduler 定时任务触发备份脚本（复用已有调度基础设施）
- 备份前后生成运维事件（BACKUP_STARTED / BACKUP_COMPLETED / BACKUP_FAILED）
- 备份失败时通过告警通道通知 Owner

#### 12.4.5 恢复验证

- **每月一次**：自动执行恢复验证（restore test）
  - 将最新备份恢复到临时 SQLite 文件
  - 校验 tasks projection 与 events 一致性
  - 校验 artifact 引用完整性
  - 结果写入运维事件
- 恢复验证失败 → critical 告警

### 12.5 故障策略与恢复

#### 12.5.1 服务级故障（对齐 §8.3.5 崩溃恢复策略）

| 崩溃位置 | 恢复方式 | 触发条件 |
|----------|---------|---------|
| Skill Pipeline 节点内 | 从最后 checkpoint 确定性恢复 | 进程重启后扫描未完成 checkpoint |
| Worker Free Loop 内 | 重启 Loop，Event 历史注入上下文，LLM 自主判断续接点 | Docker restart policy 自动拉起 |
| Orchestrator Free Loop 内 | 重启 Loop，扫描未完成 Task，重新派发或等待人工确认 | 同上 |
| Gateway | 无状态，直接重启；客户端 SSE 断线重连 | 同上 |
| LiteLLM Proxy | 容器自动重启；期间 kernel 走 fallback 或进入冷却 | 同上 |

#### 12.5.2 系统级故障

| 故障 | 检测方式 | 应对策略 |
|------|---------|---------|
| 磁盘空间不足 | `/ready` 检查 `disk_space_mb` | warn 告警 → 暂停新 task 创建 → critical 时拒绝写入 |
| OOM（内存溢出） | Docker OOM killer 日志 | `deploy.resources.limits` 限制；OOM 后容器自动重启 |
| 网络断开 | litellm 健康检查失败 | 进入降级模式：已有 task 暂停；新 task 排队；HEALTH_DEGRADED 事件 |
| 宿主机重启 | Docker `restart: unless-stopped` | 全部容器按依赖顺序自动拉起；kernel 启动时执行恢复扫描 |
| SQLite 损坏 | 启动时 `PRAGMA integrity_check` | 自动切换到最近备份；CRITICAL 告警通知 Owner |

#### 12.5.3 应用级故障

- **Provider 失败**：LiteLLM 内置 fallback + 冷却机制；事件记录失败原因与 fallback 路径
- **Worker 失败**：标记 worker unhealthy；task 根据策略进入 WAITING_INPUT（等待人工）或重派发到其他 worker
- **Plugin 失败**：自动 disable 并降级（对齐 C6）；记录 PLUGIN_DISABLED 事件；Owner 可手动重新启用
- **熔断策略**：同一组件 5 分钟内连续失败 3 次 → 触发熔断（circuit open）→ 冷却 60 秒后 half-open 探测 → 成功则恢复

#### 12.5.4 优雅关闭协议

收到 `SIGTERM` 后，系统按以下顺序关闭（对齐 C1 Durability First + C2 Everything is Event）：

```
1. 写入 SYSTEM_SHUTTING_DOWN 事件
2. 停止接受新请求（Gateway 返回 503）
3. 等待进行中的 Skill Pipeline 节点完成（最长 30s）
4. 对未完成 Task 保存 checkpoint（如支持）
5. Flush 所有待写入的事件到 SQLite
6. 关闭 SSE 连接（发送终止信号）
7. 关闭 SQLite 连接（确保 WAL checkpoint）
8. 退出进程
```

超时保护：整个关闭流程最长 60 秒，超时后强制退出（Docker `stop_grace_period: 60s`）。

#### 12.5.5 Watchdog 集成（对齐 FR-EXEC-3）

Watchdog 作为 kernel 内部组件，监控 Task 执行健康度：

- **无进展检测**：Task 在 RUNNING 状态超过配置时间未产生新事件 → 触发告警
- **策略可配**（per-task / per-worker）：
  - `warn`：通知 Owner
  - `degrade`：降级到 cheap 模型 / 减少工具集
  - `cancel`：自动取消并推进终态
- **心跳机制**：Worker 定期发送 HEARTBEAT 事件；超过 2 个周期未收到 → 标记 unhealthy

### 12.6 升级与迁移

#### 12.6.1 Schema 迁移策略

Event 表的 `schema_version` 字段（§8.1）提供版本化基础：

- 迁移工具：使用 Python 脚本（`deploy/migrations/`），不依赖重量级 ORM
- 迁移方向：仅支持向前迁移（forward-only），不支持回滚（备份即回滚）
- 迁移流程：
  1. 停止服务（维护窗口）
  2. 执行 SQLite 备份
  3. 运行迁移脚本
  4. 校验 `PRAGMA integrity_check` + projection 一致性
  5. 启动新版本

#### 12.6.1.1 F094 migrate-094（Worker Memory Parity 存量迁移占位）

F094 引入 `octo memory migrate-094` CLI 命令组（dry-run / apply / rollback 三段式）：

- **背景**：F063 Migration 已把 WORKER_PRIVATE scope 的 SoR 记录全部迁到 PROJECT_SHARED，但 audit metadata 未保留 (memory_id → 原 scope_id) mapping——意味着无法可靠反推存量 worker 私有 fact 的归属
- **降级方案 A**（GATE_DESIGN 用户拍板 + Codex spec review 锁定）：CLI 完整 + 底层 no-op
  - `dry-run` 输出 `total_facts_to_migrate=0` + `reason="F063_legacy_no_provenance"` + 当前 `memory_namespaces` 表的 kind 分布快照；不写库
  - `apply` 写一条 `memory_maintenance_runs` 审计记录（`idempotency_key="octoagent.memory.migration.094.worker_memory_parity.noop.v1"` / `metadata.no_op=true`），SoR 表零修改
  - `rollback <run_id>` 删除审计记录，rollback 后 idempotency 失效，可重新 apply
- **数据库路径**：默认 `core/config.get_db_path()`（`data/sqlite/octoagent.db`，可通过 `OCTOAGENT_DB_PATH` env 覆盖）；`--db-path` CLI 参数显式 override
- **使用流程**：
  1. `octo memory migrate-094 --dry-run` 确认输出符合预期
  2. `octo memory migrate-094 --apply --yes` 写审计记录（确认提示可用 `--yes` 跳过）
  3. 如需回滚：`octo memory migrate-094 --rollback <run_id> --yes`
- **未来若引入 worker 私有数据需要迁移**：改用新 migrate-NNN 命令而不是回头改 migrate-094 语义（避免破坏已执行的幂等性）

#### 12.6.2 配置兼容性

- 配置文件版本化（`config_version` 字段）
- 新版本必须兼容上一版本配置（或提供自动迁移）
- 配置变更生成 CONFIG_CHANGED 事件（对齐 FR-OPS-1），支持回滚

#### 12.6.3 容器升级流程

- **MVP（停机升级）**：`docker compose down && docker compose pull && docker compose up -d`
- **M2+（最小停机）**：
  - 先升级无状态服务（gateway）
  - 再升级有状态服务（kernel），利用优雅关闭保证数据完整
  - 升级前自动触发备份

### 12.7 日志管理

#### 12.7.1 日志策略（对齐 §9.10 packages/observability）

- **开发环境**：`structlog` pretty 格式，输出到 stdout
- **生产环境**：`structlog` JSON 格式，输出到 stdout（由 Docker 日志驱动收集）
- 所有日志携带 `task_id` / `trace_id`（贯穿事件与日志）

#### 12.7.2 日志轮转与持久化

- Docker 日志驱动配置（已包含在 docker-compose 的 `x-common` 中）：
  - `max-size: 10m`，`max-file: 3`（每个容器最多 30MB 日志）
- 长期日志归档：定期 `docker compose logs > archive.log` 到 NAS（可选）
- Logfire 自动采集 Pydantic AI / FastAPI 的 traces 和 spans（§9.10），无需额外配置

### 12.8 SSL/TLS 与外部访问

- Telegram webhook **要求 HTTPS**，因此生产部署必须配置 TLS
- 使用 Caddy 自动 HTTPS（内置 ACME / Let's Encrypt），零配置获取证书
- 内部服务间通信走 `octo-internal` 网络，**不加密**（Docker bridge 隔离足够）
- 外部仅暴露 reverse-proxy 的 443/80 端口，其余服务无外部端口

### 12.9 开发者体验（Developer Experience / DX）

> 目标：降低首次部署和日常运维的认知负担，让 `git clone` 到 `第一次成功调用 LLM` 的路径尽可能短。
> 对齐 Constitution C7（User-in-Control）+ C6（Degrade Gracefully）。

#### 12.9.1 `octo config` — 统一模型配置管理（M1.5，Feature 014 已交付）

当前基线以 `octoagent.yaml` 作为模型与 Provider 配置的**单一事实源**：

1. **运行模式与 Provider 配置**：
   - 支持 `echo`（零依赖开发）或 `litellm`（真实 LLM）
   - 支持通过 `octo config provider add/list/disable` 管理 OpenRouter / OpenAI / Anthropic / Azure / 本地 Ollama 等 Provider
2. **模型别名管理**：
   - 通过 `octo config alias list/set` 查看并更新 `main` / `cheap` 等 alias 到真实模型的映射
   - 用户不需要直接理解 LiteLLM 的 `model_list` 结构
3. **衍生配置同步**：
   - `litellm-config.yaml` 由 `octoagent.yaml` 自动推导生成，避免三份配置漂移
   - 保留 `octo config migrate` 兼容旧的 `.env` / `.env.litellm` / `litellm-config.yaml` 体系
4. **兼容入口**：
   - `octo init` 保留为历史引导入口；新流程以 `octo config` 为准

产出文件：`octoagent.yaml`（用户主配置）+ `litellm-config.yaml`（衍生文件）+ `.env`（运行时环境变量）

#### 12.9.2 `octo doctor` — 配置诊断（M1，M2 扩展 guided remediation）

> 对齐 §16.2 已记录的检查项。

运行 `octo doctor` 执行全面环境健康检查：

```
$ octo doctor

OctoAgent Environment Check
────────────────────────────
✅ Python 3.12.x
✅ uv installed
✅ .env exists
✅ octoagent.yaml exists
✅ litellm-config.yaml synced from octoagent.yaml
✅ OCTOAGENT_LLM_MODE = litellm
✅ LITELLM_MASTER_KEY configured
✅ Docker daemon running
✅ litellm-proxy container healthy
✅ LiteLLM Proxy reachable (http://localhost:4000/health)
✅ SQLite DB writable
✅ data/artifacts/ directory exists
⚠️  Provider credential present but not validated (use --live to test)

All checks passed! Run `octo start` to launch.
```

检查项分级：

- **必须通过**（❌ 阻断启动）：Python 版本、.env 存在、DB 可写
- **建议通过**（⚠️ 可降级运行）：Docker、Proxy 可达、Provider 凭证有效性
- `--live` 标志：发送一个 cheap 模型 ping 请求验证端到端连通性
- M2 扩展：对 Telegram pairing / webhook、JobRunner、backup 最近验证时间输出可执行修复建议

#### 12.9.3 `octo onboard` — 引导式上手与恢复（M2）

M2 在 `octo config` / `octo doctor` 之上补齐**首次使用闭环**：

1. 配置 Provider 与 alias；
2. 执行 `octo doctor --live` 做真实模型连通性验证；
3. 选择并接入第一条渠道（优先 Telegram）；
4. 完成 pairing / allowlist / webhook 自检；
5. 发送第一条测试消息并校验结果回传、审批、告警链路。

要求：

- 向导中断后可恢复到上次完成步骤；
- 每一步都给出修复动作，而不是只打印错误；
- 最终摘要应明确告知"系统已可用"还是"仍有阻塞项"。

#### 12.9.4 dotenv 自动加载（M1）

当前问题：`uvicorn` 不自动加载 `.env`，开发者必须手动 `source .env`。

解决方案：

- Gateway `main.py` 启动时使用 `python-dotenv` 自动加载 `.env`（已在 M1 依赖中）
- 加载优先级：环境变量 > `.env` 文件（不覆盖已设置的环境变量）
- 仅在开发模式加载（生产环境由 Docker `env_file` 注入）

```python
# apps/gateway/src/octoagent/gateway/main.py
from dotenv import load_dotenv
load_dotenv()  # 开发便利；生产环境由容器 env_file 覆盖
```

#### 12.9.5 `octo start` — 一键启动（M2）

统一启动入口，根据 `.env` 配置自动决定启动方式：

- `echo` 模式：仅启动 Gateway（uvicorn）
- `litellm` 模式：先确认 litellm-proxy 容器运行 → 启动 Gateway
- `full` 模式（M2+）：`docker compose up -d` 启动全部服务

Docker daemon 未运行时，由 `octoagent.provider.dx.docker_daemon.ensure_docker_daemon`
统一检测并在 macOS / Linux 下自动启动（macOS: `open -a "Docker Desktop"`；
Linux: `systemctl --user start docker` → `sudo -n systemctl start docker`）。
超时仍未就绪则降级至直接进程路径，不阻断 gateway 启动。相关环境变量：
`OCTOAGENT_AUTOSTART_DOCKER=0` 禁用预热；`OCTOAGENT_DOCKER_DAEMON_TIMEOUT` 调整超时（秒）。

---
