from __future__ import annotations

import argparse
import codecs
import json
import sys
from pathlib import Path


DEFAULT_PATHS = [
    "docs/daily-pnl.html",
    "templates/index.html",
    "static/app.js",
    "web_app.py",
    "frontend/app/page.js",
    "frontend/app/models/page.js",
    "frontend/lib/model-meta.js",
    "frontend/app/components/app-shell.js",
]


def _check_file(path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "ok": False,
        "bom": False,
        "size": 0,
    }
    if not path.exists():
        return result

    raw = path.read_bytes()
    result["size"] = len(raw)
    result["bom"] = raw.startswith(codecs.BOM_UTF8)
    try:
        raw.decode("utf-8")
        result["ok"] = True
    except UnicodeDecodeError as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def _check_utf8_header(path: Path) -> dict[str, object]:
    result = {"path": str(path), "charset_hint": False}
    if not path.exists():
        result["error"] = "missing"
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    result["charset_hint"] = "charset=utf-8" in text.lower()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify UTF-8 decoding and charset hints.")
    parser.add_argument("paths", nargs="*", help="Optional file paths to check")
    args = parser.parse_args()

    targets = [Path(p) for p in (args.paths or DEFAULT_PATHS)]
    file_results = [_check_file(p) for p in targets]
    charset_result = _check_utf8_header(Path("web_app.py"))

    ok = all(item.get("ok") for item in file_results if item.get("exists"))
    summary = {
        "ok": ok,
        "files": file_results,
        "web_app_charset": charset_result,
        "windows_console_hint": "chcp 65001; $OutputEncoding = [console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
        "wsl_hint": "export PYTHONIOENCODING=utf-8",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
