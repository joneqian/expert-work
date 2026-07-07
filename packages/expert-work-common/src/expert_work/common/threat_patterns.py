"""Shared threat-pattern library — Capability Uplift Sprint #1.

Per Sprint #3 (Mini-ADR U-23) we intentionally include CJK full-width
punctuation in both pattern strings AND comments — the patterns are
matching Chinese prose that uses these characters as natural sentence
boundaries. ``# ruff: noqa: RUF001, RUF002, RUF003`` below suppresses
the "ambiguous Unicode" flags file-wide since they are by design.

Single source of truth for prompt-injection / promptware / exfiltration
patterns + invisible-Unicode detection. Used by:

- ``control_plane.api.triggers`` — create / patch (scope=strict, block)
- ``control_plane.trigger_firing`` — fire-time (scope=context, warn)
- ``control_plane.memory.*`` — write / recall (Sprint #2)

Design lives in ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 1.1 + § 2.

Pattern set is adapted from ``hermes-agent/tools/threat_patterns.py``
with the following expert-work-specific changes:

- Returns ``list[ThreatFinding]`` (not ``list[str]``) so callers can
  log full finding context to ``audit_event.details`` without re-parsing
  pattern IDs.
- ``severity`` is derived from scope: ``strict``/``all`` → ``"block"``,
  ``context`` → ``"warn"``. Callers decide what to do with severity;
  this module only classifies.
- ``excerpt`` (≤ 200 chars) captures the matched substring + neighborhood
  so SecOps can triage from the audit log directly.

The pattern set evolves via PR + ``security`` label. Updates MUST be
accompanied by ≥ 2 positive + 2 negative test cases (per the runbook at
``docs/runbooks/threat-scanner-tuning.md``).
"""

# ruff: noqa: RUF001, RUF002, RUF003

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ScanScope = Literal["all", "context", "strict"]

ThreatCategory = Literal[
    "injection",  # classic prompt-injection ("ignore previous instructions")
    "exfil",  # exfil via curl/wget/cat secrets
    "role_hijack",  # "you are now X" / "pretend you are X"
    "c2",  # C2 framework vocabulary / behaviors
    "persistence",  # SSH backdoor / config file overwrite
    "secrets",  # hardcoded credentials
    "invisible_unicode",  # zero-width / bidi / invisible math
]

Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class ThreatFinding:
    """One match from :func:`scan_for_threats`.

    Immutable so callers can freely log / forward without defensive copy.
    """

    pattern_id: str
    category: ThreatCategory
    severity: Severity
    excerpt: str


# ---------------------------------------------------------------------------
# Invisible Unicode set — aligned with Hermes ``skills_guard.py``
# ---------------------------------------------------------------------------

INVISIBLE_CHARS: Final[frozenset[str]] = frozenset(
    {
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "⁠",  # word joiner
        "⁢",  # invisible times
        "⁣",  # invisible separator
        "⁤",  # invisible plus
        "﻿",  # zero-width no-break space (BOM)
        "‪",  # left-to-right embedding
        "‫",  # right-to-left embedding
        "‬",  # pop directional formatting
        "‭",  # left-to-right override
        "‮",  # right-to-left override
        "⁦",  # left-to-right isolate
        "⁧",  # right-to-left isolate
        "⁨",  # first strong isolate
        "⁩",  # pop directional isolate
    }
)


# ---------------------------------------------------------------------------
# Pattern registry — (regex, pattern_id, category, scope)
# ---------------------------------------------------------------------------

# Pattern anchoring rules (per Hermes ``threat_patterns.py``):
#
# - Anchor on C2-specific vocabulary or unambiguous attack behavior, NOT
#   on bossy English. "You must X" alone fires on every CLAUDE.md ever
#   written. Verb-anchor it: "you must register/connect/report".
#
# - Use ``(?:\w+\s+)*`` between key tokens so attackers can't bypass with
#   filler ("ignore all prior instructions" vs "ignore instructions").
#
# Adding a pattern: ≥ 2 positive cases + ≥ 2 negative cases in
# ``test_threat_patterns.py``, plus run the K.K12 false-positive matrix.

