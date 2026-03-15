from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://sheryloe.github.io/AutoTrading_ing....-/"
PUBLISHED = "2026-03-15"
UPDATED = "2026-03-15"
SERIES_TITLE = "AI_Auto 자동매매 리빌드 10단계"
REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
SERIES_SRC_DIR = DOCS_DIR / "series-src"
SERIES_DIR = DOCS_DIR / "series"
WIKI_SRC_DIR = DOCS_DIR / "wiki-src"
NOTION_SRC_DIR = DOCS_DIR / "notion" / "auto_trading_10step"


@dataclass
class Article:
    step: int
    title: str
    description: str
    keywords: list[str]
    slug: str
    markdown_body: str
    body_html: str

    @property
    def page_title(self) -> str:
        return f"Step {self.step}. {self.title} | {SERIES_TITLE}"


def parse_source(path: Path) -> Article:
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    meta: dict[str, str] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        if not line:
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    body = "\n".join(lines[idx:]).strip()

    html_parts: list[str] = []
    current_paragraphs: list[str] = []

    def flush_paragraphs() -> None:
        nonlocal current_paragraphs
        for paragraph in current_paragraphs:
            html_parts.append(f"<p>{html.escape(paragraph)}</p>")
        current_paragraphs = []

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            flush_paragraphs()
            html_parts.append(f"<section class=\"article-section\"><h2>{html.escape(stripped[3:])}</h2>")
            continue
        current_paragraphs.append(stripped)
        if html_parts and html_parts[-1].startswith("<section") and len(current_paragraphs) == 2:
            flush_paragraphs()
            html_parts.append("</section>")

    flush_paragraphs()
    fixed: list[str] = []
    open_section = False
    for item in html_parts:
        if item.startswith("<section"):
            if open_section:
                fixed.append("</section>")
            open_section = True
        fixed.append(item)
    if open_section and fixed[-1] != "</section>":
        fixed.append("</section>")

    return Article(
        step=int(path.stem.split("-")[1]),
        title=meta["Title"],
        description=meta["Description"],
        keywords=[item.strip() for item in meta["Keywords"].split(",") if item.strip()],
        slug=f"{meta['Slug']}.html",
        markdown_body=body + "\n",
        body_html="".join(fixed),
    )


def render_series_css() -> str:
    return """
.article-topbar { margin-bottom: 24px; }
.series-hero,.article-shell,.series-card { border: 1px solid var(--line); background: linear-gradient(180deg, rgba(10, 19, 30, 0.92) 0%, rgba(5, 11, 18, 0.96) 100%); box-shadow: var(--shadow); border-radius: var(--radius-xl); }
.series-hero { padding: 42px 36px; margin-bottom: 24px; }
.series-hero h1,.article-hero h1,.series-card strong,.article-section h2,.pager-card strong { font-family: "Space Grotesk", sans-serif; }
.series-hero h1,.article-hero h1 { margin: 0; font-size: clamp(2.4rem, 5vw, 4rem); line-height: 0.98; letter-spacing: -0.05em; }
.series-hero p,.article-description,.article-section p,.series-card p,.article-meta-row,.pager-card span { color: var(--muted); line-height: 1.95; }
.series-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.series-card { display: grid; gap: 10px; padding: 24px; }
.series-card span,.article-meta-row span,.pager-card span { font-size: 0.76rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--cyan); }
.series-card strong { font-size: 1.28rem; }
.article-shell { padding: 40px 36px; }
.article-hero { display: grid; gap: 18px; padding-bottom: 28px; border-bottom: 1px solid var(--line); }
.article-meta-row { display: flex; flex-wrap: wrap; gap: 12px; }
.article-body { display: grid; gap: 24px; padding-top: 28px; }
.article-section h2 { margin: 0 0 14px; font-size: 1.45rem; }
.article-section p { margin: 0 0 14px; }
.article-footer { margin-top: 30px; padding-top: 24px; border-top: 1px solid var(--line); }
.pager-row { display: flex; justify-content: space-between; gap: 16px; }
.pager-card { flex: 1; display: grid; gap: 6px; padding: 18px; border: 1px solid var(--line); border-radius: var(--radius-lg); background: rgba(9, 18, 29, 0.86); }
.pager-card.align-right { text-align: right; }
@media (max-width: 900px) { .series-grid { grid-template-columns: 1fr; } .pager-row { flex-direction: column; } }
@media (max-width: 760px) { .series-hero,.article-shell,.series-card { padding: 22px 18px; border-radius: 22px; } }
"""


