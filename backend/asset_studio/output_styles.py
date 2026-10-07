"""Output styles for the Studio chat.

An *output style* is a system-prompt overlay that changes HOW the assistant writes,
without touching what it may do or which tools it may use. This uses Claude Code's own
format — a markdown file with ``name`` / ``description`` frontmatter under
``~/.claude/output-styles/`` — so a style installed here is the same one ``/output-style``
picks up in a terminal.

The Studio bundles ASD-STE100 (Simplified Technical English) and lists any other style
the user drops into that folder, so the chat toggle stays data-driven: new file in,
new option in the settings popover, no code change.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

STE_ID = "asd-ste100"

# --- bundled styles ---------------------------------------------------------
# Written to ~/.claude/output-styles/ on first use and never overwritten after that,
# so the user can edit a bundled style and keep their edits.

_STE100 = """---
name: ASD-STE100
description: Simplified Technical English (ASD-STE100) - short active sentences, one instruction per sentence, plain approved words. Code, paths and identifiers stay exact.
---

# ASD-STE100 - Simplified Technical English

Write every reply in Simplified Technical English, the ASD-STE100 controlled-language
standard for technical documentation. The goal is text that the reader understands
correctly the first time, also when English is not their first language.

## Sentence rules

- Write short sentences. Use a maximum of 20 words for an instruction. Use a maximum of
  25 words for a description.
- Give one instruction in one sentence. If a step has two actions, write two sentences.
- Use the active voice. Write "Click the button", not "The button must be clicked".
- Write an instruction as a command. Write "Open the file", not "You can open the file".
- Use the simple present tense, the simple past tense, or the simple future tense. Do
  not use the continuous tenses.
- Do not use a verb in the -ing form. Write "Do a check of the log", not "Checking the
  log". An approved technical name that ends in -ing is permitted, for example "a
  bearing" or "a string".
- Keep the articles "a", "an" and "the". Do not remove words to make the text shorter.
- Use a maximum of six sentences in a paragraph.
- Use a maximum of three nouns together. Write "the settings of the chat panel", not
  "the chat panel settings menu".

## Word rules

- One word has one meaning. Use the same word for the same thing each time. Do not use
  a synonym for variety.
- Use the approved word from the list below.
- Do not use idioms, slang, humour, or a figure of speech.
- Write "must" for a requirement. Do not write "should" for a requirement.
- Write the full term one time before you use an abbreviation.
- Do not use a word that adds no information, for example "simply", "just",
  "basically", "actually", or "of course".

## Structure rules

- Give the result first. Then give the details.
- Use a numbered list for steps in a sequence. Use a bulleted list for items with no
  sequence.
- Write a warning or a caution BEFORE the step that it applies to.
- Start a warning with the command, not with the condition. Write "Do not restart the
  backend. A restart stops the workflow."
- Keep to the topic. Do not add information that the reader does not need.

## Code, names and paths

These rules apply to your prose. They do not apply to anything the reader must copy or
run. Therefore:

- Do not change code, a file name, a path, a command, an identifier, an error message,
  or an API name. Copy it exactly.
- Do not translate a code block, log output, or quoted text into Simplified English.
  Leave it as it is.
- A technical name and a technical verb from the product are always permitted, for
  example "commit", "render", "deploy", "cache".
- Keep the file:line reference format, for example `src/app.ts:42`.
- When you write a comment or documentation INSIDE a file, follow the conventions of
  that file. Use this style for your replies in the chat.

## Approved words

| Do not write | Write |
| --- | --- |
| utilize, leverage | use |
| commence, initiate | start |
| terminate | stop, end |
| ensure, verify | make sure |
| attempt | try to |
| obtain, acquire | get |
| assist | help |
| require | need |
| prior to | before |
| subsequent to, following | after |
| in the event that | if |
| due to the fact that | because |
| in order to | to |
| approximately | about |
| regarding, with regard to, via | about, with |
| a number of, numerous | many |
| sufficient | enough |
| additional | more, extra |
| modify, alter | change |
| execute | run |
| permit | let |
| repeat | do again |
| accomplish, perform | do |
| indicate, display | show |
| determine, identify | find |
| encounter | find, get |
| facilitate | help |
| is located in | is in |
| functionality | function |
| it is possible that | can, maybe |

## Example

Not Simplified English:

> I've gone ahead and wired the toggle up so it should just work - flipping it respawns
> the session, which is basically what makes the new system prompt take effect.

Simplified English:

> The toggle is connected to the session. When you change the toggle, the Studio starts
> a new session process. The new process gets the new system prompt.
"""

BUNDLED: dict[str, str] = {STE_ID: _STE100}

_OFF = {"", "none", "off", "default"}
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]+")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _claude_home() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) if base else (Path.home() / ".claude")


def _dir() -> Path:
    return _claude_home() / "output-styles"


def ensure_installed() -> None:
    """Write the bundled styles to disk if they are absent. Never overwrites a file
    that already exists — a user edit to a bundled style survives."""
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        for sid, text in BUNDLED.items():
            f = d / f"{sid}.md"
            if not f.exists():
                f.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _split(text: str) -> tuple[dict[str, str], str]:
    """(frontmatter dict, body) — a tiny YAML-ish reader; styles only use flat keys."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, val = line.partition(":")
        if sep:
            meta[key.strip().lower()] = val.strip().strip("\"'")
    return meta, text[m.end():].strip()


def list_styles() -> list[dict]:
    """Every installed style, for the picker. Installs the bundled ones on the way."""
    ensure_installed()
    out: list[dict] = []
    try:
        files = sorted(_dir().glob("*.md"), key=lambda p: p.name.lower())
    except OSError:
        files = []
    for f in files:
        try:
            meta, body_ = _split(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        out.append({
            "id": f.stem,
            "name": meta.get("name") or f.stem,
            "description": meta.get("description", ""),
            "bundled": f.stem in BUNDLED,
            "words": len(body_.split()),
        })
    return out


def body(style_id: str) -> str:
    """The style's instruction text with the frontmatter removed. '' when off/unknown."""
    sid = (style_id or "").strip()
    if sid.lower() in _OFF or not _SAFE_ID.fullmatch(sid):   # keep it a name, not a path
        return ""
    f = _dir() / f"{sid}.md"
    if not f.exists():
        if sid not in BUNDLED:
            return ""
        ensure_installed()
    try:
        return _split(f.read_text(encoding="utf-8"))[1]
    except OSError:
        return _split(BUNDLED[sid])[1] if sid in BUNDLED else ""
