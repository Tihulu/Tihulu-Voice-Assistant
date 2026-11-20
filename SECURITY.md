# Security Policy

## Development status

TIHULU JARVIS is **pre-alpha**. No complete trusted desktop-execution or cloud/local isolation system has been independently accepted. Do not use unreleased builds as a security boundary.

## Security principles

1. Cloud reasoning providers do not receive direct local Shell access.
2. Shell execution is restricted to independently authorized local Jan/Qwen components.
3. Privileged actions pass through a local policy enforcer using scoped, revocable capabilities.
4. Cloud-bound data is filtered and minimized locally. If policy or filtering fails, fail closed.
5. User content, browser pages, model output, plugins, and agent suggestions are untrusted data.
6. The default is no background listening, no automatic cloud uploads, and no privileged automation.
7. User-defined profiles cannot override global safety boundaries.

## Reporting vulnerabilities

Avoid sharing exploits containing personal data, credentials, private recordings, or tokens in public issues. Until a private reporting channel is configured, use GitHub's repository **Security → Report a vulnerability** option if available; otherwise request a private channel without disclosing sensitive information publicly.

Do not attach unredacted logs or workstation configuration to public issues.

## Release gating

Before enabling local tool execution, require negative tests for cloud/MCP/subagent bypass, scope escape, authorization replay, sanitization failures, profile escalation, and cancellation/stop behavior. No security claim may rest solely on a system prompt or a green unit-test badge.
