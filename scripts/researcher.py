#!/usr/bin/env python3
"""
pulse360 AI Researcher Agent
=============================
Discovers news from configurable sources (scripts/sources.md),
synthesizes articles via OpenAI GPT-4o-mini, and writes Markdown files
to src/content/news/{category}/{YYYY-MM-DD-slug}.md.

Run:
    python scripts/researcher.py

Environment variables required:
    OPENAI_API_KEY   — OpenAI API key
    NEWSAPI_KEY      — (optional) NewsAPI.org key
    GNEWS_KEY        — (optional) GNews.io key
"""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Load .env file automatically when running locally
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import feedparser
import frontmatter
import httpx
from openai import OpenAI
from slugify import slugify
from tenacity import retry, stop_after_attempt, wait_exponential

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
CONTENT_DIR = ROOT / "src" / "content" / "news"
SOURCES_FILE = Path(__file__).parent / "sources.md"
SITEMAP_DIR = ROOT / "public" / "sitemaps"
SITEMAP_INDEX = ROOT / "public" / "sitemap.xml"
SITE_URL = "https://pulse360.news"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
GNEWS_KEY = os.environ.get("GNEWS_KEY", "")

MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES_PER_RUN", "25"))
MAX_PER_SOURCE = int(os.environ.get("MAX_PER_SOURCE", "8"))
LLM_MODEL = "gpt-4o-mini"
LLM_WORD_TARGET = "400-600"

NEWSAPI_CATEGORIES = ["general", "business", "sports", "entertainment", "technology", "science"]
GNEWS_CATEGORIES = ["general", "business", "sports", "entertainment", "technology", "science"]

CATEGORY_MAP: dict[str, str] = {
    "general": "Politics",
    "politics": "Politics",
    "business": "Economy",
    "economy": "Economy",
    "sports": "Sports",
    "sport": "Sports",
    "entertainment": "Showbiz",
    "showbiz": "Showbiz",
    "technology": "Tech",
    "tech": "Tech",
    "science": "Tech",
}

# Category distribution quotas (must sum to 1.0)
CATEGORY_QUOTAS: dict[str, float] = {
    "Politics": 0.35,
    "Economy": 0.35,
    "Tech": 0.10,
    "Sports": 0.10,
    "Showbiz": 0.10,
}

# Hours after which an article's importance decays to ~10% of its original value.
# Articles older than this are very unlikely to appear on the homepage.
HOMEPAGE_DECAY_HOURS = float(os.environ.get("HOMEPAGE_DECAY_HOURS", "24"))


def decayed_importance(article: "RawArticle", now: datetime | None = None) -> float:
    """Apply time-based decay to an article's importance score.

    Uses linear decay over HOMEPAGE_DECAY_HOURS so that newer articles
    naturally displace older ones on the homepage, even if the older
    article had a higher raw importance score.
    """
    if now is None:
        now = datetime.now(UTC)
    age_hours = max(0, (now - article.published_at).total_seconds() / 3600)
    # Linear decay: 100% at 0h → 10% at HOMEPAGE_DECAY_HOURS, clamp at 10%
    decay_factor = max(0.10, 1.0 - 0.9 * (age_hours / HOMEPAGE_DECAY_HOURS))
    return article.importance * decay_factor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("researcher")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RawArticle:
    title: str
    url: str
    summary: str
    source_name: str
    category: str  # normalised to Politics/Economy/Sports/Showbiz
    published_at: datetime
    importance: float = 0.0  # 0-100 composite score set by score_article()


@dataclass
class SourceConfig:
    name: str
    source_type: str  # rss | newsapi | gnews
    url: str
    categories: list[str]
    active: bool


# ---------------------------------------------------------------------------
# Source credibility tiers (higher = more trusted / globally significant)
# ---------------------------------------------------------------------------

SOURCE_TIER: dict[str, int] = {
    # Tier 1 — Major global wire services & broadsheets
    "Reuters":          100,
    "Associated Press": 100,
    "AP News":          100,
    "BBC News":          95,
    "BBC World":         95,
    "Al Jazeera":        90,
    # Tier 2 — Strong national / international outlets
    "Sky News":          80,
    "BBC Sport":         75,
    "ESPN":              70,
    # Tier 3 — Tech press
    "TechCrunch":        80,
    "Ars Technica":      80,
    "The Verge":         75,
    "Wired":             75,
    # Tier 4 — Specialist / entertainment press
    "Variety":           65,
    "Hollywood Reporter":65,
    "Deadline":          60,
    # Aggregator APIs get a moderate baseline
    "NewsAPI":           55,
    "GNews":             55,
}

