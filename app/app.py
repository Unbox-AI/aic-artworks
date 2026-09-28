"""Curate my wall: browse the Art Institute of Chicago with BehaviorGPT.

uv run streamlit run app/app.py

To try the art-and-fashion pilot, point it at a mixed catalog from
build_mixed.py; the art grids are then filtered to art and a fashion tab appears:

ART_CATALOG_STATE=mixed_bridged.json uv run streamlit run app/app.py
"""

import datetime
import hmac
import html
import json
import os
import threading
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st
from behaviorgpt import AddToCart, RemoveFromCart, Search, UnboxAIClient, View
from behaviorgpt._exceptions import UnboxAIError
from dotenv import load_dotenv

from art_map import COLOR_BY, figure, load_fashion, load_points, load_works
from download_data import download

DATA = Path(__file__).parent / "data"
STATE = DATA / os.environ.get("ART_CATALOG_STATE", "catalog.json")
# Mixed catalogs from build_mixed.py sit next to their state file.
MIXED_CATALOG = STATE.with_suffix(".parquet")
MIXED = STATE.name.startswith("mixed_") and MIXED_CATALOG.exists()
ART_ONLY = {"id": {"$regex": r"\d+"}} if MIXED else None
FASHION_ONLY = {"id": {"$regex": "hm_.*"}}
# Caps on what visitors can spend of the API key.
DAILY_CALL_LIMIT = int(os.environ.get("DAILY_CALL_LIMIT", "3000"))
SESSION_CLICK_LIMIT = int(os.environ.get("SESSION_CLICK_LIMIT", "150"))
OVER_BUDGET = (
    "The demo has reached its daily limit of recommendations. Please come back "
    "tomorrow."
)
GRID_COLUMNS = 4
GRID_SIZE = 12
EVENTS = {
    "search": Search,
    "view": View,
    "hang": AddToCart,
    "take_down": RemoveFromCart,
}
TRAIL = {
    "search": (":material/search:", "blue", "Searched"),
    "view": (":material/visibility:", "violet", "Looked at"),
    "hang": (":material/star:", "orange", "Hung"),
    "take_down": (":material/do_not_disturb_on:", "gray", "Took down"),
}
TRAIL_LENGTH = 20
FRAMES = ["gold", "walnut", "black", "white"]
TILTS = [-1.2, 0.8, -0.4, 1.1, 0.3, -0.9]
WALL_CSS = """
<style>
.st-key-gallery_wall {
  background:
    radial-gradient(ellipse 70% 55% at 50% 0%,
                    rgba(255, 236, 190, .38), transparent 70%),
    repeating-linear-gradient(90deg, #6d2e3b 0 26px, #652a37 26px 52px);
  border-bottom: 20px solid #7a5230;
  box-shadow: inset 0 -14px 22px rgba(0, 0, 0, .28), 0 6px 16px rgba(0, 0, 0, .18);
  border-radius: 10px;
  padding: 18px 26px 22px;
}
.st-key-gallery_row {
  overflow-x: auto; padding: 14px 4px 6px;
  mask-image: linear-gradient(to right, black 93%, transparent);
}
.st-key-gallery_wall button p,
.st-key-gallery_wall button [data-testid="stIconMaterial"] { color: #f3e6c8; }
.wall-sign {
  display: inline-block; color: #3a2c0c; font: 600 12px/1 serif;
  letter-spacing: .18em; text-transform: uppercase; padding: 7px 14px;
  background: linear-gradient(#efe0ad, #c7ad62); border-radius: 3px;
  box-shadow: 0 3px 6px rgba(0, 0, 0, .45);
}
.frame {
  display: inline-block; padding: 12px; position: relative;
  box-shadow: 0 14px 22px rgba(0, 0, 0, .5), inset 0 0 0 2px rgba(0, 0, 0, .25);
}
.frame-gold {
  background: linear-gradient(135deg, #b88a2e, #f5de8e 35%, #a57c2c 60%, #e8c76c);
}
.frame-walnut { background: linear-gradient(135deg, #3b220f, #7b4a26 50%, #4a2c17); }
.frame-black { background: linear-gradient(135deg, #0d0d0d, #2b2b2b 50%, #111); }
.frame-white { background: linear-gradient(135deg, #e9e6df, #ffffff 50%, #d8d4cb); }
.frame-empty {
  background: transparent; box-shadow: none;
  border: 2px dashed rgba(243, 230, 200, .45);
}
.mat {
  background: #f7f3ea; padding: 12px;
  box-shadow: inset 0 0 8px rgba(0, 0, 0, .35);
}
.mat img {
  display: block; height: 150px; width: auto; max-width: 230px; object-fit: contain;
}
.mat-text {
  width: 120px; height: 150px; display: flex; align-items: center;
  justify-content: center; text-align: center; font: italic 13px serif; color: #6b6255;
}
.frame-empty .mat { background: transparent; box-shadow: none; }
.frame-empty .mat-text { color: rgba(243, 230, 200, .7); }
.plaque {
  margin: 12px auto 0; max-width: 210px; padding: 4px 8px; text-align: center;
  background: linear-gradient(#e9d9a6, #c7ad62); color: #3a2c0c; border-radius: 2px;
  font: 11px/1.3 serif; box-shadow: 0 2px 4px rgba(0, 0, 0, .45);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
</style>
"""
TABS = ["For you", "More like the last one", "Map of art"] + (
    ["Fashion for you"] if MIXED else []
)
SKELETON_CSS = """
<style>
@keyframes shimmer {
  from { background-position: -600px 0 }
  to { background-position: 600px 0 }
}
.skeleton {
  border-radius: 8px;
  background: linear-gradient(90deg, rgba(128,128,128,.10) 25%,
              rgba(128,128,128,.24) 40%, rgba(128,128,128,.10) 60%);
  background-size: 1200px 100%;
  animation: shimmer 1.3s infinite linear;
}
</style>
"""
SKELETON_CARD = (
    '<div class="skeleton" style="aspect-ratio: 4 / 5"></div>'
    '<div class="skeleton" style="height: 14px; width: 80%; margin: 12px 0 8px"></div>'
    '<div class="skeleton" style="height: 12px; width: 50%; margin-bottom: 28px"></div>'
)

