"""Direct HTTP fetch fallback for ingestion.

Some authoritative sources (e.g. u.ae) block Firecrawl's scraping proxies but
serve clean server-rendered HTML to a normal client. When Firecrawl fails we
fetch the page ourselves and extract readable text with the stdlib HTML parser
(no extra dependencies). Lower fidelity than Firecrawl's markdown, but keeps the
canonical gov sources ingestible.
"""

from html.parser import HTMLParser

import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Tags whose text content we drop entirely.
_SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
# Block-level tags that should force a line break around their text.
_BLOCK = {
    "p", "div", "section", "article", "li", "ul", "ol", "tr", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "header", "main",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        # Collapse runs of blank lines and trim trailing spaces per line.
        raw = " ".join(self._parts)
        lines = [ln.strip() for ln in raw.replace("\n ", "\n").split("\n")]
        out: list[str] = []
        for ln in lines:
            if ln:
                out.append(ln)
        return "\n".join(out)


def html_to_text(html: str) -> tuple[str, str | None]:
    parser = _TextExtractor()
    parser.feed(html)
    title = (parser.title or "").strip() or None
    return parser.text(), title


async def fetch_markdown(url: str, timeout: float = 40.0) -> dict:
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text, title = html_to_text(resp.text)
        return {"markdown": text, "title": title}
