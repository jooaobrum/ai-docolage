---
status: accepted
---

# Retire the Spark/Databricks entry point; the Service is the only batch mechanism

Supersedes ADR-0004, which exposed two entry points — `docolage.parse(path)` for one document and `docolage.parse_udf()` for `mapInPandas` batch runs over a Spark DataFrame.

With the pipeline moving off Databricks, batch throughput now comes from the **Service**: submit documents to the API and let the Worker pool fan out over page jobs. `parse_udf`, the `spark` extra, the bundle definition and the Databricks notebooks are removed rather than left in place, and `docolage.parse()` becomes the single entry point to the **Core**. We rejected keeping them as an untested, undocumented extra: dead code that constrains the Core's signature (pandas-batch shaped inputs, Databricks-specific path handling) and quietly rots is worse than no code, and reinstating a UDF wrapper over `parse()` is a small job if Databricks ever returns.

**Consequences.** UC Volume paths disappear from configuration in favour of object-storage URIs. Anyone currently running the `mapInPandas` path has no in-package replacement and must go through the Service.
