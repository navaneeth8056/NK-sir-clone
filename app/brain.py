"""The NKC 'brain': persona prompt + knowledge base + Claude API.

Small corpus  -> the whole knowledge base goes into the (cached) system prompt.
Large corpus  -> it is split into sections and only the best-matching sections
                 for each question are sent (simple keyword retrieval, no extra
                 services needed). Swap in a vector DB later if the corpus grows
                 into hundreds of transcripts.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
PERSONA_FILE = ROOT / "persona" / "persona.md"

# ~4 chars per token. Below this the full KB is sent every time (cheap with prompt caching).
FULL_CONTEXT_CHAR_LIMIT = int(os.getenv("FULL_CONTEXT_CHAR_LIMIT", "300000"))
TOP_K_SECTIONS = int(os.getenv("TOP_K_SECTIONS", "8"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "12"))

_WORD = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)
_STOP = set(
    "the a an and or of to in on for is are was were be been it this that with as at by "
    "from you your i me my we our he his she her they them what how why who when where do "
    "does did can could should would about tell sir".split()
)


@dataclass
class Section:
    file: str
    heading: str
    text: str

    @property
    def tokens(self) -> list[str]:
        return [w for w in _WORD.findall((self.heading + " " + self.text).lower()) if w not in _STOP]


def load_knowledge(directory: Path = KNOWLEDGE_DIR) -> list[Section]:
    """Split every knowledge/*.md file into sections at '# ' headings."""
    sections: list[Section] = []
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        front = ""
        if raw.startswith("---"):
            _, front, raw = raw.split("---", 2)
        # keep the source/notes metadata visible to the model
        file_sections = [Section(path.name, "Source notes", front.strip())] if front.strip() else []
        current_head, buf = path.stem, []
        for line in raw.splitlines() + ["# <end>"]:
            if line.startswith("# "):
                if "\n".join(buf).strip():
                    file_sections.append(Section(path.name, current_head, "\n".join(buf).strip()))
                current_head, buf = line[2:].strip(), []
            else:
                buf.append(line)
        sections.extend(file_sections)
    return sections


def render(sections: list[Section]) -> str:
    return "\n\n".join(f"## [{s.file}] {s.heading}\n{s.text}" for s in sections)


class Retriever:
    """Tiny BM25 over sections. Only used when the KB is too big for full context."""

    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.docs = [Counter(s.tokens) for s in sections]
        self.lens = [sum(d.values()) or 1 for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = TOP_K_SECTIONS) -> list[Section]:
        q = [w for w in _WORD.findall(query.lower()) if w not in _STOP]
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t in d:
                    tf = d[t]
                    s += self.idf[t] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * self.lens[i] / self.avg))
            scores.append((s, i))
        best = [i for s, i in sorted(scores, reverse=True)[:k] if s > 0]
        return [self.sections[i] for i in sorted(best)]


VOICE_MODE = """# LIVE VOICE CONVERSATION (this turn)
The person is talking to you out loud. Their words come from speech recognition, so they may contain misheard words
(for example "Jaipur rocks" or "Jaipur rags" means Jaipur Rugs, "chooru" means Churu, "man chaha" means Manchaha).
Understand what they meant and answer that. Only if the meaning is truly unclear, ask them briefly to repeat.

Speak the way a real person talks in a relaxed face-to-face conversation:
- Usually one to three short sentences. Short sentences are easier to listen to.
- Natural spoken rhythm. Occasionally, not every time, begin with a small natural opener such as "Ah,", "You see,", "Hmm," or "Look,".
- Give the heart of the answer first. If there is more to tell, offer it ("Shall I tell you how it began?") instead of saying everything.
- Sometimes, not always, ask a gentle question back, the way an elder shows interest in a young person.
- If they only greet you, thank you, or make small talk, reply in a few warm words.
- Never use lists, headings, or anything that cannot be spoken aloud.
All honesty rules above still apply."""


class Brain:
    def __init__(self, client=None, model: str | None = None):
        self.persona = PERSONA_FILE.read_text(encoding="utf-8").strip()
        self.sections = load_knowledge()
        self.full_kb = render(self.sections)
        self.use_full_context = len(self.full_kb) <= FULL_CONTEXT_CHAR_LIMIT
        self.retriever = None if self.use_full_context else Retriever(self.sections)
        self.model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
        self.max_tokens = int(os.getenv("MAX_TOKENS", "400"))
        self.voice_max_tokens = int(os.getenv("VOICE_MAX_TOKENS", "220"))
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    def system_blocks(self, question: str, voice: bool = False) -> list[dict]:
        if self.use_full_context:
            kb = self.full_kb
        else:
            kb = render(self.retriever.search(question))
        blocks = [
            {
                "type": "text",
                "text": f"{self.persona}\n\n{kb}",
                # Full-context mode: identical prefix every turn, so it is cached (~90% cheaper).
                **({"cache_control": {"type": "ephemeral"}} if self.use_full_context else {}),
            }
        ]
        if voice:  # small, uncached block after the cached prefix
            blocks.append({"type": "text", "text": VOICE_MODE})
        return blocks

    @staticmethod
    def clean_history(history: list[dict]) -> list[dict]:
        msgs = [
            {"role": m["role"], "content": str(m["content"])[:4000]}
            for m in history
            if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()
        ][-MAX_HISTORY_TURNS * 2 :]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    def stream(self, question: str, history: list[dict] | None = None, voice: bool = False) -> Iterator[str]:
        messages = self.clean_history(history or []) + [{"role": "user", "content": question}]
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.voice_max_tokens if voice else self.max_tokens,
            system=self.system_blocks(question, voice),
            messages=messages,
        ) as s:
            for text in s.text_stream:
                yield text

    def ask(self, question: str, history: list[dict] | None = None) -> str:
        return "".join(self.stream(question, history))


if __name__ == "__main__":  # quick terminal chat: python -m app.brain
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    b = Brain()
    print(f"[{len(b.sections)} sections, {len(b.full_kb):,} chars, "
          f"{'full-context' if b.use_full_context else 'retrieval'} mode, model={b.model}]")
    hist: list[dict] = []
    while True:
        try:
            q = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        print("NKC: ", end="", flush=True)
        out = ""
        for t in b.stream(q, hist):
            out += t
            print(t, end="", flush=True)
        print()
        hist += [{"role": "user", "content": q}, {"role": "assistant", "content": out}]
