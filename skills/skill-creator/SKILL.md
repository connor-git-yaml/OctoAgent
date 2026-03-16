---
name: skill-creator
description: Create or update SKILL.md files. Use when designing, structuring, or packaging skills with scripts, references, and assets.
version: 1.0.0
author: OctoAgent
tags:
  - skill
  - creator
  - meta
---

# Skill Creator

This skill provides guidance for creating effective SKILL.md files for OctoAgent.

## About Skills

Skills are modular, self-contained packages that extend OctoAgent's capabilities by providing
specialized knowledge, workflows, and tools. They transform the agent from a general-purpose assistant
into a specialized agent equipped with procedural knowledge.

### What Skills Provide

1. Specialized workflows - Multi-step procedures for specific domains
2. Tool integrations - Instructions for working with specific file formats or APIs
3. Domain expertise - Company-specific knowledge, schemas, business logic
4. Bundled resources - Scripts, references, and assets for complex and repetitive tasks

## Core Principles

### Concise is Key

The context window is a shared resource. Skills share it with system prompt, conversation history,
other Skills, and the user request. Only add context the LLM doesn't already have.

### Anatomy of a Skill

Every skill consists of a required SKILL.md file and optional bundled resources:

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required) - lowercase letters, digits, hyphens
│   │   └── description: (required) - clear, comprehensive
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation loaded into context as needed
    └── assets/           - Files used in output (templates, icons, fonts, etc.)
```

### SKILL.md Format

- **Frontmatter** (YAML): Must contain `name` and `description`. Optional: `version`, `author`, `tags`, `trigger_patterns`, `tools_required`.
- **Body** (Markdown): Instructions and guidance. Only loaded AFTER the skill triggers.

## Skill Creation Process

1. Understand the skill with concrete examples
2. Plan reusable skill contents (scripts, references, assets)
3. Create the skill directory and SKILL.md file
4. Write the frontmatter with clear name and description
5. Write the body with concise instructions
6. Test by loading the skill via `skills action=load name=<name>`
7. Iterate based on real usage

### Skill Naming Rules

- Use lowercase letters, digits, and hyphens only
- Name must match `^[a-z0-9]+(-[a-z0-9]+)*$`
- Under 64 characters
- Prefer short, verb-led phrases that describe the action

### Skill Installation

Skills are installed to one of three directories (priority: project > user > builtin):
- **Builtin**: `skills/` in the OctoAgent repository
- **User**: `~/.octoagent/skills/`
- **Project**: `{project_root}/skills/`

Same-named skills at higher priority override lower priority.

## Writing Guidelines

- Use imperative/infinitive form
- Keep SKILL.md body under 500 lines
- Split large content into reference files
- Include "When to Use" and "When NOT to Use" sections
- Provide concrete code examples
