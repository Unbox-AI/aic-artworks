"""Download every catalog image and shrink it to 512 px wide, for re-hosting.

    uv run python app/fetch_images.py

The Art Institute's Cloudflare blocks datacenter IPs, so BehaviorGPT's fetcher
cannot read www.artic.edu directly. The works are CC0, so copies may be re-hosted.
Writes `data/images/<artwork id>.jpg`; files already present are skipped.
"""

import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import pandas as pd
from PIL import Image

DATA = Path(__file__).parent / "data"
CATALOG = DATA / "art_catalog.parquet"
RAW = DATA / "artworks.jsonl"
IMAGES = DATA / "images"
# 843 px is the size the museum's own site uses, so it is almost always cached;
# other sizes are rendered on demand and routinely time out.
IIIF = "https://www.artic.edu/iiif/2/{}/full/843,/0/default.jpg"
WIDTH = 512
WORKERS = 8
PROGRESS_EVERY = 250
# Cached images arrive in well under a second; one that isn't rarely arrives at all,
# so give up fast and let a later run retry it.
ATTEMPTS = 1

http = httpx.Client(
    headers={"AIC-User-Agent": "behaviorgpt-art-museum-demo"},
    timeout=10,
    follow_redirects=True,
)


def fetch(artwork_id: str, image_id: str) -> str | None:
    target = IMAGES / f"{artwork_id}.jpg"
    if target.exists():
        return None
    url = IIIF.format(image_id)
    for _ in range(ATTEMPTS):
        try:
            response = http.get(url)
            if response.status_code == 200:
                image = Image.open(io.BytesIO(response.content)).convert("RGB")
                image.thumbnail((WIDTH, WIDTH * 4))
                image.save(target, "JPEG", quality=85)
                return None
        except (httpx.TransportError, OSError):
            continue
    return artwork_id


if __name__ == "__main__":
    IMAGES.mkdir(parents=True, exist_ok=True)
    image_ids = {}
    for line in RAW.open():
        record = json.loads(line)
        image_ids[str(record["id"])] = record["image_id"]
    df = pd.read_parquet(CATALOG, columns=["id"])
    failed = []
    with ThreadPoolExecutor(WORKERS) as pool:
        jobs = [pool.submit(fetch, i, image_ids[i]) for i in df["id"]]
        for n, job in enumerate(as_completed(jobs), 1):
            if (missing := job.result()) is not None:
                failed.append(missing)
            if n % PROGRESS_EVERY == 0:
                print(f"{n}/{len(jobs)} done, {len(failed)} failed", flush=True)
    print(f"{len(df) - len(failed)} images in {IMAGES}; failed: {failed[:20]}")
