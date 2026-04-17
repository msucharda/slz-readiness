"""Plan subpackage — deterministic summaries only.

The narrated ``plan.md`` is produced by the ``slz-plan`` skill (LLM-driven).
The ``slz-plan-summary`` console script here emits a deterministic
``plan.summary.{json,md}`` directly from ``gaps.json`` so humans always have a
citation-safe snapshot independent of the LLM narration.
"""
