import re
from datetime import date, timedelta
from pathlib import Path
import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

# ───────────────────────── CONFIG ─────────────────────────
st.set_page_config(page_title="Small Dams Water Dashboard", layout="wide")
st.title("💧 Water Levels & Status Dashboard of Small Dams")

# Put these files in the same folder as app.py.
# Existing Talagang file: dams_data_new.csv
# New Islamabad file: dams_data_new_Isb.csv / .xlsx / .xls
DISTRICT_SOURCES = {
    "Talagang": [Path("dams_data_new.csv")],
    "Islamabad": [
        Path("dams_data_new_Isb.csv"),
        Path("dams_data_new_Isb.xlsx"),
        Path("dams_data_new_Isb.xls"),
    ],
}

# Map messy spreadsheet headers → canonical names used in the app
RAW_TO_CANON = {
    "Height \n(ft)": "Height (ft)",
    "Completion Cost \n(million)": "Completion Cost (million)",
    "Gross Storage Capacity \n(Aft)": "Gross Storage Capacity (Aft)",
    "Live storage \n(Aft)": "Live storage (Aft)",
    "Live Storage \n(Aft)": "Live storage (Aft)",
    "C.C.A. \n(Acres)": "C.C.A. (Acres)",
    "Capacity of Channel \n(Cfs)": "Capacity of Channel (Cfs)",
    "Length of Canal \n(ft)": "Length of Canal (ft)",
    "DSL \n(ft)": "DSL (ft)",
    "NPL \n(ft)": "NPL (ft)",
    "HFL \n(ft)": "HFL (ft)",
    "Catchment Area \n(Sq. Km)": "Catchment Area (Sq. Km)",
    "Water Level (ft)": "Water_Level_ft",
    "Water Level_ft": "Water_Level_ft",
    "Water Level Ft": "Water_Level_ft",
    "Water_Level (ft)": "Water_Level_ft",
    "Dam": "Location",
    "Dam Name": "Location",
    "Name": "Location",
    "Lat": "Latitude",
    "Long": "Longitude",
    "Lng": "Longitude",
}

REQUIRED_COLS = [
    "Date",
    "Location",
    "Water_Level_ft",
    "Height (ft)",
    "Gross Storage Capacity (Aft)",
    "Live storage (Aft)",
    "C.C.A. (Acres)",
    "Capacity of Channel (Cfs)",
    "Length of Canal (ft)",
    "DSL (ft)",
    "NPL (ft)",
    "HFL (ft)",
    "River / Nullah",
    "Year of Completion",
    "Catchment Area (Sq. Km)",
    "Latitude",
    "Longitude",
]

OPTIONAL_COLS = [
    "Completion Cost (million)",
    "Remarks",
    "District",
]

NUM_COLS = [
    "Water_Level_ft",
    "DSL (ft)",
    "NPL (ft)",
    "HFL (ft)",
    "Height (ft)",
    "Gross Storage Capacity (Aft)",
    "Live storage (Aft)",
    "C.C.A. (Acres)",
    "Capacity of Channel (Cfs)",
    "Length of Canal (ft)",
    "Catchment Area (Sq. Km)",
    "Latitude",
    "Longitude",
    "Completion Cost (million)",
]

STATUS_COLORS = {
    "Below Dead Level": [255, 255, 0],
    "Low Storage": [220, 20, 60],
    "Medium Storage": [255, 140, 0],
    "High Storage": [0, 200, 0],
    "Spill Watch": [65, 105, 225],
    "Spill Anytime": [30, 144, 255],
    "Spilling": [0, 0, 255],
    "Unknown": [120, 120, 120],
}

STATUS_BG = {
    "Below Dead Level": "#FFF9C4",
    "Low Storage": "#FFCDD2",
    "Medium Storage": "#FFE0B2",
    "High Storage": "#C8E6C9",
    "Spill Watch": "#BBDEFB",
    "Spill Anytime": "#90CAF9",
    "Spilling": "#64B5F6",
    "Unknown": "#F5F5F5",
}

