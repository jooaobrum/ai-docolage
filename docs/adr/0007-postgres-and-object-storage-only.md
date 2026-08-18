---
status: accepted
---

# Postgres and object storage are the only infrastructure; Postgres is the queue

Moving off Databricks means `docolage` now needs its own queue, job store, and blob store. The reference architecture we sketched named Kafka (sync queue), RabbitMQ (job/page/markdown queues), Redis (results cache and SSE fan-out) and object storage — four pieces of infrastructure for one MVP's worth of work.

We chose **Postgres + S3-compatible object storage (MinIO in development), and nothing else**. Postgres holds job metadata, the page-job queue (claimed with `SELECT ... FOR UPDATE SKIP LOCKED`), and completed page results; object storage holds uploads, page images, and Element crops. At MVP throughput a relational queue is comfortably fast enough, and collapsing the queue and the job store into one system means claiming work, recording a result, and updating job status are a single transaction — no dual-write between a broker and a database, and no separate ack/visibility-timeout semantics to reason about.

**Consequences.** There is no Redis, so the Sync path cannot use pub/sub for fan-out; the SSE handler polls the page-result table (~300–500ms) instead, trading a little latency for a mechanism that needs no extra dependency, survives API restarts, and works unchanged across multiple API replicas. There is no message broker, so throughput is bounded by Postgres — the point at which that binds is the point to revisit this decision, and swapping in a broker later is contained to the queue module.
