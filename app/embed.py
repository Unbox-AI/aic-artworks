"""Upload the art catalog to BehaviorGPT and remember its catalog id for the app.

uv run python app/embed.py           # upload and wait
uv run python app/embed.py --resume  # keep waiting on the saved job
uv run python app/embed.py data/mixed_plain.parquet data/mixed_plain.json
"""

import json
import sys
import time
from pathlib import Path

import httpx
from behaviorgpt import ProgressPrinter, UnboxAIClient
from behaviorgpt._exceptions import UnboxAIError
from dotenv import load_dotenv

APP = Path(__file__).parent
DATA = APP / "data"
paths = [APP / a for a in sys.argv[1:] if not a.startswith("--")]
CATALOG = paths[0] if paths else DATA / "art_catalog.parquet"
STATE = paths[1] if len(paths) > 1 else DATA / "catalog.json"

load_dotenv()
client = UnboxAIClient(market="us")

LOCK_RETRY_SECONDS = 120


def upload() -> dict:
    """Upload, waiting out the one-ingest-per-key lock a previous job may hold."""
    while True:
        try:
            return client.embed(CATALOG).model_dump()
        except UnboxAIError as exc:
            if "already in progress" not in str(exc):
                raise
            print(f"another ingest holds the lock; retrying in {LOCK_RETRY_SECONDS}s")
            time.sleep(LOCK_RETRY_SECONDS)
        except httpx.TransportError as exc:
            print(f"network hiccup ({exc}), retrying", flush=True)
            time.sleep(10)


if "--resume" in sys.argv:
    job = json.loads(STATE.read_text())
else:
    job = upload()
    STATE.write_text(json.dumps(job, indent=2))
    print(f"job {job['job_id']} started, catalog_id {job['catalog_id']}")


def catalog_status(catalog_id: str) -> str:
    """Status from the catalog listing, for when only the catalog id is known."""
    listing = client._http_client.get("/catalogs").json()["catalogs"]
    return next(c["status"] for c in listing if c["catalog_id"] == catalog_id)


progress = ProgressPrinter()
last_status = None
while True:
    try:
        if job.get("job_id"):
            client.catalogs.wait_for_job(
                job["job_id"], timeout=3600.0, on_progress=progress
            )
            break
        status = catalog_status(job["catalog_id"])
        if status != last_status:
            print(status, flush=True)
            last_status = status
        if status == "ready":
            break
        if status == "failed":
            sys.exit(f"catalog {job['catalog_id']} failed to embed")
        time.sleep(15)
    except httpx.TransportError as exc:
        print(f"network hiccup ({exc}), retrying", flush=True)
        time.sleep(10)

print(f"catalog {job['catalog_id']} is ready; saved to {STATE}")
