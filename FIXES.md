# What needs fixing

Found while building this app. Grouped by where the fix belongs. File references are to this repo unless noted.

## BehaviorGPT SDK ([Unbox-AI/behaviorgpt](https://github.com/Unbox-AI/behaviorgpt))

1. **Publish to PyPI.** The SDK README says `pip install behaviorgpt`, but the package is not on PyPI. `pyproject.toml` here pins a git commit instead; switch to a version once it is published.
2. **Export `UnboxAIError` publicly.** `app.py` and `embed.py` import it from the private `behaviorgpt._exceptions`.
3. **Add a way to list catalogs and their status.** `GET /catalogs` exists in the API but not in the SDK, so `embed.py` calls it through the private `client._http_client`.
4. **Return map coordinates as data.** `client.umap()` returns a 6 MB Plotly HTML page. `art_map.parse_umap_html` digs the coordinates out of the embedded JavaScript, which breaks as soon as the page changes. A method returning `id, x, y` rows would make the map reusable.
5. **Make `embed(wait=True)` robust.** One network timeout while polling raised out of `embed`, and the job id was never returned, so the running job could not be followed. Return the job details before waiting, and retry transient errors while polling.

## BehaviorGPT API

1. **Explain failed embed jobs, and don't fail on images alone.** When every image fetch returned 403, the job just reported `failed`, twice, after fetching about 19,000 images. Report the reason, and fall back to text-only embedding as the catalog format docs promise for unreachable images.
2. **Consider fetching images like a browser.** Image hosts behind Cloudflare and similar bot protection block the fetcher. A browser user agent, or accepting images uploaded next to the parquet, would remove the need to re-host.
3. **Release the ingest lock when a job dies.** After a failed job, new uploads were refused with "an ingest is already in progress" for most of a day, and the catalog stayed `pending` throughout.
4. **Stabilise `/umap`.** It returned 500 for this catalog for a while after embedding, and still returns 500 for the shared `retail_catalog`.
5. **Check how popularity is used at cold start.** With no history, `complete` ranks textile fragments above the museum's most famous paintings, although `frequency` puts those first. Either the signal is ignored or its weight is too low.
6. **Clarify catalogs per key.** Every upload replaced the same catalog id, so a 500-row test and the full catalog could not exist side by side.

## This repo, before a public launch

1. **Delete the old personal image dataset.** The images now live in `unboxai/aic-artworks`; `bruel/aic-artworks` is no longer used.
2. **Publish the catalog as a shared catalog**, like `retail_catalog`, so visitors can use the app, and the [demo](https://behaviorgpt.unboxai.com/), without embedding it first. The app could then default to that catalog id instead of requiring `embed.py`.
3. **Use a limited demo key before going public.** The deployment runs on the admin key while it is private. Give the public app a key with rate and spend limits, re-embed the catalog under it, and update the `ART_CATALOG_ID` secret.
4. **Seed the cold start.** Until the ranking issue above is fixed, open with a curated set of well-known works so the first screen is not textile fragments.
5. **Fill the 671 missing images.** Their 843 px renditions timed out on the museum's server; re-run `fetch_images.py` later, then `upload_images.py`, `build_catalog.py` and `embed.py`. Until then those works embed from text only.
6. **Clean up style labels.** The museum uses both "19th century" and "Nineteenth century", so they show as two colours on the map (`art_map._style_label`).
7. **Fetch every year in full.** `build_catalog.py` keeps only the first 1,000 works for a single year above that limit (1800 and 1801). Split those years further, for example by department, if full coverage matters.
8. **Add CI**: `ruff check`, and a smoke test that runs the app with Streamlit's `AppTest` against a small catalog.

## GitHub

The organisation name `UnboxAI` (without the hyphen) is unclaimed. Consider registering it so nobody else can.
