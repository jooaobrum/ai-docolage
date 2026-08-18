---
status: superseded by ADR-0010
---

# Figures escalate to OpenAI on low confidence, not on type alone

We initially planned to send every `type='figure'` element to the External OpenAI API for captioning. The user pushed back: mix PaddleOCR-VL and OpenAI, but rely on OpenAI less. We considered three ways to decide when a figure needs OpenAI: a per-job quality-tier flag (cheap vs. high-quality mode), a heuristic on crop properties (size/aspect-ratio/text-density), or per-element confidence/emptiness-based escalation after PaddleOCR-VL has already attempted a description.

We chose per-element confidence-based escalation: always run PaddleOCR-VL on a figure first, and only call OpenAI when its output is empty or below a confidence threshold. This directly minimizes OpenAI calls to the figures that actually need them (true photos/diagrams PaddleOCR-VL can't describe), rather than an all-or-nothing job-level switch or a crop-property proxy that would sometimes escalate or skip the wrong crops. The trade-off: this depends on PaddleOCR-VL exposing a usable confidence/quality signal per element, which needs to be verified against its actual output during implementation — if it doesn't expose one cleanly, we'll need to fall back to a proxy heuristic (e.g. output length) instead.
