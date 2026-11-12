
"""
Southern California NFDRS4 Dashboard V2.1

Run:
    python visualize_interactive_bi.py

Reads:
    live/nfdrs_results/*_indexes.csv
    live/nfdrs_forecast/*_forecast_indexes.csv

Creates:
    nfdrs_dashboard.html
"""

from pathlib import Path
import subprocess
import html

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent

HISTORY_DIR = ROOT / "live" / "nfdrs_results"
FORECAST_DIR = ROOT / "live" / "nfdrs_forecast"
OUTPUT_FILE = ROOT / "nfdrs_dashboard.html"

LOCATIONS = {
    "Los Angeles": "los_angeles",
    "Ventura": "ventura",
    "Riverside": "riverside",
    "San Bernardino": "san_bernardino",
    "Santa Ana": "santa_ana",
    "San Diego": "san_diego",
}

INDEX_LABELS = {
    "BI": "Burning Index",
    "ERC": "Energy Release Component",
    "SC": "Spread Component",
    "IC": "Ignition Component",
    "GSI": "Growing Season Index",
    "KBDI": "Keetch-Byram Drought Index",
}

COLORS = {
    "Los Angeles": "#2563eb",
    "Ventura": "#0d9488",
    "Riverside": "#ea580c",
    "San Bernardino": "#9333ea",
    "Santa Ana": "#db2777",
    "San Diego": "#64748b",
}

BI_LEVELS = [
    ("Low", 0, 38, "#65bd77"),
    ("Moderate", 38, 48, "#f5db63"),
    ("High", 48, 111, "#f7a34e"),
    ("Very High", 111, 162, "#e86c5b"),
    ("Extreme", 162, None, "#a94d97"),
]


def read_indexes(path, location, period):
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing NFDRS4 output: {path}"
        )

    df = pd.read_csv(path)

    required = ["DateTime", *INDEX_LABELS.keys()]
    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"{path.name}: missing columns {missing}"
        )

    df["DateTime"] = pd.to_datetime(
        df["DateTime"],
        utc=True,
        errors="coerce"
    )

    df = df.dropna(subset=["DateTime"]).copy()

    for col in INDEX_LABELS:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["location"] = location
    df["period"] = period

    return (
        df.drop_duplicates(
            subset=["DateTime"],
            keep="last"
        )
        .sort_values("DateTime")
        .reset_index(drop=True)
    )


def load_data():
    frames = []

    for location, slug in LOCATIONS.items():
        history = read_indexes(
            HISTORY_DIR / f"{slug}_indexes.csv",
            location,
            "Historical"
        )

        forecast = read_indexes(
            FORECAST_DIR /
            f"{slug}_forecast_indexes.csv",
            location,
            "Forecast"
        )

        if history.empty or forecast.empty:
            raise ValueError(
                f"No usable records for {location}"
            )

        if (
            forecast["DateTime"].min()
            <= history["DateTime"].max()
        ):
            raise ValueError(
                f"Historical/forecast overlap: {location}"
            )

        frames.extend([history, forecast])

        print(
            f"{location}: "
            f"{len(history)} historical, "
            f"{len(forecast)} forecast"
        )

    return pd.concat(
        frames,
        ignore_index=True
    )


def bi_level(value):
    if pd.isna(value):
        return "Unknown"

    for name, low, high, color in BI_LEVELS:
        if value >= low and (
            high is None or value < high
        ):
            return name

    return "Unknown"


