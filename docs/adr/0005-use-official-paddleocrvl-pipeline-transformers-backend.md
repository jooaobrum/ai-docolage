---
status: superseded by ADR-0010
---

# Use the official PaddleOCRVL pipeline with the transformers backend; defer vLLM

Supersedes ADR-0001, which assumed vLLM's offline `LLM(...)` API could run PaddleOCR-VL end to end. Research showed it cannot: PaddleOCR-VL decomposes into a layout detection model (PP-DocLayoutV3) that **always** runs via PaddlePaddle, and a VLM recognition stage that alone can be delegated to a backend. The vLLM recipe states plainly that calling the 0.9B model through vLLM directly "is not equivalent to executing the full PaddleOCR-VL pipeline" — it skips layout analysis, cross-page table merging, and heading-hierarchy refinement. Additionally, vLLM and PaddlePaddle have dependency conflicts and must live in separate virtual environments, so a single in-process env serving both is not possible at all.

We therefore instantiate PaddleOCR's own `PaddleOCRVL` pipeline object inside the MLflow pyfunc, using its default **transformers** VLM backend for now. This gives correct, officially-supported output (the whole reason for the project's "really close to AI Doc Parser" precision goal) with one Python environment, one pip install, no init script, no subprocess, and no port management. At the agreed scale — hundreds to low thousands of pages, batch/ad-hoc — the throughput cost versus vLLM is minutes, not hours.

The escape hatch is deliberate and cheap: switching the VLM stage to vLLM later means standing up a `vllm serve` subprocess in an isolated venv (via cluster init script) and setting two parameters, `vl_rec_backend="vllm-server"` and `vl_rec_server_url="http://localhost:8000/v1"`. Nothing else in the pipeline changes, so no architectural lock-in is created by starting simple. Note that the rest of ADR-0001 still holds in spirit: inference runs in-process on the Spark GPU executor via `mapInPandas`, with no externally-hosted endpoint and nothing extra to provision on Databricks.
