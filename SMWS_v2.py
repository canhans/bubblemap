# smartglass_wm_app.py
# Run with: streamlit run smartglass_wm_app.py

import math
import hashlib
import pandas as pd
import plotly.express as px
import streamlit as st

# ---------- 1) Geo dictionaries (countries in WM.xlsx) ----------

COUNTRY_TO_LATLON = {
    "Canada":      (56.1304,  -106.3468),
    "China":       (35.8617,   104.1954),
    "France":      (46.2276,     2.2137),
    "Germany":     (51.1657,    10.4515),
    "Ireland":     (53.1424,    -7.6921),
    "Israel":      (31.0461,    34.8516),
    "Italy":       (41.8719,    12.5674),
    "Japan":       (36.2048,   138.2529),
    "Netherlands": (52.1326,     5.2913),
    "Norveç":      (60.4720,     8.4689),   # Norway (Turkish name)
    "S.Korea":     (35.9078,   127.7669),
    "South Korea": (35.9078,   127.7669),
    "Spain":       (40.4637,    -3.7492),
    "Sweden":      (60.1282,    18.6435),
    "Taiwan":      (23.6978,   120.9605),
    "UK":          (55.0000,    -3.0000),
    "United Kingdom": (55.0000, -3.0000),
    "USA":         (37.0902,   -95.7129),
    "United States": (37.0902, -95.7129),
}

COUNTRY_TO_CONTINENT = {
    "Canada": "Americas",
    "USA": "Americas",
    "United States": "Americas",

    "France": "Europe",
    "Germany": "Europe",
    "Ireland": "Europe",
    "Italy": "Europe",
    "Netherlands": "Europe",
    "Norveç": "Europe",
    "Spain": "Europe",
    "Sweden": "Europe",
    "UK": "Europe",
    "United Kingdom": "Europe",

    "China": "Asia",
    "Japan": "Asia",
    "S.Korea": "Asia",
    "South Korea": "Asia",
    "Taiwan": "Asia",
    "Israel": "Asia",
}

# Her teknoloji için ülke merkezine ana offset (aynı teknoloji aynı tarafa toplanıyor)
TECH_OFFSETS = {
    "PDLC": (0.6, 0.5),
    "SPD":  (-0.6, 0.5),
    "EC":   (0.5, -0.6),
    "Elektroforetik cihaz": (-0.5, -0.6),
}

# Teknoloji renkleri:
TECH_COLORS = {
    "SPD": "blue",                     # mavi
    "EC": "green",                     # yeşil
    "PDLC": "red",                     # kırmızı
    "Elektroforetik cihaz": "orange",  # turuncu
}

# ---------- 2) Parsing helpers ----------

def parse_size(value):
    """Parse WM.xlsx 'Size' formats into an approximate employee count (float)."""
    if pd.isna(value):
        return None

    # Already numeric
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return float(value)

    s = str(value).strip()
    # Remove thousand separators like '55.000+'
    s_clean = s.replace('.', '').replace(' ', '')
    # Treat ';' as range separator ('50;100' -> '50-100')
    s_clean = s_clean.replace(';', '-')

    # Plus sign means 'or more'
    plus = s_clean.endswith('+')
    if plus:
        s_clean = s_clean[:-1]

    # Range case: e.g. '50-100'
    if '-' in s_clean:
        parts = [p for p in s_clean.split('-') if p]
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                pass

        if len(nums) >= 2:
            return sum(nums[:2]) / 2.0  # average of the band
        elif len(nums) == 1:
            return nums[0]
        else:
            return None

    # Single number: e.g. '6000' or '6000+'
    try:
        num = float(s_clean)
        if plus:
            return num * 1.2  # a bit above the threshold
        return num
    except ValueError:
        return None


def parse_revenue_musd(value):
    """Parse 'Bildirilen ya da tahmini gelir' into M USD (float)."""
    if pd.isna(value):
        return None

    s = str(value).strip()
    if s.lower().startswith("bilinmiyor"):
        return None

    # Remove 'USD', normalize decimal comma
    s = s.replace("USD", "").strip()
    s = s.replace(",", ".")

    # '< 1 M USD' -> assume 0.5 M
    if s.startswith("<"):
        return 0.5

    unit = None
    if " B" in s:
        unit = "B"
    elif " M" in s:
        unit = "M"

    import re as _re
    match_nums = _re.findall(r"[\d\.]+", s)
    if not match_nums:
        return None

    num = float(match_nums[0])

    if unit == "B":
        # convert B USD -> M USD
        return num * 1000.0
    else:
        # default M
        return num


