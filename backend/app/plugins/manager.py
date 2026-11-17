"""Safe, manifest-driven plugin installation and execution.

Gloss plugins are declarative paper-analysis modules. They may read the current
paper, request an AI completion, and use host-controlled scholarly search when
those permissions are declared, but they do not execute downloaded Python or
JavaScript inside the desktop application.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..config import PLUGINS_DIR, output_language
from ..features.common import text_complete, truncate_to_tokens
from ..features.reading_lens import (
    cache_revision,
    guidance_for,
    resolve_paper_lens,
)
from ..library import store
from ..platform_support import read_utf8_text
from ..search import deep_research
from . import visual_explainer

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
_ALLOWED_PERMISSIONS = {
    "paper:read", "ai:complete", "network:research", "image:generate",
}
_REQUIREMENT_DEFINITIONS = {
    "body_text": {"reason": "no_body_text"},
    "formulas": {"reason": "no_formulas"},
    "method_content": {"reason": "no_method_content"},
    "claims_or_evidence": {"reason": "no_claims_or_evidence"},
    "terms": {"reason": "no_terms"},
    "figures_or_tables": {"reason": "no_figures_or_tables"},
}
_ALLOWED_REQUIREMENTS = set(_REQUIREMENT_DEFINITIONS)
_AGENT_MESSAGE_STAGES = {
    "default", "preparing", "reading", "searching", "thinking", "writing", "saving",
}
DEFAULT_AGENT = {
    "name": "Research Assistant",
    "name_zh": "研究助手",
    "icon": "🔬",
    "messages": {
        "default": "Researching the paper",
        "preparing": "Preparing the paper",
        "reading": "Reading the paper",
        "searching": "Searching scholarly sources",
        "thinking": "Analyzing the evidence",
        "writing": "Writing the result",
        "saving": "Saving the result",
    },
}
_CONFIG_TYPES = {"boolean", "string", "number", "integer", "array"}
_CONFIG_SCHEMA_FIELDS = {
    "type", "default", "title", "title_zh", "description", "description_zh",
    "markdownDescription", "enum",
    "enumDescriptions", "markdownEnumDescriptions", "enumItemLabels",
    "enumItemLabels_zh",
    "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
    "pattern", "format", "order", "editPresentation", "items",
    "deprecationMessage", "markdownDeprecationMessage", "tags",
}
PLUGIN_API_VERSION = 1
CONTRIBUTION_POINTS = [
    {
        "id": "paper.sidebar.panel",
        "status": "stable",
        "description": "Adds a manifest-defined panel to the paper reader sidebar.",
    },
    {
        "id": "paper.toolbar.action",
        "status": "planned",
        "description": "Adds an action to the paper toolbar.",
    },
    {
        "id": "pdf.annotation.layer",
        "status": "planned",
        "description": "Adds a persistent, independently toggleable PDF layer.",
    },
    {
        "id": "library.view",
        "status": "planned",
        "description": "Adds a custom paper-library view.",
    },
]

PLUGIN_TEMPLATE = {
    "api_version": PLUGIN_API_VERSION,
    "id": "example.claim-checker",
    "name": "Claim Checker",
    "version": "1.0.0",
    "author": "Your Name",
    "description": "Checks the evidence supplied for the paper's central claims.",
    "permissions": ["paper:read", "ai:complete"],
    "requirements": ["body_text"],
    "agent": {
        "name": "Evidence Scout",
        "icon": "🔎",
        "messages": {
            "reading": "Reading the paper's claims",
            "thinking": "Checking the supporting evidence",
            "writing": "Writing the claim review",
        },
    },
    "contributes": {
        "paper_sidebar": {
            "tab_name": "Claims",
            "icon": "🔎",
        },
        "configuration": {
            "title": "Claim Checker",
            "properties": {
                "strictness": {
                    "type": "string",
                    "enum": ["balanced", "strict"],
                    "default": "balanced",
                    "description": "Controls how aggressively claims are challenged.",
                    "order": 10,
                },
                "includeFollowUps": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include proposed follow-up experiments.",
                    "order": 20,
                },
            },
        },
    },
    "prompt": "List the central claims and evaluate the evidence supplied for each one.",
}

PLUGIN_MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/computersniper/gloss/plugin.schema.v1.json",
    "title": "Gloss plugin manifest",
    "type": "object",
    "additionalProperties": True,
    "required": ["id", "name", "version", "description", "permissions", "prompt"],
    "properties": {
        "api_version": {"type": "integer", "const": PLUGIN_API_VERSION, "default": 1},
        "id": {"type": "string", "pattern": _ID.pattern, "maxLength": 64},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "name_zh": {"type": "string", "maxLength": 80},
        "version": {"type": "string", "pattern": _VERSION.pattern},
        "author": {"type": "string", "maxLength": 80},
        "icon": {"type": "string", "maxLength": 8},
        "description": {"type": "string", "minLength": 1, "maxLength": 500},
        "description_zh": {"type": "string", "maxLength": 500},
        "permissions": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"enum": sorted(_ALLOWED_PERMISSIONS)},
        },
        "requirements": {
            "type": "array",
            "uniqueItems": True,
            "items": {"enum": sorted(_ALLOWED_REQUIREMENTS)},
            "default": [],
        },
        "agent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "maxLength": 40},
                "name_zh": {"type": "string", "maxLength": 40},
                "icon": {"type": "string", "maxLength": 8},
                "messages": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        stage: {
                            "oneOf": [
                                {"type": "string", "maxLength": 100},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "en": {"type": "string", "maxLength": 100},
                                        "zh": {"type": "string", "maxLength": 100},
                                    },
                                },
                            ]
                        }
                        for stage in sorted(_AGENT_MESSAGE_STAGES)
                    },
                },
            },
        },
        "contributes": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "paper_sidebar": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tab_name"],
                    "properties": {
                        "tab_name": {"type": "string", "minLength": 1, "maxLength": 24},
                        "tab_name_zh": {"type": "string", "maxLength": 24},
                        "icon": {"type": "string", "maxLength": 8},
                    },
                },
                "configuration": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "maxLength": 80},
                        "title_zh": {"type": "string", "maxLength": 80},
                        "properties": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "additionalProperties": True,
                                "required": ["type", "default"],
                            },
                        },
                    },
                    "required": ["properties"],
                },
            },
        },
        "prompt": {"type": "string", "minLength": 1, "maxLength": 12000},
    },
    "examples": [PLUGIN_TEMPLATE],
}

CORE_EXTENSIONS = [
    {
        "id": "core.summary", "name": "Summary", "name_zh": "速览",
        "icon": "✦", "description": "Built-in structured paper summary.",
        "description_zh": "内置的结构化论文速览。", "builtin": True,
    },
    {
        "id": "core.notes", "name": "Deep Paper Note", "name_zh": "深度论文笔记",
        "icon": "📑", "description": "AI deep-reading note with a one-sentence overview.",
        "description_zh": "AI 深度阅读笔记，包含一句话概述。", "builtin": True,
    },
    {
        "id": "core.personal-notes", "name": "Notes", "name_zh": "我的笔记",
        "icon": "📝", "description": "Your own notes, stored separately for each paper.",
        "description_zh": "你为每篇论文记录的个人笔记，与 AI 笔记分开保存。", "builtin": True,
    },
    {
        "id": "core.mindmap", "name": "Mind Map", "name_zh": "思维导图",
        "icon": "⌘", "description": "Built-in interactive paper concept map.",
        "description_zh": "内置的交互式论文概念图。", "builtin": True,
    },
]

MARKETPLACE = [
    {
        "id": "gloss.deep-research",
        "name": "Deep Research",
        "name_zh": "深度研究",
        "version": "1.1.0",
        "author": "Gloss",
        "icon": "DR",
        "description": (
            "Search live scholarly sources and build a traceable, evidence-grounded report."
        ),
        "description_zh": "联网检索学术来源，并基于论文全文生成可追溯的深度研究报告。",
        "tab_name": "Research",
        "tab_name_zh": "深度研究",
        "permissions": ["paper:read", "ai:complete", "network:research"],
        "requirements": ["body_text"],
        "agent": {
            "name": "Deep Researcher",
            "name_zh": "深度研究员",
            "icon": "DR",
            "messages": {
                "reading": "Reading the paper",
                "searching": "Searching Semantic Scholar, OpenAlex, and citation links",
                "thinking": "Comparing the paper with retrieved evidence",
                "writing": "Writing the result",
            },
        },
        "contributes": {
            "paper_sidebar": {
                "tab_name": "Research",
                "tab_name_zh": "深度研究",
                "icon": "DR",
            },
            "configuration": {
                "title": "Deep Research",
                "properties": {
                    "focus": {
                        "type": "string",
                        "enum": [
                            "comprehensive",
                            "methods",
                            "evidence",
                            "reproduction",
                        ],
                        "enumItemLabels": [
                            "Comprehensive",
                            "Methods",
                            "Evidence",
                            "Reproduction",
                        ],
                        "default": "comprehensive",
                        "description": "The primary lens for the research report.",
                        "order": 10,
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["standard", "exhaustive"],
                        "enumItemLabels": ["Standard", "Exhaustive"],
                        "default": "exhaustive",
                        "description": "Controls the level of detail in the report.",
                        "order": 20,
                    },
                    "includeResearchAgenda": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include prioritized follow-up research questions.",
                        "order": 30,
                    },
                    "citePaperLocations": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Reference sections, figures, tables, and reported values when available."
                        ),
                        "order": 40,
                    },
                },
            },
        },
        "prompt": (
            "Produce a rigorous deep-research report about the supplied paper using both the "
            "paper and the host-provided VERIFIED NETWORK RESEARCH bundle. The bundle contains "
            "live results from scholarly indexes and citation links, each identified as [S1], "
            "[S2], and so on. Never invent citations, URLs, related works, experiments, numbers, "
            "or author intent. Cite every external comparison with its exact [S#] identifier, "
            "and never cite an external source absent from the bundle. Distinguish (1) facts "
            "reported by the paper, (2) claims made by its authors, (3) facts or positions from "
            "retrieved sources, and (4) your reasoned synthesis. Conflicting sources must be "
            "shown as disagreement, not silently reconciled.\n\n"
            "Adapt the report structure to the paper's reading lens. Cover an executive "
            "synthesis; research context and lineage; the central question or thesis; approach, "
            "materials, evidence, or methods; a claim-to-evidence analysis; what retrieved work "
            "supports, complicates, or contradicts; novelty in context; assumptions and validity "
            "risks; limitations and missing evidence; and implications. Do not force datasets, "
            "experiments, formulas, or metrics onto humanities or qualitative work. When plugin "
            "settings request a research agenda, end with prioritized follow-up questions. When "
            "location citations are requested, cite only identifiable paper locations. Finish "
            "with a Sources table listing every cited [S#], title, year, database, and exact URL. "
            "Mark unavailable information as not reported or not found rather than guessing."
        ),
    },
    {
        "id": "gloss.visual-explainer",
        "name": "Visual Explainer",
        "name_zh": "图像解释",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "VE",
        "description": (
            "Turn a paper into a diagram, a four-frame animated explanation, "
            "or a teacher-style visual lesson using Codex image_gen or gpt-image-2."
        ),
        "description_zh": (
            "使用 Codex image_gen 或 gpt-image-2 将论文生成示意图、四帧动画式讲解或老师风格的图像课程。"
        ),
        "tab_name": "Visual",
        "tab_name_zh": "图解",
        "permissions": ["paper:read", "ai:complete", "image:generate"],
        "requirements": ["body_text"],
        "agent": {
            "name": "Visual Teacher",
            "name_zh": "图解老师",
            "icon": "VE",
            "messages": {
                "reading": {"en": "Reading the paper for visual anchors", "zh": "正在寻找论文中的视觉线索"},
                "thinking": {"en": "Planning the visual explanation", "zh": "正在规划图像讲解"},
                "writing": {"en": "Finalizing captions and frames", "zh": "正在整理讲解与分镜"},
                "saving": {"en": "Saving generated images", "zh": "正在保存生成的图像"},
            },
        },
        "contributes": {
            "paper_sidebar": {
                "tab_name": "Visual",
                "tab_name_zh": "图解",
                "icon": "VE",
            },
            "configuration": {
                "title": "Visual explanation style",
                "title_zh": "图像讲解风格",
                "properties": {
                    "imageProvider": {
                        "type": "string",
                        "title": "Image backend",
                        "title_zh": "生图后端",
                        "enum": ["auto", "codex", "openai"],
                        "enumItemLabels": [
                            "Auto (prefer Codex)", "Codex image_gen", "OpenAI gpt-image-2",
                        ],
                        "enumItemLabels_zh": [
                            "自动（优先 Codex）", "Codex image_gen（无需 API Key）", "OpenAI gpt-image-2",
                        ],
                        "default": "auto",
                        "description": (
                            "Auto uses Codex subscription image generation when the Codex CLI is available. "
                            "The active Codex runtime/provider must expose image_gen. OpenAI is an explicit "
                            "API-key-backed option."
                        ),
                        "description_zh": (
                            "自动模式在 Codex CLI 可用时使用订阅内置生图，且当前 Codex 运行时/Provider 需暴露 "
                            "image_gen；OpenAI 是需配置 API Key 的显式选项。"
                        ),
                        "order": 5,
                    },
                    "style": {
                        "type": "string",
                        "title": "Style",
                        "title_zh": "风格",
                        "enum": ["diagram", "animated", "teacher"],
                        "enumItemLabels": ["Diagram", "Animated explanation", "Teacher lesson"],
                        "enumItemLabels_zh": ["示意图", "动画式讲解", "老师风格讲解"],
                        "default": "diagram",
                        "description": "Choose one image, four sequential frames, or a teacher-led scene.",
                        "description_zh": "选择单张示意图、四张连续分镜或老师讲解场景。",
                        "order": 10,
                    },
                    "quality": {
                        "type": "string",
                        "title": "Image quality",
                        "title_zh": "图像质量",
                        "enum": ["low", "medium", "high"],
                        "enumItemLabels": ["Low", "Medium", "High"],
                        "enumItemLabels_zh": ["低", "中", "高"],
                        "default": "medium",
                        "description": "Higher quality can take longer and cost more.",
                        "description_zh": "更高质量通常耗时更长、费用更高。",
                        "order": 20,
                    },
                },
            },
        },
        "prompt": (
            "Create a paper-grounded visual explanation. Use the selected style and quality. "
            "Never invent facts, results, sources, quotations, or author intent."
        ),
    },
    {
        "id": "gloss.critical-review",
        "name": "Critical Review",
        "name_zh": "批判性审阅",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "⚖",
        "description": "Review assumptions, evidence, limitations, and threats to validity.",
        "description_zh": "审查论文假设、证据、局限与有效性威胁。",
        "tab_name": "Review",
        "tab_name_zh": "审阅",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "claims_or_evidence"],
        "prompt": (
            "Write a rigorous critical review of the paper. Separate major strengths, "
            "unsupported or weakly supported claims, methodological risks, missing "
            "baselines, threats to validity, and concrete follow-up experiments. Cite "
            "sections, figures, tables, and reported numbers when available."
        ),
    },
    {
        "id": "gloss.reproducibility-checklist",
        "name": "Reproducibility Checklist",
        "name_zh": "复现检查表",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "✓",
        "description": "Extract a practical checklist for reproducing the paper.",
        "description_zh": "提取可执行的论文复现检查表。",
        "tab_name": "Reproduce",
        "tab_name_zh": "复现",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "method_content"],
        "prompt": (
            "Create an actionable reproducibility checklist for this paper. Include "
            "datasets, preprocessing, model details, objectives, hyperparameters, "
            "randomness, compute, evaluation, expected outputs, and every missing detail. "
            "Use Markdown checkboxes and clearly label facts versus inferred requirements."
        ),
    },
    {
        "id": "gloss.equation-guide",
        "name": "Equation Guide",
        "name_zh": "公式导读",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "∑",
        "description": "Explain the important equations and their role in the method.",
        "description_zh": "解释关键公式及其在方法中的作用。",
        "tab_name": "Equations",
        "tab_name_zh": "公式",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "formulas"],
        "prompt": (
            "Build a guided tour of the paper's important equations. Define every symbol, "
            "explain the intuition, connect each equation to the algorithm, and point out "
            "implementation-sensitive details. Preserve mathematical notation in LaTeX."
        ),
    },
    {
        "id": "gloss.implementation-blueprint",
        "name": "Implementation Blueprint",
        "name_zh": "实现蓝图",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "⌨",
        "description": "Turn the method into an engineering plan with modules, interfaces, and pseudocode.",
        "description_zh": "将方法整理为模块、接口与伪代码组成的工程实现计划。",
        "tab_name": "Build",
        "tab_name_zh": "实现",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "method_content"],
        "prompt": (
            "Create an implementation blueprint for this paper. Identify the inputs, "
            "outputs, data flow, model modules, loss functions, training loop, evaluation "
            "loop, and failure-prone details. Include language-agnostic pseudocode and a "
            "short list of implementation decisions that the paper leaves unspecified."
        ),
    },
    {
        "id": "gloss.evidence-table",
        "name": "Evidence Table",
        "name_zh": "证据表",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "▤",
        "description": "Map every central claim to supporting evidence, caveats, and source locations.",
        "description_zh": "将核心结论对应到支持证据、限制条件和论文位置。",
        "tab_name": "Evidence",
        "tab_name_zh": "证据",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "claims_or_evidence"],
        "prompt": (
            "Build a rigorous evidence table for the paper. For each important claim, list "
            "the supporting experiment, result, figure/table/section location, strength of "
            "the evidence, assumptions, and the strongest caveat. Do not invent results."
        ),
    },
    {
        "id": "gloss.terminology-glossary",
        "name": "Terminology Glossary",
        "name_zh": "术语词汇表",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "Aa",
        "description": "Create a reader-friendly glossary of the paper's terms, symbols, and abbreviations.",
        "description_zh": "生成论文术语、符号与缩写的易读词汇表。",
        "tab_name": "Glossary",
        "tab_name_zh": "术语",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "terms"],
        "prompt": (
            "Create a compact glossary for this paper. Cover domain-specific terms, all "
            "important abbreviations, and mathematical symbols. Give a plain-language "
            "definition, the paper-specific meaning, and where the reader should look next."
        ),
    },
    {
        "id": "gloss.figure-table-guide",
        "name": "Figure & Table Guide",
        "name_zh": "图表导读",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "▧",
        "description": "Explain the role, reading order, and takeaway of the paper's important figures and tables.",
        "description_zh": "解释重要图表的作用、阅读顺序和应得出的结论。",
        "tab_name": "Figures",
        "tab_name_zh": "图表",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text", "figures_or_tables"],
        "prompt": (
            "Write a guided tour of the paper's figures and tables. For every important one, "
            "state the question it answers, how to read axes/rows/columns, the key result, "
            "and any misleading or missing comparison. Refer to its exact label when present."
        ),
    },
    {
        "id": "gloss.presentation-outline",
        "name": "Presentation Outline",
        "name_zh": "汇报提纲",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "▱",
        "description": "Turn the paper into a clear 8–12 slide journal-club or lab-meeting outline.",
        "description_zh": "将论文整理成适合组会或 journal club 的 8–12 页汇报提纲。",
        "tab_name": "Present",
        "tab_name_zh": "汇报",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text"],
        "prompt": (
            "Create an 8–12 slide presentation outline for this paper. For each slide, give "
            "a title, 2–4 speaking bullets, the most useful figure or table to show, and a "
            "speaker note. End with discussion questions and an honest limitations slide."
        ),
    },
    {
        "id": "gloss.reading-plan",
        "name": "Reading Plan",
        "name_zh": "阅读路线",
        "version": "1.0.0",
        "author": "Gloss",
        "icon": "◷",
        "description": "Build a time-boxed reading route for skimming, studying, or reproducing the paper.",
        "description_zh": "为速读、精读或复现制定分阶段、限时的阅读路线。",
        "tab_name": "Plan",
        "tab_name_zh": "路线",
        "permissions": ["paper:read", "ai:complete"],
        "requirements": ["body_text"],
        "prompt": (
            "Offer three practical reading routes for this paper: a 10-minute skim, a "
            "45-minute study session, and a reproduction-oriented deep read. For each route, "
            "give section order, questions to answer, what to annotate, and a concrete outcome."
        ),
    },
]

COMING_SOON = [
    {
        "id": "gloss.citation-graph",
        "name": "Citation Graph",
        "name_zh": "引用关系图",
        "icon": "🕸️",
        "description": "Explore the papers, authors, and ideas connected to the current work.",
        "description_zh": "探索与当前论文相关的论文、作者和观点网络。",
        "planned_contribution": "library.view",
    },
    {
        "id": "gloss.compare-papers",
        "name": "Compare Papers",
        "name_zh": "论文对比",
        "icon": "⇄",
        "description": "Compare methods, assumptions, datasets, and results across papers.",
        "description_zh": "横向对比多篇论文的方法、假设、数据集与结果。",
        "planned_contribution": "paper.sidebar.panel",
    },
    {
        "id": "gloss.figure-lab",
        "name": "Figure Lab",
        "name_zh": "图表工作台",
        "icon": "▧",
        "description": "Explain, extract, and organize important figures and tables.",
        "description_zh": "解释、提取并整理关键图表。",
        "planned_contribution": "paper.toolbar.action",
    },
    {
        "id": "gloss.reproduction-workspace",
        "name": "Reproduction Workspace",
        "name_zh": "复现工作区",
        "icon": "🧪",
        "description": "Turn a paper into an executable reproduction plan and evidence log.",
        "description_zh": "将论文整理为可执行的复现计划与证据记录。",
        "planned_contribution": "paper.sidebar.panel",
    },
    {
        "id": "gloss.dataset-explorer",
        "name": "Dataset Explorer",
        "name_zh": "数据集浏览器",
        "icon": "◫",
        "description": "Inspect dataset provenance, splits, licenses, and known limitations.",
        "description_zh": "检查数据集来源、划分、许可证与已知局限。",
        "planned_contribution": "paper.sidebar.panel",
    },
    {
        "id": "gloss.annotation-canvas",
        "name": "Annotation Canvas",
        "name_zh": "批注画布",
        "icon": "✎",
        "description": "Add shapes, callouts, stamps, and collaborative annotation layers.",
        "description_zh": "增加图形、标注框、印章与协作批注图层。",
        "planned_contribution": "pdf.annotation.layer",
    },
]


def _clean_text(value: Any, field: str, *, maximum: int, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"plugin {field} is required")
    if len(text) > maximum:
        raise ValueError(f"plugin {field} is too long")
    return text


def _value_matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    expected = schema.get("type")
    if expected == "boolean":
        matches = isinstance(value, bool)
    elif expected == "string":
        matches = isinstance(value, str)
    elif expected == "integer":
        matches = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        matches = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "array":
        matches = isinstance(value, list)
        item_type = (schema.get("items") or {}).get("type")
        if matches and item_type:
            matches = all(_value_matches_schema(item, {"type": item_type}) for item in value)
    else:
        return False
    if not matches:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            return False
        if len(value) > int(schema.get("maxLength", len(value))):
            return False
        if schema.get("pattern") and re.fullmatch(str(schema["pattern"]), value) is None:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            return False
        if len(value) > int(schema.get("maxItems", len(value))):
            return False
    return True


def _normalize_agent(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("plugin agent must be an object")
    unsupported = set(raw) - {"name", "name_zh", "icon", "messages"}
    if unsupported:
        raise ValueError("unsupported plugin agent fields: " + ", ".join(sorted(unsupported)))
    messages = raw.get("messages") or {}
    if not isinstance(messages, dict):
        raise ValueError("plugin agent messages must be an object")
    unsupported_stages = set(messages) - _AGENT_MESSAGE_STAGES
    if unsupported_stages:
        raise ValueError(
            "unsupported plugin agent message stages: " + ", ".join(sorted(unsupported_stages))
        )
    normalized_messages = dict(DEFAULT_AGENT["messages"])
    for stage, message in messages.items():
        if isinstance(message, dict):
            unsupported_languages = set(message) - {"en", "zh"}
            if unsupported_languages:
                raise ValueError(
                    f"unsupported languages for agent.messages.{stage}: "
                    + ", ".join(sorted(unsupported_languages))
                )
            localized = {
                key: _clean_text(value, f"agent.messages.{stage}.{key}", maximum=100)
                for key, value in message.items()
                if value is not None
            }
            if not localized:
                raise ValueError(f"agent.messages.{stage} requires en or zh text")
            normalized_messages[stage] = localized
        else:
            normalized_messages[stage] = _clean_text(
                message, f"agent.messages.{stage}", maximum=100,
            )
    return {
        "name": _clean_text(
            raw.get("name") or DEFAULT_AGENT["name"], "agent.name", maximum=40,
        ),
        "name_zh": _clean_text(
            raw.get("name_zh") or DEFAULT_AGENT["name_zh"],
            "agent.name_zh",
            maximum=40,
        ),
        "icon": _clean_text(
            raw.get("icon") or DEFAULT_AGENT["icon"], "agent.icon", maximum=8,
        ),
        "messages": normalized_messages,
    }


def _normalize_configuration(plugin_id: str, raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"title": "", "properties": {}}
    if not isinstance(raw, dict):
        raise ValueError("plugin contributes.configuration must be an object")
    unsupported = set(raw) - {"title", "title_zh", "properties"}
    if unsupported:
        raise ValueError(
            "unsupported plugin configuration fields: " + ", ".join(sorted(unsupported))
        )
    properties = raw.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("plugin configuration properties must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    relative_keys: list[str] = []
    for original_key, property_schema in properties.items():
        key = str(original_key).strip()
        prefix = f"{plugin_id}."
        if key.startswith(prefix):
            key = key[len(prefix):]
        if not key or key.startswith(".") or key.endswith(".") or ".." in key:
            raise ValueError(f"invalid plugin configuration key: {original_key}")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*", key):
            raise ValueError(f"invalid plugin configuration key: {original_key}")
        if key in normalized:
            raise ValueError(f"duplicate plugin configuration key: {key}")
        if not isinstance(property_schema, dict):
            raise ValueError(f"plugin configuration {key} must be an object")
        unsupported_schema = set(property_schema) - _CONFIG_SCHEMA_FIELDS
        if unsupported_schema:
            raise ValueError(
                f"unsupported fields for plugin configuration {key}: "
                + ", ".join(sorted(unsupported_schema))
            )
        schema = dict(property_schema)
        expected = schema.get("type")
        if expected not in _CONFIG_TYPES:
            raise ValueError(f"unsupported type for plugin configuration {key}: {expected}")
        if "default" not in schema:
            raise ValueError(f"plugin configuration {key} requires a default")
        for field in ("minLength", "maxLength", "minItems", "maxItems"):
            if field in schema and (
                not isinstance(schema[field], int)
                or isinstance(schema[field], bool)
                or schema[field] < 0
            ):
                raise ValueError(
                    f"plugin configuration {key} {field} must be a non-negative integer"
                )
        for minimum_field, maximum_field in (
            ("minLength", "maxLength"),
            ("minItems", "maxItems"),
        ):
            if (
                minimum_field in schema
                and maximum_field in schema
                and schema[minimum_field] > schema[maximum_field]
            ):
                raise ValueError(
                    f"plugin configuration {key} has inconsistent "
                    f"{minimum_field}/{maximum_field}"
                )
        for field in ("minimum", "maximum"):
            if field in schema and (
                not isinstance(schema[field], (int, float))
                or isinstance(schema[field], bool)
            ):
                raise ValueError(
                    f"plugin configuration {key} {field} must be a number"
                )
        if (
            "minimum" in schema
            and "maximum" in schema
            and schema["minimum"] > schema["maximum"]
        ):
            raise ValueError(
                f"plugin configuration {key} has inconsistent minimum/maximum"
            )
        if "pattern" in schema:
            if not isinstance(schema["pattern"], str):
                raise ValueError(
                    f"plugin configuration {key} pattern must be a string"
                )
            try:
                re.compile(schema["pattern"])
            except re.error as error:
                raise ValueError(
                    f"plugin configuration {key} has an invalid pattern"
                ) from error
        if "order" in schema and (
            not isinstance(schema["order"], int)
            or isinstance(schema["order"], bool)
        ):
            raise ValueError(f"plugin configuration {key} order must be an integer")
        if schema.get("editPresentation") not in (None, "multilineText"):
            raise ValueError(
                f"plugin configuration {key} has an unsupported editPresentation"
            )
        if expected == "array":
            items = schema.get("items")
            if not isinstance(items, dict) or items.get("type") not in {
                "boolean", "string", "number", "integer",
            }:
                raise ValueError(
                    f"plugin configuration {key} arrays require simple typed items"
                )
            if set(items) != {"type"}:
                raise ValueError(
                    f"plugin configuration {key} array items only support type"
                )
        if not _value_matches_schema(schema["default"], schema):
            raise ValueError(f"plugin configuration {key} has an invalid default")
        enum_values = schema.get("enum")
        if enum_values is not None:
            if not isinstance(enum_values, list) or not enum_values:
                raise ValueError(f"plugin configuration {key} enum must be a non-empty list")
            if any(not _value_matches_schema(value, {**schema, "enum": [value]}) for value in enum_values):
                raise ValueError(f"plugin configuration {key} enum contains an invalid value")
            for descriptions_key in (
                "enumDescriptions", "markdownEnumDescriptions", "enumItemLabels",
                "enumItemLabels_zh",
            ):
                descriptions = schema.get(descriptions_key)
                if descriptions is not None and (
                    not isinstance(descriptions, list)
                    or len(descriptions) != len(enum_values)
                    or any(not isinstance(item, str) for item in descriptions)
                ):
                    raise ValueError(
                        f"plugin configuration {key} {descriptions_key} must match enum"
                    )
        normalized[key] = schema
        relative_keys.append(key)
    for key in relative_keys:
        if any(other != key and other.startswith(f"{key}.") for other in relative_keys):
            raise ValueError(
                f"plugin configuration key {key} cannot be a prefix of another key"
            )
    return {
        "title": _clean_text(
            raw.get("title"), "contributes.configuration.title",
            maximum=80, required=False,
        ),
        "title_zh": _clean_text(
            raw.get("title_zh"), "contributes.configuration.title_zh",
            maximum=80, required=False,
        ),
        "properties": normalized,
    }


def resolve_configuration(
    manifest: dict[str, Any], overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configuration = (manifest.get("contributes") or {}).get("configuration") or {}
    schemas = configuration.get("properties") or {}
    provided = dict(overrides or {})
    resolved: dict[str, Any] = {}
    unknown = set(provided)
    for key, schema in schemas.items():
        qualified = f"{manifest['id']}.{key}"
        if key in provided:
            value = provided[key]
            unknown.discard(key)
        elif qualified in provided:
            value = provided[qualified]
            unknown.discard(qualified)
        else:
            value = schema["default"]
        if not _value_matches_schema(value, schema):
            raise ValueError(f"invalid value for plugin configuration {key}")
        resolved[key] = value
    if unknown:
        raise ValueError(
            "unknown plugin configuration values: " + ", ".join(sorted(unknown))
        )
    return resolved


def _preflight_failure(manifest: dict[str, Any], parsed: dict[str, Any]) -> dict[str, str] | None:
    """Return the first unmet manifest requirement without contacting an AI provider."""
    text = str(parsed.get("full_text") or "").strip()
    lowered = text.lower()
    pages = parsed.get("pages") or []
    has_formula = bool(re.search(
        r"(?:[A-Za-zα-ωΑ-Ω][\wα-ωΑ-Ω]*(?:\s*\([^)]{1,40}\))?\s*"
        r"(?:=|≈|≜|≤|≥|∈|∝|→|←|↦)\s*\S+|[∑∏∫√∀∃]|\\[\[(])",
        text,
    ))
    has_method = bool(re.search(
        r"\b(?:method(?:ology)?|algorithm|training|objective|loss function|"
        r"hyperparameter|preprocessing|implementation|optimization|dataset)\b|"
        r"(?:方法|算法|训练|目标函数|损失函数|超参数|预处理|实现|数据集)",
        lowered,
    ))
    has_claims_or_evidence = bool(re.search(
        r"\b(?:we (?:show|find|observe|demonstrate|propose)|results?|experiments?|"
        r"evaluation|baseline|accuracy|f1|auc|table\s*\d+|figure\s*\d+)\b|"
        r"(?:结果|实验|评估|基线|准确率|表\s*\d+|图\s*\d+)",
        lowered,
    ))
    has_terms = len(set(re.findall(r"\b(?:[A-Z][A-Z0-9-]{1,}|[A-Za-z][A-Za-z-]{7,})\b", text))) >= 2
    has_figures_or_tables = any(page.get("images") for page in pages) or bool(re.search(
        r"\b(?:figure|fig\.|table)\s*\d+|(?:图|表)\s*\d+", lowered
    ))
    satisfied = {
        "body_text": len(text) >= 160,
        "formulas": has_formula,
        "method_content": has_method,
        "claims_or_evidence": has_claims_or_evidence,
        "terms": has_terms,
        "figures_or_tables": has_figures_or_tables,
    }
    for requirement in manifest.get("requirements", []):
        if not satisfied[requirement]:
            return {
                "code": _REQUIREMENT_DEFINITIONS[requirement]["reason"],
                "requirement": requirement,
            }
    return None


def validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("plugin manifest must be an object")
    api_version = payload.get("api_version", PLUGIN_API_VERSION)
    if api_version != PLUGIN_API_VERSION:
        raise ValueError(
            f"unsupported plugin api_version {api_version}; expected {PLUGIN_API_VERSION}"
        )
    plugin_id = _clean_text(payload.get("id"), "id", maximum=64)
    if not _ID.fullmatch(plugin_id) or plugin_id.startswith("core."):
        raise ValueError("plugin id must be a safe lowercase identifier")
    version = _clean_text(payload.get("version"), "version", maximum=40)
    if not _VERSION.fullmatch(version):
        raise ValueError("plugin version must use semantic versioning")
    permissions = payload.get("permissions") or ["paper:read", "ai:complete"]
    if not isinstance(permissions, list) or not permissions:
        raise ValueError("plugin permissions must be a non-empty list")
    permissions = list(dict.fromkeys(str(item) for item in permissions))
    unsupported = set(permissions) - _ALLOWED_PERMISSIONS
    if unsupported:
        raise ValueError(f"unsupported plugin permissions: {', '.join(sorted(unsupported))}")
    if "paper:read" not in permissions or "ai:complete" not in permissions:
        raise ValueError("analysis plugins require paper:read and ai:complete")
    requirements = payload.get("requirements") or []
    if not isinstance(requirements, list):
        raise ValueError("plugin requirements must be a list")
    requirements = list(dict.fromkeys(str(item) for item in requirements))
    unsupported_requirements = set(requirements) - _ALLOWED_REQUIREMENTS
    if unsupported_requirements:
        raise ValueError(
            "unsupported plugin requirements: " + ", ".join(sorted(unsupported_requirements))
        )

    name = _clean_text(payload.get("name"), "name", maximum=80)
    name_zh = _clean_text(payload.get("name_zh"), "name_zh", maximum=80, required=False)
    icon = _clean_text(payload.get("icon") or "🧩", "icon", maximum=8)
    raw_contributes = payload.get("contributes")
    if raw_contributes is None:
        raw_contributes = {
            "paper_sidebar": {
                "tab_name": payload.get("tab_name") or name,
                "tab_name_zh": payload.get("tab_name_zh") or name_zh,
                "icon": icon,
            }
        }
    if not isinstance(raw_contributes, dict):
        raise ValueError("plugin contributes must be an object")
    unsupported_contributions = set(raw_contributes) - {"paper_sidebar", "configuration"}
    if unsupported_contributions:
        raise ValueError(
            "unsupported plugin contribution points: "
            + ", ".join(sorted(unsupported_contributions))
        )
    paper_sidebar = raw_contributes.get("paper_sidebar")
    if not isinstance(paper_sidebar, dict):
        raise ValueError("analysis plugins must contribute a paper_sidebar panel")
    tab_name = _clean_text(
        paper_sidebar.get("tab_name") or payload.get("tab_name") or name,
        "contributes.paper_sidebar.tab_name",
        maximum=24,
    )
    tab_name_zh = _clean_text(
        paper_sidebar.get("tab_name_zh") or payload.get("tab_name_zh") or name_zh,
        "contributes.paper_sidebar.tab_name_zh",
        maximum=24,
        required=False,
    )
    panel_icon = _clean_text(
        paper_sidebar.get("icon") or icon,
        "contributes.paper_sidebar.icon",
        maximum=8,
    )
    configuration = _normalize_configuration(
        plugin_id, raw_contributes.get("configuration"),
    )
    agent = _normalize_agent(payload.get("agent"))

    return {
        "api_version": PLUGIN_API_VERSION,
        "id": plugin_id,
        "name": name,
        "name_zh": name_zh,
        "version": version,
        "author": _clean_text(payload.get("author") or "Community", "author", maximum=80),
        "icon": icon,
        "description": _clean_text(payload.get("description"), "description", maximum=500),
        "description_zh": _clean_text(
            payload.get("description_zh"), "description_zh", maximum=500, required=False
        ),
        "tab_name": tab_name,
        "tab_name_zh": tab_name_zh,
        "contributes": {
            "paper_sidebar": {
                "tab_name": tab_name,
                "tab_name_zh": tab_name_zh,
                "icon": panel_icon,
            },
            "configuration": configuration,
        },
        "agent": agent,
        "permissions": permissions,
        "requirements": requirements,
        "prompt": _clean_text(payload.get("prompt"), "prompt", maximum=12000),
        "output": "visual" if "image:generate" in permissions else "markdown",
        "builtin": False,
    }


def _manifest_path(plugin_id: str) -> Path:
    if not _ID.fullmatch(plugin_id) or plugin_id.startswith("core."):
        raise ValueError("invalid plugin id")
    return PLUGINS_DIR / plugin_id / "manifest.json"


def _write_manifest(manifest: dict[str, Any]) -> None:
    path = _manifest_path(manifest["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def list_installed() -> list[dict[str, Any]]:
    installed = []
    if not PLUGINS_DIR.exists():
        return installed
    for path in sorted(PLUGINS_DIR.glob("*/manifest.json")):
        try:
            installed.append(validate_manifest(json.loads(read_utf8_text(path))))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return installed


def get_installed(plugin_id: str) -> dict[str, Any] | None:
    try:
        path = _manifest_path(plugin_id)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return validate_manifest(json.loads(read_utf8_text(path)))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def marketplace_snapshot() -> dict[str, Any]:
    installed = list_installed()
    installed_ids = {plugin["id"] for plugin in installed}
    marketplace = [
        {**validate_manifest(plugin), "installed": plugin["id"] in installed_ids}
        for plugin in MARKETPLACE
    ]
    return {
        "api_version": PLUGIN_API_VERSION,
        "permissions": sorted(_ALLOWED_PERMISSIONS),
        "contribution_points": CONTRIBUTION_POINTS,
        "core": CORE_EXTENSIONS,
        "marketplace": marketplace,
        "installed": installed,
        "coming_soon": COMING_SOON,
    }


def install_marketplace(plugin_id: str) -> dict[str, Any]:
    source = next((plugin for plugin in MARKETPLACE if plugin["id"] == plugin_id), None)
    if not source:
        raise ValueError("plugin was not found in the marketplace")
    manifest = validate_manifest(source)
    _write_manifest(manifest)
    return manifest


def install_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    manifest = validate_manifest(payload)
    _write_manifest(manifest)
    return manifest


def _semver_tuple(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def upgrade_installed_marketplace_plugins() -> list[str]:
    """Upgrade installed first-party manifests without touching custom plugins."""
    marketplace = {item["id"]: item for item in MARKETPLACE}
    upgraded: list[str] = []
    for installed in list_installed():
        source = marketplace.get(installed["id"])
        if not source or installed.get("author") != "Gloss":
            continue
        if _semver_tuple(str(source["version"])) <= _semver_tuple(str(installed["version"])):
            continue
        manifest = validate_manifest(source)
        _write_manifest(manifest)
        store.cache_delete_prefix(f"plugin:{manifest['id']}:")
        upgraded.append(manifest["id"])
    return upgraded


def uninstall(plugin_id: str) -> None:
    path = _manifest_path(plugin_id)
    if not path.exists():
        raise ValueError("plugin is not installed")
    path.unlink()
    store.cache_delete_prefix(f"plugin:{plugin_id}:")
    visual_explainer.remove_plugin_assets(plugin_id)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def _plugin_cache_key(
    manifest: dict[str, Any],
    language: str,
    configuration: dict[str, Any] | None,
    lens_revision: str = "reading-lens-v1:auto:general",
) -> tuple[str, dict[str, Any]]:
    prompt_revision = hashlib.sha256(
        manifest["prompt"].encode("utf-8")
    ).hexdigest()[:12]
    resolved_configuration = resolve_configuration(manifest, configuration)
    settings_revision = hashlib.sha256(
        json.dumps(
            resolved_configuration, sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    network_revision = (
        deep_research.NETWORK_RESEARCH_REVISION
        if "network:research" in manifest["permissions"]
        else "local"
    )
    image_revision = (
        visual_explainer.IMAGE_PIPELINE_REVISION
        if "image:generate" in manifest["permissions"]
        else "text"
    )
    return (
        f"plugin:{manifest['id']}:{manifest['version']}:{prompt_revision}:"
        f"{settings_revision}:{language}:{lens_revision}:{network_revision}:{image_revision}",
        resolved_configuration,
    )


def _cached_plugin_response(cached: Any) -> dict[str, Any]:
    if isinstance(cached, dict) and isinstance(cached.get("markdown"), str):
        response = {
            "status": "ready",
            "reason": None,
            "markdown": cached["markdown"],
            "research": cached.get("research"),
        }
        if cached.get("visual_explanation") is not None:
            response["visual_explanation"] = cached["visual_explanation"]
        return response
    return {"status": "ready", "reason": None, "markdown": cached}


def get_cached_result(
    paper_id: str,
    plugin_id: str,
    *,
    language: str | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read a plugin result without running preflight or contacting a provider."""
    manifest = get_installed(plugin_id)
    if not manifest:
        raise ValueError("plugin is not installed")
    lang = language or output_language()
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    resolution = resolve_paper_lens(paper_id, parsed.get("full_text", ""))
    cache_key, _ = _plugin_cache_key(
        manifest, lang, configuration, cache_revision(resolution)
    )
    cached = store.cache_get(paper_id, cache_key)
    if cached is None:
        return None
    if (
        isinstance(cached, dict)
        and cached.get("visual_explanation") is not None
        and not visual_explainer.assets_available(cached["visual_explanation"])
    ):
        return None
    return _cached_plugin_response(cached)


