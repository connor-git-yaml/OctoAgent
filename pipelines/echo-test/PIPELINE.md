---
name: echo-test
description: "内置测试 Pipeline。按顺序执行三个透传节点，用于验证 Pipeline 注册表扫描和引擎基本执行流程。"
version: 1.0.0
author: OctoAgent
tags:
  - test
  - echo
trigger_hint: "仅用于系统测试，不要在用户请求中使用此 Pipeline"
input_schema:
  message:
    type: string
    description: "测试消息"
    required: false
    default: "hello"
output_schema:
  result:
    type: string
    description: "最终输出"
nodes:
  - id: step-1
    label: "第一步：接收输入"
    type: transform
    handler_id: transform.passthrough
    next: step-2
  - id: step-2
    label: "第二步：处理数据"
    type: transform
    handler_id: transform.passthrough
    next: step-3
  - id: step-3
    label: "第三步：输出结果"
    type: transform
    handler_id: transform.passthrough
entry_node: step-1
---

# Echo Test Pipeline

内置测试 Pipeline，用于验证 PIPELINE.md 解析和 Pipeline Engine 基本执行流程。

## 节点说明

### step-1
接收输入参数，透传到下一步。

### step-2
模拟数据处理，透传到下一步。

### step-3
输出最终结果（终止节点，无 next）。