def render_series_index(articles: list[Article]) -> str:
    cards = "".join(
        f"""
        <a class="series-card" href="./{article.slug}">
          <span>Step {article.step:02d}</span>
          <strong>{html.escape(article.title)}</strong>
          <p>{html.escape(article.description)}</p>
        </a>
        """
        for article in articles
    )
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{SERIES_TITLE} | AI_Auto</title>
    <meta name="description" content="AI_Auto를 서비스형 선물 자동매매 콘솔로 재구성한 과정과 운영 기준을 10단계로 정리한 시리즈입니다.">
    <link rel="canonical" href="{BASE_URL}series/index.html">
    <meta property="og:type" content="website">
    <meta property="og:title" content="{SERIES_TITLE} | AI_Auto">
    <meta property="og:description" content="서비스형 선물 자동매매 콘솔로 재구성한 과정과 운영 기준을 10단계로 정리한 시리즈">
    <meta property="og:url" content="{BASE_URL}series/index.html">
    <meta property="og:image" content="{BASE_URL}assets/screenshots/auto-trading-cover.png">
    <link rel="stylesheet" href="../styles.css">
    <link rel="stylesheet" href="./series.css">
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
  </head>
  <body>
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="page-shell">
      <header class="topbar article-topbar">
        <a class="brand" href="../index.html">
          <span class="brand-mark"></span>
          <span class="brand-copy">
            <strong>AI_Auto</strong>
            <span>{SERIES_TITLE}</span>
          </span>
        </a>
        <nav class="topnav">
          <a href="../index.html">랜딩</a>
          <a href="https://github.com/sheryloe/AutoTrading_ing....-/wiki">GitHub Wiki</a>
          <a href="https://github.com/sheryloe/AutoTrading_ing....-/blob/main/README.md">README</a>
        </nav>
      </header>
      <main>
        <section class="series-hero">
          <p class="eyebrow">SEO Blog Series</p>
          <h1>{SERIES_TITLE}</h1>
          <p>AI_Auto를 단순한 자동매매 스크립트에서 서비스형 선물 데모 콘솔로 재구성한 과정을 10단계로 나눠 정리했습니다. 운영 리스크, provider vault, 8분 배치, intrabar 체결, 하드 리셋, autotune, 라이브 전환 체크리스트까지 한 흐름으로 이어집니다.</p>
        </section>
        <section class="series-grid">{cards}</section>
      </main>
    </div>
  </body>
</html>
"""


def render_article_html(article: Article, previous_article: Article | None, next_article: Article | None) -> str:
    article_url = BASE_URL + f"series/{article.slug}"
    keywords = ", ".join(article.keywords)
    schema = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": article.page_title,
        "description": article.description,
        "datePublished": PUBLISHED,
        "dateModified": UPDATED,
        "inLanguage": "ko-KR",
        "keywords": article.keywords,
        "url": article_url,
        "mainEntityOfPage": article_url,
        "author": {"@type": "Organization", "name": "AI_Auto"},
        "publisher": {"@type": "Organization", "name": "AI_Auto"},
    }
    previous_html = (
        f"<a class=\"pager-card\" href=\"./{previous_article.slug}\"><span>이전 글</span><strong>Step {previous_article.step}. {html.escape(previous_article.title)}</strong></a>"
        if previous_article
        else "<span></span>"
    )
    next_html = (
        f"<a class=\"pager-card align-right\" href=\"./{next_article.slug}\"><span>다음 글</span><strong>Step {next_article.step}. {html.escape(next_article.title)}</strong></a>"
        if next_article
        else "<span></span>"
    )
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(article.page_title)}</title>
    <meta name="description" content="{html.escape(article.description)}">
    <meta name="keywords" content="{html.escape(keywords)}">
    <link rel="canonical" href="{article_url}">
    <meta property="og:type" content="article">
    <meta property="og:title" content="{html.escape(article.page_title)}">
    <meta property="og:description" content="{html.escape(article.description)}">
    <meta property="og:url" content="{article_url}">
    <meta property="og:image" content="{BASE_URL}assets/screenshots/auto-trading-cover.png">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{html.escape(article.page_title)}">
    <meta name="twitter:description" content="{html.escape(article.description)}">
    <link rel="stylesheet" href="../styles.css">
    <link rel="stylesheet" href="./series.css">
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
    <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
  </head>
  <body>
    <div class="ambient ambient-a"></div>
    <div class="ambient ambient-b"></div>
    <div class="page-shell">
      <header class="topbar article-topbar">
        <a class="brand" href="../index.html">
          <span class="brand-mark"></span>
          <span class="brand-copy">
            <strong>AI_Auto</strong>
            <span>{SERIES_TITLE}</span>
          </span>
        </a>
        <nav class="topnav">
          <a href="./index.html">시리즈 홈</a>
          <a href="../index.html">랜딩</a>
          <a href="https://github.com/sheryloe/AutoTrading_ing....-/wiki">GitHub Wiki</a>
        </nav>
      </header>
      <main>
        <article class="article-shell">
          <header class="article-hero">
            <p class="eyebrow">Step {article.step:02d}</p>
            <h1>{html.escape(article.title)}</h1>
            <p class="article-description">{html.escape(article.description)}</p>
            <div class="article-meta-row">
              <span>게시일 {PUBLISHED}</span>
              <span>수정일 {UPDATED}</span>
              <span>키워드 {html.escape(keywords)}</span>
            </div>
          </header>
          <div class="article-body">{article.body_html}</div>
          <footer class="article-footer">
            <div class="pager-row">{previous_html}{next_html}</div>
          </footer>
        </article>
      </main>
    </div>
  </body>
</html>
"""


