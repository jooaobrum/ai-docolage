---
status: superseded by ADR-0005, ADR-0010
---

# In-process MLflow pyfunc with vLLM's offline batch API, not a standalone vLLM HTTP server

We considered running PaddleOCR-VL as a standalone `vllm serve` process that Spark workers call over HTTP (matching the "decoupled endpoint" shape used by `ai_parse_document` and the originally sketched flow), versus wrapping vLLM's offline `LLM(...)` batch API inside an `mlflow.pyfunc.PythonModel` invoked in-process from a Spark `mapInPandas`/pandas UDF on GPU workers.

We chose the in-process pyfunc. It matches the stated goal of shipping a single deployable "package/function" rather than operating a live service, avoids reintroducing an endpoint-style dependency conceptually similar to the Databricks Foundation Model APIs we're explicitly avoiding, and lets vLLM's own continuous batching handle throughput per GPU worker without a network hop. The trade-off: the model's lifecycle is now tied to the Spark job/cluster (no cross-job reuse, GPU autoscaling must account for model load time), and if we ever need external non-Spark callers we'll have to add a serving layer on top rather than pointing new clients at an existing endpoint.