ALERT_STATUSES = {"Spill Watch", "Spill Anytime", "Spilling"}

ARROWS = {
    "Rising": "▲ Rising",
    "Falling": "▼ Falling",
    "Stable": "▬ Stable",
    "Rising then Falling": "▲▼ Rise then Fall",
    "Falling then Rising": "▼▲ Fall then Rise",
    "Variable": "≈ Variable",
    "No Data": "—",
}

# ──────────────────────── HELPERS ────────────────────────
def _clean_header(s: str) -> str:
    s = str(s).replace('"', "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_source_path(district: str) -> Path:
    """Return the first available source path for the selected district."""
    for p in DISTRICT_SOURCES[district]:
        if p.exists():
            return p
    # Default to the first option if nothing exists yet.
    return DISTRICT_SOURCES[district][0]


def ensure_file(path: Path):
    """Create a blank CSV only if the selected file does not exist."""
    if not path.exists():
        blank = pd.DataFrame(columns=REQUIRED_COLS + ["District"])
        blank.to_csv(path, index=False)


def read_table(path: Path) -> pd.DataFrame:
    ensure_file(path)
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(path)
    return pd.read_csv(path)


def write_table(df: pd.DataFrame, path: Path):
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        out.to_excel(path, index=False)
    else:
        out.to_csv(path, index=False)


@st.cache_data(ttl=30)
def load_data_for_district(district: str) -> pd.DataFrame:
    """
    Load the selected district source file, normalize headers, parse dates,
    make numeric columns numeric, and forward/back-fill static dam attributes.
    """
    source_path = resolve_source_path(district)
    df = read_table(source_path)

    # 1) Clean headers
    df.columns = [_clean_header(c) for c in df.columns]

    # 2) Map messy / alternative headers to canonical names
    for raw, canon in RAW_TO_CANON.items():
        raw_clean = _clean_header(raw)
        if raw in df.columns and canon not in df.columns:
            df.rename(columns={raw: canon}, inplace=True)
        elif raw_clean in df.columns and canon not in df.columns:
            df.rename(columns={raw_clean: canon}, inplace=True)

    # 3) Add optional district column for display/filtering
    if "District" not in df.columns:
        df["District"] = district
    else:
        df["District"] = df["District"].fillna(district).astype(str).str.strip()
        df.loc[df["District"].eq("") | df["District"].str.lower().eq("nan"), "District"] = district

    # 4) Validate required columns
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        st.error(
            f"{source_path.name} is missing required columns after normalization: {missing}"
        )
        st.stop()

    # 5) Parse dates and normalize text
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    df["Location"] = df["Location"].astype(str).str.strip()
    df = df[df["Location"].notna() & (df["Location"].astype(str).str.lower() != "nan")]

    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 6) Fill static columns per dam
    df = df.sort_values(["Location", "Date"], na_position="last")
    static_cols = [c for c in REQUIRED_COLS + OPTIONAL_COLS if c in df.columns and c not in ["Date", "Location", "Water_Level_ft"]]
    for col in static_cols:
        df[col] = df.groupby("Location")[col].transform(lambda s: s.ffill().bfill())

    df["Source_File"] = source_path.name
    df["Selected_District"] = district
    return df.reset_index(drop=True)


@st.cache_data(ttl=30)
def load_all_data() -> pd.DataFrame:
    frames = []
    for district in DISTRICT_SOURCES:
        try:
            frames.append(load_data_for_district(district))
        except Exception:
            # Keep app usable if one district file has a problem.
            pass
    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLS + ["District", "Selected_District", "Source_File"])
    return pd.concat(frames, ignore_index=True)


def compute_status_row(wl: float, npl: float, dsl: float) -> str:
    if pd.isna(wl) or pd.isna(npl) or pd.isna(dsl):
        return "Unknown"
    if wl < dsl:
        return "Below Dead Level"
    if wl > npl:
        return "Spilling"
    if abs(wl - npl) < 1e-9:
        return "Spill Anytime"
    if abs(wl - npl) <= 2:
        return "Spill Watch"
    if wl - dsl <= 5:
        return "Low Storage"
    diff = npl - wl
    if diff < 5:
        return "High Storage"
    return "Medium Storage"


