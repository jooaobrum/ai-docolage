---
status: accepted
---

# Output VARIANT schema mirrors ai_parse_document, not a PaddleOCR-VL-native shape

We considered designing the VARIANT/JSON schema around what PaddleOCR-VL naturally emits (its own element types, LaTeX for formulas, a reading-order index) versus constraining it to match `ai_parse_document`'s actual output shape (`document.pages[].elements[]`, `type`, `bbox`, `content`, tables as HTML, `error_status`, `metadata`).

We chose to mirror `ai_parse_document`. The explicit goal of this project is a self-hosted pipeline that is "really close to Databricks AI Doc Parser," so matching field names/nesting makes the two directly comparable and lets any downstream SQL/`parse_json()` code written against one work against the other unchanged. The trade-off: richer or differently-shaped data PaddleOCR-VL provides natively (e.g. LaTeX formulas, explicit reading order) has to be squeezed into `ai_parse_document`'s field shape rather than exposed on its own terms, and if `ai_parse_document`'s schema changes upstream, this schema either drifts or has to be deliberately re-aligned.