def make_trend_chart(df, forecast_only=False):
    fig = go.Figure()
    metadata = []

    for location in LOCATIONS:
        city = df[
            df["location"] == location
        ]

        for index in INDEX_LABELS:
            for period in ["Historical", "Forecast"]:
                subset = city[
                    city["period"] == period
                ]

                if forecast_only and period == "Historical":
                    last_history = subset["DateTime"].max()
                    subset = subset[
                        subset["DateTime"]
                        >= last_history - pd.Timedelta(hours=24)
                    ]

                default = (
                    location == "Los Angeles"
                    and index == "BI"
                )

                fig.add_trace(
                    go.Scatter(
                        x=subset["DateTime"],
                        y=subset[index],
                        mode="lines",
                        name=period,
                        visible=default,
                        line=dict(
                            color=COLORS[location],
                            width=2.5,
                            dash=(
                                "dash"
                                if period == "Forecast"
                                else "solid"
                            ),
                        ),
                        hovertemplate=(
                            "%{x|%b %d, %Y %H:%M} UTC"
                            "<br>Value: %{y:.2f}"
                            f"<br>{period}"
                            "<extra></extra>"
                        ),
                    )
                )

                metadata.append(
                    (location, index, period)
                )

    buttons = []

    for location in LOCATIONS:
        for index, label in INDEX_LABELS.items():
            visible = [
                city == location
                and metric == index
                for city, metric, period in metadata
            ]

            buttons.append(
                dict(
                    label=f"{location} | {index}",
                    method="update",
                    args=[
                        {"visible": visible},
                        {
                            "title.text": (
                                f"{location} — {label}"
                            ),
                            "yaxis.title.text": label,
                            "yaxis.autorange": True,
                        },
                    ],
                )
            )

    fig.update_layout(
        title=dict(
            text="Los Angeles — Burning Index",
            x=0.02,
            y=0.98,
            xanchor="left",
        ),
        template="plotly_white",
        height=680 if not forecast_only else 560,
        margin=dict(
            t=190 if not forecast_only else 160,
            l=75,
            r=45,
            b=75,
        ),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            x=0,
            y=1.16 if not forecast_only else 1.08,
        ),
        xaxis=dict(
            title="UTC time",
            rangeslider=dict(
                visible=not forecast_only,
                thickness=0.10,
            ),
            rangeselector=(
                dict(
                    buttons=[
                        dict(
                            count=1,
                            label="24h",
                            step="day",
                            stepmode="backward",
                        ),
                        dict(
                            count=7,
                            label="7d",
                            step="day",
                            stepmode="backward",
                        ),
                        dict(
                            count=30,
                            label="30d",
                            step="day",
                            stepmode="backward",
                        ),
                        dict(
                            step="all",
                            label="All",
                        ),
                    ],
                    x=0,
                    y=1.27,
                )
                if not forecast_only
                else None
            ),
        ),
        yaxis=dict(
            title="Burning Index",
            rangemode="tozero",
        ),
        updatemenus=[
            dict(
                buttons=buttons,
                direction="down",
                x=0.02,
                y=1.43 if not forecast_only else 1.30,
                xanchor="left",
                yanchor="top",
                showactive=True,
                bgcolor="#ffffff",
                bordercolor="#cbd5e1",
                borderwidth=1,
                font=dict(size=12),
            )
        ],
    )

    return fig


def make_comparison_chart(df):
    latest = (
        df[df["period"] == "Historical"]
        .sort_values("DateTime")
        .groupby("location")
        .tail(1)
    )

    fig = go.Figure()

    for index, label in INDEX_LABELS.items():
        fig.add_trace(
            go.Bar(
                x=latest["location"],
                y=latest[index],
                name=index,
                visible=index == "BI",
                marker_color=[
                    COLORS[city]
                    for city in latest["location"]
                ],
                text=[
                    f"{value:.2f}"
                    if pd.notna(value)
                    else "N/A"
                    for value in latest[index]
                ],
                textposition="outside",
                cliponaxis=False,
                customdata=latest[
                    "DateTime"
                ].dt.strftime(
                    "%Y-%m-%d %H:%M UTC"
                ),
                hovertemplate=(
                    "%{x}"
                    "<br>Value: %{y:.2f}"
                    "<br>Time: %{customdata}"
                    "<extra></extra>"
                ),
            )
        )

    buttons = []

    for i, (index, label) in enumerate(
        INDEX_LABELS.items()
    ):
        buttons.append(
            dict(
                label=f"{index} — {label}",
                method="update",
                args=[
                    {
                        "visible": [
                            j == i
                            for j in range(
                                len(INDEX_LABELS)
                            )
                        ]
                    },
                    {
                        "title.text": (
                            f"Latest Historical — {label}"
                        ),
                        "yaxis.title.text": label,
                        "yaxis.autorange": True,
                    },
                ],
            )
        )

    fig.update_layout(
        title=dict(
            text="Latest Historical — Burning Index",
            x=0.02,
            y=0.98,
            xanchor="left",
        ),
        template="plotly_white",
        height=550,
        margin=dict(
            t=170,
            l=70,
            r=40,
            b=85,
        ),
        yaxis=dict(
            title="Burning Index",
            rangemode="tozero",
        ),
        xaxis=dict(
            title="Location"
        ),
        showlegend=False,
        uniformtext=dict(
            minsize=10,
            mode="show",
        ),
        updatemenus=[
            dict(
                buttons=buttons,
                direction="down",
                x=0.02,
                y=1.35,
                xanchor="left",
                yanchor="top",
                showactive=True,
                bgcolor="#ffffff",
                bordercolor="#cbd5e1",
                borderwidth=1,
                font=dict(size=12),
            )
        ],
    )

    return fig