def with_status(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        df["Status"] = []
        return df
    df["Status"] = df.apply(
        lambda r: compute_status_row(r.get("Water_Level_ft"), r.get("NPL (ft)"), r.get("DSL (ft)")),
        axis=1,
    )
    return df


def _pct_full_row(wl, npl, dsl):
    if pd.isna(wl) or pd.isna(npl) or pd.isna(dsl) or (npl == dsl):
        return pd.NA
    return (wl - dsl) / (npl - dsl)


def _trend_label_from_slice(s: pd.Series, dates: pd.Series, change_thresh: float = 0.5, recent_window_days: int = 10) -> str:
    s = s.dropna()
    if len(s) < 2:
        return "No Data"
    dates = pd.to_datetime(dates)
    if dates.isna().all():
        return "No Data"

    s = s.reset_index(drop=True)
    dates = dates.reset_index(drop=True)
    first = float(s.iloc[0])
    last = float(s.iloc[-1])
    overall_change = last - first
    max_val = float(s.max())
    min_val = float(s.min())

    if (max_val - min_val) < change_thresh:
        return "Stable"

    end_time = dates.iloc[-1]
    start_recent = end_time - pd.Timedelta(days=recent_window_days)
    mask_recent = dates >= start_recent

    if mask_recent.sum() >= 2:
        s_recent = s[mask_recent]
        d_recent = dates[mask_recent]
        days_recent = max((d_recent.iloc[-1] - d_recent.iloc[0]).days, 1)
        slope_recent = (float(s_recent.iloc[-1]) - float(s_recent.iloc[0])) / days_recent
    else:
        days_all = max((dates.iloc[-1] - dates.iloc[0]).days, 1)
        slope_recent = overall_change / days_all

    if overall_change >= change_thresh and slope_recent >= 0:
        return "Rising"
    if overall_change <= -change_thresh and slope_recent <= 0:
        return "Falling"
    if overall_change >= change_thresh and slope_recent < 0:
        return "Rising then Falling"
    if overall_change <= -change_thresh and slope_recent > 0:
        return "Falling then Rising"
    return "Variable"


def compute_trend_for_loc_asof(df_all: pd.DataFrame, loc: str, as_of: date, window: int = 7) -> str:
    mask = (df_all["Location"] == loc) & (df_all["Date"] <= as_of)
    hist = df_all.loc[mask].sort_values("Date")
    if hist.empty:
        return "No Data"
    start_date = as_of - timedelta(days=window - 1)
    hist = hist[hist["Date"] >= start_date]
    return _trend_label_from_slice(hist["Water_Level_ft"], hist["Date"])


def upsert_reading(df: pd.DataFrame, d: date, loc: str, wl: float, selected_district: str) -> pd.DataFrame:
    loc_norm = str(loc).strip()
    if loc_norm not in df["Location"].unique():
        st.error("Location not found in static data. Add one base row first.")
        st.stop()

    static_row = df[df["Location"] == loc_norm].sort_values("Date").iloc[-1].to_dict()
    new_row = {k: static_row.get(k, None) for k in df.columns}
    new_row["Date"] = d
    new_row["Water_Level_ft"] = wl
    new_row["District"] = selected_district
    new_row["Selected_District"] = selected_district

    m = (df["Location"] == loc_norm) & (df["Date"] == d)
    if m.any():
        df.loc[m, "Water_Level_ft"] = wl
    else:
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    return df.sort_values(["Date", "Location"]).reset_index(drop=True)


def save_df_for_district(df: pd.DataFrame, selected_district: str):
    path = resolve_source_path(selected_district)
    drop_cols = ["Source_File", "Selected_District"]
    out = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")
    write_table(out, path)
    st.cache_data.clear()


def _safe_float(x):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return float(x)
    except Exception:
        return None


def make_map(deck_df: pd.DataFrame):
    df_map = deck_df.copy()
    df_map["lat"] = df_map["Latitude"].apply(_safe_float)
    df_map["lon"] = df_map["Longitude"].apply(_safe_float)
    df_map = df_map[df_map["lat"].notnull() & df_map["lon"].notnull()]
    if df_map.empty:
        return None

    def _row_to_record(r):
        wl = _safe_float(r["Water_Level_ft"])
        npl = _safe_float(r["NPL (ft)"])
        dsl = _safe_float(r["DSL (ft)"])
        hfl = _safe_float(r.get("HFL (ft)"))
        status = str(r["Status"]) if r["Status"] is not None else "Unknown"
        is_alert = status in ALERT_STATUSES
        label = ("⚠ " if is_alert else "") + str(r.get("Location", ""))
        color = [30, 144, 255] if is_alert else STATUS_COLORS.get(status, [120, 120, 120])
        size = 30 if is_alert else 23
        return {
            "LocationLabel": label,
            "District": str(r.get("District", r.get("Selected_District", ""))),
            "Status": status,
            "WL_ft": round(wl, 2) if wl is not None else None,
            "NPL_ft": round(npl, 2) if npl is not None else None,
            "DSL_ft": round(dsl, 2) if dsl is not None else None,
            "HFL_ft": round(hfl, 2) if hfl is not None else None,
            "Date_str": str(r["Date"]),
            "Latitude": float(r["lat"]),
            "Longitude": float(r["lon"]),
            "color": color,
            "icon": "marker",
            "size": size,
        }

    records = [_row_to_record(r) for _, r in df_map.iterrows()]
    if not records:
        return None

    view_state = pdk.ViewState(
        latitude=sum(rec["Latitude"] for rec in records) / len(records),
        longitude=sum(rec["Longitude"] for rec in records) / len(records),
        zoom=7 if len(records) <= 10 else 6,
    )

    icon_url = "https://raw.githubusercontent.com/visgl/deck.gl-data/master/website/icon-atlas.png"
    icon_mapping = {
        "marker": {
            "x": 128,
            "y": 0,
            "width": 128,
            "height": 128,
            "anchorY": 128,
            "mask": True,
        }
    }

    layer = pdk.Layer(
        "IconLayer",
        data=records,
        get_icon="icon",
        get_size="size",
        size_scale=2,
        get_position="[Longitude, Latitude]",
        get_color="color",
        pickable=True,
        icon_atlas=icon_url,
        icon_mapping=icon_mapping,
    )

    tooltip = {
        "html": "<b>{LocationLabel}</b><br/>District: {District}<br/>Status: {Status}"
        "<br/>WL: {WL_ft} ft<br/>NPL: {NPL_ft} ft<br/>DSL: {DSL_ft} ft<br/>HFL: {HFL_ft} ft"
        "<br/>Date: {Date_str}",
        "style": {"backgroundColor": "white", "color": "black"},
    }
    return pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip)


