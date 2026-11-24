"""Prompts are code: files in prompts/, one per version, pinned by a lock file of hashes.

    prompts/system.v1.md      the text (a str.format template)
    prompts/prompts.lock.json {"system.v1": "<sha256>"}

Rules this enforces, with a test:
- A published version never changes. Editing system.v1.md without updating the lock fails CI,
  so a prompt change has to be a new file (system.v2.md) with its own lock entry.
- Which version runs is a setting (`system_prompt_version`), so v1 and v2 can be compared with
  the same eval suite before switching, and rolled back without a deploy of new code.
- The version is recorded with every question (audit log) and every trace (`prompt.version`),
  so any answer can be traced back to the exact prompt that produced it.
"""

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
LOCK_FILE = PROMPTS_DIR / "prompts.lock.json"


class UnknownPromptError(KeyError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    template: str

    @property
    def id(self) -> str:
        return f"{self.name}.{self.version}"

    @property
    def sha256(self) -> str:
        return digest(self.template)

    def render(self, **values: str) -> str:
        return self.template.format(**values)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@cache
def load(name: str, version: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.{version}.md"
    if not path.is_file():
        raise UnknownPromptError(f"{name}.{version}")
    return Prompt(name, version, path.read_text(encoding="utf-8").rstrip("\n"))


def available() -> list[str]:
    """Every prompt file, as name.version."""
    return sorted(path.name.removesuffix(".md") for path in PROMPTS_DIR.glob("*.md"))


def lock() -> dict[str, str]:
    """What the lock file says each version must hash to."""
    locked: dict[str, str] = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    return locked


def write_lock() -> None:
    """Pin new versions: `uv run python -m platform_copilot.prompts`. Existing entries stay."""
    locked = lock()
    for prompt_id in available():
        name, version = prompt_id.split(".", 1)
        locked.setdefault(prompt_id, load(name, version).sha256)
    LOCK_FILE.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_lock()
