"""Deploy the app to a Hugging Face Space.

    uv run python space/deploy.py unboxai/aic-artworks [--public]

Docker Spaces need a paid plan: PRO for a personal account, Team for an
organisation. The app itself deploys for free on Streamlit Community Cloud.

Uploads the code, this folder's Dockerfile and README, and sets the Space's
ART_CATALOG_ID variable from app/data/catalog.json. The API key is a secret:
set UNBOXAI_API_KEY in the Space settings, or pass --set-key to copy it from .env.
The Space is created private unless --public is given.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import CommitOperationAdd, HfApi

ROOT = Path(__file__).parent.parent
SPACE = Path(__file__).parent
CODE = ["pyproject.toml", "uv.lock", ".python-version", "LICENSE"]
# The fashion pilot stays out: its H&M data may not be shown publicly.
APP_FILES = ["app.py", "art_map.py", "download_data.py"]

load_dotenv(ROOT / ".env")
repo_id = sys.argv[1]
api = HfApi()
api.create_repo(
    repo_id, repo_type="space", space_sdk="docker", exist_ok=True,
    private="--public" not in sys.argv,
)  # fmt: skip

catalog_id = json.loads((ROOT / "app/data/catalog.json").read_text())["catalog_id"]
api.add_space_variable(repo_id, "ART_CATALOG_ID", catalog_id)
if "--set-key" in sys.argv:
    api.add_space_secret(repo_id, "UNBOXAI_API_KEY", os.environ["UNBOXAI_API_KEY"])

operations = (
    [CommitOperationAdd(name, ROOT / name) for name in CODE]
    + [CommitOperationAdd(f"app/{name}", ROOT / "app" / name) for name in APP_FILES]
    + [CommitOperationAdd(name, SPACE / name) for name in ["Dockerfile", "README.md"]]
)
api.create_commit(
    repo_id, operations, commit_message="Deploy", repo_type="space"
)  # fmt: skip
print(f"deployed https://huggingface.co/spaces/{repo_id} (catalog {catalog_id})")
