"""Fetch public-domain artworks from the Art Institute of Chicago and build a catalog.

    uv run python app/build_catalog.py

Writes `data/artworks.jsonl` (raw API records, reused on later runs),
`data/art_catalog.parquet` (the 19,000 best-known works, ready for `client.embed`)
and `data/works.parquet` (the museum fields the map colours and labels by).
"""

import json
import os
import time
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from behaviorgpt.resources.catalogs import CATALOG_SCHEMA, check_catalog_schema
from dotenv import load_dotenv

load_dotenv()
DATA = Path(__file__).parent / "data"
RAW = DATA / "artworks.jsonl"
CATALOG = DATA / "art_catalog.parquet"
WORKS = DATA / "works.parquet"
IMAGES = DATA / "images"
# Artwork ids whose image is at ART_IMAGE_BASE_URL; written by upload_images.py.
HOSTED_LIST = Path(__file__).parent / "hosted_images.txt"
MAX_ITEMS = 19_000

API = "https://api.artic.edu/api/v1/artworks/search"
IMAGE = "https://www.artic.edu/iiif/2/{}/full/843,/0/default.jpg"
# Re-hosted copies from fetch_images.py + upload_images.py, laid out by
# hosted_image_path. BehaviorGPT's fetcher is blocked by the museum's Cloudflare,
# so embed with this set.
IMAGE_BASE_URL = os.environ.get("ART_IMAGE_BASE_URL", "").rstrip("/")
# The search API refuses offset + limit beyond 1,000, so date ranges are split
# until each holds fewer works than that. A single year above it is truncated.
SEARCH_WINDOW = 1_000
PAGE = 100
# Anonymous clients get 60 requests per minute.
REQUEST_INTERVAL = 1.05
FIELDS = [
    "id", "title", "artist_title", "image_id", "date_display", "date_start",
    "department_title", "classification_title", "style_title", "medium_display",
    "place_of_origin", "term_titles", "subject_titles", "style_titles",
    "short_description", "boost_rank", "is_boosted", "has_not_been_viewed_much",
]  # fmt: skip

http = httpx.Client(
    headers={"AIC-User-Agent": "behaviorgpt-art-museum-demo"}, timeout=60
)
_last_request = 0.0


def search(date_from: int, date_to: int, limit: int, page: int = 1) -> dict:
    global _last_request
    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"is_public_domain": True}},
                    {"exists": {"field": "image_id"}},
                    {"range": {"date_start": {"gte": date_from, "lte": date_to}}},
                ]
            }
        },
        "fields": FIELDS,
        "limit": limit,
        "page": page,
    }
    for attempt in range(5):
        _last_request = time.monotonic()
        response = http.post(API, json=body)
        if response.status_code == 429:
            time.sleep(30 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    response.raise_for_status()
    return {}


def date_ranges(lo: int, hi: int):
    total = search(lo, hi, limit=0)["pagination"]["total"]
    if total == 0:
        return
    if total < SEARCH_WINDOW or lo == hi:
        yield lo, hi, total
        return
    mid = (lo + hi) // 2
    yield from date_ranges(lo, mid)
    yield from date_ranges(mid + 1, hi)


def fetch_all() -> list[dict]:
    DATA.mkdir(exist_ok=True)
    records: dict[int, dict] = {}
    for lo, hi, total in date_ranges(-8000, 2100):
        pages = min(total, SEARCH_WINDOW - PAGE) // PAGE + 1
        print(f"years {lo}..{hi}: {total} works, {pages} pages", flush=True)
        for page in range(1, pages + 1):
            for record in search(lo, hi, PAGE, page)["data"]:
                records[record["id"]] = record
    with RAW.open("w") as f:
        for record in records.values():
            f.write(json.dumps(record) + "\n")
    return list(records.values())


def unique(values) -> list[str] | None:
    texts = (v.strip() for v in values if isinstance(v, str))
    seen = list(dict.fromkeys(t for t in texts if t))
    return seen or None


def as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def popularity(row) -> int:
    """AIC exposes no view counts, so this ranks by the museum's own signals."""
    if row["is_boosted"]:
        rank = row["boost_rank"] if pd.notna(row["boost_rank"]) else 900
        return 100_000 - 100 * int(rank)
    if row["has_not_been_viewed_much"] is False:
        return 1_000
    return 10


def categories(row) -> str | None:
    parts = unique(
        [row["department_title"], row["classification_title"], row["style_title"]]
    )
    return ", ".join(parts) if parts else None


def search_keywords(row) -> list[str] | None:
    extra = [row["medium_display"], row["place_of_origin"], row["date_display"]]
    return unique(as_list(row["term_titles"]) + extra)


def keywords(row) -> list[str] | None:
    return unique(as_list(row["style_titles"]) + as_list(row["subject_titles"]))


def hosted_image_path(artwork_id: str) -> str:
    """Path under the image host. Hugging Face caps a folder at 10,000 files."""
    return f"{int(artwork_id) % 10}/{artwork_id}.jpg"


def hosted_ids() -> set[str]:
    if HOSTED_LIST.exists():
        return set(HOSTED_LIST.read_text().split())
    return {path.stem for path in IMAGES.glob("*.jpg")}


def image_urls(df: pd.DataFrame) -> pd.Series:
    if not IMAGE_BASE_URL:
        return df["image_id"].map(IMAGE.format)
    hosted = hosted_ids()
    ids = df["id"].astype(str)
    return ids.map(
        lambda i: f"{IMAGE_BASE_URL}/{hosted_image_path(i)}" if i in hosted else None
    )


def build_catalog(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    df = df[df["image_id"].notna() & df["title"].fillna("").str.strip().ne("")]
    df["frequency"] = df.apply(popularity, axis=1).astype("int64")
    df["described"] = df["short_description"].notna()
    df = df.sort_values(["frequency", "described"], ascending=False).head(MAX_ITEMS)

    return pd.DataFrame(
        {
            "id": df["id"].astype(str),
            "name": df["title"].str.strip(),
            "brand": df["artist_title"],
            "categories": df.apply(categories, axis=1),
            "image_url": image_urls(df),
            "event_type": "product",
            "group": "product",
            "sales_since": None,
            "timestamp": pd.array([None] * len(df), dtype="Int64"),
            "frequency": df["frequency"],
            "market": None,
            "price": None,
            "currency": None,
            "search_keywords": df.apply(search_keywords, axis=1),
            "keywords": df.apply(keywords, axis=1),
        }
    )


def build_works(records: list[dict], ids: pd.Series) -> pd.DataFrame:
    works = pd.DataFrame(
        {
            "id": [str(r["id"]) for r in records],
            "department": [r.get("department_title") for r in records],
            "style": [r.get("style_title") for r in records],
            "date": [r.get("date_display") for r in records],
            "year": pd.array([r.get("date_start") for r in records], dtype="Int64"),
        }
    )
    return works[works["id"].isin(set(ids))]


if __name__ == "__main__":
    if RAW.exists():
        records = [json.loads(line) for line in RAW.open()]
        print(f"reusing {len(records)} records from {RAW}")
    else:
        records = fetch_all()
    catalog = build_catalog(records)
    table = pa.Table.from_pandas(
        catalog, schema=pa.schema(CATALOG_SCHEMA), preserve_index=False
    )
    pq.write_table(table, CATALOG)
    check_catalog_schema(CATALOG)
    build_works(records, catalog["id"]).to_parquet(WORKS, index=False)
    print(f"{len(catalog)} artworks -> {CATALOG} and {WORKS}")
