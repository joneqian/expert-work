"""Benchmark instance -> expert_work retrieval shape — Stream CM-N5.

Maps both benchmarks onto one neutral schema (:class:`RetrievalInstance`)
so the P0 harness is benchmark-agnostic:

- **LongMemEval**: one instance per question; ground truth is the
  benchmark's own ``answer_session_ids`` (session level) and per-turn
  ``has_answer`` flags (turn level). The 30 abstention questions
  (``_abs`` ids) are skipped by default — the official retrieval
  protocol excludes them because they have no ground-truth location.
- **LoCoMo**: one instance per QA over the shared conversation corpus;
  ground truth is the ``evidence`` dia_id list. Category 5
  (adversarial / unanswerable) is excluded from the retrieval tier for
  the same no-ground-truth reason (it returns ``adversarial_answer``,
  no evidence).

**Time shift (Mini-ADR CM-K4)**: temporal decay (CM-6) only depends on
relative age (``now - anchor``), so instead of injecting a fake ``now``
into the stores (a protocol change + doubles sweep), the harness shifts
every timestamp forward by ``real_now - question_date``. Ages are
preserved exactly; the stores' hardcoded ``datetime.now(UTC)`` is
correct as-is. Wall-clock drift over a multi-hour run is <0.5% of the
30-day half-life — noted, ignored.

Date parsing is locale-independent on purpose: ``strptime`` ``%a``/``%B``
depend on the process locale, so both formats are parsed with explicit
regexes + an English month map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

#: LoCoMo gives no per-question date — questions are asked "after the
#: conversation". Anchor them one day past the newest session so decay
#: ages stay meaningful.
_LOCOMO_QUESTION_OFFSET = timedelta(days=1)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

#: ``2023/04/10 (Mon) 23:07`` — the weekday parenthetical is ignored.
_LONGMEMEVAL_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\D*?(\d{1,2}):(\d{2})")

#: ``1:56 pm on 8 May, 2023``
_LOCOMO_DATE = re.compile(
    r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.IGNORECASE
)


def parse_longmemeval_date(raw: str) -> datetime:
    match = _LONGMEMEVAL_DATE.search(raw)
    if match is None:
        raise ValueError(f"unparseable LongMemEval date: {raw!r}")
    year, month, day, hour, minute = (int(g) for g in match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def parse_locomo_date(raw: str) -> datetime:
    match = _LOCOMO_DATE.search(raw)
    if match is None:
        raise ValueError(f"unparseable LoCoMo date: {raw!r}")
    hour_s, minute_s, meridiem, day_s, month_name, year_s = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        raise ValueError(f"unknown month in LoCoMo date: {raw!r}")
    hour = int(hour_s) % 12
    if meridiem.lower() == "pm":
        hour += 12
    return datetime(int(year_s), month, int(day_s), hour, int(minute_s), tzinfo=UTC)


# ---------------------------------------------------------------------------
# Neutral schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryDoc:
    """One chunk destined for the memory store."""

    doc_id: str
    session_id: str
    content: str
    timestamp: datetime | None
    has_answer: bool = False


@dataclass(frozen=True)
class RetrievalInstance:
    """One question + its corpus + ground truth, benchmark-agnostic."""

    question_id: str
    question: str
    question_type: str
    question_date: datetime
    answer: str | None
    docs: tuple[MemoryDoc, ...]
    answer_session_ids: frozenset[str]
    #: Turn-level ground truth. Explicit field (not derived from the
    #: docs' ``has_answer`` flags) so LoCoMo instances can share one
    #: corpus tuple per conversation instead of copying ~600 docs per QA.
    answer_doc_ids: frozenset[str]


def shift_to_now(instance: RetrievalInstance, *, now: datetime) -> RetrievalInstance:
    """Shift every timestamp by ``now - question_date`` (CM-K4).

    Relative ages are preserved exactly, so temporal decay behaves as it
    would have at question time — with zero store-protocol changes.
    """
    delta = now - instance.question_date
    docs = tuple(
        replace(d, timestamp=d.timestamp + delta) if d.timestamp is not None else d
        for d in instance.docs
    )
    return replace(instance, question_date=now, docs=docs)


def strip_timestamps(instance: RetrievalInstance) -> RetrievalInstance:
    """Drop all doc timestamps — the decay-off ablation arm.

    ``_decay_for`` treats "no timestamp at all" as "decays nothing", so
    omitting timestamps at write time is a clean data-level switch — no
    monkeypatching the stores.
    """
    docs = tuple(replace(d, timestamp=None) for d in instance.docs)
    return replace(instance, docs=docs)


# ---------------------------------------------------------------------------
# LongMemEval
# ---------------------------------------------------------------------------


def load_longmemeval(
    path: Path,
    *,
    granularity: Literal["turn", "session"] = "turn",
    include_abstention: bool = False,
) -> list[RetrievalInstance]:
    """Parse a LongMemEval JSON file (oracle / S / M — same schema).

    ``turn`` granularity writes one doc per non-empty turn (the natural
    ``memory_item`` grain; enables turn-level metrics via ``has_answer``);
    ``session`` joins each session into one doc (the coarser official
    alternative — turn metrics then degenerate to session metrics).
    """
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    instances: list[RetrievalInstance] = []
    for entry in raw:
        question_id = str(entry["question_id"])
        if question_id.endswith("_abs") and not include_abstention:
            continue
        session_ids = [str(s) for s in entry["haystack_session_ids"]]
        dates = [parse_longmemeval_date(d) for d in entry["haystack_dates"]]
        docs: list[MemoryDoc] = []
        for session_id, session_date, session in zip(
            session_ids, dates, entry["haystack_sessions"], strict=True
        ):
            if granularity == "session":
                content = "\n".join(
                    f"{turn['role']}: {turn['content']}" for turn in session if turn.get("content")
                )
                docs.append(
                    MemoryDoc(
                        doc_id=session_id,
                        session_id=session_id,
                        content=content,
                        timestamp=session_date,
                        has_answer=any(turn.get("has_answer") for turn in session),
                    )
                )
                continue
            for idx, turn in enumerate(session):
                content = turn.get("content")
                if not content:
                    continue
                docs.append(
                    MemoryDoc(
                        doc_id=f"{session_id}#t{idx}",
                        session_id=session_id,
                        content=f"{turn['role']}: {content}",
                        timestamp=session_date,
                        has_answer=bool(turn.get("has_answer")),
                    )
                )
        instances.append(
            RetrievalInstance(
                question_id=question_id,
                question=str(entry["question"]),
                question_type=str(entry["question_type"]),
                question_date=parse_longmemeval_date(str(entry["question_date"])),
                answer=str(entry["answer"]) if entry.get("answer") is not None else None,
                docs=tuple(docs),
                answer_session_ids=frozenset(str(s) for s in entry["answer_session_ids"]),
                answer_doc_ids=frozenset(d.doc_id for d in docs if d.has_answer),
            )
        )
    return instances


# ---------------------------------------------------------------------------
# LoCoMo
# ---------------------------------------------------------------------------

_LOCOMO_SESSION_KEY = re.compile(r"^session_(\d+)$")

#: Mem0-paper naming for the numeric categories.
LOCOMO_CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}


def load_locomo(
    path: Path,
    *,
    categories: frozenset[int] = frozenset({1, 2, 3, 4}),
) -> list[RetrievalInstance]:
    """Parse ``locomo10.json`` into one instance per QA.

    Category 5 (adversarial) is excluded by default: it has no evidence
    annotation, so there is nothing for the retrieval tier to score
    (and counting it into overall numbers is exactly the Zep 84->58
    miscount — getzep/zep-papers#5). The corpus docs are shared across
    every QA of the same conversation (built once, referenced by tuple).
    """
    raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    instances: list[RetrievalInstance] = []
    for sample in raw:
        sample_id = str(sample["sample_id"])
        conversation = sample["conversation"]
        docs: list[MemoryDoc] = []
        latest: datetime | None = None
        for key, turns in conversation.items():
            match = _LOCOMO_SESSION_KEY.match(key)
            if match is None:
                continue
            session_date = parse_locomo_date(str(conversation[f"{key}_date_time"]))
            latest = session_date if latest is None else max(latest, session_date)
            for turn in turns:
                text = str(turn.get("text", ""))
                caption = turn.get("blip_caption")
                if caption:
                    text = (
                        f"{text} [shared photo: {caption}]"
                        if text
                        else f"[shared photo: {caption}]"
                    )
                if not text:
                    continue
                docs.append(
                    MemoryDoc(
                        doc_id=str(turn["dia_id"]),
                        session_id=key,
                        content=f"{turn['speaker']}: {text}",
                        timestamp=session_date,
                    )
                )
        if latest is None:
            continue
        corpus = tuple(docs)
        sessions_by_dia = {d.doc_id: d.session_id for d in corpus}
        question_date = latest + _LOCOMO_QUESTION_OFFSET
        for idx, qa in enumerate(sample["qa"]):
            category = int(qa["category"])
            if category not in categories:
                continue
            evidence = [str(e) for e in qa.get("evidence", [])]
            answer = qa.get("answer")
            instances.append(
                RetrievalInstance(
                    question_id=f"{sample_id}-q{idx}",
                    question=str(qa["question"]),
                    question_type=f"locomo-{LOCOMO_CATEGORY_NAMES[category]}",
                    question_date=question_date,
                    answer=str(answer) if answer is not None else None,
                    docs=corpus,
                    answer_session_ids=frozenset(
                        sessions_by_dia[e] for e in evidence if e in sessions_by_dia
                    ),
                    answer_doc_ids=frozenset(e for e in evidence if e in sessions_by_dia),
                )
            )
    return instances
