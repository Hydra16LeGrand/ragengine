# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Report it privately:

- GitHub private vulnerability reporting ("Report a vulnerability" in the Security tab of this
  repository), when it is enabled; or
- e-mail: baradjiamara17@gmail.com (phone: +225 05 56 74 85 29)

Please include the affected version or commit, the component (API, worker, frontend, Docker
configuration), steps to reproduce, the impact you observed, and whether the issue is already
public. Do not access, modify or delete data that is not yours while testing.

## Response time (indicative)

RAGEngine is maintained by one person; these are targets, not guarantees.

| Step | Target |
|---|---|
| Acknowledgement of your report | 5 working days |
| First assessment (confirmed or not, severity) | 15 working days |
| Fix or mitigation for a confirmed high or critical issue | 90 days at most, usually sooner |

You will be told when a fix is released. With your agreement, you will be credited in the release
notes.

## Supported versions

There are no long-term support branches yet. Security fixes are made on the latest state of the
default branch only; upgrade to it before reporting.

| Version | Supported |
|---|---|
| Default branch (latest commit) | Yes |
| Anything older | No |

## Scope

In scope: the code in this repository (backend, frontend, Compose files, scripts) and its default
configuration. Out of scope: third-party components (RAGFlow, Elasticsearch, Text Embeddings
Inference, Langfuse…) — report those to their maintainers — and deployments whose configuration
departs from the documented defaults (for example ports published outside `127.0.0.1`).
