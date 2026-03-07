# Feature 018 Verification Report

## 结论

- 状态：PASS
- 范围：`octoagent.protocol` 新包 + Feature 018 contract fixture + 相关回归

## 执行记录

### 静态检查

```bash
uv run ruff check packages/protocol
```

- 结果：PASS

### 单元与回归测试

```bash
uv run pytest packages/protocol/tests packages/core/tests/test_artifact_store.py packages/core/tests/test_us6_artifact.py apps/gateway/tests/test_worker_runtime.py -q
```

- 结果：PASS
- 汇总：`32 passed`

## 验证覆盖

- A2A-Lite message payload coercion / alias / hop guard
- 状态映射 terminal round-trip
- artifact 映射 metadata 保留
- replay protection 区分 `accepted / duplicate / replayed`
- `DispatchEnvelope -> A2AMessage -> DispatchEnvelope` round-trip
- `TASK.to` 缺失 `worker_capability` 时按 receiver URI 回填
- inline image artifact 映射为可传输的 base64 file part
- `WAITING_APPROVAL / PAUSED / CREATED` 压缩后仍通过 `metadata.internal_status` 保留语义
- payload `state` 非 canonical A2A state 时在协议边界直接拒绝
- result / error / cancel / heartbeat adapter 输出
- fixture JSON round-trip 校验
- core ArtifactStore 回归
- gateway WorkerRuntime 回归

## 剩余风险

- replay 保护当前为内存实现，跨进程/重启持久化留给后续 Feature 019/023。
- `Artifact.append / last_chunk` 仍未进入持久化层，本次 fixture 与映射统一输出默认值 `false`。