def latest_per_dam(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.loc[df.groupby("Location")["Date"].transform("max") == df["Date"]].copy()


def style_status(val):
    bg = STATUS_BG.get(val, "#F5F5F5")
    return f"background-color: {bg}; color: #000000; font-weight: 600;"


def style_trend(val):
    if isinstance(val, str) and "▲" in val:
        color = "#006400"
    elif isinstance(val, str) and "▼" in val:
        color = "#b00020"
    else:
        color = "#555555"
    return f"color: {color}; font-weight: 600;"


# ────────────────────── DISTRICT SELECTOR ──────────────────────
with st.sidebar:
    st.header("🏞️ Select Dashboard Area")
    selected_district = st.radio(
        "District / Area",
        list(DISTRICT_SOURCES.keys()),
        index=0,
        horizontal=False,
        help="Talagang reads dams_data_new.csv. Islamabad reads dams_data_new_Isb.csv/xlsx/xls.",
    )
    source_path = resolve_source_path(selected_district)
    st.caption(f"Active data file: `{source_path.name}`")

# Load selected district only for editing and main display
df_raw = load_data_for_district(selected_district)
df = with_status(df_raw)

# ────────────────────── SIDEBAR: ADD/UPDATE ──────────────────────
with st.sidebar:
    st.divider()
    st.header("➕ Add / Update Daily Reading")

    if df.empty:
        st.warning("No dam records found in this district file. Add static rows in the CSV first.")
    else:
        sel_loc = st.selectbox("Dam", sorted(df["Location"].dropna().unique()))
        sel_date = st.date_input("Date", value=date.today(), key="entry_date")

        latest_val_series = df[df["Location"] == sel_loc].sort_values("Date")["Water_Level_ft"].dropna()
        default_wl = float(latest_val_series.iloc[-1]) if not latest_val_series.empty else 0.0
        wl = st.number_input("Water Level (ft)", value=default_wl, step=0.1, format="%.2f")

        if st.button("Save Reading", use_container_width=True):
            df_new = upsert_reading(df_raw, sel_date, sel_loc, wl, selected_district)
            save_df_for_district(df_new, selected_district)
            st.success(f"Saved {sel_loc} @ {sel_date} = {wl:.2f} ft in {selected_district}")

# ───────────────────── MAIN DASHBOARD ─────────────────────
st.subheader(f"📍 {selected_district} Dams")

if df.empty:
    st.info("No data available for the selected district.")
    st.stop()

# Filters
f1, f2, f3, f4 = st.columns([1.1, 1.2, 1, 1])
with f1:
    _dates = pd.to_datetime(df["Date"], errors="coerce")
    default_day = _dates.dropna().max().date() if not _dates.dropna().empty else date.today()
    show_date = st.date_input("Show date", value=default_day, key="show_date")
with f2:
    filt_locs = st.multiselect("Filter dams", sorted(df["Location"].dropna().unique()))
with f3:
    status_filter = st.multiselect("Filter status", sorted(df["Status"].dropna().unique()))
with f4:
    only_latest = st.checkbox("Quick: latest date for each dam", value=False)

view = df.copy()
if only_latest:
    view = latest_per_dam(view)
else:
    view = view[view["Date"] == show_date]

if filt_locs:
    view = view[view["Location"].isin(filt_locs)]
if status_filter:
    view = view[view["Status"].isin(status_filter)]

view = view.copy()
view["Frac_Full"] = view.apply(lambda r: _pct_full_row(r["Water_Level_ft"], r["NPL (ft)"], r["DSL (ft)"]), axis=1).astype("Float64")
view["Frac_Full_Clip"] = view["Frac_Full"].clip(lower=0, upper=1)
view["Storage_%"] = (view["Frac_Full_Clip"] * 100).round(1)
view["Distance_to_NPL_ft"] = (view["NPL (ft)"] - view["Water_Level_ft"]).round(2)
view["Distance_to_HFL_ft"] = (view["HFL (ft)"] - view["Water_Level_ft"]).round(2)
view["Trend"] = view.apply(lambda r: compute_trend_for_loc_asof(df, r["Location"], r["Date"], window=7), axis=1)
view["TrendDisp"] = view["Trend"].map(ARROWS).fillna("—")

# ───────── KPIs ─────────
st.markdown("### 🔍 Overview & Spill Alerts")
tab_overview, tab_spill, tab_capacity = st.tabs(["📊 Storage Overview", "⚠️ Spill Alerts", "🏗️ Capacity Summary"])

if not view.empty:
    max_val = view["Frac_Full_Clip"].max()
    min_val = view["Frac_Full_Clip"].min()
    max_names = ", ".join(sorted(view.loc[view["Frac_Full_Clip"] == max_val, "Location"].unique()))
    min_names = ", ".join(sorted(view.loc[view["Frac_Full_Clip"] == min_val, "Location"].unique()))
    alert_df = view[view["Status"].isin(ALERT_STATUSES)]
    below_dead_df = view[view["Status"] == "Below Dead Level"]
    below_dead_names = ", ".join(sorted(below_dead_df["Location"].unique()))

    with tab_overview:
        c1, c2, c3, c4, c5 = st.columns([1, 1.4, 1.8, 1.8, 1.8])
        c1.metric("Dams shown", view["Location"].nunique())
        c2.metric("Average storage", f"{view['Storage_%'].dropna().mean():.1f}%" if view["Storage_%"].notna().any() else "—")
        with c3:
            st.markdown("<span style='font-weight:600; color:#1f4e79;'>Max Storage Dam(s)</span>", unsafe_allow_html=True)
            st.markdown(f"<small style='color:#1f4e79;'>{max_names or '—'}</small>", unsafe_allow_html=True)
        with c4:
            st.markdown("<span style='font-weight:600; color:#7f0000;'>Lowest Storage Dam(s)</span>", unsafe_allow_html=True)
            st.markdown(f"<small style='color:#7f0000;'>{min_names or '—'}</small>", unsafe_allow_html=True)
        with c5:
            st.markdown("<span style='font-weight:600; color:#8b0000;'>Below Dead Level</span>", unsafe_allow_html=True)
            st.markdown(f"<small style='color:#8b0000;'>{below_dead_names or 'None'}</small>", unsafe_allow_html=True)

    with tab_spill:
        c1, c2, c3 = st.columns([1, 1, 3])
        c1.metric("Alert dams", int(alert_df["Location"].nunique()))
        c2.metric("Below DSL", int(below_dead_df["Location"].nunique()))
        with c3:
            if alert_df.empty:
                st.success("No dams currently in Spill Watch / Spill Anytime / Spilling.")
            else:
                st.error("⚠ " + ", ".join(sorted(alert_df["Location"].unique())))
                st.dataframe(
                    alert_df[["Location", "Date", "Water_Level_ft", "NPL (ft)", "Distance_to_NPL_ft", "Status", "TrendDisp"]]
                    .rename(columns={"Location": "Dam", "TrendDisp": "Trend"})
                    .sort_values(["Status", "Dam"]),
                    use_container_width=True,
                    hide_index=True,
                )

    with tab_capacity:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total gross storage", f"{view['Gross Storage Capacity (Aft)'].dropna().sum():,.0f} Aft")
        c2.metric("Total live storage", f"{view['Live storage (Aft)'].dropna().sum():,.0f} Aft")
        c3.metric("Total C.C.A.", f"{view['C.C.A. (Acres)'].dropna().sum():,.0f} acres")
        c4.metric("Total catchment area", f"{view['Catchment Area (Sq. Km)'].dropna().sum():,.1f} sq.km")
else:
    st.info("No records match the selected filters.")

st.caption(
    "⚠ **Spill Watch**: water level within about 2 ft below NPL; "
    "**Spill Anytime**: water level is at NPL; "
    "**Spilling**: water level is above NPL."
)

# ───────── Data Table ─────────
st.markdown("### 📋 Data")
st.caption("Trend is based on the last 7 days of water levels for each dam.")

cols_show = [
    "District",
    "Date",
    "Location",
    "Water_Level_ft",
    "DSL (ft)",
    "NPL (ft)",
    "HFL (ft)",
    "Storage_%",
    "Distance_to_NPL_ft",
    "Status",
    "TrendDisp",
]
cols_show = [c for c in cols_show if c in view.columns]

df_display = (
    view[cols_show]
    .rename(columns={
        "Location": "Dam",
        "TrendDisp": "Trend",
        "Distance_to_NPL_ft": "NPL Gap (ft)",
    })
    .sort_values(["Dam"])
    .reset_index(drop=True)
)

for col in ["Water_Level_ft", "DSL (ft)", "NPL (ft)", "HFL (ft)", "Storage_%", "NPL Gap (ft)"]:
    if col in df_display.columns:
        df_display[col] = df_display[col].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "")