def render_wiki_home(articles: list[Article]) -> str:
    lines = [
        "# AI_Auto Wiki",
        "",
        "AI_Auto는 Top 5 메이저 코인을 대상으로 8분 배치 futures demo를 운영하는 서비스형 자동매매 콘솔입니다.",
        "",
        "## 현재 핵심 기능",
        "",
        "- Vercel 운영 콘솔: 개요 / 모델 성과 / 포지션 / 설정",
        "- Supabase 상태 원장: heartbeat, setup, 포지션, 일별 PnL, 튜닝 상태, provider vault",
        "- GitHub Actions 8분 배치",
        "- 4개 planner 모델의 entry / TP / SL 제안",
        "- 1분봉 intrabar 체결 시뮬레이션",
        "- 선물 데모 시드 10000 USDT 하드 리셋",
        "",
        "## 10단계 블로그 시리즈",
        "",
    ]
    for article in articles:
        page_name = f"Step-{article.step:02d}-{article.title}".replace("/", "-")
        lines.append(f"- [[{page_name}]]")
    lines.extend(
        [
            "",
            "## 외부 링크",
            "",
            "- [GitHub Pages 랜딩](https://sheryloe.github.io/AutoTrading_ing....-/)",
            "- [10단계 시리즈](https://sheryloe.github.io/AutoTrading_ing....-/series/index.html)",
            "- [저장소 README](https://github.com/sheryloe/AutoTrading_ing....-/blob/main/README.md)",
        ]
    )
    return "\n".join(lines) + "\n"


def render_sidebar(articles: list[Article]) -> str:
    lines = ["## AI_Auto Wiki", "", "- [[Home]]", ""]
    for article in articles:
        page_name = f"Step-{article.step:02d}-{article.title}".replace("/", "-")
        lines.append(f"- [[{page_name}|Step {article.step:02d}. {article.title}]]")
    return "\n".join(lines) + "\n"


def main() -> None:
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_SRC_DIR.mkdir(parents=True, exist_ok=True)
    NOTION_SRC_DIR.mkdir(parents=True, exist_ok=True)

    articles = [parse_source(path) for path in sorted(SERIES_SRC_DIR.glob("step-*.md"))]

    (SERIES_DIR / "series.css").write_text(render_series_css(), encoding="utf-8")
    (SERIES_DIR / "index.html").write_text(render_series_index(articles), encoding="utf-8")

    sitemap_urls = [BASE_URL, BASE_URL + "series/index.html"]
    notion_index = []
    for idx, article in enumerate(articles):
        previous_article = articles[idx - 1] if idx > 0 else None
        next_article = articles[idx + 1] if idx < len(articles) - 1 else None
        (SERIES_DIR / article.slug).write_text(
            render_article_html(article, previous_article, next_article),
            encoding="utf-8",
        )
        page_name = f"Step-{article.step:02d}-{article.title}".replace("/", "-")
        (WIKI_SRC_DIR / f"{page_name}.md").write_text(
            f"# Step {article.step}. {article.title}\n\n{article.markdown_body}",
            encoding="utf-8",
        )
        (NOTION_SRC_DIR / f"Step {article.step}. {article.title}.md").write_text(
            f"# Step {article.step}. {article.title}\n\n{article.markdown_body}",
            encoding="utf-8",
        )
        notion_index.append(
            {
                "step": article.step,
                "title": article.title,
                "slug": article.slug,
                "description": article.description,
                "page_name": page_name,
                "length": len(article.markdown_body),
            }
        )
        sitemap_urls.append(BASE_URL + f"series/{article.slug}")

    (WIKI_SRC_DIR / "Home.md").write_text(render_wiki_home(articles), encoding="utf-8")
    (WIKI_SRC_DIR / "_Sidebar.md").write_text(render_sidebar(articles), encoding="utf-8")
    (NOTION_SRC_DIR / "index.json").write_text(
        json.dumps(notion_index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in sitemap_urls:
        priority = "0.9" if url == BASE_URL else ("0.85" if url.endswith("series/index.html") else "0.75")
        sitemap.extend(
            [
                "  <url>",
                f"    <loc>{html.escape(url)}</loc>",
                f"    <lastmod>{UPDATED}</lastmod>",
                "    <changefreq>weekly</changefreq>",
                f"    <priority>{priority}</priority>",
                "  </url>",
            ]
        )
    sitemap.append("</urlset>")
    (DOCS_DIR / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")

    manifest = {
        "name": "AI_Auto",
        "short_name": "AI_Auto",
        "description": "서비스형 futures demo 자동매매 콘솔과 운영 블로그 시리즈.",
        "lang": "ko-KR",
        "start_url": BASE_URL,
        "scope": BASE_URL,
        "display": "standalone",
        "background_color": "#081019",
        "theme_color": "#081019",
    }
    (DOCS_DIR / "site.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "series_pages": len(articles),
                "wiki_pages": len(articles) + 2,
                "notion_pages": len(articles),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