DEFAULT_SOURCE_TIER = 50  # unknown sources

# Keywords that signal high global importance
IMPORTANCE_KEYWORDS_HIGH: set[str] = {
    "war", "ceasefire", "peace", "invasion", "nuclear", "sanctions",
    "election", "president", "prime minister", "summit", "treaty",
    "pandemic", "outbreak", "vaccine", "earthquake", "hurricane",
    "tsunami", "terrorism", "assassination", "coup", "refugee",
    "climate", "emissions", "UN", "NATO", "WHO", "G7", "G20",
    "recession", "inflation", "crash", "default", "debt crisis",
    "breakthrough", "historic", "unprecedented",
}

# Keywords that signal moderate importance
IMPORTANCE_KEYWORDS_MED: set[str] = {
    "trade", "tariff", "GDP", "policy", "reform", "protest",
    "strike", "scandal", "investigation", "verdict", "ruling",
    "champion", "world cup", "olympics", "final", "record",
    "merger", "acquisition", "IPO", "layoffs", "AI", "tech",
}


def source_credibility(source_name: str) -> int:
    """Return the credibility score (0-100) for a source name."""
    # Try exact match first, then partial match
    if source_name in SOURCE_TIER:
        return SOURCE_TIER[source_name]
    name_lower = source_name.lower()
    for key, score in SOURCE_TIER.items():
        if key.lower() in name_lower or name_lower in key.lower():
            return score
    return DEFAULT_SOURCE_TIER


def score_article(article: RawArticle) -> float:
    """Compute a 0-100 importance score from content signals + source credibility."""
    text = f"{article.title} {article.summary}".lower()

    # --- Content importance (0-50) ---
    high_hits = sum(1 for kw in IMPORTANCE_KEYWORDS_HIGH if kw.lower() in text)
    med_hits = sum(1 for kw in IMPORTANCE_KEYWORDS_MED if kw.lower() in text)
    content_score = min(50.0, high_hits * 10 + med_hits * 4)

    # --- Source credibility (0-35) ---
    cred = source_credibility(article.source_name)
    source_score = cred * 0.35  # max 35

    # --- Recency bonus (0-15) ---
    age_hours = (datetime.now(UTC) - article.published_at).total_seconds() / 3600
    if age_hours < 1:
        recency = 15.0
    elif age_hours < 4:
        recency = 12.0
    elif age_hours < 12:
        recency = 8.0
    elif age_hours < 24:
        recency = 4.0
    else:
        recency = 0.0

    return round(min(100.0, content_score + source_score + recency), 1)


# ---------------------------------------------------------------------------
# Source loader
# ---------------------------------------------------------------------------

def load_sources() -> list[SourceConfig]:
    """Parse scripts/sources.md table and return active sources."""
    text = SOURCES_FILE.read_text(encoding="utf-8")
    sources: list[SourceConfig] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| Name") or set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5:
            continue
        name, source_type, url = parts[0], parts[1], parts[2]
        # Categories can be in column 3 or 4 depending on table format
        # Try to find the categories and active columns
        categories_raw = ""
        active_raw = "no"
        if len(parts) >= 6:
            # Columns: Name(0) | Type(1) | URL(2) | Countries(3) | Categories(4) | Active(5)
            categories_raw = parts[4]
            active_raw = parts[5]
        elif len(parts) >= 5:
            categories_raw = parts[3]
            active_raw = parts[4]

        if active_raw.lower() != "yes":
            continue
        sources.append(SourceConfig(
            name=name,
            source_type=source_type.lower(),
            url=url,
            categories=[c.strip() for c in categories_raw.split(",")],
            active=True,
        ))
    log.info("Loaded %d active sources from sources.md", len(sources))
    return sources


# ---------------------------------------------------------------------------
# Source domain extractor
# ---------------------------------------------------------------------------