def deterministic_jitter(key: str, scale: float) -> float:
    """
    Deterministic jitter in [-scale, +scale] based on a hash of the key.
    Böylece her çalıştırmada aynı şirket aynı yerde kalır.
    """
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    val_int = int(h[:8], 16)
    val = val_int / 0xFFFFFFFF  # 0-1
    return (val * 2.0 - 1.0) * scale  # -scale..+scale


def load_wm_data(path: str = "WM.xlsx") -> pd.DataFrame:
    """Load WM.xlsx and prepare dataframe with geo + numeric + scattered coordinates."""
    raw = pd.read_excel(path)

    df = raw.rename(
        columns={
            "Company Name": "name",
            "Country": "country",
            "Role": "role",
            "Product": "product",
            "Size": "size_raw",
            "Bildirilen ya da tahmini gelir": "revenue_raw",
        }
    )

    # Numeric conversions
    df["size_num"] = df["size_raw"].apply(parse_size)
    df["revenue_musd"] = df["revenue_raw"].apply(parse_revenue_musd)

    # Geo fields (ülke merkezleri)
    df["base_lat"] = df["country"].map(lambda c: COUNTRY_TO_LATLON.get(c, (None, None))[0])
    df["base_lon"] = df["country"].map(lambda c: COUNTRY_TO_LATLON.get(c, (None, None))[1])
    df["continent"] = df["country"].map(lambda c: COUNTRY_TO_CONTINENT.get(c, "Other"))

    # Temel filtremiz: koordinat + size varsa
    df = df.dropna(subset=["base_lat", "base_lon", "size_num"])

    # Scatter koordinatlarını hesapla (daha fazla saçılma)
    lat_list = []
    lon_list = []

    for _, row in df.iterrows():
        base_lat = row["base_lat"]
        base_lon = row["base_lon"]
        product = str(row["product"]) if not pd.isna(row["product"]) else "OTHER"

        # Teknolojiye bağlı ana offset
        dlat_tech, dlon_tech = TECH_OFFSETS.get(product, (0.0, 0.0))

        # Şirket adına göre deterministic jitter (daha büyük ölçek)
        name = str(row["name"])
        jitter_lat = deterministic_jitter(name + "_lat", scale=0.6)   # ±0.6°
        jitter_lon = deterministic_jitter(name + "_lon", scale=0.9)   # ±0.9°

        lat_scatter = base_lat + dlat_tech + jitter_lat
        lon_scatter = base_lon + dlon_tech + jitter_lon

        # Çok uçlara kaçmasın diye kaba bir clamp
        lat_scatter = max(min(lat_scatter, 85.0), -85.0)

        lat_list.append(lat_scatter)
        lon_list.append(lon_scatter)

    df["lat"] = lat_list
    df["lon"] = lon_list

    return df

# ---------- 3) Streamlit UI ----------

st.set_page_config(
    page_title="Global Smart Glass Landscape",
    layout="wide",
)

st.title("🌍 Global Smart Glass Companies – WM.xlsx")

st.write(
    "Bu arayüz, **WM.xlsx** içindeki akıllı cam şirketlerini harita üzerinde gösterir. "
    "Balon boyutu şirket büyüklüğünü (çalışan sayısı bandı) ya da geliri temsil eder. "
    "Şirketler, bulundukları ülke ve teknolojiye göre kümelenmiş ve deprem haritası "
    "gibi hafifçe saçılmıştır."
)

# Load data
try:
    df = load_wm_data("WM.xlsx")
except FileNotFoundError:
    st.error("WM.xlsx dosyası bu klasörde bulunamadı. Lütfen .py dosyasıyla aynı klasöre koy.")
    st.stop()
except Exception as e:
    st.error(f"Excel okunurken hata oluştu: {e}")
    st.stop()

# ---------- Sidebar filters ----------

st.sidebar.header("Filtreler")

# Technology (Product)
techs = sorted(df["product"].dropna().unique())
selected_techs = st.sidebar.multiselect(
    "Teknoloji (Product)",
    techs,
    default=techs,
)