st.set_page_config(page_title="Curate my wall", layout="wide")
st.markdown(SKELETON_CSS + WALL_CSS, unsafe_allow_html=True)


def unlocked() -> bool:
    """With APP_PASSWORD set, nothing reaches the API until the visitor enters it."""
    password = os.environ.get("APP_PASSWORD")
    if not password or st.session_state.get("unlocked"):
        return True
    st.title("Curate my wall")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered.encode(), password.encode()):
        st.session_state.unlocked = True
        st.rerun()
    if entered:
        st.error("Wrong password.")
    return False


if not unlocked():
    st.stop()

if not STATE.exists() and os.environ.get("ART_CATALOG_ID"):
    with st.spinner("Fetching the collection...", show_time=True):
        download(os.environ["ART_CATALOG_ID"])
if not STATE.exists():
    st.error("No catalog yet. Run `uv run python app/embed.py` first.")
    st.stop()
CATALOG_ID = json.loads(STATE.read_text())["catalog_id"]


@st.cache_resource
def get_client() -> UnboxAIClient:
    load_dotenv()
    return UnboxAIClient(market="us", default_catalog_id=CATALOG_ID)


class OverBudget(Exception):
    pass


@st.cache_resource
def call_budget() -> dict:
    """Shared by every session in this process, so it resets on restart."""
    return {"day": None, "calls": 0, "lock": threading.Lock()}


def spend() -> None:
    """Count one API call against the daily limit; cache hits don't get here."""
    budget = call_budget()
    with budget["lock"]:
        today = datetime.date.today()
        if budget["day"] != today:
            budget.update(day=today, calls=0)
        if budget["calls"] >= DAILY_CALL_LIMIT:
            raise OverBudget
        budget["calls"] += 1


@st.cache_data(show_spinner=False)
def recommend(
    history: tuple[tuple[str, str], ...], limit: int, fashion: bool = False
) -> list[dict]:
    spend()
    events = [EVENTS[kind](value) for kind, value in history]
    response = get_client().complete(
        history=events, limit=limit, filters=FASHION_ONLY if fashion else ART_ONLY
    )
    return [{"id": item.id, **item.data} for item in response.products.items]


