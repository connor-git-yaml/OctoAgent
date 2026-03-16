---
name: github
description: "GitHub operations via `gh` CLI: issues, PRs, CI runs, code review, API queries. Use when: (1) checking PR status or CI, (2) creating/commenting on issues, (3) listing/filtering PRs or issues, (4) viewing run logs. NOT for: local git operations (use git directly), non-GitHub repos, or cloning (use git clone)."
version: 1.0.0
tags:
  - github
  - git
  - ci
  - pr
  - issues
tools_required:
  - terminal.exec
---

# GitHub Skill

Use the `gh` CLI to interact with GitHub repositories, issues, PRs, and CI.

## When to Use

- Checking PR status, reviews, or merge readiness
- Viewing CI/workflow run status and logs
- Creating, closing, or commenting on issues
- Creating or merging pull requests
- Querying GitHub API for repository data
- Listing repos, releases, or collaborators

## When NOT to Use

- Local git operations (commit, push, pull, branch) -> use `git` directly
- Non-GitHub repos (GitLab, Bitbucket) -> different CLIs
- Cloning repositories -> use `git clone`

## Setup

```bash
gh auth login
gh auth status
```

## Common Commands

### Pull Requests

```bash
gh pr list --repo owner/repo
gh pr checks 55 --repo owner/repo
gh pr view 55 --repo owner/repo
gh pr create --title "feat: add feature" --body "Description"
gh pr merge 55 --squash --repo owner/repo
```

### Issues

```bash
gh issue list --repo owner/repo --state open
gh issue create --title "Bug: something broken" --body "Details..."
gh issue close 42 --repo owner/repo
```

### CI/Workflow Runs

```bash
gh run list --repo owner/repo --limit 10
gh run view <run-id> --repo owner/repo
gh run view <run-id> --repo owner/repo --log-failed
gh run rerun <run-id> --failed --repo owner/repo
```

### API Queries

```bash
gh api repos/owner/repo/pulls/55 --jq '.title, .state, .user.login'
gh api repos/owner/repo/labels --jq '.[].name'
```

## JSON Output

```bash
gh issue list --repo owner/repo --json number,title --jq '.[] | "\(.number): \(.title)"'
gh pr list --json number,title,state,mergeable --jq '.[] | select(.mergeable == "MERGEABLE")'
```

## Notes

- Always specify `--repo owner/repo` when not in a git directory
- Use URLs directly: `gh pr view https://github.com/owner/repo/pull/55`
- Rate limits apply; use `gh api --cache 1h` for repeated queries