# Region
regions = ["Global", "Europe", "Asia", "Americas", "Other"]
selected_region = st.sidebar.selectbox("Bölge (continent)", regions, index=0)

# Bubble size metric
metric = st.sidebar.radio(
    "Balon boyutu metriği",
    ["Company size (employees)", "Revenue (M USD)"],
    index=0,
)

# Size slider
min_size = int(df["size_num"].min())
max_size = int(df["size_num"].max())
size_range = st.sidebar.slider(
    "Çalışan bandı (yaklaşık)",
    min_value=min_size,
    max_value=max_size,
    value=(min_size, max_size),
)

# Revenue slider (if any revenue data)
if df["revenue_musd"].notna().any():
    min_rev = float(df["revenue_musd"].dropna().min())
    max_rev = float(df["revenue_musd"].dropna().max())
else:
    min_rev, max_rev = 0.0, 0.0

if max_rev > 0:
    rev_range = st.sidebar.slider(
        "Gelir aralığı (M USD)",
        min_value=float(round(min_rev, 1)),
        max_value=float(round(max_rev, 1)),
        value=(float(round(min_rev, 1)), float(round(max_rev, 1))),
    )
else:
    rev_range = (0.0, 0.0)

# ---------- Filtering ----------

mask = df["product"].isin(selected_techs)
mask &= df["size_num"].between(size_range[0], size_range[1])

if selected_region != "Global":
    mask &= df["continent"] == selected_region

if metric == "Revenue (M USD)" and max_rev > 0:
    mask &= df["revenue_musd"].between(rev_range[0], rev_range[1])

df_filt = df[mask].copy()

# ---------- Layout: map left, table right ----------

col_map, col_table = st.columns([2.2, 1])

with col_map:
    if df_filt.empty:
        st.warning("Seçili filtrelerle eşleşen şirket yok.")
    else:
        st.subheader("Akıllı cam şirket haritası")

        # --- Bubble size hesapla ---
        if metric == "Company size (employees)":
            df_filt["bubble_size"] = df_filt["size_num"].apply(
                lambda x: math.sqrt(x) if pd.notna(x) and x > 0 else None
            )
        else:
            df_filt["bubble_size"] = df_filt["revenue_musd"].apply(
                lambda x: math.sqrt(x) if pd.notna(x) and x > 0 else None
            )

        # Minimum balon boyutunu biz verelim (küçükleri büyütmek için)
        df_filt["bubble_size_plot"] = df_filt["bubble_size"].clip(lower=3)

        fig = px.scatter_geo(
            df_filt,
            lat="lat",
            lon="lon",
            size="bubble_size_plot",
            color="product",
            hover_name="name",
            hover_data={
                "country": True,
                "role": True,
                "size_raw": True,
                "size_num": True,
                "revenue_raw": True,
                "revenue_musd": True,
                "continent": True,
            },
            projection="natural earth",
            size_max=80,  # balonları büyüt
            color_discrete_map=TECH_COLORS,
        )

        # Kartografik görünümü iyileştir
        fig.update_geos(
            showland=True,
            landcolor="rgb(243, 243, 243)",
            showocean=True,
            oceancolor="rgb(220, 230, 250)",
            showcountries=True,
            countrycolor="rgb(180, 180, 180)",
            showcoastlines=True,
            coastlinecolor="rgb(150, 150, 150)",
        )

        # Zoom by region
        if selected_region == "Europe":
            fig.update_geos(lonaxis_range=[-25, 60], lataxis_range=[30, 72])
        elif selected_region == "Asia":
            fig.update_geos(lonaxis_range=[40, 150], lataxis_range=[-10, 70])
        elif selected_region == "Americas":
            fig.update_geos(lonaxis_range=[-170, -30], lataxis_range=[-60, 75])

        fig.update_layout(
            margin=dict(l=0, r=0, t=0, b=0),
            legend_title_text="Teknoloji (Product)",
        )

        st.plotly_chart(fig, use_container_width=True)

with col_table:
    st.subheader("Şirket listesi")
    if df_filt.empty:
        st.info("Bu bölge / filtre kombinasyonu için şirket yok.")
    else:
        st.dataframe(
            df_filt[
                [
                    "name",
                    "country",
                    "continent",
                    "product",
                    "role",
                    "size_raw",
                    "revenue_raw",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )
