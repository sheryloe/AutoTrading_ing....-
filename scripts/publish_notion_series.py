from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTION_DIR = REPO_ROOT / "docs" / "notion" / "auto_trading_10step"
API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TEXT_LIMIT = 1800


def api_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code}: {body}") from exc


def load_articles() -> list[Path]:
    return sorted(path for path in NOTION_DIR.glob("Step *.md"))


def strip_meta(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).strip()
        return title, body
    title = ""
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line:
            break
        key, _, value = line.partition(":")
        if key.strip() == "Title":
            title = value.strip()
    body = "\n".join(lines[idx:]).strip()
    return title, body


def split_chunks(text: str, limit: int = TEXT_LIMIT) -> Iterable[str]:
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def rich_text(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}}]


def paragraph_blocks(text: str) -> list[dict]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text(chunk)},
        }
        for chunk in split_chunks(text)
    ]


def heading_block(level: int, text: str) -> dict:
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text(text.strip())},
    }


def markdown_to_blocks(body: str, source_url: str) -> list[dict]:
    blocks: list[dict] = []
    current_paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal current_paragraph
        if current_paragraph:
            text = " ".join(part.strip() for part in current_paragraph if part.strip()).strip()
            if text:
                blocks.extend(paragraph_blocks(text))
            current_paragraph = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        if line.startswith("## "):
            flush_paragraph()
            blocks.append(heading_block(2, line[3:]))
            continue
        current_paragraph.append(line)

    flush_paragraph()
    blocks.append(heading_block(2, "원문 링크"))
    blocks.extend(
        paragraph_blocks(
            f"GitHub Pages HTML 원문: {source_url}\n이 페이지는 AI_Auto 운영형 자동매매 콘솔 구축 과정을 10단계 시리즈로 정리한 문서입니다."
        )
    )
    return blocks[:95]


def list_existing_child_titles(parent_page_id: str, token: str) -> dict[str, str]:
    titles: dict[str, str] = {}
    cursor = None
    while True:
        suffix = f"/blocks/{parent_page_id}/children?page_size=100"
        if cursor:
            suffix += f"&start_cursor={cursor}"
        data = api_request("GET", suffix, token)
        for item in data.get("results", []):
            if item.get("type") == "child_page":
                title = item.get("child_page", {}).get("title", "")
                if title:
                    titles[title] = item.get("id", "")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return titles


def create_page(parent_page_id: str, token: str, title: str, blocks: list[dict]) -> dict:
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {"title": {"title": rich_text(title)}},
        "children": blocks,
    }
    return api_request("POST", "/pages", token, payload)


def main() -> int:
    token = os.environ.get("NOTION_TOKEN")
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not token or not parent_page_id:
        print("NOTION_TOKEN and NOTION_PARENT_PAGE_ID are required", file=sys.stderr)
        return 1

    existing = list_existing_child_titles(parent_page_id, token)
    created: list[str] = []
    skipped: list[str] = []

    source_entries = json.loads((NOTION_DIR / "index.json").read_text(encoding="utf-8"))
    source_map = {item["step"]: item["slug"] for item in source_entries}

    for md_path in load_articles():
        raw = md_path.read_text(encoding="utf-8-sig")
        title, body = strip_meta(raw)
        if not title:
            title = md_path.stem
        if title in existing:
            skipped.append(title)
            continue
        step_number = None
        if title.lower().startswith("step "):
            try:
                step_number = int(title.split(".", 1)[0].split()[1])
            except (IndexError, ValueError):
                step_number = None
        source_slug = source_map.get(step_number, "")
        source_url = f"https://sheryloe.github.io/AutoTrading_ing....-/series/{source_slug}" if source_slug else "https://sheryloe.github.io/AutoTrading_ing....-/series/index.html"
        blocks = markdown_to_blocks(body, source_url)
        create_page(parent_page_id, token, title, blocks)
        created.append(title)

    print(json.dumps({"created": created, "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
