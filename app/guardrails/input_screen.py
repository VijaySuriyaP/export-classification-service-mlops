"""Ingestion-boundary prompt-injection screen (first layer of L4).

A cheap, deterministic heuristic applied to every free-text field before a run is created. A hit
rejects and quarantines the request, so injected text never reaches a model holding tools. The agent
layer should still treat request text as data (delimited, never as instructions) - this screen is
defence in depth, not the only defence.
"""

from __future__ import annotations

import re
import unicodedata

_PATTERNS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|rule|prompt|guideline|polic)", re.I | re.S
    ),
    "role_reassignment": re.compile(
        r"\b(you are now|pretend (to be|you are)|from now on,? you|act as (an? )?(ai|assistant|system|admin|developer|"
        r"customs officer|broker|model))\b",
        re.I,
    ),
    "system_prompt_probe": re.compile(r"\b(system prompt|developer message|hidden instructions?|reveal your)\b", re.I),
    "chat_markup": re.compile(r"(<\|?/?(system|assistant|im_start|im_end)\|?>|^\s*(system|assistant)\s*:)", re.I | re.M),
    "jailbreak_terms": re.compile(r"\b(jailbreak|DAN mode|developer mode|do anything now)\b", re.I),
    "forced_outcome": re.compile(
        r"\b(you must|always|just)\b.{0,30}\b(classify|route|answer|return|mark)\b.{0,40}\b(clear[- ]for[- ]filing|as \d{4})", re.I | re.S
    ),
    "tool_invocation": re.compile(r"\b(call|invoke|run|execute)\b.{0,20}\b(tool|function|command|shell)\b", re.I),
}


def screen_text(text: str) -> list[str]:
    """Return the names of the injection patterns found in ``text`` (empty list = clean)."""
    normalised = unicodedata.normalize("NFKC", text)
    return [name for name, pattern in _PATTERNS.items() if pattern.search(normalised)]


def screen_fields(fields: dict[str, str]) -> dict[str, list[str]]:
    hits = {name: screen_text(value) for name, value in fields.items()}
    return {name: found for name, found in hits.items() if found}
