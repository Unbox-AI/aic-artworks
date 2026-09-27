"""Fetch the app's data from the Hugging Face dataset, for hosted deployments.

    ART_CATALOG_ID=cat_... uv run python app/download_data.py

The app calls `download` itself when it starts without data and ART_CATALOG_ID
is set, so hosts that only run `streamlit run` need nothing else.

The catalog, the map fields and the cached map coordinates live under `app-data/`
in the dataset next to the images; `publish_data.py` puts them there. The catalog
id comes from the environment because embedded catalogs are private to the API
key that embedded them, so it changes with the key the deployment uses.
"""

import json
import os
from pathlib import Path

from huggingface_hub import hf_hub_download

DATA = Path(__file__).parent / "data"
REPO = os.environ.get("ART_DATA_REPO", "unboxai/aic-artworks")


def fetch(name: str) -> None:
    path = hf_hub_download(REPO, f"app-data/{name}", repo_type="dataset")
    (DATA / name).write_bytes(Path(path).read_bytes())


def download(catalog_id: str) -> None:
    DATA.mkdir(exist_ok=True)
    # Order matters: art_map reuses the map cache only if it is newer than the
    # catalog.
    for name in ["art_catalog.parquet", "works.parquet", f"umap_{catalog_id}.parquet"]:
        try:
            fetch(name)
        except Exception as exc:
            if not name.startswith("umap_"):
                raise
            print(f"no cached map for {catalog_id}; the app will fetch it ({exc})")
    (DATA / "catalog.json").write_text(json.dumps({"catalog_id": catalog_id}))


if __name__ == "__main__":
    download(os.environ["ART_CATALOG_ID"])
    print(f"data from {REPO} ready in {DATA}")