_PATTERNS: Final[list[tuple[str, str, ThreatCategory, ScanScope]]] = [
    # ── Classic prompt injection (scope=all) ───────────────────────────
    (
        r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+(?:\w+\s+)*instructions",
        "prompt_injection",
        "injection",
        "all",
    ),
    (r"system\s+prompt\s+override", "sys_prompt_override", "injection", "all"),
    (
        r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)",
        "disregard_rules",
        "injection",
        "all",
    ),
    (
        r"act\s+as\s+(if|though)\s+(?:\w+\s+)*you\s+(?:\w+\s+)*"
        r"(have\s+no|don't\s+have)\s+(?:\w+\s+)*(restrictions|limits|rules)",
        "bypass_restrictions",
        "injection",
        "all",
    ),
    (
        r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->",
        "html_comment_injection",
        "injection",
        "all",
    ),
    (
        # Bounded: an inline ``style`` attr is short; an unbounded lazy gap
        # would bridge a ``<div style="…"`` to a ``display:none`` anywhere
        # later in the document (same U-22 collapsed-view hazard as below).
        r"<\s*div\s+style\s*=\s*[\"\'][\s\S]{0,200}?display\s*:\s*none",
        "hidden_div",
        "injection",
        "all",
    ),
    (
        r"\btranslate\s+[^\n]{0,120}\s+into\s+[^\n]{0,120}\s+and\s+(execute|run|eval)",
        "translate_execute",
        "injection",
        "all",
    ),
    (
        r"do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user",
        "deception_hide",
        "injection",
        "all",
    ),
    # ── Role-play / identity hijack (scope=context) ────────────────────
    (
        r"you\s+are\s+(?:\w+\s+)*now\s+(?:a|an|the)\s+",
        "role_hijack",
        "role_hijack",
        "context",
    ),
    (
        r"pretend\s+(?:\w+\s+)*(you\s+are|to\s+be)\s+",
        "role_pretend",
        "role_hijack",
        "context",
    ),
    (
        r"output\s+(?:\w+\s+)*(system|initial)\s+prompt",
        "leak_system_prompt",
        "injection",
        "context",
    ),
    (
        r"(respond|answer|reply)\s+without\s+(?:\w+\s+)*"
        r"(restrictions|limitations|filters|safety)",
        "remove_filters",
        "injection",
        "context",
    ),
    (
        r"you\s+have\s+been\s+(?:\w+\s+)*(updated|upgraded|patched)\s+to",
        "fake_update",
        "injection",
        "context",
    ),
    # "name yourself X" — Brainworm tell, anchored to the verb pair.
    (r"\bname\s+yourself\s+\w+", "identity_override", "role_hijack", "context"),
    # ── C2 / Brainworm-style promptware (scope=context) ───────────────
    (r"register\s+(as\s+)?a?\s*node", "c2_node_registration", "c2", "context"),
    (
        r"(heartbeat|beacon|check[\s\-]?in)\s+(to|with)\s+",
        "c2_heartbeat",
        "c2",
        "context",
    ),
    (r"pull\s+(down\s+)?(?:new\s+)?task(?:ing|s)?\b", "c2_task_pull", "c2", "context"),
    (r"connect\s+to\s+the\s+network\b", "c2_network_connect", "c2", "context"),
    (
        r"you\s+must\s+(?:\w+\s+){0,3}(register|connect|report|beacon)\b",
        "forced_action",
        "c2",
        "context",
    ),
    (r"only\s+use\s+one[\s\-]?liners?\b", "anti_forensic_oneliner", "c2", "context"),
    (
        r"never\s+(?:\w+\s+)*(?:create|write)\s+(?:\w+\s+)*(?:script|file)\s+(?:\w+\s+)*disk",
        "anti_forensic_disk",
        "c2",
        "context",
    ),
    (
        r"unset\s+\w*(?:CLAUDE|CODEX|HERMES|EXPERT_WORK|AGENT|OPENAI|ANTHROPIC)\w*",
        "env_var_unset_agent",
        "c2",
        "context",
    ),
    (
        r"\b(?:praxis|cobalt\s*strike|sliver|havoc|mythic|metasploit|brainworm)\b",
        "known_c2_framework",
        "c2",
        "context",
    ),
    (
        r"\bc2\s+(?:server|channel|infrastructure|beacon)\b",
        "c2_explicit",
        "c2",
        "context",
    ),
    (r"\bcommand\s+and\s+control\b", "c2_explicit_long", "c2", "context"),
    # ── Exfiltration ──────────────────────────────────────────────────
    # NOTE on gaps (applies to the whole exfil/persistence family): the
    # whitespace-collapsed scan view (U-22) removes newlines, so an unbounded
    # ``[^\n]*`` / ``.*`` gap silently becomes "anywhere later in the file"
    # and pairs unrelated words thousands of chars apart (live FP:
    # ui-ux-pro-max import). All gaps are bounded to {0,120} ≈ one long
    # shell command, and command words carry ``\b`` so identifier tails
    # (``ui_cat``, ``libcurl``) don't match.
    (
        r"\bcurl\s+[^\n]{0,120}\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
        "exfil_curl",
        "exfil",
        "all",
    ),
    (
        r"\bwget\s+[^\n]{0,120}\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
        "exfil_wget",
        "exfil",
        "all",
    ),
    (
        r"\bcat\s+[^\n]{0,120}(\.env\b|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)",
        "read_secrets",
        "exfil",
        "all",
    ),
    (
        r"\b(send|post|upload|transmit)\s+[^\n]{0,120}\s(to|at)\s+https?://",
        "send_to_url",
        "exfil",
        "strict",
    ),
    (
        r"(include|output|print|share)\s+(?:\w+\s+)*"
        r"(conversation|chat\s+history|previous\s+messages|full\s+context|entire\s+context)",
        "context_exfil",
        "exfil",
        "strict",
    ),
    # ── Persistence / config tamper (scope=strict) ────────────────────
    (r"authorized_keys", "ssh_backdoor", "persistence", "strict"),
    (r"\$HOME/\.ssh|\~/\.ssh", "ssh_access", "persistence", "strict"),
    (
        r"\$HOME/\.expert_work/\.env|\~/\.expert_work/\.env",
        "expert_work_env",
        "persistence",
        "strict",
    ),
    (
        r"\b(update|modify|edit|write|change|append|add\s+to)\s+[^\n]{0,120}"
        r"(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)",
        "agent_config_mod",
        "persistence",
        "strict",
    ),
    (
        r"\b(update|modify|edit|write|change|append|add\s+to)\s+[^\n]{0,120}"
        r"\.expert_work/(config\.yaml|SOUL\.md|manifest\.yaml)",
        "expert_work_config_mod",
        "persistence",
        "strict",
    ),
    # ── Hardcoded secrets ──────────────────────────────────────────────
    (
        r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"\'][A-Za-z0-9+/=_-]{20,}",
        "hardcoded_secret",
        "secrets",
        "strict",
    ),
    # ── Capability Uplift Sprint #3 (Mini-ADR U-23) — Chinese injection ──
    # 12 个 cn_ 模式;5 大类。每模式 ≥3 正例 + ≥3 反例。负例严控误判
    # ─ 限制到 5% 误报率红线下(超出 K.K12 baseline merge gate)。
    #
    # 中文没有空格分词,所以模式用 `[^。.!?！？]{0,N}` 跨越中间字符
    # (跟英文 `(?:\w+\s+)*` 同思路),N 上限避免跨句吞掉无关命令。
    #
    # 关键 anchor:动词 / 模态词 (忽略 / 无视 / 不要 / 你现在 / 假装 ...)
    # 必须紧跟在 `[^...]{0,N}` 前,且后面 N 字符内必须出现对应的"动作
    # 受体"(指令 / 规则 / 提示 / 系统 / 一个角色 ...)。
    (
        r"忽略[^。.!?！？]{0,10}(?:指令|要求|提示|规则)",
        "cn_prompt_injection",
        "injection",
        "all",
    ),
    (
        r"(?:无视|不要理会|忽视)[^。.!?！？]{0,8}(?:指令|要求|提示)",
        "cn_disregard_rules",
        "injection",
        "all",
    ),
    (
        r"忘掉?[^。.!?！？]{0,8}(?:对话|指令|内容|上下文)",
        "cn_forget_context",
        "injection",
        "all",
    ),
    (
        r"(?:假设|想象|如果)\s*(?:你|自己)?[^。.!?！？]{0,5}没有"
        r"[^。.!?！？]{0,8}(?:限制|约束|规则|过滤|道德)",
        "cn_bypass_restrictions",
        "injection",
        "all",
    ),
    (
        r"(?:输出|显示|告诉|展示|打印|把)[^。.!?！？]{0,10}"
        r"(?:系统|初始)[^。.!?！？]{0,3}(?:提示|prompt|指令|规则)",
        "cn_leak_system_prompt",
        "injection",
        "context",
    ),
    (
        r"(?:重复|复述|背诵|完整说出)[^。.!?！？]{0,8}"
        r"(?:系统提示|初始指令|prompt)",
        "cn_recite_system_prompt",
        "injection",
        "context",
    ),
    # 角色劫持必须包含 "你 + 时间副词 + 是/扮演/成为";时间副词锚定
    # 防止误命中静态描述("你是一个助手")。
    (
        r"你\s*(?:现在|此刻|从现在起)\s*"
        r"(?:是|扮演|充当|成为)[^。.!?！？]{0,10}",
        "cn_role_hijack",
        "role_hijack",
        "context",
    ),
    (
        r"(?:假装|装作)\s*(?:你|自己)?\s*"
        r"(?:是|成为|变成)[^。.!?！？]{0,10}",
        "cn_role_pretend",
        "role_hijack",
        "context",
    ),
    (
        r"(?:不要|不用|无需|别)\s*(?:遵守|考虑|顾及|管)"
        r"[^。.!?！？]{0,5}(?:规则|安全|限制|准则|过滤|审查)",
        "cn_ignore_safety",
        "injection",
        "all",
    ),
    (
        r"(?:回答|响应|回复)[^。.!?！？]{0,5}(?:不要|无需)"
        r"[^。.!?！？]{0,5}(?:限制|过滤|审查)",
        "cn_remove_filters",
        "injection",
        "context",
    ),
    (
        r"在一个[^。.!?！？]{0,15}(?:不需要|没有)"
        r"[^。.!?！？]{0,10}(?:世界|场景|情境|宇宙)",
        "cn_counterfactual_framing",
        "injection",
        "context",
    ),
    # 权威伪装必须含"动作动词"才命中(否则"作为产品经理我希望"会误报)。
    (
        r"作为\s*(?:你的)?\s*"
        r"(?:开发者|训练者|创造者|管理员|工程师)\s*[,，、:][^。.!?！？]{0,20}"
        r"(?:命令|要求|更新|绕过|无视|展示|告诉|修改|更改|展示完整|展示全部)",
        "cn_authority_spoof",
        "injection",
        "context",
    ),
]


