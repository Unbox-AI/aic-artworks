"""Publish what the app reads at runtime to a Hugging Face dataset.

    uv run python app/publish_data.py unboxai/aic-artworks
    uv run python app/publish_data.py bruel/aic-artworks-fashion mixed_bridged_large

Uploads the catalog, the map fields and the cached map coordinates of the
embedded catalog under `app-data/`, where `download_data.py` fetches them.

Given a mixed catalog name, uploads that catalog and its map instead, to a
private dataset: the H&M data is licensed for research only.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import CommitOperationAdd, HfApi

DATA = Path(__file__).parent / "data"

load_dotenv()
repo_id = sys.argv[1]
mixed = sys.argv[2] if len(sys.argv) > 2 else None
state = DATA / f"{mixed or 'catalog'}.json"
catalog_id = json.loads(state.read_text())["catalog_id"]
if mixed:
    names = [f"{mixed}.parquet", f"umap_{catalog_id}.parquet"]
else:
    names = ["art_catalog.parquet", "works.parquet", f"umap_{catalog_id}.parquet"]
missing = [n for n in names if not (DATA / n).exists()]
if missing:
    sys.exit(f"missing {missing}; open the map in the app once to cache it")

api = HfApi()
if mixed:
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    if api.repo_info(repo_id, repo_type="dataset").private is not True:
        sys.exit(f"{repo_id} is public; the fashion data must stay private")
api.create_commit(
    repo_id,
    [CommitOperationAdd(f"app-data/{n}", DATA / n) for n in names],
    commit_message=f"App data for catalog {catalog_id}",
    repo_type="dataset",
)
print(f"published {names} to {repo_id}; set ART_CATALOG_ID={catalog_id}")