def extract_domain(url: str) -> str:
    """Extract a clean domain name from a URL."""
    try:
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        return hostname.replace("www.", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Sanitisation — defence against injected HTML / JS from feeds
# ---------------------------------------------------------------------------

_RE_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_RE_EVENT_ATTR = re.compile(r"\s+on\w+\s*=\s*[\"'][^\"']*[\"']", re.IGNORECASE)
_RE_JS_PROTOCOL = re.compile(r"javascript\s*:", re.IGNORECASE)
_RE_ALL_TAGS = re.compile(r"<[^>]+>")
_RE_ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)


def strip_dangerous_html(text: str) -> str:
    """Remove <script> blocks, event-handler attributes, and javascript: URIs."""
    text = _RE_SCRIPT.sub("", text)
    text = _RE_EVENT_ATTR.sub("", text)
    text = _RE_JS_PROTOCOL.sub("", text)
    return text


def strip_all_html(text: str) -> str:
    """Remove every HTML tag from text (leaves inner text)."""
    text = strip_dangerous_html(text)
    return _RE_ALL_TAGS.sub("", text).strip()


def sanitize_plain_text(text: str) -> str:
    """For titles / descriptions — strip all HTML (including anchors) and collapse whitespace."""
    text = strip_dangerous_html(text)
    # Unwrap anchors: keep link text, drop the tag
    text = _RE_ANCHOR.sub(r"\1", text)
    # Remove any remaining tags
    text = _RE_ALL_TAGS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def sanitize_body(text: str) -> str:
    """For article body Markdown — remove script/JS but keep safe Markdown-HTML."""
    text = strip_dangerous_html(text)
    # Unwrap anchors that may have crept in
    text = _RE_ANCHOR.sub(r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def fetch_rss(source: SourceConfig) -> list[RawArticle]:
    """Fetch and parse an RSS feed. Shuffle and cap to MAX_PER_SOURCE."""
    articles: list[RawArticle] = []
    try:
        feed = feedparser.parse(source.url)
    except Exception as exc:
        log.warning("RSS fetch failed for %s: %s", source.name, exc)
        return articles

    entries = list(feed.entries)
    # Shuffle to avoid always getting the same top items
    random.shuffle(entries)

    for entry in entries[:MAX_PER_SOURCE]:
        title = sanitize_plain_text(entry.get("title") or "")
        url = (entry.get("link") or "").strip()
        summary = sanitize_plain_text(entry.get("summary") or entry.get("description") or "")

        if not title or not url:
            continue

        # Attempt to parse published date
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub:
            from time import mktime
            published_at = datetime.fromtimestamp(mktime(pub), tz=UTC)
        else:
            published_at = datetime.now(UTC)

        # Derive category from source config
        raw_cat = source.categories[0] if source.categories else "general"
        category = CATEGORY_MAP.get(raw_cat.lower(), "Politics")

        articles.append(RawArticle(
            title=title,
            url=url,
            summary=summary[:800],
            source_name=source.name,
            category=category,
            published_at=published_at,
        ))

    log.info("RSS %s → %d articles", source.name, len(articles))
    return articles


def fetch_newsapi(source: SourceConfig) -> list[RawArticle]:
    """Fetch headlines from NewsAPI.org across categories."""
    if not NEWSAPI_KEY:
        log.warning("NEWSAPI_KEY not set, skipping NewsAPI source")
        return []

    articles: list[RawArticle] = []
    with httpx.Client(timeout=15) as client:
        for cat in NEWSAPI_CATEGORIES:
            try:
                resp = client.get(
                    source.url,
                    params={"category": cat, "language": "en", "apiKey": NEWSAPI_KEY, "pageSize": MAX_PER_SOURCE},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("NewsAPI error (%s): %s", cat, exc)
                continue

            items = data.get("articles", [])
            random.shuffle(items)

            for item in items[:MAX_PER_SOURCE]:
                title = sanitize_plain_text(item.get("title") or "")
                url = (item.get("url") or "").strip()
                summary = sanitize_plain_text(item.get("description") or item.get("content") or "")[:800]
                if not title or not url or url == "https://removed.com":
                    continue

                pub_str = item.get("publishedAt", "")
                try:
                    published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except ValueError:
                    published_at = datetime.now(UTC)

                category = CATEGORY_MAP.get(cat, "Politics")
                src_name = sanitize_plain_text(item.get("source", {}).get("name") or source.name)

                articles.append(RawArticle(
                    title=title,
                    url=url,
                    summary=summary,
                    source_name=src_name,
                    category=category,
                    published_at=published_at,
                ))

    log.info("NewsAPI → %d articles", len(articles))
    return articles


def fetch_gnews(source: SourceConfig) -> list[RawArticle]:
    """Fetch headlines from GNews.io across categories."""
    if not GNEWS_KEY:
        log.warning("GNEWS_KEY not set, skipping GNews source")
        return []

    articles: list[RawArticle] = []
    with httpx.Client(timeout=15) as client:
        for cat in GNEWS_CATEGORIES:
            try:
                resp = client.get(
                    source.url,
                    params={"topic": cat, "token": GNEWS_KEY, "max": MAX_PER_SOURCE, "lang": "en"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("GNews error (%s): %s", cat, exc)
                continue

            items = data.get("articles", [])
            random.shuffle(items)

            for item in items[:MAX_PER_SOURCE]:
                title = sanitize_plain_text(item.get("title") or "")
                url = (item.get("url") or "").strip()
                summary = sanitize_plain_text(item.get("description") or "")[:800]
                if not title or not url:
                    continue

                pub_str = item.get("publishedAt", "")
                try:
                    published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except ValueError:
                    published_at = datetime.now(UTC)

                category = CATEGORY_MAP.get(cat, "Politics")
                src_name = sanitize_plain_text(item.get("source", {}).get("name") or source.name)

                articles.append(RawArticle(
                    title=title,
                    url=url,
                    summary=summary,
                    source_name=src_name,
                    category=category,
                    published_at=published_at,
                ))

    log.info("GNews → %d articles", len(articles))
    return articles


def discover(sources: list[SourceConfig]) -> list[RawArticle]:
    """Run all active sources, deduplicate, score, and return sorted by importance."""
    all_articles: list[RawArticle] = []

    for source in sources:
        if source.source_type == "rss":
            all_articles.extend(fetch_rss(source))
        elif source.source_type == "newsapi":
            all_articles.extend(fetch_newsapi(source))
        elif source.source_type == "gnews":
            all_articles.extend(fetch_gnews(source))
        else:
            log.warning("Unknown source type '%s' for %s", source.source_type, source.name)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[RawArticle] = []
    for a in all_articles:
        if a.url not in seen_urls:
            seen_urls.add(a.url)
            unique.append(a)

    # Score every article
    for a in unique:
        a.importance = score_article(a)

    # Sort by importance (highest first), then diversify so adjacent
    # articles aren't all from the same source
    unique.sort(key=lambda a: a.importance, reverse=True)
    unique = diversify_sorted(unique)

    if unique:
        log.info("Top scored: [%.1f] %s (%s)", unique[0].importance, unique[0].title[:60], unique[0].source_name)

    log.info("Discovery complete: %d unique articles across all sources", len(unique))
    return unique


def interleave_sources(articles: list[RawArticle]) -> list[RawArticle]:
    """Interleave articles from different sources so no single source clusters."""
    from collections import defaultdict
    by_source: dict[str, list[RawArticle]] = defaultdict(list)
    for a in articles:
        by_source[a.source_name].append(a)

    # Sort each source's articles by date (newest first)
    for src in by_source:
        by_source[src].sort(key=lambda a: a.published_at, reverse=True)

    # Round-robin interleave
    result: list[RawArticle] = []
    source_names = list(by_source.keys())
    random.shuffle(source_names)  # randomize starting source
    idx = {s: 0 for s in source_names}
    total = sum(len(v) for v in by_source.values())

    while len(result) < total:
        added_this_round = False
        for s in source_names:
            if idx[s] < len(by_source[s]):
                result.append(by_source[s][idx[s]])
                idx[s] += 1
                added_this_round = True
        if not added_this_round:
            break

    return result


def diversify_sorted(articles: list[RawArticle], max_consecutive: int = 2) -> list[RawArticle]:
    """Re-order an importance-sorted list so no source appears more than
    `max_consecutive` times in a row, while keeping high-importance items
    near the top."""
    if len(articles) <= 2:
        return articles

    result: list[RawArticle] = []
    remaining = list(articles)  # work on a copy

    while remaining:
        placed = False
        for i, article in enumerate(remaining):
            # Check how many consecutive articles from this source are at the tail
            tail_count = 0
            for r in reversed(result):
                if r.source_name == article.source_name:
                    tail_count += 1
                else:
                    break
            if tail_count < max_consecutive:
                result.append(remaining.pop(i))
                placed = True
                break
        if not placed:
            # All remaining are from the same source — just append
            result.extend(remaining)
            break

    return result


# ---------------------------------------------------------------------------
# Deduplication (file-system)
# ---------------------------------------------------------------------------

def build_existing_slugs() -> set[str]:
    """Scan the content directory and return a set of existing article slugs."""
    existing: set[str] = set()
    if not CONTENT_DIR.exists():
        return existing
    for md_file in CONTENT_DIR.rglob("*.md"):
        existing.add(md_file.stem)
    log.info("Found %d existing articles on disk", len(existing))
    return existing


@dataclass
class ExistingArticle:
    """Represents an article already on disk from a previous run."""
    slug: str
    path: Path
    importance: float
    title: str
    category: str
    displayOrder: int = 999


def load_existing_articles() -> list[ExistingArticle]:
    """Read all existing articles from disk and return them with their importance scores."""
    articles: list[ExistingArticle] = []
    if not CONTENT_DIR.exists():
        return articles
    for md_file in CONTENT_DIR.rglob("*.md"):
        try:
            post = frontmatter.load(str(md_file))
            articles.append(ExistingArticle(
                slug=md_file.stem,
                path=md_file,
                importance=float(post.get("importance", 0)),
                title=str(post.get("title", md_file.stem)),
                category=str(post.get("category", "")),
                displayOrder=int(post.get("displayOrder", 999)),
            ))
        except Exception as exc:
            log.warning("Failed to parse %s: %s", md_file.name, exc)
    log.info("Loaded %d existing articles from disk", len(articles))
    return articles


def make_slug(article: RawArticle) -> str:
    date_str = article.published_at.strftime("%Y-%m-%d")
    title_slug = slugify(article.title, max_length=60)
    return f"{date_str}-{title_slug}"


def is_duplicate(slug: str, existing_slugs: set[str]) -> bool:
    return slug in existing_slugs


# ---------------------------------------------------------------------------
# AI Synthesis
# ---------------------------------------------------------------------------

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SYSTEM_PROMPT = (
    "You are a sophisticated, neutral, and authoritative news writer for pulse360, "
    "a global news platform. Your writing is calm, precise, and insightful — free of "
    "sensationalism, bias, and clickbait. Use Markdown headers (## ) to structure the "
    "article. Write in English regardless of the source language. "
    f"Target length: {LLM_WORD_TARGET} words."
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
def synthesize(article: RawArticle) -> str:
    """Call GPT-4o-mini to synthesize a full Markdown article body."""
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")

    user_content = (
        f"Headline: {article.title}\n\n"
        f"Source snippet:\n{article.summary}\n\n"
        f"Category: {article.category}\n\n"
        "Write a complete, well-structured news article based on the above information."
    )

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.4,
        max_tokens=900,
    )
    return response.choices[0].message.content or ""


def infer_sentiment(text: str) -> str:
    """Simple keyword-based sentiment tagger — avoids an extra LLM call."""
    text_lower = text.lower()
    positive_words = {"growth", "peace", "recovery", "win", "success", "breakthrough", "progress", "celebrate", "record", "rise", "award", "profit", "improve", "cure", "save"}
    negative_words = {"war", "conflict", "crisis", "death", "killed", "attack", "protest", "collapse", "sanction", "flood", "disaster", "recession", "violence", "arrest", "murder"}
    pos = sum(1 for w in positive_words if w in text_lower)
    neg = sum(1 for w in negative_words if w in text_lower)
    if pos > neg:
        return "Positive"
    if neg > pos:
        return "Negative"
    return "Neutral"


def truncate_description(text: str, max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_article(article: RawArticle, body: str, slug: str) -> Path:
    """Persist a synthesized article as a Markdown file with YAML frontmatter."""
    output_dir = CONTENT_DIR / article.category.lower()
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{slug}.md"

    # Final sanitisation pass before writing to disk
    clean_title = sanitize_plain_text(article.title)
    clean_desc = sanitize_plain_text(article.summary or article.title)
    description = truncate_description(clean_desc)
    clean_body = sanitize_body(body)
    sentiment = infer_sentiment(clean_body)
    source_domain = extract_domain(article.url)

    post = frontmatter.Post(
        clean_body,
        title=clean_title,
        pubDate=article.published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        description=description,
        category=article.category,
        sourceUrl=article.url,
        source=source_domain,
        importance=article.importance,
        displayOrder=999,
        heroImage="",
        sentiment=sentiment,
        tags=[article.category],
    )

    output_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    try:
        display = output_path.relative_to(ROOT)
    except ValueError:
        display = output_path
    log.info("Wrote %s", display)
    return output_path


def update_display_order(
    top_items: list[dict],
    fallen_off: list[ExistingArticle],
) -> None:
    """Update displayOrder for the active pool (top 50) and reset
    any previously-active articles that fell out back to 999.
    Only touches ~50 files per run, never the full archive."""
    updated = 0

    # Assign positions 1..N to the active pool
    for position, item in enumerate(top_items, start=1):
        if item["is_new"]:
            article: RawArticle = item["article"]
            slug: str = item["slug"]
            cat_dir = CONTENT_DIR / article.category.lower()
            path = cat_dir / f"{slug}.md"
        else:
            path = item["path"]

        if not path.exists():
            continue
        try:
            post = frontmatter.load(str(path))
            if post.get("displayOrder") != position:
                post["displayOrder"] = position
                path.write_text(frontmatter.dumps(post), encoding="utf-8")
                updated += 1
        except Exception as exc:
            log.warning("Failed to update displayOrder for %s: %s", path.name, exc)

    # Reset fallen-off articles to 999 so they leave the homepage
    for ea in fallen_off:
        if not ea.path.exists():
            continue
        try:
            post = frontmatter.load(str(ea.path))
            if post.get("displayOrder") != 999:
                post["displayOrder"] = 999
                ea.path.write_text(frontmatter.dumps(post), encoding="utf-8")
                updated += 1
        except Exception as exc:
            log.warning("Failed to reset displayOrder for %s: %s", ea.path.name, exc)

    log.info("Updated displayOrder on %d articles", updated)


# ---------------------------------------------------------------------------
# Sitemap generation
# ---------------------------------------------------------------------------

def generate_batch_sitemap(new_files: list[Path]) -> Path | None:
    """Generate a sitemap XML for the current batch of new articles."""
    if not new_files:
        return None

    SITEMAP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    batch_file = SITEMAP_DIR / f"sitemap-{timestamp}.xml"

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    urls: list[str] = []
    for path in new_files:
        try:
            rel = path.relative_to(CONTENT_DIR)
        except ValueError:
            continue
        url_path = str(rel.with_suffix("")).replace("\\", "/")
        urls.append(f"  <url>\n    <loc>{SITE_URL}/news/{url_path}</loc>\n    <lastmod>{today}</lastmod>\n  </url>")

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls) + "\n"
    xml += "</urlset>\n"

    batch_file.write_text(xml, encoding="utf-8")
    log.info("Generated batch sitemap: %s with %d URLs", batch_file.name, len(urls))
    return batch_file


def _ensure_static_sitemap() -> None:
    """Create a sitemap for static pages if it doesn't already exist."""
    static_file = SITEMAP_DIR / "sitemap-static.xml"
    if static_file.exists():
        return
    SITEMAP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += f"  <url>\n    <loc>{SITE_URL}/</loc>\n    <lastmod>{today}</lastmod>\n    <changefreq>always</changefreq>\n    <priority>1.0</priority>\n  </url>\n"
    xml += f"  <url>\n    <loc>{SITE_URL}/archive</loc>\n    <lastmod>{today}</lastmod>\n    <changefreq>always</changefreq>\n    <priority>0.8</priority>\n  </url>\n"
    xml += "</urlset>\n"
    static_file.write_text(xml, encoding="utf-8")
    log.info("Created static sitemap")


def update_sitemap_index() -> None:
    """Rebuild the sitemap index from all batch sitemaps in public/sitemaps/."""
    _ensure_static_sitemap()
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    batch_files = sorted(SITEMAP_DIR.glob("sitemap-*.xml"))
    if not batch_files:
        return

    entries: list[str] = []
    for bf in batch_files:
        entries.append(
            f"  <sitemap>\n    <loc>{SITE_URL}/sitemaps/{bf.name}</loc>\n    <lastmod>{today}</lastmod>\n  </sitemap>"
        )

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(entries) + "\n"
    xml += "</sitemapindex>\n"

    SITEMAP_INDEX.write_text(xml, encoding="utf-8")
    log.info("Updated sitemap index with %d sitemaps", len(batch_files))


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------

def git_commit_all(new_files: list[Path]) -> None:
    """Stage all new files and perform a single batch commit."""
    if not new_files:
        log.info("No new articles — nothing to commit")
        return

    try:
        subprocess.run(["git", "add", str(CONTENT_DIR)], cwd=ROOT, check=True)
        subprocess.run(["git", "add", str(SITEMAP_DIR), str(SITEMAP_INDEX)], cwd=ROOT, check=True)
        # Check if there are staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--exit-code"],
            cwd=ROOT,
            capture_output=True,
        )
        if result.returncode == 0:
            log.info("Git diff shows no changes after add — skipping commit")
            return

        date_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        msg = f"chore(content): add {len(new_files)} article(s) [{date_str}]"
        subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        log.info("Committed %d new articles", len(new_files))
    except subprocess.CalledProcessError as exc:
        log.error("Git commit failed: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Category-quota selection
# ---------------------------------------------------------------------------

def select_with_quotas(
    candidates: list[tuple[RawArticle, str]],
    total: int,
    use_decay: bool = False,
) -> list[tuple[RawArticle, str]]:
    """Select *total* articles respecting CATEGORY_QUOTAS distribution.
    Candidates must already be sorted by importance (highest first).
    Unfilled quota slots are redistributed to other categories.

    When *use_decay* is True, sort by time-decayed importance so that
    fresher articles are preferred over stale high-importance ones.
    """
    import math
    from collections import defaultdict

    now = datetime.now(UTC)

    def _sort_key(item: tuple[RawArticle, str]) -> float:
        if use_decay:
            return decayed_importance(item[0], now)
        return item[0].importance

    # Re-sort candidates by the chosen key (decayed or raw)
    candidates = sorted(candidates, key=_sort_key, reverse=True)

    # Bucket candidates by category (preserving sort order)
    by_cat: dict[str, list[tuple[RawArticle, str]]] = defaultdict(list)
    for item in candidates:
        by_cat[item[0].category].append(item)

    # Compute target slots per category
    slots: dict[str, int] = {}
    for cat, pct in CATEGORY_QUOTAS.items():
        slots[cat] = max(1, math.floor(total * pct))

    # Fill each category up to its quota
    selected: list[tuple[RawArticle, str]] = []
    used: set[str] = set()
    remaining_candidates: list[tuple[RawArticle, str]] = []

    for cat, quota in slots.items():
        taken = 0
        for item in by_cat.get(cat, []):
            if taken >= quota:
                remaining_candidates.append(item)
                continue
            selected.append(item)
            used.add(item[1])  # slug
            taken += 1
        # Leftover candidates from this category
        if taken < quota:
            pass  # not enough candidates; remaining slots filled below

    # Add unused candidates from uncategorised buckets
    for cat, items in by_cat.items():
        if cat not in slots:
            for item in items:
                if item[1] not in used:
                    remaining_candidates.append(item)

    # Fill remaining slots from overflow
    remaining_candidates.sort(key=_sort_key, reverse=True)
    for item in remaining_candidates:
        if len(selected) >= total:
            break
        if item[1] not in used:
            selected.append(item)
            used.add(item[1])

    # Final sort by chosen key
    selected.sort(key=_sort_key, reverse=True)

    cat_counts = defaultdict(int)
    for a, _ in selected:
        cat_counts[a.category] += 1
    log.info("Category distribution: %s", dict(cat_counts))

    return selected[:total]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY environment variable is not set — aborting")
        sys.exit(1)

    log.info("=== pulse360 researcher starting ===")
    homepage_size = MAX_ARTICLES_PER_RUN * 2  # 2 iterations → 50

    # Phase 1: Discover & score new articles (no AI tokens spent)
    sources = load_sources()
    raw_articles = discover(sources)

    # Phase 2: Load existing articles from disk
    existing_articles = load_existing_articles()
    existing_slugs = {a.slug for a in existing_articles}

    # Phase 3: Pick top N *new* candidates by importance → synthesize
    new_candidates: list[tuple[RawArticle, str]] = []
    for article in raw_articles:
        slug = make_slug(article)
        if slug not in existing_slugs:
            new_candidates.append((article, slug))

    new_candidates.sort(key=lambda x: x[0].importance, reverse=True)
    to_synthesize = select_with_quotas(new_candidates, MAX_ARTICLES_PER_RUN)

    log.info(
        "Discovered %d new candidates → synthesizing top %d",
        len(new_candidates), len(to_synthesize),
    )

    new_files: list[Path] = []
    for article, slug in to_synthesize:
        log.info("Synthesizing [%.1f]: %s", article.importance, article.title[:80])
        try:
            body = synthesize(article)
        except Exception as exc:
            log.warning("Synthesis failed for '%s': %s — skipping", article.title[:60], exc)
            continue
        path = write_article(article, body, slug)
        new_files.append(path)


    # Phase 4: Build the homepage pool using quota logic for all articles (new + existing)
    # Gather all articles (newly written and existing)
    all_candidates: list[tuple[RawArticle, str, Path, bool]] = []  # (article, slug, path, is_new)
    # Add new articles
    for article, slug in to_synthesize:
        cat_dir = CONTENT_DIR / article.category.lower()
        path = cat_dir / f"{slug}.md"
        if path.exists():
            all_candidates.append((article, slug, path, True))
    # Add all existing articles
    for ea in existing_articles:
        # Try to reconstruct a RawArticle for quota logic
        try:
            post = frontmatter.load(str(ea.path))
            ra = RawArticle(
                title=post.get("title", ea.title),
                url=post.get("sourceUrl", ""),
                summary=post.get("description", ""),
                source_name=post.get("source", ""),
                category=post.get("category", ea.category),
                published_at=datetime.strptime(post.get("pubDate", "1970-01-01T00:00:00Z"), "%Y-%m-%dT%H:%M:%SZ"),
                importance=float(post.get("importance", ea.importance)),
            )
        except Exception:
            # Fallback to minimal
            ra = RawArticle(
                title=ea.title,
                url="",
                summary="",
                source_name="",
                category=ea.category,
                published_at=datetime.now(UTC),
                importance=ea.importance,
            )
        all_candidates.append((ra, ea.slug, ea.path, False))

    # Sort all by time-decayed importance so fresher articles are preferred
    now = datetime.now(UTC)
    all_candidates.sort(key=lambda x: decayed_importance(x[0], now), reverse=True)
    # Use select_with_quotas to pick homepage pool (top 50 by quota + recency)
    homepage_pool = select_with_quotas([(a, s) for a, s, _, _ in all_candidates], homepage_size, use_decay=True)
    homepage_slugs = {s for _, s in homepage_pool}

    # Build top_pool for update_display_order
    top_pool = []
    for a, s in homepage_pool:
        for ra, slug, path, is_new in all_candidates:
            if slug == s:
                entry = {"slug": slug, "importance": a.importance, "is_new": is_new, "article": a, "path": path}
                if not is_new:
                    entry["title"] = a.title
                top_pool.append(entry)
                break

    # Any article not in homepage_pool should be reset (not just previously active)
    all_fallen_off = [ea for ea in existing_articles if ea.slug not in homepage_slugs]

    log.info(
        "Homepage pool: %d articles (cap %d), %d reset",
        len(top_pool), homepage_size, len(all_fallen_off),
    )

    # Phase 5: Update displayOrder on the active pool + reset all others
    update_display_order(top_pool, all_fallen_off)

    # Phase 6: Generate batch sitemap and update sitemap index
    generate_batch_sitemap(new_files)
    update_sitemap_index()

    log.info("Processed %d new articles", len(new_files))
    git_commit_all(new_files)
    log.info("=== pulse360 researcher done ===")


if __name__ == "__main__":
    # Ensure scripts/ is on sys.path so imports work
    sys.path.insert(0, str(Path(__file__).parent))
    main()
