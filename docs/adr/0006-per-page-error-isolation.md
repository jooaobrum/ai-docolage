---
status: accepted
---

# Per-page error isolation, mirroring ai_parse_document exactly

We needed to decide what happens when a single page fails (corrupt render, VLM OOM, timeout) partway through a multi-page document. Consistent with ADR-0002, we checked what `ai_parse_document` itself does rather than inventing a policy.

It isolates failures per page: successfully parsed pages are still returned, and failed pages are recorded in an `error_status` **array** of `{error_message: STRING, page_id: INT}` objects. There is no checkpoint or resume concept — it is error-reporting only. `docolage` does the same. One bad page never discards the GPU work already spent on the rest of the document, and failures stay queryable in SQL. Downstream consumers must therefore tolerate partial documents and check `error_status` rather than assume every page produced elements.

Two incidental findings from confirming the schema, worth recording because they affect other decisions: `elements` lives at the **document** level (not nested inside `pages`), with each element's `bbox` carrying its own `page_id`; and each element already exposes `confidence DOUBLE` and `description STRING`. The `confidence` field is the signal ADR-0003's escalation logic needs, and `description` is where a figure's generated caption belongs.
