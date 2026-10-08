# Commercial license

RAGEngine is **source-available** under the Apache License 2.0 with the Commons Clause License
Condition v1.0 ([`LICENSE`](LICENSE)). It is not "open source" in the sense of the Open Source
Initiative: the Commons Clause removes one right, the right to **sell** the software.

This page explains, in plain words, what is free, what requires a commercial license, and how to
get one. It also states an **additional permission** granted by the author (next section). It does
not replace the text of [`LICENSE`](LICENSE), which prevails where the two differ, except that the
additional permission below is a grant from the author and is binding on the author. When in
doubt, ask before you start.

## Free of charge, for everyone

You may, without asking and without paying:

- use RAGEngine for any purpose, **including inside a company, a public administration or a
  non-profit**, in production, for any number of users;
- install and operate it for your own organisation, on your own servers or on a cloud account you
  control, including to serve your own customers' questions about **your** documents (for example
  a support chatbot on your own website);
- modify it, keep your modifications private, or publish them;
- redistribute it, modified or not, at no charge, under the same license and with its notices
  (the Commons Clause notice included);
- study it, teach with it, write about it, benchmark it;
- build on its API an application whose value comes from **your own** product or content, where
  RAGEngine is one internal component among others and not what your customers pay for.

## Additional permission for integrators

In addition to the license in [`LICENSE`](LICENSE), the author, as Licensor, grants the following
permission to everyone. It is a permission, not a condition: it widens what the Commons Clause
would otherwise prevent, and it does not modify the text of [`LICENSE`](LICENSE).

1. **Services around your client's own installation.** You may install, configure, integrate,
   adapt or extend RAGEngine **for a client**, and be paid for that work, **without a commercial
   license**. The client runs, and is the operator of, its own instance (on its own servers or on a
   cloud account it controls). This covers one-off projects, retainers, training and support for
   that client's instance, whether you are a freelancer, an agency or a systems integrator.
2. **Your own product with RAGEngine inside.** You may sell a product or service of your own that
   embeds RAGEngine as an **internal component**, **without a commercial license**, provided that
   you do not resell RAGEngine itself and do not host it as such for third parties (see below).
   What your customers pay for must be your product, not access to RAGEngine.

This permission requires that the notices of [`LICENSE`](LICENSE) (Apache 2.0 and the Commons
Clause notice) are kept with every copy of RAGEngine you distribute or deploy, and it does not
extend to anything listed under "Requires a commercial license" below. It is granted irrevocably
for the versions of RAGEngine published under this license.

### Quick answers

| You want to... | Commercial license? |
|---|---|
| Be paid to deploy and configure RAGEngine on a client's servers | No |
| Be paid to write connectors, plugins or a custom front end for a client's RAGEngine | No |
| Run a paid support or training contract on a client's RAGEngine instance | No |
| Ship your document-management product with RAGEngine as its search component | No |
| Build an internal tool for your employer on top of RAGEngine | No |
| Sell RAGEngine itself, or a rebranded copy of it | **Yes** |
| Offer RAGEngine as a paid hosted service ("upload documents, get a RAG API") | **Yes** |
| Host one instance per customer and charge for access to it, with RAGEngine as the offer | **Yes** |
| Sell subscriptions, credits or pay-per-use access to a public RAGEngine instance | **Yes** |
| Sell an appliance or marketplace image whose main value is RAGEngine | **Yes** |

If your case is not clearly on one side, ask first: a short e-mail is enough.

## What RAGEngine contains

RAGEngine is the self-hosted core: interface, knowledge bases, documents, ingestion with human
validation of the precheck, jobs, Playground (search and chat), evaluations, organisations and
roles, API keys, personal LLM keys, providers, observability and Docker deployment. Three features
are specific to RAGPlatform, the hosted service operated by the author, and are not part of
RAGEngine: public chatbots, guest mode (trial without an account) and the administration console.

## Requires a commercial license

The Commons Clause forbids you to "Sell" the software: to provide to third parties, for a fee or
any other consideration, a product or service whose value derives entirely or substantially from
the functionality of RAGEngine. Beyond the additional permission above, examples that require a
commercial license:

- selling RAGEngine, or a rebranded or modified version of it, as a product or a license;
- offering RAGEngine as a hosted or managed service (SaaS) to paying customers, for example
  "upload your documents, get a RAG search API or chatbot", where RAGEngine provides that
  functionality;
- selling paid hosting or managed operation of RAGEngine, where you run the instance and the
  customer pays for access to it (installing, configuring or supporting an instance that the
  client operates is covered by the additional permission above);
- reselling or sublicensing RAGEngine itself, including bundling it into a paid appliance, a paid
  marketplace image or a paid distribution where it is the substantial value of the offer;
- charging access to a public instance of RAGEngine (subscriptions, credits, pay-per-use).

The definition is deliberately broad; the commercial agreement makes it precise for your case.

## How to get one

Contact the author, Amara Baradji:

- e-mail: baradjiamara17@gmail.com
- phone: +225 05 56 74 85 29

Please describe your organisation, the product or service you have in mind, how RAGEngine is used
in it, and the expected scale. Terms (scope, duration, fee or revenue share, support) are
negotiated case by case.

## Contributions

Contributions are accepted under the [Contributor License Agreement](CLA.md), which lets the author
include them in commercially licensed editions. This is what keeps commercial licensing possible
for the whole code base.

## Permanence

The Commons Clause restriction is permanent: there is no planned conversion of RAGEngine to an
open-source license.