try:
    styled = df_display.style.applymap(style_status, subset=["Status"]).applymap(style_trend, subset=["Trend"])
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    st.dataframe(df_display, use_container_width=True, hide_index=True)

# Download current view
csv_download = view.drop(columns=[c for c in ["Frac_Full", "Frac_Full_Clip"] if c in view.columns], errors="ignore").to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Download current filtered data as CSV",
    data=csv_download,
    file_name=f"{selected_district.lower()}_dams_filtered.csv",
    mime="text/csv",
)

# ───── Status Distribution ─────
st.markdown("### 🧭 Status Distribution")
if not view.empty:
    status_counts = view.groupby("Status", as_index=False)["Location"].nunique().rename(columns={"Location": "Number of Dams"})
    fig_status = px.bar(status_counts, x="Status", y="Number of Dams", text="Number of Dams")
    fig_status.update_layout(xaxis_title="Status", yaxis_title="Number of Dams")
    st.plotly_chart(fig_status, use_container_width=True)
else:
    st.info("No status distribution available for selected filters.")

# ───── Trend Chart with Custom Date Range ─────
st.markdown("### 📈 Water Level Trend (Custom Range)")
eligible = sorted(view["Location"].unique()) if not view.empty else sorted(df["Location"].unique())
if eligible:
    dam_for_trend = st.selectbox("Select a dam for trend", eligible)
    dam_df = df[df["Location"] == dam_for_trend].dropna(subset=["Date"]).sort_values("Date")

    if dam_df.empty:
        st.info("No readings available for the selected dam.")
    else:
        dam_min_date = dam_df["Date"].min()
        dam_max_date = dam_df["Date"].max()
        default_start = max(dam_min_date, dam_max_date - timedelta(days=6))

        date_range = st.date_input(
            "Select date range",
            value=(default_start, dam_max_date),
            min_value=dam_min_date,
            max_value=dam_max_date,
            key="trend_range",
        )

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date, end_date = dam_min_date, dam_max_date

        if start_date > end_date:
            st.error("Start date must be on or before end date.")
        else:
            win = dam_df[(dam_df["Date"] >= start_date) & (dam_df["Date"] <= end_date)].sort_values("Date")
            if win.empty:
                st.info("No readings available in the selected date range.")
            else:
                trend_now = _trend_label_from_slice(win["Water_Level_ft"], win["Date"])
                npl_series = dam_df["NPL (ft)"].dropna()
                dsl_series = dam_df["DSL (ft)"].dropna()
                hfl_series = dam_df["HFL (ft)"].dropna()
                npl_val = npl_series.iloc[-1] if not npl_series.empty else None
                dsl_val = dsl_series.iloc[-1] if not dsl_series.empty else None
                hfl_val = hfl_series.iloc[-1] if not hfl_series.empty else None

                fig = px.line(win, x="Date", y="Water_Level_ft", markers=True, title=f"{dam_for_trend} • {start_date} to {end_date}")
                if npl_val is not None:
                    fig.add_hline(y=npl_val, line_dash="dash", line_color="green", annotation_text="NPL", annotation_position="top left")
                if dsl_val is not None:
                    fig.add_hline(y=dsl_val, line_dash="dot", line_color="red", annotation_text="DSL", annotation_position="bottom left")
                if hfl_val is not None:
                    fig.add_hline(y=hfl_val, line_dash="dashdot", line_color="blue", annotation_text="HFL", annotation_position="top right")
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Trend over selected range: {ARROWS.get(trend_now, trend_now)}")

                # Additional storage percentage trend
                win2 = win.copy()
                win2["Storage_%"] = win2.apply(lambda r: _pct_full_row(r["Water_Level_ft"], r["NPL (ft)"], r["DSL (ft)"]), axis=1).astype("Float64") * 100
                if win2["Storage_%"].notna().any():
                    fig2 = px.line(win2, x="Date", y="Storage_%", markers=True, title=f"{dam_for_trend} • Storage Percentage")
                    fig2.update_layout(yaxis_title="Storage between DSL and NPL (%)")
                    st.plotly_chart(fig2, use_container_width=True)