async def run_plugin(
    paper_id: str,
    plugin_id: str,
    *,
    refresh: bool = False,
    language: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = get_installed(plugin_id)
    if not manifest:
        raise ValueError("plugin is not installed")
    lang = language or output_language()
    parsed = store.load_parsed(paper_id)
    if not parsed:
        raise ValueError("paper not parsed")
    resolution = resolve_paper_lens(paper_id, parsed.get("full_text", ""))
    cache_key, resolved_configuration = _plugin_cache_key(
        manifest, lang, configuration, cache_revision(resolution)
    )
    unavailable = _preflight_failure(manifest, parsed)
    if unavailable:
        return {"status": "unavailable", "reason": unavailable, "markdown": ""}

    if not refresh:
        cached = store.cache_get(paper_id, cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("visual_explanation") is not None
            and not visual_explainer.assets_available(cached["visual_explanation"])
        ):
            cached = None
        if cached is not None:
            return _cached_plugin_response(cached)

    paper_text = truncate_to_tokens(parsed.get("full_text", ""), 60000)
    if "image:generate" in manifest["permissions"]:
        response = await visual_explainer.run(
            paper_id=paper_id,
            plugin_id=plugin_id,
            paper_text=paper_text,
            language=lang,
            style=str(resolved_configuration.get("style") or "diagram"),
            quality=str(resolved_configuration.get("quality") or "medium"),
            image_provider=str(resolved_configuration.get("imageProvider") or "auto"),
            lens_guidance=guidance_for(resolution),
            cache_revision=cache_key,
            provider=provider,
            model=model,
        )
        if not get_installed(plugin_id):
            raise ValueError("plugin was uninstalled while the task was running")
        store.cache_set(
            paper_id,
            cache_key,
            {
                "markdown": response["markdown"],
                "visual_explanation": response["visual_explanation"],
            },
        )
        return response

    research_evidence = None
    if "network:research" in manifest["permissions"]:
        research_evidence = await deep_research.collect_external_evidence(
            paper_id,
            depth=str(resolved_configuration.get("depth") or "exhaustive"),
        )
    instructions = manifest["prompt"].replace("{{language}}", lang)
    settings_block = ""
    if resolved_configuration:
        settings_block = (
            "\n\n=== PLUGIN SETTINGS ===\n"
            + json.dumps(resolved_configuration, ensure_ascii=False, indent=2)
        )
    system = (
        f"You are running the Gloss plugin '{manifest['name']}' version "
        f"{manifest['version']}. Follow its instructions faithfully and write entirely "
        f"in {lang}. Use only the provided paper and, when present, the host-retrieved "
        f"VERIFIED NETWORK RESEARCH bundle. Never cite an external source outside that "
        f"bundle. Return Markdown. "
        f"{guidance_for(resolution)}"
    )
    research_block = ""
    if research_evidence is not None:
        research_block = (
            "\n\n=== VERIFIED NETWORK RESEARCH ===\n"
            + deep_research.prompt_bundle(research_evidence)
        )
    user = (
        f"=== PLUGIN INSTRUCTIONS ===\n{instructions}{settings_block}"
        f"{research_block}"
        f"\n\n=== PAPER ===\n{paper_text}"
    )
    result = await text_complete(system, user, provider=provider, model=model)
    if not get_installed(plugin_id):
        raise ValueError("plugin was uninstalled while the task was running")
    response = {"status": "ready", "reason": None, "markdown": result}
    if research_evidence is not None:
        response["research"] = deep_research.public_metadata(research_evidence)
        store.cache_set(
            paper_id,
            cache_key,
            {"markdown": result, "research": response["research"]},
        )
    else:
        store.cache_set(paper_id, cache_key, result)
    return response
