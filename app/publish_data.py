"""Publish what the app reads at runtime to the Hugging Face dataset.

    uv run python app/publish_data.py unboxai/aic-artworks

Uploads the catalog, the map fields and the cached map coordinates of the
embedded catalog under `app-data/`, where `download_data.py` fetches them.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import CommitOperationAdd, HfApi

DATA = Path(__file__).parent / "data"

load_dotenv()
repo_id = sys.argv[1]
catalog_id = json.loads((DATA / "catalog.json").read_text())["catalog_id"]
names = ["art_catalog.parquet", "works.parquet", f"umap_{catalog_id}.parquet"]
missing = [n for n in names if not (DATA / n).exists()]
if missing:
    sys.exit(f"missing {missing}; open the map in the app once to cache it")
HfApi().create_commit(
    repo_id,
    [CommitOperationAdd(f"app-data/{n}", DATA / n) for n in names],
    commit_message=f"App data for catalog {catalog_id}",
    repo_type="dataset",
)
print(f"published {names} to {repo_id}; set ART_CATALOG_ID={catalog_id} on the Space")
