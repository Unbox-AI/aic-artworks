"""Build a small mixed catalog of artworks and H&M fashion for the fashion pilot.

    uv run python app/build_mixed.py            # 2,000 + 2,000 items
    uv run python app/build_mixed.py 500 500
    uv run python app/build_mixed.py 19000 19000 large  # mixed_*_large.parquet

Reads `data/art_catalog.parquet` (from build_catalog.py) and
`data/fashion/articles.csv` (the H&M Personalized Fashion Recommendations
release on Kaggle; its rules allow non-commercial research only, so this pilot
must not ship publicly). Writes two catalogs that differ only in their text:

- `data/mixed_plain.parquet`: each side described in its own words.
- `data/mixed_bridged.parquet`: both sides also tagged with a shared colour and
  motif vocabulary, so the embedding has words in common to connect them.

Fashion ids are prefixed `hm_`; art ids stay numeric. The app and the probe
split the two with an `id` regex filter.
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from behaviorgpt.resources.catalogs import CATALOG_SCHEMA, check_catalog_schema

DATA = Path(__file__).parent / "data"
ART = DATA / "art_catalog.parquet"
ARTICLES = DATA / "fashion" / "articles.csv"
VARIANTS = ["plain", "bridged"]
FASHION_PREFIX = "hm_"
IMAGE = (
    "https://storage.googleapis.com/unboxai-public-images/hm_fashion/media/"
    "hm_fashion/images/{prefix}/{id}.jpg"
)
SEED = 7

# Adult clothing and accessories; children's wear, underwear and odds and ends
# have no counterpart in a museum.
FASHION_INDEX_GROUPS = {"Ladieswear", "Divided", "Menswear", "Sport"}
FASHION_PRODUCT_GROUPS = {
    "Garment Upper body", "Garment Lower body", "Garment Full body",
    "Accessories", "Shoes", "Swimwear", "Bags",
}  # fmt: skip

COLOURS = [
    "black", "white", "grey", "blue", "red", "green", "yellow", "orange",
    "pink", "purple", "brown", "beige", "gold", "silver",
]  # fmt: skip
FASHION_COLOURS = {
    "Khaki green": "green", "Turquoise": "blue", "Mole": "brown",
    "Lilac Purple": "purple", "Yellowish Green": "green", "Bluish Green": "green",
    "Metal": "silver",
}  # fmt: skip
# Shared motif -> regexes that signal it in art keywords or fashion descriptions.
MOTIFS = {
    "floral": [r"\bflower", r"\bfloral", r"\bbotan", r"\bfoliage", r"\bvines?\b"],
    "stripes": [r"\bstripe"],
    "checks": [r"\bcheck(ed|s)?\b", r"\bplaid", r"\btartan", r"\bgingham"],
    "dots": [r"\bdots?\b", r"\bdotted", r"\bpolka"],
    "geometric": [r"\bgeometric", r"\bcircles\b", r"\bargyle", r"\bzigzag"],
    "animal": [r"\banimal", r"\bleopard", r"\bzebra", r"\bbirds?\b", r"\btiger"],
    "lace": [r"\blace\b(?!-up| up)"],
    "embroidery": [r"\bembroider"],
    "metallic": [r"\bmetallic", r"\bglitter", r"\bsequin", r"\bgold \(color\)"],
    "waves": [r"\bwaves\b", r"\bsea\b", r"\bocean"],
    "landscape": [r"\blandscapes?\b", r"\bmountains?\b", r"\bpalm trees?\b"],
}
FASHION_APPEARANCE_MOTIFS = {
    "Stripe": "stripes", "Check": "checks", "Dot": "dots", "Lace": "lace",
    "Embroidery": "embroidery", "Glittering/Metallic": "metallic",
    "Metallic": "metallic", "Sequin": "metallic", "Argyle": "geometric",
    "Jacquard": "geometric",
}  # fmt: skip


def motifs(text: str) -> list[str]:
    text = text.lower()
    return [m for m, cues in MOTIFS.items() if any(re.search(c, text) for c in cues)]


def art_colours(keywords) -> list[str]:
    found = []
    for k in keywords:
        match = re.fullmatch(r"(\w+) \(color\)", k)
        if match and match.group(1) in COLOURS:
            found.append(match.group(1))
    return found


def fashion_colour(row) -> list[str]:
    """'Dark Blue' -> blue, 'Gold' -> gold; else the broader perceived colour."""
    words = row["colour_group_name"].lower().split()
    named = [w for w in words if w in COLOURS]
    if named:
        return named[-1:]
    master = row["perceived_colour_master_name"]
    colour = FASHION_COLOURS.get(master, master.lower())
    return [colour] if colour in COLOURS else []


def with_tags(categories: str | None, tags: list[str]) -> str | None:
    tags = list(dict.fromkeys(tags))
    if not tags:
        return categories
    return ", ".join([p for p in [categories] if p] + tags)


def sample_art(n: int) -> pd.DataFrame:
    """The best-known works of each department, in proportion to its size."""
    art = pd.read_parquet(ART)
    art["department"] = art["categories"].str.split(", ").str[0]
    shares = art["department"].value_counts(normalize=True)
    picks = [
        group.nlargest(max(1, round(n * shares[dept])), "frequency")
        for dept, group in art.groupby("department")
    ]
    return pd.concat(picks).nlargest(n, "frequency").drop(columns="department")


def image_exists(client: httpx.Client, url: str) -> bool:
    try:
        return client.head(url).status_code == 200
    except httpx.HTTPError:
        return False


def sample_fashion(n: int) -> pd.DataFrame:
    """One random colourway per product, keeping only articles with an image."""
    articles = pd.read_csv(ARTICLES, dtype=str)
    articles = articles[
        articles["index_group_name"].isin(FASHION_INDEX_GROUPS)
        & articles["product_group_name"].isin(FASHION_PRODUCT_GROUPS)
    ]
    one_per_product = articles.groupby("product_code").sample(1, random_state=SEED)
    candidates = one_per_product.sample(frac=1, random_state=SEED)
    candidates["image_url"] = [
        IMAGE.format(prefix=a[:3], id=a) for a in candidates["article_id"]
    ]

    kept: list[pd.DataFrame] = []
    with httpx.Client(timeout=20) as client:
        for start in range(0, len(candidates), 1000):
            batch = candidates.iloc[start : start + 1000]
            with ThreadPoolExecutor(64) as pool:
                urls = batch["image_url"]
                ok = list(pool.map(lambda u: image_exists(client, u), urls))
            kept.append(batch[ok])
            if sum(len(k) for k in kept) >= n:
                break
    return pd.concat(kept).head(n)


def art_rows(art: pd.DataFrame, bridged: bool) -> pd.DataFrame:
    rows = art.copy()
    if bridged:
        rows["categories"] = [
            with_tags(cats, art_colours(kws) + motifs(" ".join(kws or [])))
            for cats, kws in zip(rows["categories"], rows["keywords"].map(as_list))
        ]
    return rows


def as_list(value) -> list[str]:
    return list(value) if value is not None else []


def fashion_rows(fashion: pd.DataFrame, bridged: bool) -> pd.DataFrame:
    def categories(row) -> str:
        parts = [
            row["product_type_name"], row["product_group_name"],
            row["index_group_name"], row["garment_group_name"],
            row["colour_group_name"], row["graphical_appearance_name"],
        ]  # fmt: skip
        base = ", ".join(dict.fromkeys(p for p in parts if p and p != "Unknown"))
        if not bridged:
            return base
        appearance = FASHION_APPEARANCE_MOTIFS.get(row["graphical_appearance_name"])
        tags = fashion_colour(row) + ([appearance] if appearance else [])
        return with_tags(base, tags + motifs(str(row["detail_desc"])))

    return pd.DataFrame(
        {
            "id": FASHION_PREFIX + fashion["article_id"],
            "name": fashion["prod_name"].str.strip(),
            "brand": "H&M",
            "categories": fashion.apply(categories, axis=1),
            "image_url": fashion["image_url"],
            "event_type": "product",
            "group": "product",
            "sales_since": None,
            "timestamp": pd.array([None] * len(fashion), dtype="Int64"),
            "frequency": pd.array([None] * len(fashion), dtype="Int64"),
            "market": None,
            "price": None,
            "currency": None,
            "search_keywords": fashion["detail_desc"].map(
                lambda d: [d] if isinstance(d, str) and d.strip() else None
            ),
            "keywords": fashion.apply(fashion_keywords, axis=1),
        }
    )


def fashion_keywords(row) -> list[str] | None:
    values = [row["colour_group_name"], row["graphical_appearance_name"]]
    return [v for v in values if v and v != "Unknown"] or None


def write(catalog: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(
        catalog, schema=pa.schema(CATALOG_SCHEMA), preserve_index=False
    )
    pq.write_table(table, path)
    check_catalog_schema(path)


if __name__ == "__main__":
    n_art, n_fashion = (int(x) for x in (sys.argv[1:3] or [2000, 2000]))
    suffix = f"_{sys.argv[3]}" if len(sys.argv) > 3 else ""
    art = sample_art(n_art)
    fashion = sample_fashion(n_fashion)
    print(f"{len(art)} artworks, {len(fashion)} fashion items with images")
    for variant in VARIANTS:
        path = DATA / f"mixed_{variant}{suffix}.parquet"
        bridged = variant == "bridged"
        catalog = pd.concat(
            [art_rows(art, bridged), fashion_rows(fashion, bridged)], ignore_index=True
        )
        write(catalog, path)
        print(f"{variant}: {len(catalog)} rows -> {path}")