# ───── Compare Dams ─────
st.markdown("### 📊 Compare Dams")
compare_options = sorted(df["Location"].dropna().unique())
compare_dams = st.multiselect("Select dams to compare", compare_options, default=compare_options[: min(3, len(compare_options))])
if compare_dams:
    comp = df[df["Location"].isin(compare_dams)].sort_values("Date")
    fig_comp = px.line(comp, x="Date", y="Water_Level_ft", color="Location", markers=True, title="Water Level Comparison")
    st.plotly_chart(fig_comp, use_container_width=True)
else:
    st.info("Select one or more dams to compare water levels.")

# ───── Map ─────
st.markdown("### 🗺️ Dams on Map (colored by Status)")
deck = make_map(view)
if deck is not None:
    try:
        st.pydeck_chart(deck)
        st.markdown(
            "🟡 Below Dead Level &nbsp;&nbsp;&nbsp; "
            "🟥 Low Storage &nbsp;&nbsp;&nbsp; "
            "🟧 Medium Storage &nbsp;&nbsp;&nbsp; "
            "🟩 High Storage &nbsp;&nbsp;&nbsp; "
            "🔵 Spill Watch / Anytime / Spilling",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.error(f"Map rendering failed: {e}")
else:
    st.info("No valid coordinates available to display the map for current filter.")

# ───── Details ─────
st.markdown("### 🏗️ Dam Details")
c1, c2 = st.columns([1, 2])
with c1:
    pick = st.selectbox("Select a dam", sorted(df["Location"].unique()), key="details_pick")
with c2:
    details_df = df[df["Location"] == pick].sort_values("Date")
    if not details_df.empty:
        details = details_df.iloc[-1]
        details_dict = {
            "District": details.get("District", selected_district),
            "Height (ft)": details.get("Height (ft)"),
            "Gross Storage (Aft)": details.get("Gross Storage Capacity (Aft)"),
            "Live Storage (Aft)": details.get("Live storage (Aft)"),
            "C.C.A. (Acres)": details.get("C.C.A. (Acres)"),
            "Capacity of Channel (Cfs)": details.get("Capacity of Channel (Cfs)"),
            "Length of Canal (ft)": details.get("Length of Canal (ft)"),
            "DSL (ft)": details.get("DSL (ft)"),
            "NPL (ft)": details.get("NPL (ft)"),
            "HFL (ft)": details.get("HFL (ft)"),
            "River / Nullah": details.get("River / Nullah"),
            "Year of Completion": details.get("Year of Completion"),
            "Catchment Area (Sq. Km)": details.get("Catchment Area (Sq. Km)"),
            "Latitude": details.get("Latitude"),
            "Longitude": details.get("Longitude"),
            "Latest WL (ft)": details.get("Water_Level_ft"),
            "Status": details.get("Status"),
            "Source File": details.get("Source_File"),
        }
        if "Completion Cost (million)" in details.index:
            details_dict["Completion Cost (million)"] = details.get("Completion Cost (million)")
        st.write(details_dict)
    else:
        st.info("No details available.")

# ───── All-district summary without editing ─────
with st.expander("🌐 Optional: View combined latest summary for Talagang + Islamabad"):
    all_df = with_status(load_all_data())
    if all_df.empty:
        st.info("Combined data is not available.")
    else:
        all_latest = latest_per_dam(all_df)
        all_latest["Storage_%"] = all_latest.apply(lambda r: _pct_full_row(r["Water_Level_ft"], r["NPL (ft)"], r["DSL (ft)"]), axis=1).astype("Float64").clip(0, 1) * 100
        st.dataframe(
            all_latest[["Selected_District", "Location", "Date", "Water_Level_ft", "DSL (ft)", "NPL (ft)", "Storage_%", "Status", "Source_File"]]
            .rename(columns={"Selected_District": "District", "Location": "Dam"})
            .sort_values(["District", "Dam"]),
            use_container_width=True,
            hide_index=True,
        )
        fig_all = px.bar(
            all_latest.groupby(["Selected_District", "Status"], as_index=False)["Location"].nunique().rename(columns={"Location": "Number of Dams"}),
            x="Selected_District",
            y="Number of Dams",
            color="Status",
            barmode="group",
            title="Latest Status by District",
        )
        st.plotly_chart(fig_all, use_container_width=True)

st.caption("Developer: **Junaid Ahmad, PhD**")