def main():
    df = load_data()

    historical = df[
        df["period"] == "Historical"
    ]

    forecast = df[
        df["period"] == "Forecast"
    ]

    trend_fig = make_trend_chart(df)
    forecast_fig = make_trend_chart(
        df,
        forecast_only=True
    )
    comparison_fig = make_comparison_chart(df)

    trend_html = trend_fig.to_html(
        full_html=False,
        include_plotlyjs=True,
    )

    forecast_html = forecast_fig.to_html(
        full_html=False,
        include_plotlyjs=False,
    )

    comparison_html = comparison_fig.to_html(
        full_html=False,
        include_plotlyjs=False,
    )

    bands_html = " ".join(
        (
            f'<span class="band">'
            f'<i style="background:{color}"></i>'
            f'{html.escape(name)} '
            f'({low}–{high - 1 if high is not None else "∞"})'
            f'</span>'
        )
        for name, low, high, color in BI_LEVELS
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">
<title>Southern California NFDRS4 Dashboard</title>
<style>
* {{
    box-sizing: border-box;
}}

body {{
    font-family: Arial, sans-serif;
    background: #f3f6fb;
    color: #172033;
    margin: 0;
    padding: 24px;
}}

.container {{
    max-width: 1450px;
    margin: auto;
}}

h1 {{
    margin-bottom: 8px;
}}

.subtitle {{
    color: #64748b;
    margin-bottom: 24px;
}}

.stats {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin-bottom: 22px;
}}

.stat,
.card {{
    background: white;
    border-radius: 14px;
    box-shadow: 0 3px 15px rgba(0,0,0,.05);
}}

.stat {{
    padding: 22px;
}}

.stat strong {{
    display: block;
    font-size: 30px;
}}

.stat span {{
    color: #64748b;
}}

.card {{
    padding: 20px;
    margin-bottom: 22px;
}}

.card h2 {{
    margin: 4px 0 12px;
}}

.note {{
    font-size: 13px;
    color: #64748b;
    line-height: 1.7;
}}

.band {{
    display: inline-flex;
    align-items: center;
    margin: 8px 20px 8px 0;
}}

.band i {{
    width: 12px;
    height: 12px;
    border-radius: 3px;
    margin-right: 6px;
}}

@media(max-width:700px) {{
    .stats {{
        grid-template-columns: 1fr;
    }}

    body {{
        padding: 12px;
    }}

    .card {{
        padding: 10px;
    }}
}}
</style>
</head>

<body>
<div class="container">

<h1>Southern California Fire-Danger Dashboard</h1>

<p class="subtitle">
Open-Meteo weather + NFDRS4 experimental calculations
</p>

<div class="stats">
    <div class="stat">
        <strong>6</strong>
        <span>Locations</span>
    </div>

    <div class="stat">
        <strong>{len(historical):,}</strong>
        <span>Historical records</span>
    </div>

    <div class="stat">
        <strong>{len(forecast):,}</strong>
        <span>Forecast records</span>
    </div>
</div>

<div class="card">
    <h2>Historical and Forecast Trends</h2>
    {trend_html}

    <p class="note">
        Solid lines represent historical weather calculations.
        Dashed lines represent forecast calculations.
        Use the dropdown to select a location and index.
    </p>
</div>

<div class="card">
    <h2>Forecast Detail — Next 10 Hours</h2>
    {forecast_html}

    <p class="note">
        Shows the last 24 historical hours and the
        available 10 forecast hours.
        Forecast results are experimental estimates.
    </p>
</div>

<div class="card">
    <h2>Six-Location Comparison</h2>
    {comparison_html}

    <p class="note">
        Latest historical NFDRS4 values for each location.
        Zero values are displayed as 0.00 rather than
        treated as missing.
        Hover over bars for timestamps.
    </p>
</div>

<div class="card">
    <h2>Burning Index Reference Bands</h2>
    <p>{bands_html}</p>

    <p class="note">
        These project-defined thresholds are not universal
        NFDRS danger ratings.
        They are references only.
    </p>
</div>

<div class="card">
    <h2>Research Limitations</h2>

    <p class="note">
        Open-Meteo supplies modeled historical and
        forecast weather rather than physical CIMIS
        station observations.
        NFDRS4 currently uses sample initialization
        parameters that require location-specific
        validation and calibration.
        These results should not be used for
        operational wildfire decisions.
    </p>
</div>

</div>
</body>
</html>"""

    OUTPUT_FILE.write_text(
        page,
        encoding="utf-8",
    )

    print("\nDashboard V2.1 created successfully.")
    print("Saved:", OUTPUT_FILE)
    print("Historical records:", len(historical))
    print("Forecast records:", len(forecast))
    print("Locations:", df["location"].nunique())

    subprocess.Popen([
        "cmd", "/c", "start", "", "chrome",
        str(OUTPUT_FILE)
    ])


if __name__ == "__main__":
    main()
