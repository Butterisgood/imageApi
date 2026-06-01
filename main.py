from fastapi import FastAPI
from PIL import Image
import requests
from io import BytesIO

app = FastAPI()

# ✔ Health check
@app.get("/")
def home():
    return {"status": "online"}

# ✔ Image → pixel API (WITH QUALITY CONTROL)
@app.get("/image")
def image(url: str, size: int = 32):

    try:
        # Fix blocked requests (Imgur / Bing / etc.)
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        # Download image
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()

        # Open image
        img = Image.open(BytesIO(r.content)).convert("RGB")

        # QUALITY CONTROL (THIS IS YOUR NEW FEATURE)
        # size = 16 / 32 / 64 / 128 etc.
w, h = img.size

scale = size / max(w, h)

new_w = int(w * scale)
new_h = int(h * scale)

img = img.resize((new_w, new_h))

        pixels = []

        # Convert to RGB grid
        for y in range(img.height):
            row = []
            for x in range(img.width):
                row.append(list(img.getpixel((x, y))))
            pixels.append(row)

        return pixels

    except Exception as e:
        return {"error": str(e)}
