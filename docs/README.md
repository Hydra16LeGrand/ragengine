# Documentation index

The reference documentation is in English. The ADRs (`adr/`), the code comments and docstrings,
and the audits are written in French: they are the history of the project, kept as they were
written. The project overview, quick start and license are in [`README.md`](../README.md); the
development rules are in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Getting started

| Document | Purpose | Read it when |
|---|---|---|
| [`../README.md`](../README.md) | Overview, development and production setup, license | First contact with the repository |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Development rules (architecture, API, backend, frontend, commits) and the contribution agreement | Before any code, backend or frontend |

## Developer reference

| Document | Purpose | Read it when |
|---|---|---|
| [`api-guide.md`](api-guide.md) | Authentication, the ingestion (Schema A) and retrieval (Schema B) flows, error convention | Integrating with the REST API |
| [`extending.md`](extending.md) | Extending RAGEngine: provider (with the full checklist), ingestion engine adapter, plugin | Before building on it |
| [`providers-architecture.md`](providers-architecture.md) | The ports and adapters already in place | To understand or modify an existing adapter |
| [`ingestion-engine-architecture.md`](ingestion-engine-architecture.md) | Normative reference of the ingestion port and of the RAGFlow adapter | Before touching `providers/engine/` or the ingestion pipeline |
| [`domain-model.md`](domain-model.md) | Domain model (states, permissions) — prevails in case of doubt | Before modifying a SQLAlchemy model or a Pydantic schema |

## Operations

| Document | Purpose | Read it when |
|---|---|---|
| [`runbook-deploiement.md`](runbook-deploiement.md) | Short procedures for day-to-day operations in production | Day 2: restart a service, roll back, back up, restore |

## Architecture decisions

| Document | Purpose | Read it when |
|---|---|---|
| [`adr/README.md`](adr/README.md) | Index of the ADRs (one line per decision) | To know whether an architecture decision already exists on a topic |
| [`adr/`](adr/README.md) | One ADR per non-trivial decision (context/decision/consequences) | Before revisiting a decision already settled |

Some ADRs cite measurement campaigns, audits or phase plans conducted on a private document
corpus: those sources are not published (distribution rights of the documents), only their
conclusions are, in the ADRs themselves.
