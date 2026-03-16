---
name: weather
description: "Get current weather and forecasts via wttr.in. Use when: user asks about weather, temperature, or forecasts for any location. NOT for: historical weather data, severe weather alerts, or detailed meteorological analysis. No API key needed."
version: 1.0.0
author: OctoAgent
tags:
  - weather
  - forecast
  - temperature
tools_required:
  - terminal.exec
---

# Weather Skill

Get current weather conditions and forecasts.

## When to Use

- "What's the weather?"
- "Will it rain today/tomorrow?"
- "Temperature in [city]"
- "Weather forecast for the week"
- Travel planning weather checks

## When NOT to Use

- Historical weather data -> use weather archives/APIs
- Climate analysis or trends -> use specialized data sources
- Severe weather alerts -> check official NWS sources
- Aviation/marine weather -> use specialized services (METAR, etc.)

## Location

Always include a city, region, or airport code in weather queries.

## Commands

### Current Weather

```bash
curl "wttr.in/London?format=3"
curl "wttr.in/London?0"
curl "wttr.in/New+York?format=3"
```

### Forecasts

```bash
# 3-day forecast
curl "wttr.in/London"

# Week forecast
curl "wttr.in/London?format=v2"

# Specific day (0=today, 1=tomorrow, 2=day after)
curl "wttr.in/London?1"
```

### Format Options

```bash
curl "wttr.in/London?format=%l:+%c+%t+%w"
curl "wttr.in/London?format=j1"  # JSON output
```

### Format Codes

- `%c` -- Weather condition emoji
- `%t` -- Temperature
- `%f` -- "Feels like"
- `%w` -- Wind
- `%h` -- Humidity
- `%p` -- Precipitation
- `%l` -- Location

## Quick Responses

```bash
# "What's the weather?"
curl -s "wttr.in/London?format=%l:+%c+%t+(feels+like+%f),+%w+wind,+%h+humidity"

# "Will it rain?"
curl -s "wttr.in/London?format=%l:+%c+%p"
```

## Notes

- No API key needed (uses wttr.in)
- Rate limited; don't spam requests
- Works for most global cities
- Supports airport codes: `curl wttr.in/ORD`
