---
status: superseded by ADR-0011
---

# docolage exposes two entry points, not one

We considered three shapes for the package's public interface: a Spark-only `parse_documents(df) -> df` DataFrame-in/DataFrame-out function matching how `ai_parse_document` itself is invoked, a CLI plus library, or two exported entry points sharing the same underlying pyfunc — `docolage.parse(path)` for a single document and `docolage.parse_udf()` returning a `mapInPandas`-ready function for batch runs.

We chose the dual-entry-point shape. A Spark-only interface has no lightweight way to test or debug against a single local file without standing up a DataFrame first, and a full CLI is more surface area than a small/portfolio-scale project needs. Exposing both `parse()` and `parse_udf()` off the same pyfunc covers "try it on one weird PDF in a notebook" and "run it over a UC Volume of thousands of files" without duplicating the underlying logic. The trade-off: two entry points is two things to keep in sync and document, versus the single-function surface of the Spark-only option.
