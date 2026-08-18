---
status: accepted
---

# One pipeline, two delivery contracts — not two pipelines

The reference architecture drew a Sync pipeline and an Async pipeline as separate lanes: separate brokers, separate splitters, separate worker pools, separate model hosts. Looking at the two lanes side by side, they were the same topology twice — the only genuine difference was how the result reaches the client.

We build **one** queue, one Worker pool and one parse path, with two delivery adapters on top. The **Sync path** holds the client's connection and streams page results as they land (SSE, backed by polling the page-result table). The **Async path** returns a job id immediately and the client polls for status and the final result. Submissions above the **Sync gate** (a page-count/size limit) are refused on the Sync path and must be submitted asynchronously, which bounds how long a client connection and a Worker slot can be held.

**Consequences.** A single pool means a large Async document could otherwise starve a waiting Sync client, so page jobs carry a **priority** and Workers claim `ORDER BY priority, created_at`; Sync jobs jump the queue. One integer column buys the QoS the two-lane design was really after. The residual risk is inverted — a sustained flood of Sync traffic can now starve Async work — and is accepted for the MVP because the Sync gate bounds each Sync job's cost. Physically separate pools (reserved Sync Workers) remain available later without changing the parse path.

We considered having the Sync path bypass the queue and call the Core in-process in the API. Rejected: it would put GPU work in the API process with no backpressure, and a single slow document would block an API worker.
