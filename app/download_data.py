"""Fetch the app's data from the Hugging Face datasets, for hosted deployments.

    ART_CATALOG_ID=cat_... uv run python app/download_data.py

The app calls `download` itself when it starts without data and ART_CATALOG_ID
is set, so hosts that only run `streamlit run` need nothing else.

The catalog, the map fields and the cached map coordinates live under `app-data/`
in the public dataset next to the images; `publish_data.py` puts them there. The
catalog id comes from the environment because embedded catalogs are private to
the API key that embedded them, so it changes with the key the deployment uses.

For the fashion pilot (ART_CATALOG_STATE=mixed_*.json), the mixed catalog and its
map come from a private dataset instead, ART_FASHION_REPO, read with HF_TOKEN:
the H&M data is licensed for research only and may not be redistributed.
"""

import json
import os
from pathlib import Path

from huggingface_hub import hf_hub_download

DATA = Path(__file__).parent / "data"
REPO = os.environ.get("ART_DATA_REPO", "unboxai/aic-artworks")
FASHION_REPO = os.environ.get("ART_FASHION_REPO", "bruel/aic-artworks-fashion")


def fetch(repo: str, name: str) -> None:
    path = hf_hub_download(
        repo, f"app-data/{name}", repo_type="dataset", token=os.environ.get("HF_TOKEN")
    )
    (DATA / name).write_bytes(Path(path).read_bytes())


def download(catalog_id: str, state: Path = DATA / "catalog.json") -> None:
    DATA.mkdir(exist_ok=True)
    mixed = state.name.startswith("mixed_")
    files = [(REPO, "art_catalog.parquet"), (REPO, "works.parquet")]
    if mixed:
        files.append((FASHION_REPO, state.with_suffix(".parquet").name))
    # Order matters: art_map reuses the map cache only if it is newer than the
    # catalog.
    files.append((FASHION_REPO if mixed else REPO, f"umap_{catalog_id}.parquet"))
    for repo, name in files:
        try:
            fetch(repo, name)
        except Exception as exc:
            if not name.startswith("umap_"):
                raise
            print(f"no cached map for {catalog_id}; the app will fetch it ({exc})")
    state.write_text(json.dumps({"catalog_id": catalog_id}))


if __name__ == "__main__":
    state = DATA / os.environ.get("ART_CATALOG_STATE", "catalog.json")
    download(os.environ["ART_CATALOG_ID"], state)
    print(f"data ready in {DATA}")
