---
name: model-usage
description: "View LiteLLM Proxy model usage and cost data. Use when: user asks about model costs, token usage, spending breakdown, or API usage statistics. Queries LiteLLM spend endpoints."
version: 1.0.0
author: OctoAgent
tags:
  - model
  - usage
  - cost
  - litellm
tools_required:
  - terminal.exec
  - runtime.inspect
---

# Model Usage

Query LiteLLM Proxy spend endpoints for model usage and cost data.

## When to Use

- "How much have I spent on API calls?"
- "What models am I using?"
- "Show me cost breakdown by model"
- "Token usage for today/this week"

## Data Source

OctoAgent uses LiteLLM Proxy as the model gateway. All LLM calls are routed through it,
and LiteLLM tracks per-model spend data internally.

## LiteLLM Spend Endpoints

The LiteLLM Proxy exposes spend tracking via its API:

### Total Spend

```bash
curl -s http://localhost:4000/spend/logs \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .
```

### Spend by Model

```bash
curl -s "http://localhost:4000/global/spend/models?limit=10" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .
```

### Spend by API Key

```bash
curl -s "http://localhost:4000/global/spend/keys?limit=10" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .
```

### Date Range Filtering

```bash
curl -s "http://localhost:4000/spend/logs?start_date=2026-03-01&end_date=2026-03-16" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq .
```

## Quick Summary

To get a quick overview of spending:

```bash
# Total spend
curl -s http://localhost:4000/global/spend/report \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.total_spend'

# Top models by cost
curl -s "http://localhost:4000/global/spend/models?limit=5" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | \
  jq '.[] | "\(.model): $\(.total_spend)"'
```

## Configuration

- LiteLLM Proxy URL: configured via `LITELLM_PROXY_URL` or project config
- Master Key: `LITELLM_MASTER_KEY` environment variable
- Default port: 4000

## Notes

- Spend data is persisted in LiteLLM's internal SQLite database
- Token counts and costs are per-request, aggregated by model
- Use `runtime.inspect` to check current LiteLLM Proxy connection status
- Cost data may have slight delays due to async logging