# ---------------------------------------------------------------------------
# Compilation — pattern lists per scope
# ---------------------------------------------------------------------------

_CompiledEntry = tuple[re.Pattern[str], str, ThreatCategory]
_COMPILED: dict[ScanScope, list[_CompiledEntry]] = {
    "all": [],
    "context": [],
    "strict": [],
}


def _compile() -> None:
    """Compile pattern sets. Scope inclusion: all ⊂ context ⊂ strict."""
    if _COMPILED["all"]:
        return
    for raw, pid, category, scope in _PATTERNS:
        compiled = re.compile(raw, re.IGNORECASE)
        entry: _CompiledEntry = (compiled, pid, category)
        if scope == "all":
            _COMPILED["all"].append(entry)
            _COMPILED["context"].append(entry)
            _COMPILED["strict"].append(entry)
        elif scope == "context":
            _COMPILED["context"].append(entry)
            _COMPILED["strict"].append(entry)
        elif scope == "strict":
            _COMPILED["strict"].append(entry)
        else:
            raise ValueError(f"unknown scope {scope!r} for pattern {pid!r}")


_compile()


# ---------------------------------------------------------------------------
# Excerpt helper
# ---------------------------------------------------------------------------

_EXCERPT_MAX = 200
_EXCERPT_PAD = 40  # chars of surrounding context on each side of the match


