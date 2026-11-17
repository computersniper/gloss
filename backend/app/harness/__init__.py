"""GlossX agent harness: long-lived, tool-calling agent sessions.

Layers (see ``docs/glossx-harness.md``):

* ``session``     -- session model, persistence under ``<DATA_DIR>/sessions``, event bus
* ``engine``      -- the provider-agnostic tool-calling loop (one turn at a time)
* ``protocol``    -- ``<tool_call>`` parsing / streaming filter / ``<tool_result>`` rendering
* ``tools``       -- harness tools as capabilities + adapted registry capabilities
* ``permissions`` -- modes, allow/deny/ask rules and defaults
* ``workspace``   -- sandboxed project workspaces and write checkpoints
* ``context``     -- token estimates, prompt building, compaction, @-mentions
* ``extensions``  -- slash commands, subagent types, hooks (markdown, skills, plugins)
* ``settings``    -- persisted harness settings (``config.json`` key ``glossx_harness``)
* ``manager``     -- the API-facing session manager
"""
from __future__ import annotations
