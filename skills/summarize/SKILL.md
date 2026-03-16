---
name: summarize
description: Summarize or extract text/transcripts from URLs, podcasts, and local files. Use when asked to summarize a URL, article, video, or local document.
version: 1.0.0
author: OctoAgent
tags:
  - summarize
  - extract
  - url
  - transcript
tools_required:
  - terminal.exec
---

# Summarize

Fast CLI to summarize URLs, local files, and YouTube links.

## When to Use

- "summarize this URL/article"
- "what's this link/video about?"
- "transcribe this YouTube/video" (best-effort transcript extraction)

## Quick Start

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube: Summary vs Transcript

Best-effort transcript (URLs only):

```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

If the transcript is huge, return a tight summary first, then ask which section to expand.

## Model + Keys

Set the API key for your chosen provider:

- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`
- Google: `GEMINI_API_KEY`

Default model is `google/gemini-3-flash-preview` if none is set.

## Useful Flags

- `--length short|medium|long|xl|xxl|<chars>`
- `--max-output-tokens <count>`
- `--extract-only` (URLs only)
- `--json` (machine readable)
- `--firecrawl auto|off|always` (fallback extraction)
- `--youtube auto` (Apify fallback if `APIFY_API_TOKEN` set)

## Config

Optional config file: `~/.summarize/config.json`

```json
{ "model": "openai/gpt-5.2" }
```
