"""Mirror scraped images into the Cloudflare R2 ``resido`` bucket.

Scraped image URLs don't last: Google Maps photo links are token-signed and
expire, ticket-site images are hotlink-protected or rate-limited. So at ingest
we download each image once and re-host it in R2, then store *our* stable public
URL on the row instead of the source URL.

R2 speaks the S3 API. Rather than pull in boto3 (sync, heavy, and a moving target
on default-checksum behaviour with S3-compatible stores), we sign a single
PutObject with SigV4 and send it over httpx — same raw-REST approach the rest of
ingestion uses for Apify/Exa/Firecrawl.

Everything degrades gracefully: with R2 unconfigured, or on any download/upload
failure, we keep the original source URL so a card still shows *something*.
"""

import asyncio
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Caps + manners for the download side.
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — scraped thumbnails are far smaller
_DOWNLOAD_TIMEOUT = 30.0
_UPLOAD_TIMEOUT = 30.0
_CONCURRENCY = 8  # parallel mirrors per batch
# A browser-y UA so hotlink-protective CDNs (Google, ticket sites) serve us.
# Accept advertises ONLY jpeg/png (not avif/webp): content-negotiating CDNs then
# serve a format Flutter's built-in image codec can decode — Flutter can't render
# AVIF, so advertising it gets us undisplayable cards. `*/*;q=0.8` keeps
# non-negotiating servers from 406-ing (they just send their default).
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "image/jpeg,image/png,*/*;q=0.8",
}

# Leading magic bytes → content-type, for CDNs that mislabel or omit Content-Type.
_SNIFF = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_warned_disabled = False  # log the "R2 not configured" notice at most once per process


def _sniff_content_type(body: bytes) -> str | None:
    for sig, ct in _SNIFF:
        if body.startswith(sig):
            return ct
    # WebP is a RIFF container; require the WEBP fourcc so WAV/AVI don't match.
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


# Formats Flutter's built-in image codec can decode. AVIF/SVG/x-icon get
# rejected here (Exa sometimes returns a .avif file, a logo SVG, or a favicon as
# a page's "image") so we never store an image a card can't render.
_RENDERABLE = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _resolve_content_type(header_ct: str | None, body: bytes) -> str | None:
    ct = (header_ct or "").split(";")[0].strip().lower()
    if ct not in _RENDERABLE:
        ct = _sniff_content_type(body) or ""  # trust the bytes over a wrong/missing header
    return ct if ct in _RENDERABLE else None  # None ⇒ not a renderable photo; skip it


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


async def _put_object(client: httpx.AsyncClient, *, key: str, body: bytes, content_type: str) -> None:
    """SigV4-sign and PUT one object to ``s3://{R2_BUCKET}/{key}``. Raises on non-2xx."""
    host = settings.r2_endpoint_host
    region, service = "auto", "s3"
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    # Path-style addressing; encode the key but keep its "/" separators.
    canonical_uri = "/" + settings.R2_BUCKET + "/" + quote(key, safe="/")
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    k_date = _hmac(("AWS4" + settings.R2_SECRET_ACCESS_KEY).encode(), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={settings.R2_ACCESS_KEY_ID}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    resp = await client.put(
        f"https://{host}{canonical_uri}",
        content=body,
        headers={
            "Authorization": authorization,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "content-type": content_type,
        },
        timeout=_UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()


def _public_url(key: str) -> str:
    return settings.R2_PUBLIC_URL.rstrip("/") + "/" + quote(key, safe="/")


def is_r2_url(url: str | None) -> bool:
    """True if ``url`` points at our R2 public bucket (i.e. a mirror succeeded)."""
    return bool(url and settings.R2_PUBLIC_URL and url.startswith(settings.R2_PUBLIC_URL.rstrip("/")))


async def mirror_image(src_url: str, *, key: str, client: httpx.AsyncClient) -> str:
    """Download ``src_url`` and re-host it in R2 under ``key``; return the public R2
    URL. On any failure (or a non-image response) returns ``src_url`` unchanged."""
    try:
        resp = await client.get(
            src_url, headers=_DOWNLOAD_HEADERS, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
        )
        resp.raise_for_status()
        body = resp.content
        if not body or len(body) > _MAX_BYTES:
            return src_url
        content_type = _resolve_content_type(resp.headers.get("content-type"), body)
        if content_type is None:  # not actually an image — leave the source URL be
            logger.warning("r2_skip_non_image", src=src_url[:200])
            return src_url
        await _put_object(client, key=key, body=body, content_type=content_type)
        return _public_url(key)
    except Exception as exc:  # mirroring is best-effort; never sink an ingest run
        logger.warning("r2_mirror_failed", src=src_url[:200], key=key, error=str(exc))
        return src_url


async def mirror_best(candidates, *, key: str, client: httpx.AsyncClient) -> str | None:
    """Try each candidate source URL in order; return the public R2 URL of the
    first that mirrors as a renderable photo, or None if none do.

    Used when several images might do (e.g. a per-event search returns multiple
    results, some unusable AVIF/SVG/favicons): the first that downloads as a
    jpeg/png/webp/gif and uploads wins; the rest, and an empty list, yield None."""
    if not settings.r2_enabled:
        return None
    for src in candidates:
        if not src:
            continue
        url = await mirror_image(src, key=key, client=client)
        if is_r2_url(url):  # mirror_image returns the source URL on any failure
            return url
    return None


async def mirror_field(rows, *, src_field: str, key_fn) -> None:
    """Mirror ``row[src_field]`` for every row to R2, in place, concurrently.

    ``key_fn(row) -> str`` returns the full object key (e.g. ``"providers/<id>"``).
    No-op (rows untouched) when R2 isn't configured. Rows with no/empty source
    value are skipped. Intended for the write path only — keep dry-runs out."""
    global _warned_disabled
    targets = [r for r in rows if r.get(src_field)]
    if not targets:
        return
    if not settings.r2_enabled:
        if not _warned_disabled:
            logger.warning(
                "r2_not_configured",
                msg="R2_* env vars incomplete — keeping source image URLs, not mirroring",
            )
            _warned_disabled = True
        return

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient() as client:

        async def _one(row: dict) -> None:
            async with sem:
                row[src_field] = await mirror_image(
                    row[src_field], key=key_fn(row), client=client
                )

        await asyncio.gather(*(_one(r) for r in targets))