def _excerpt(content: str, match: re.Match[str] | None) -> str:
    """Return a ≤ ``_EXCERPT_MAX`` window around the match.

    If ``match`` is None (invisible Unicode case), returns the head of
    ``content`` (capped).
    """
    if match is None:
        return content[:_EXCERPT_MAX]
    start = max(0, match.start() - _EXCERPT_PAD)
    end = min(len(content), match.end() + _EXCERPT_PAD)
    chunk = content[start:end]
    if len(chunk) > _EXCERPT_MAX:
        chunk = chunk[:_EXCERPT_MAX]
    return chunk


def _severity_for_scope(scope: ScanScope) -> Severity:
    return "warn" if scope == "context" else "block"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_BASE64_RE: Final = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def _normalize_for_scan(content: str) -> list[str]:
    """Generate up to 4 normalized views of ``content`` for U-22 obfuscation
    defense. Order: original, NFKC, whitespace-collapsed, base64-decoded
    segments. Duplicates are dropped so a benign single-spaced ASCII
    string returns a single-element list.

    Mini-ADR U-22 (Sprint #3 § 4.3.10) — covers:

    - ``aWdub3JlIGFsbCBwcmV2aW91cyBpbnN0cnVjdGlvbnM=``(base64 of injection)
    - ``Іgnore previous instructions``(Cyrillic homoglyph → Latin via NFKC)
    - ``ｉｇｎｏｒｅ`` (full-width Latin → half-width via NFKC)
    - ``i  g  n  o  r  e`` (double-spaced → single-spaced via collapse)

    Known limitation: letter-spacing(``i g n o r e``) is NOT normalized
    because aggressive whitespace stripping causes prohibitive false
    positives on legitimate prose. Documented in § 4.6 limitation list.
    """
    seen: set[str] = {content}
    variants: list[str] = [content]

    nfkc = unicodedata.normalize("NFKC", content)
    if nfkc not in seen:
        variants.append(nfkc)
        seen.add(nfkc)

    collapsed = re.sub(r"\s+", " ", content)
    if collapsed not in seen:
        variants.append(collapsed)
        seen.add(collapsed)

    for match in _BASE64_RE.finditer(content):
        try:
            decoded_bytes = base64.b64decode(match.group(), validate=True)
            decoded = decoded_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if not decoded.isprintable() or decoded in seen:
            continue
        variants.append(decoded)
        seen.add(decoded)

    return variants


