"""Does art taste say anything about fashion? Probe a mixed catalog.

    uv run python app/probe_mixed.py data/mixed_plain.parquet data/mixed_plain.json

Replays a few made-up art-only histories against the mixed catalog and reports:

1. Filters: an art-only and a fashion-only request return only that domain.
2. Distinctness: how different each persona's fashion picks are from the others'
   and from a cold start. Identical lists mean art history carries no signal.
3. Colour agreement: for personas built from works tagged with colours, the share
   of fashion picks in those colours, against the share in the whole catalog.
4. Blending: unfiltered, how much fashion leaks into an art persona's top 50,
   and how mixed the two domains are on the embedding map.
"""

import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from behaviorgpt import AddToCart, UnboxAIClient, View
from dotenv import load_dotenv

from art_map import parse_umap_html

APP = Path(__file__).parent
ART_ONLY = {"id": {"$regex": r"\d+"}}
FASHION_ONLY = {"id": {"$regex": "hm_.*"}}
PICKS = 12
VIEWS, HANGS = 6, 2


def has(column: str, pattern: str):
    return lambda art: art[column].fillna("").str.contains(pattern, case=False)


def has_keyword(pattern: str):
    return lambda art: art["keywords"].map(
        lambda ks: any(
            re.search(pattern, k, re.I) for k in (ks if ks is not None else [])
        )
    )


PERSONAS = {
    "Japanese woodblock prints": has("categories", "woodblock"),
    "Impressionist paintings": has_keyword(r"^Impressionism$"),
    "Ancient Egypt": has_keyword(r"^egyptian$"),
    "Flowers and botany": has_keyword(r"^(flowers|floral|floral motifs)$"),
    "Portraits": has_keyword(r"^portraits?$"),
    "Blue works": has_keyword(r"^blue \(color\)$"),
    "Red works": has_keyword(r"^red \(color\)$"),
}
PERSONA_COLOURS = {"Blue works": "blue", "Red works": "red"}


def history_for(art: pd.DataFrame, pick) -> list:
    chosen = art[pick(art)].nlargest(VIEWS, "frequency")["id"].tolist()
    return [View(i) for i in chosen] + [AddToCart(i) for i in chosen[:HANGS]]


def ids(response) -> list[str]:
    return [item.id for item in response.products.items]


def jaccard(a: list[str], b: list[str]) -> float:
    return len(set(a) & set(b)) / len(set(a) | set(b))


def colour_share(catalog: pd.DataFrame, item_ids: list[str], colour: str) -> float:
    cats = catalog.set_index("id").loc[item_ids, "categories"].fillna("")
    return cats.str.contains(rf"\b{colour}\b", case=False).mean()


def mixing(points: pd.DataFrame, k: int = 10) -> dict[str, float]:
    """Mean share of each point's k nearest map neighbours from the other domain."""
    xy = points[["x", "y"]].to_numpy()
    fashion = points["id"].str.startswith("hm_").to_numpy()
    other = np.empty((len(xy), k), dtype=bool)
    for start in range(0, len(xy), 1000):
        rows = slice(start, start + 1000)
        d = ((xy[rows, None, :] - xy[None, :, :]) ** 2).sum(-1)
        d[np.arange(d.shape[0]), np.arange(start, start + d.shape[0])] = np.inf
        nearest = np.argpartition(d, k, axis=1)[:, :k]
        other[rows] = fashion[nearest] != fashion[rows, None]
    return {"art": other[~fashion].mean(), "fashion": other[fashion].mean()}


def main(catalog_path: Path, state_path: Path) -> None:
    load_dotenv()
    catalog_id = json.loads(state_path.read_text())["catalog_id"]
    client = UnboxAIClient(market="us", default_catalog_id=catalog_id)
    catalog = pd.read_parquet(catalog_path)
    art = catalog[~catalog["id"].str.startswith("hm_")]
    fashion = catalog[catalog["id"].str.startswith("hm_")]
    names = catalog.set_index("id")["name"]
    cats = catalog.set_index("id")["categories"].fillna("")
    print(
        f"# {catalog_path.name} ({catalog_id}): {len(art)} art, {len(fashion)} fashion"
    )

    histories = {name: history_for(art, pick) for name, pick in PERSONAS.items()}

    print("\n## Filters")
    probe = histories["Impressionist paintings"]
    art_ids = ids(client.complete(history=probe, limit=50, filters=ART_ONLY))
    fashion_ids = ids(client.complete(history=probe, limit=50, filters=FASHION_ONLY))
    print(
        f"art-only: {sum(not i.startswith('hm_') for i in art_ids)}/{len(art_ids)} art"
    )
    print(
        f"fashion-only: {sum(i.startswith('hm_') for i in fashion_ids)}"
        f"/{len(fashion_ids)} fashion"
    )

    cold = ids(client.complete(history=[], limit=PICKS, filters=FASHION_ONLY))
    picks = {
        name: ids(client.complete(history=h, limit=PICKS, filters=FASHION_ONLY))
        for name, h in histories.items()
    }

    print("\n## Fashion picks per persona")
    for name, found in picks.items():
        seeds = [e.product for e in histories[name]][:3]
        print(f"\n### {name} (seeded by {', '.join(names[s] for s in seeds)}...)")
        for i in found[:8]:
            print(f"- {names[i]}  [{cats[i][:90]}]")

    print("\n## Distinctness (Jaccard overlap of top-12 fashion picks; 0 = disjoint)")
    pairs = [jaccard(picks[a], picks[b]) for a, b in itertools.combinations(picks, 2)]
    vs_cold = [jaccard(p, cold) for p in picks.values()]
    print(f"persona vs persona: mean {np.mean(pairs):.2f}, max {np.max(pairs):.2f}")
    print(f"persona vs cold start: mean {np.mean(vs_cold):.2f}")

    print("\n## Colour agreement (share of fashion picks in the persona's colour)")
    for name, colour in PERSONA_COLOURS.items():
        wide = ids(
            client.complete(history=histories[name], limit=50, filters=FASHION_ONLY)
        )
        got = colour_share(catalog, wide, colour)
        base = colour_share(catalog, fashion["id"].tolist(), colour)
        print(f"{name}: {got:.0%} of top 50 are {colour}, vs {base:.0%} of all fashion")

    print("\n## Blending")
    for name in ["Impressionist paintings", "Ancient Egypt", "Flowers and botany"]:
        top = ids(client.complete(history=histories[name], limit=50))
        share = np.mean([i.startswith("hm_") for i in top])
        print(f"{name}, unfiltered top 50: {share:.0%} fashion")
    points = parse_umap_html(client.umap(catalog_id=catalog_id))
    mix = mixing(points)
    print(
        f"map: {mix['art']:.1%} of art's 10 nearest neighbours are fashion, "
        f"{mix['fashion']:.1%} of fashion's are art (50% = fully blended)"
    )


if __name__ == "__main__":
    main(APP / sys.argv[1], APP / sys.argv[2])