@st.cache_data(show_spinner=False)
def similar(product_id: str, limit: int) -> list[dict]:
    spend()
    # The client's similar_products doesn't take filters; its resource method does.
    response = get_client().catalogs.get_similar_products(
        product_id, limit=limit, catalog_id=CATALOG_ID, filters=ART_ONLY,
        headers={"x-catalog-id": CATALOG_ID},
    )  # fmt: skip
    return [{"id": item.id, **item.data} for item in response.products.items]


@st.cache_data(show_spinner=False)
def map_space(catalog_id: str) -> pd.DataFrame:
    spend()
    points = load_points(get_client(), catalog_id)
    works = load_works()
    if MIXED:
        works = pd.concat([works, load_fashion(MIXED_CATALOG)], ignore_index=True)
    return points.merge(works, on="id", how="inner")


state = st.session_state
state.setdefault("history", [])
state.setdefault("wall", {})
state.setdefault("seen", {})
state.setdefault("map_selected", None)
state.setdefault("clicks", 0)


def remember(works: list[dict]) -> list[dict]:
    for work in works:
        state.seen[work["id"]] = work
    return works


def title(work: dict) -> str:
    return work.get("name") or "Untitled"


def record(kind: str, value: str) -> bool:
    """Add an event unless the session has used its clicks; Start over keeps them."""
    if state.clicks >= SESSION_CLICK_LIMIT:
        st.toast(
            "That's the limit for one visit to this demo. Thanks for exploring!",
            icon=":material/block:",
        )
        return False
    state.clicks += 1
    state.history.append((kind, value))
    return True


def look_closer(work: dict) -> None:
    remember([work])
    if record("view", work["id"]):
        st.toast(f"Looking closer at *{title(work)}*", icon=":material/visibility:")


def hang(work: dict) -> None:
    remember([work])
    if record("hang", work["id"]):
        state.wall[work["id"]] = work
        st.toast(f"Hung *{title(work)}* on your wall", icon=":material/star:")


def take_down(work: dict) -> None:
    if record("take_down", work["id"]):
        state.wall.pop(work["id"], None)
        st.toast(f"Took down *{title(work)}*", icon=":material/do_not_disturb_on:")


def search() -> None:
    query = state.query.strip()
    if query and record("search", query):
        st.toast(f'Searching for "{query}"', icon=":material/search:")
    state.query = ""


def reset() -> None:
    state.history.clear()
    state.wall.clear()
    state.map_selected = None


def select_on_map() -> None:
    points = state.art_map.selection.points
    if points and points[0].get("customdata"):
        state.map_selected = str(points[0]["customdata"][0])


def action_buttons(work: dict, key: str) -> None:
    left, right = st.columns(2)
    left.button(
        "Look closer", key=f"{key}-view-{work['id']}", icon=":material/visibility:",
        on_click=look_closer, args=(work,), width="stretch",
    )  # fmt: skip
    right.button(
        "Hang it", key=f"{key}-hang-{work['id']}", icon=":material/star:",
        type="primary", on_click=hang, args=(work,), width="stretch",
        disabled=work["id"] in state.wall,
    )  # fmt: skip


def show_grid(works: list[dict], key: str) -> None:
    columns = st.columns(GRID_COLUMNS)
    for i, work in enumerate(works):
        with columns[i % GRID_COLUMNS]:
            if work.get("image_url"):
                st.image(work["image_url"], width="stretch")
            st.markdown(f"**{title(work)}**")
            st.caption(work.get("brand") or "Unknown artist")
            action_buttons(work, key)


def show_skeleton(count: int) -> None:
    columns = st.columns(GRID_COLUMNS)
    for i in range(count):
        with columns[i % GRID_COLUMNS]:
            st.markdown(SKELETON_CARD, unsafe_allow_html=True)


def loading_grid(label: str, fetch, key: str) -> list[dict]:
    """Shimmering placeholder cards while `fetch` runs, then the real grid."""
    slot = st.empty()
    with slot.container():
        with st.spinner(label, show_time=True):
            show_skeleton(GRID_SIZE)
            try:
                works = fetch()
            except OverBudget:
                works = None
    with slot.container():
        if works is None:
            st.info(OVER_BUDGET, icon=":material/hourglass_empty:")
            return []
        show_grid(works, key)
    return works


