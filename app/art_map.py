"""The catalog's embedding space as an interactive map.

BehaviorGPT's umap endpoint returns a finished Plotly page. The point coordinates
are pulled out of it once, cached next to the catalog, and redrawn here with the
museum's own metadata so the map can be recoloured, searched and clicked.
"""

import base64
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from behaviorgpt import UnboxAIClient

DATA = Path(__file__).parent / "data"
RAW = DATA / "artworks.jsonl"
CATALOG = DATA / "art_catalog.parquet"

COLOR_BY = ["Department", "Style", "Year"]
MAX_GROUPS = 12
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948",
    "#B07AA1", "#FF9DA7", "#9C755F", "#86BCB6", "#D37295", "#A0CBE8",
]  # fmt: skip
OTHER_COLOR = "rgba(150, 150, 160, 0.35)"
BACKGROUND = "#11131a"
WALL_COLOR = "#FFD23F"
PICKED_COLOR = "#00D1FF"
MATCH_COLOR = "#B6FF3B"
YEAR_RANGE = (1400, 1950)
HOVER = (
    "<b>%{customdata[1]}</b><br>%{customdata[2]}<br>"
    "<span style='color:#999'>%{customdata[3]}</span><extra></extra>"
)


def _array(values) -> np.ndarray:
    """Plotly stores long arrays as base64 typed arrays: {"dtype", "bdata"}."""
    if isinstance(values, dict):
        raw = base64.b64decode(values["bdata"])
        return np.frombuffer(raw, dtype=np.dtype(values["dtype"])).astype(float)
    return np.asarray(values, dtype=float)


def parse_umap_html(html: str) -> pd.DataFrame:
    start = html.index("[", html.index("Plotly.newPlot("))
    traces, _ = json.JSONDecoder().raw_decode(html, start)
    frames = [
        pd.DataFrame(
            {
                "id": [str(row[-1]) for row in trace["customdata"]],
                "x": _array(trace["x"]),
                "y": _array(trace["y"]),
            }
        )
        for trace in traces
    ]
    return pd.concat(frames, ignore_index=True)


def load_points(client: UnboxAIClient, catalog_id: str) -> pd.DataFrame:
    cache = DATA / f"umap_{catalog_id}.parquet"
    if cache.exists() and cache.stat().st_mtime > CATALOG.stat().st_mtime:
        return pd.read_parquet(cache)
    points = parse_umap_html(client.umap(catalog_id=catalog_id))
    points.to_parquet(cache)
    return points


def _ordinal(n: int) -> str:
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def _style_label(style) -> str | None:
    """'Japanese (culture or style)' -> 'Japanese', 'roman (ancient...)' -> 'Roman'."""
    if not isinstance(style, str):
        return None
    label = re.sub(r"\s*\(.*?\)", "", style).strip()
    return label[:1].upper() + label[1:] if label else None


def load_works() -> pd.DataFrame:
    """Catalog rows with the museum fields the map colours and labels by."""
    catalog = pd.read_parquet(CATALOG, columns=["id", "name", "brand", "image_url"])
    records = [json.loads(line) for line in RAW.open()]
    raw = pd.DataFrame(
        {
            "id": [str(r["id"]) for r in records],
            "department": [r.get("department_title") for r in records],
            "style": [r.get("style_title") for r in records],
            "date": [r.get("date_display") for r in records],
            "year": [r.get("date_start") for r in records],
        }
    )
    raw["style"] = raw["style"].map(_style_label)
    works = catalog.merge(raw, on="id", how="left")
    works["brand"] = works["brand"].fillna("Unknown artist")
    works["date"] = works["date"].fillna("")
    return works


def _points_trace(df, name, color, *, size=5, symbol="circle", line=None, legend=True):
    return go.Scattergl(
        x=df["x"],
        y=df["y"],
        mode="markers",
        name=name,
        showlegend=legend,
        customdata=df[["id", "name", "brand", "date"]].to_numpy(),
        hovertemplate=HOVER,
        marker={
            "size": size,
            "color": color,
            "symbol": symbol,
            "line": line or {"width": 0},
        },
    )


def figure(
    space: pd.DataFrame,
    color_by: str,
    *,
    wall: set[str] = frozenset(),
    picked: set[str] = frozenset(),
    matches: set[str] = frozenset(),
    selected: str | None = None,
) -> go.Figure:
    fig = go.Figure()

    if color_by == "Year":
        dated = space[space["year"].notna()]
        # Most of the collection sits between 1400 and 1950; stretching the scale to
        # the ancient works would paint all of that a single colour.
        low, high = YEAR_RANGE
        fig.add_trace(
            go.Scattergl(
                x=dated["x"],
                y=dated["y"],
                mode="markers",
                name="Dated works",
                showlegend=False,
                customdata=dated[["id", "name", "brand", "date"]].to_numpy(),
                hovertemplate=HOVER,
                marker={
                    "size": 5,
                    "color": dated["year"].clip(low, high),
                    "colorscale": "Turbo",
                    "opacity": 0.8,
                    "colorbar": {
                        "title": {"text": "Year"},
                        "thickness": 12,
                        "tickvals": [1400, 1500, 1600, 1700, 1800, 1900, 1950],
                        "ticktext": [
                            "≤1400",
                            "1500",
                            "1600",
                            "1700",
                            "1800",
                            "1900",
                            "1950+",
                        ],
                    },
                },
            )
        )
        undated = space[space["year"].isna()]
        if len(undated):
            fig.add_trace(_points_trace(undated, "Undated", OTHER_COLOR))
    else:
        column = color_by.lower()
        groups = space[column].fillna("Other")
        top = [g for g in groups.value_counts().index if g != "Other"][:MAX_GROUPS]
        for group, color in zip(top, PALETTE):
            fig.add_trace(_points_trace(space[groups == group], group, color))
        rest = space[~groups.isin(top)]
        if len(rest):
            fig.add_trace(_points_trace(rest, "Other", OTHER_COLOR))

    overlays = [
        (matches, "Search matches", MATCH_COLOR, 11, "circle-open", 2),
        (picked, "Picked for you", PICKED_COLOR, 15, "circle-open", 2.5),
        (wall, "On your wall", WALL_COLOR, 18, "star", 1),
    ]
    for ids, name, color, size, symbol, width in overlays:
        subset = space[space["id"].isin(ids)]
        if len(subset):
            line = {"width": width, "color": color if "open" in symbol else "white"}
            fig.add_trace(
                _points_trace(subset, name, color, size=size, symbol=symbol, line=line)
            )
    if selected is not None:
        subset = space[space["id"] == selected]
        fig.add_trace(
            _points_trace(
                subset, "Selected", "white", size=26, symbol="circle-open",
                line={"width": 3, "color": "white"}, legend=False,
            )
        )  # fmt: skip

    fig.update_layout(
        height=720,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        xaxis={"visible": False},
        yaxis={"visible": False, "scaleanchor": "x"},
        plot_bgcolor=BACKGROUND,
        paper_bgcolor=BACKGROUND,
        font={"color": "#d7dae0"},
        dragmode="pan",
        hovermode="closest",
        uirevision="art-map",
        legend={
            "x": 0.01,
            "y": 0.99,
            "xanchor": "left",
            "yanchor": "top",
            "bgcolor": "rgba(17, 19, 26, 0.8)",
            "itemsizing": "constant",
            "font": {"size": 11},
        },
        hoverlabel={"bgcolor": "#1c1f2a", "font": {"color": "white"}},
    )
    return fig
