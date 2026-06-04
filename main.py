from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image
import httpx
import asyncio
import time
import ipaddress
import re
from io import BytesIO
from functools import lru_cache
from collections import defaultdict

app = FastAPI()

# ── Config ────────────────────────────────────────────────────────────────────
MAX_SIZE = 128          # max pixel grid dimension
MIN_SIZE = 1
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 10 MB download cap
FETCH_TIMEOUT = 8       # seconds
RATE_LIMIT_REQUESTS = 30             # per window
RATE_LIMIT_WINDOW = 60              # seconds

# ── Rate limiter (in-memory, per IP) ─────────────────────────────────────────
_rate_store: dict[str, list[float]] = defaultdict(list)

def is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW
    calls = _rate_store[ip]
    # Prune old entries
    _rate_store[ip] = [t for t in calls if t > window_start]
    if len(_rate_store[ip]) >= RATE_LIMIT_REQUESTS:
        return True
    _rate_store[ip].append(now)
    return False

# ── SSRF protection ───────────────────────────────────────────────────────────
BLOCKED_PATTERNS = re.compile(
    r"(localhost|127\.|0\.0\.0\.0|::1|169\.254\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)",
    re.IGNORECASE,
)

def is_safe_url(url: str) -> bool:
    if BLOCKED_PATTERNS.search(url):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    return True

# ── Image fetch (async + size-capped) ────────────────────────────────────────
async def fetch_image_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=3,
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": "PixelAPI/1.0"},
    ) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                raise ValueError(f"Not an image (content-type: {content_type})")
            chunks = []
            total = 0
            async for chunk in r.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds 10 MB limit")
                chunks.append(chunk)
            return b"".join(chunks)

# ── Pixel conversion (fast, via numpy-style list comp) ───────────────────────
def image_to_pixels(img: Image.Image, size: int) -> list:
    w, h = img.size
    scale = size / max(w, h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    # Fast extraction via tobytes()
    data = list(img.tobytes())
    pixels = []
    idx = 0
    for _ in range(img.height):
        row = []
        for _ in range(img.width):
            row.append([data[idx], data[idx + 1], data[idx + 2]])
            idx += 3
        pixels.append(row)
    return pixels

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return {"status": "online"}

@app.get("/image")
async def image(request: Request, url: str, size: int = 32):
    # Rate limit
    client_ip = request.client.host
    if is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Slow down.")

    # Validate size
    if not (MIN_SIZE <= size <= MAX_SIZE):
        raise HTTPException(status_code=400, detail=f"size must be between {MIN_SIZE} and {MAX_SIZE}")

    # SSRF / URL safety
    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or disallowed URL")

    # Fetch
    try:
        raw = await fetch_image_bytes(url)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Remote server returned {e.response.status_code}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image fetch timed out")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch image: {e}")

    # Decode & convert
    try:
        img = Image.open(BytesIO(raw)).convert("RGB")
        pixels = image_to_pixels(img, size)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not process image: {e}")

    return pixels