def picks() -> list[dict]:
    works = remember(recommend(tuple(state.history), GRID_SIZE + len(state.wall)))
    return [w for w in works if w["id"] not in state.wall][:GRID_SIZE]


def framed(work: dict | None, position: int) -> str:
    """One picture frame; `None` draws an empty outline waiting to be filled."""
    tilt = TILTS[position % len(TILTS)]
    if work is None:
        return (
            f'<div class="frame frame-empty" style="transform: rotate({tilt}deg)">'
            '<div class="mat"><div class="mat-text">Hang a work here</div></div></div>'
        )
    style = FRAMES[int(work["id"]) % len(FRAMES)]
    name = html.escape(title(work))
    if work.get("image_url"):
        inner = f'<img src="{html.escape(work["image_url"])}" alt="{name}">'
    else:
        inner = f'<div class="mat-text">{name}</div>'
    artist = html.escape(work.get("brand") or "Unknown artist")
    return (
        f'<div class="frame frame-{style}" style="transform: rotate({tilt}deg)">'
        f'<div class="mat">{inner}</div></div>'
        f'<div class="plaque" title="{name} by {artist}">'
        f"<b>{name}</b><br>{artist}</div>"
    )


def show_wall() -> None:
    with st.container(key="gallery_wall"):
        count = len(state.wall)
        label = "Your wall" + (f" · {count} work{'s' * (count != 1)}" if count else "")
        st.markdown(f'<span class="wall-sign">{label}</span>', unsafe_allow_html=True)
        with st.container(
            key="gallery_row", horizontal=True, wrap=False, gap="large",
            vertical_alignment="bottom",
        ):  # fmt: skip
            works = list(reversed(state.wall.values()))
            if not works:
                for position in range(3):
                    st.markdown(framed(None, position), unsafe_allow_html=True)
            for position, work in enumerate(works):
                with st.container(width="content", horizontal_alignment="center"):
                    st.markdown(framed(work, position), unsafe_allow_html=True)
                    st.button(
                        "Take down", key=f"take-down-{work['id']}", type="tertiary",
                        icon=":material/do_not_disturb_on:", on_click=take_down,
                        args=(work,),
                    )  # fmt: skip


def show_trail() -> None:
    events = list(reversed(state.history))
    if not events:
        st.caption("Your searches, looks and hangs will appear here.")
    for kind, value in events[:TRAIL_LENGTH]:
        icon, color, label = TRAIL[kind]
        work = None if kind == "search" else state.seen.get(value, {})
        thumb, text = st.columns([1, 3], vertical_alignment="center")
        with thumb:
            if work and work.get("image_url"):
                st.image(work["image_url"], width="stretch")
            else:
                st.markdown(f"### :{color}[{icon}]")
        with text:
            st.markdown(f":{color}[{icon} **{label}**]")
            st.caption(f'"{value}"' if kind == "search" else title(work))
    if len(events) > TRAIL_LENGTH:
        st.caption(f"...and {len(events) - TRAIL_LENGTH} earlier.")


with st.sidebar:
    st.header("Taste trail")
    show_trail()
    st.button(
        "Start over", on_click=reset, width="stretch", icon=":material/restart_alt:"
    )

show_wall()

st.title("Curate my wall")
st.caption(
    "19,000 public-domain works from the Art Institute of Chicago. Every click is "
    "an event in your history; BehaviorGPT predicts what you'll want to see next."
)
st.text_input(
    "Search the collection",
    key="query",
    on_change=search,
    placeholder="stormy sea, japanese woodblock, portrait of a dog...",
)

tabs = st.tabs(TABS, key="tab", on_change="rerun")
for_you, more_like, space = tabs[:3]
fashion_tab = tabs[3] if MIXED else None

if for_you.open:
    with for_you:
        history = state.history
        if not history:
            st.subheader("Most loved in the collection")
        elif history[-1][0] == "search":
            st.subheader(f'"{history[-1][1]}", in your taste')
        else:
            st.subheader("Picked for you")
        loading_grid("Curating your wall...", picks, "for-you")

