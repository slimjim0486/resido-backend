"""Best-effort ``og:image`` extraction for event source pages.

The lifestyle events feed is built from Exa (page text only) and Claude
extraction (structured fields, no media), so event rows have no image — cards
fall back to a gradient. This pulls the page's social-share image (the
`og:image` / `twitter:image` meta tag) so a card can show a real photo. It's
the source for the events backfill; the live Exa pipeline prefers Exa's own
`image` field and only needs this as a fallback.

Dependency-free (regex over the HTML head, no bs4) to match the rest of
ingestion. Returns None on anything unexpected — callers degrade to the
gradient.
"""

import html
import re
from urllib.parse import urldefrag, urljoin

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 20.0
_HEAD_SCAN = 200_000  # og tags live in <head>; don't regex megabytes of body
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# A <meta> tag whose property/name marks it as the share image, in preference
# order. We match the whole tag, then pull its content= regardless of attr order.
_META_PATTERNS = [
    re.compile(r"<meta\b[^>]*\b(?:property|name)\s*=\s*[\"']og:image(?::url)?[\"'][^>]*>", re.I),
    re.compile(r"<meta\b[^>]*\b(?:property|name)\s*=\s*[\"']twitter:image(?::src)?[\"'][^>]*>", re.I),
]
_CONTENT = re.compile(r"\bcontent\s*=\s*[\"']([^\"']+)[\"']", re.I)


def _extract(page_html: str, *, base_url: str) -> str | None:
    head = page_html[:_HEAD_SCAN]
    for pattern in _META_PATTERNS:
        for tag in pattern.finditer(head):
            m = _CONTENT.search(tag.group(0))
            if m and m.group(1).strip():
                # Meta content is HTML-escaped (e.g. `&amp;` in signed image URLs);
                # unescape before use or the download 400s on the broken query string.
                return urljoin(base_url, html.unescape(m.group(1).strip()))
    return None


async def fetch_og_image(client: httpx.AsyncClient, page_url: str) -> str | None:
    """Fetch ``page_url`` and return its og:image / twitter:image as an absolute
    URL, or None. The URL fragment (``#event-slug``) is stripped first — many of
    our event URLs are listicle anchors that resolve to the article page."""
    url = urldefrag(page_url)[0]
    try:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=_TIMEOUT)
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype:
            return None
        return _extract(resp.text, base_url=str(resp.url))
    except Exception as exc:  # best-effort; the card keeps its gradient
        logger.warning("og_image_fetch_failed", url=url, error=str(exc))
        return None
