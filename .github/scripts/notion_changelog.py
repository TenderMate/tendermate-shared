#!/usr/bin/env python3
"""Append one paragraph about the current main push to a Notion page.

A commit subject that already says what changed is kept. A blank or generic
subject is replaced by one sentence built from file and function names.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import requests

NOTION_VERSION = "2022-06-28"
DIFF_LIMIT = 12_000

# A subject is generic when, after the ticket id is removed, no concrete word remains.
GENERIC = {
    "fix", "fixes", "fixed", "fixing",
    "update", "updates", "updated", "updating",
    "wip", "tmp", "temp", "test", "tests", "testing",
    "commit", "commits", "change", "changes", "changed",
    "minor", "tweak", "tweaks", "cleanup", "clean",
    "refactor", "refactoring", "format", "formatting",
    "typo", "typos", "nit", "nits", "misc", "chore",
    "style", "done", "stuff", "things", "edit", "edits",
    "lint", "fmt", "bump", "xxx", "asdf", "todo",
    "правки", "правка", "поправки", "поправка",
    "фикс", "фиксы", "апдейт", "обновление", "коммит",
    "изменения", "изменение", "мелочь", "мелкие",
}


def notion(method: str, path: str, body: dict) -> None:
    token = os.environ["NOTION_TOKEN"]
    response = requests.request(
        method,
        "https://api.notion.com" + path,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()


def is_informative(subject: str) -> bool:
    text = re.sub(r"\b(?:TEN-\d+|#\d+)\b", " ", subject, flags=re.IGNORECASE)
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]{2,}", text)
    useful = [word for word in words if word.lower() not in GENERIC]
    if len(useful) >= 2:
        return True
    return len(useful) == 1 and len(useful[0]) >= 6


def git_diff() -> str:
    try:
        proc = subprocess.run(
            ["git", "diff", "--unified=1", "HEAD^", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return clip_diff(proc.stdout)


def clip_diff(diff: str) -> str:
    kept: list[str] = []
    block: list[str] = []
    size = 0
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") and block:
            piece = "".join(block)
            if size + len(piece) > DIFF_LIMIT:
                break
            kept.append(piece)
            size += len(piece)
            block = [line]
        else:
            block.append(line)
    if block and size < DIFF_LIMIT:
        kept.append("".join(block)[: DIFF_LIMIT - size])
    return "".join(kept)


def local_summary(diff: str) -> str:
    files: list[str] = []
    symbols: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            match = re.search(r" b/(\S+)$", line)
            if match and match.group(1) not in files:
                files.append(match.group(1))
        elif line.startswith("@@"):
            tail = line.split("@@")[-1].strip()
            name = re.search(r"(?:def|class|function|func)\s+([A-Za-z_][A-Za-z0-9_]*)", tail)
            symbol = name.group(1) if name else tail.split("(")[0].strip()
            if symbol and symbol not in symbols and len(symbol) < 80:
                symbols.append(symbol)
    if not files and not symbols:
        return "В коммите нет описания, а по diff содержательные изменения не прочитались."
    bits = ["В коммите нет нормального описания."]
    if symbols:
        shown = ", ".join(symbols[:4])
        bits.append(f"По diff менялись {shown}.")
    if files:
        shown_files = ", ".join(files[:5])
        extra = f" и ещё {len(files) - 5}" if len(files) > 5 else ""
        bits.append(f"Файлы diff: {shown_files}{extra}.")
    return " ".join(bits)


def describe_change(subject: str) -> str:
    if is_informative(subject):
        return subject[:300]
    return local_summary(git_diff())


def paragraph() -> str:
    message = os.environ.get("COMMIT_MESSAGE", "").strip().splitlines()
    subject = message[0] if message else ""
    body = "\n".join(message[1:]).strip()
    files = os.environ.get("CHANGED_FILES", "").strip()
    if len(files) > 800:
        files = files[:800] + "…"
    parts = [
        f"Пуш в main. {os.environ['GITHUB_REPOSITORY']} {os.environ['GITHUB_SHA'][:7]}",
        f"от {os.environ['GITHUB_ACTOR']}.",
        describe_change(subject),
    ]
    if body:
        parts.append(body)
    if files:
        parts.append(f"Файлы: {files}.")
    return " ".join(part for part in parts if part)[:1900]


def main() -> None:
    page_id = os.environ["NOTION_PAGE_ID"]
    text = paragraph()
    notion(
        "PATCH",
        f"/v1/blocks/{page_id}/children",
        {
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    },
                }
            ]
        },
    )
    print("appended", text)
    try:
        notion(
            "POST",
            "/v1/comments",
            {
                "parent": {"page_id": page_id},
                "rich_text": [{"type": "text", "text": {"content": text}}],
            },
        )
        print("commented")
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
        response = getattr(exc, "response", None)
        print("comment skipped", response.status_code if response is not None else exc)


if __name__ == "__main__":
    main()