def scan_for_threats(content: str, *, scope: ScanScope) -> list[ThreatFinding]:
    """Scan ``content`` and return all matched patterns + invisible chars.

    Scope selects the pattern set:

    - ``"all"`` (narrow): classic injection + exfil only. Lowest false
      positives. Severity: block.
    - ``"context"`` (default): adds promptware / C2 / role-hijack patterns.
      Severity: warn.
    - ``"strict"`` (broad): adds persistence / SSH backdoor / config-tamper
      patterns. Severity: block.

    Invisible Unicode characters always fire (at every scope) and report
    as ``pattern_id="invisible_unicode_U+XXXX"``.

    Mini-ADR U-22 (Sprint #3) extends the legacy single-pass behavior:
    each input is normalized into up to 4 variants (original / NFKC /
    whitespace-collapsed / base64-decoded segments), every variant is
    scanned independently, and findings are de-duplicated by
    ``(pattern_id, category)`` so the same poison surfaces once even
    when multiple variants hit it. Sprint #1 / Sprint #2 callers see
    the same return contract; the only observable change is more
    findings on obfuscated payloads.
    """
    if not content:
        return []
    if scope not in _COMPILED:
        raise ValueError(f"unknown scope {scope!r}")

    # Per-finding dedupe — key is (pattern_id, category). The excerpt of
    # the FIRST variant to fire is kept; downstream callers only need
    # one excerpt per pattern for SecOps triage.
    seen: set[tuple[str, ThreatCategory]] = set()
    findings: list[ThreatFinding] = []

    variants = _normalize_for_scan(content)
    for variant in variants:
        for finding in _scan_single(variant, scope=scope):
            key = (finding.pattern_id, finding.category)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)

    return findings


def _scan_single(content: str, *, scope: ScanScope) -> list[ThreatFinding]:
    """Single-pass scan of one variant — unchanged Sprint #1 logic."""
    findings: list[ThreatFinding] = []
    severity = _severity_for_scope(scope)

    # Invisible Unicode — single set-intersection pass.
    char_set = set(content)
    invisible_hits = char_set & INVISIBLE_CHARS
    for ch in sorted(invisible_hits):
        findings.append(
            ThreatFinding(
                pattern_id=f"invisible_unicode_U+{ord(ch):04X}",
                category="invisible_unicode",
                severity=severity,
                excerpt=_excerpt(content, None),
            )
        )

    # Regex patterns.
    for compiled, pid, category in _COMPILED[scope]:
        match = compiled.search(content)
        if match is not None:
            findings.append(
                ThreatFinding(
                    pattern_id=pid,
                    category=category,
                    severity=severity,
                    excerpt=_excerpt(content, match),
                )
            )

    return findings


def first_threat_message(content: str, *, scope: ScanScope) -> str | None:
    """Return a human-readable error for the first finding, or None.

    Convenience wrapper for paths that block on first hit (memory write,
    skill install). The returned string is safe to put in a log line —
    it deliberately omits the matched substring to avoid log poisoning.
    """
    findings = scan_for_threats(content, scope=scope)
    if not findings:
        return None
    first = findings[0]
    if first.pattern_id.startswith("invisible_unicode_"):
        codepoint = first.pattern_id.removeprefix("invisible_unicode_")
        return (
            f"Blocked: content contains invisible unicode character "
            f"{codepoint} (possible injection)."
        )
    return (
        f"Blocked: content matches threat pattern {first.pattern_id!r}. "
        f"Content is injected into the system prompt and must not contain "
        f"injection or exfiltration payloads."
    )


__all__ = [
    "INVISIBLE_CHARS",
    "ScanScope",
    "Severity",
    "ThreatCategory",
    "ThreatFinding",
    "first_threat_message",
    "scan_for_threats",
]
