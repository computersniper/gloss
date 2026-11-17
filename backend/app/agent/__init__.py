"""GlossX agent harness.

Kernel: ``capabilities`` (registry + adapters, one Capability ABI), ``gate``
(permissions, approvals, budget meter), ``runs`` (scheduler, SSE trace,
persistence). Core agent: ``loop`` (route -> workflow -> loop plugin),
``core`` (planner / writer / verifier / reflect roles), ``loops/``
(plan-execute-verify, react), ``workflow`` (v2 manifest engine), ``decider``.
Context: ``context`` (evidence spans, working context, untrusted wrapping) and
``projects`` (long-term project memory). External: ``adapters/`` (skills, MCP
client, CLI delegates, SSH) configured through ``connections``.
"""
from __future__ import annotations