if more_like.open:
    with more_like:
        last = next(
            (v for k, v in reversed(state.history) if k in ("view", "hang")), None
        )
        if last is None:
            st.caption("Look closer at a work to see its nearest neighbours.")
        else:
            st.subheader(f"Nearest to {title(state.seen.get(last, {}))}")

            def neighbours() -> list[dict]:
                found = remember(similar(last, GRID_SIZE + 1))
                return [w for w in found if w["id"] != last][:GRID_SIZE]

            loading_grid("Finding neighbours...", neighbours, "similar")

if space.open:
    with space:
        st.caption(
            "Every artwork placed by BehaviorGPT's embedding: nearby points are works "
            "the model sees as alike. Scroll to zoom, drag to pan, click a point to "
            "inspect it. Your wall and your current picks light up."
        )
        controls = st.columns([2, 3])
        color_by = controls[0].segmented_control(
            "Colour by", COLOR_BY, default="Department", key="color_by"
        )
        term = controls[1].text_input(
            "Highlight on the map",
            key="map_term",
            placeholder="Monet, samurai, cats...",
        )
        try:
            with st.spinner("Mapping 19,000 works...", show_time=True):
                works = map_space(CATALOG_ID)
                picked = {w["id"] for w in picks()}
        except OverBudget:
            st.info(OVER_BUDGET, icon=":material/hourglass_empty:")
            st.stop()
        except (httpx.HTTPError, UnboxAIError) as exc:
            st.warning(f"The map isn't available right now: {exc}")
            st.stop()

        matches = set()
        if term.strip():
            needle = term.strip()
            hit = works["name"].str.contains(needle, case=False, regex=False) | works[
                "brand"
            ].str.contains(needle, case=False, regex=False)
            matches = set(works.loc[hit, "id"])
            st.caption(f'{len(matches)} works match "{needle}".')

        chart, detail = st.columns([3, 1])
        with chart:
            st.plotly_chart(
                figure(
                    works,
                    color_by or "Department",
                    wall=set(state.wall),
                    picked=picked,
                    matches=matches,
                    selected=state.map_selected,
                ),
                key="art_map",
                theme=None,
                on_select=select_on_map,
                selection_mode="points",
                config={"scrollZoom": True, "displaylogo": False},
            )
        with detail:
            row = works[works["id"] == state.map_selected]
            if row.empty:
                st.info(
                    "Click any point to see the artwork here.",
                    icon=":material/touch_app:",
                )
            else:
                work = row.iloc[0].to_dict()
                if work.get("image_url"):
                    st.image(work["image_url"], width="stretch")
                st.markdown(f"**{title(work)}**")
                st.caption(" · ".join(p for p in [work["brand"], work["date"]] if p))
                tags = [t for t in [work.get("department"), work.get("style")] if t]
                if tags:
                    st.caption(" / ".join(tags))
                if not work["id"].startswith("hm_"):
                    action_buttons(work, "map")

if fashion_tab is not None and fashion_tab.open:
    with fashion_tab:
        st.caption(
            "H&M pieces BehaviorGPT picks from your art history alone: the same "
            "clicks, filtered to fashion. Pilot data, for research use only."
        )
        if not state.history:
            st.subheader("Popular before you've looked at anything")
        else:
            st.subheader("What your art taste says you'd wear")

        def outfits() -> list[dict]:
            return recommend(tuple(state.history), GRID_SIZE, fashion=True)

        slot = st.empty()
        with slot.container():
            with st.spinner("Dressing you...", show_time=True):
                show_skeleton(GRID_SIZE)
                try:
                    items = outfits()
                except OverBudget:
                    items = []
        with slot.container():
            if not items:
                st.info(OVER_BUDGET, icon=":material/hourglass_empty:")
            columns = st.columns(GRID_COLUMNS)
            for i, item in enumerate(items):
                with columns[i % GRID_COLUMNS]:
                    if item.get("image_url"):
                        st.image(item["image_url"], width="stretch")
                    st.markdown(f"**{title(item)}**")
                    st.caption(
                        ", ".join((item.get("categories") or "").split(", ")[:3])
                    )
