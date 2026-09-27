"""Publish the downloaded artwork images as a public Hugging Face dataset.

    uv run python app/upload_images.py <user>/<repo>

Needs HF_TOKEN (a write token) in the environment or in .env.

Writes app/hosted_images.txt and prints the ART_IMAGE_BASE_URL to put in .env
before rebuilding the catalog.
Safe to re-run; files already uploaded are skipped.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import CommitOperationAdd, HfApi

from build_catalog import HOSTED_LIST, hosted_image_path

IMAGES = Path(__file__).parent / "data" / "images"
# The Hub recommends keeping commits to a few thousand files.
COMMIT_SIZE = 2000
README = """---
license: cc0-1.0
---
Public-domain artwork images from the Art Institute of Chicago
(https://www.artic.edu/open-access), resized to 512 px wide. Each image is at
`images/<id % 10>/<id>.jpg`, where `<id>` is the artwork id in
https://api.artic.edu/api/v1/artworks/<id>.

`app-data/` holds the catalog and map data that the "Curate my wall" app
(https://github.com/Unbox-AI/aic-artworks) fetches when it starts.
"""

load_dotenv()
repo_id = sys.argv[1]
api = HfApi()
api.create_repo(repo_id, repo_type="dataset", exist_ok=True, private=False)
api.upload_file(
    path_or_fileobj=README.encode(),
    path_in_repo="README.md",
    repo_id=repo_id,
    repo_type="dataset",
)

uploaded = set(api.list_repo_files(repo_id, repo_type="dataset"))
pending = sorted(
    (f"images/{hosted_image_path(p.stem)}", p)
    for p in IMAGES.glob("*.jpg")
    if f"images/{hosted_image_path(p.stem)}" not in uploaded
)
print(f"{len(uploaded)} files already in {repo_id}; uploading {len(pending)}")
for start in range(0, len(pending), COMMIT_SIZE):
    batch = pending[start : start + COMMIT_SIZE]
    api.create_commit(
        repo_id,
        [CommitOperationAdd(path, local) for path, local in batch],
        commit_message=f"Add {len(batch)} images",
        repo_type="dataset",
        num_threads=16,
    )
    print(f"{start + len(batch)}/{len(pending)} uploaded", flush=True)

hosted = sorted(
    Path(f).stem
    for f in api.list_repo_files(repo_id, repo_type="dataset")
    if f.startswith("images/") and f.endswith(".jpg")
)
HOSTED_LIST.write_text("\n".join(hosted) + "\n")
print(f"{len(hosted)} hosted images listed in {HOSTED_LIST}")
print(
    "Set in .env: ART_IMAGE_BASE_URL="
    f"https://huggingface.co/datasets/{repo_id}/resolve/main/images"
)
