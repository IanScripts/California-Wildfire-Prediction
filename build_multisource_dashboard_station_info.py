"""Build a standalone, interactive dashboard from the *local* NFDRS4 outputs.

Run in the NFDRS4 root:
    python build_multisource_dashboard_station_info.py
    Start-Process chrome.exe -ArgumentList (Resolve-Path ".\nfdrs_multisource_dashboard_station_info.html").Path

Does not rerun NFDRS4, download data, or overwrite the original dashboard.
"""
from pathlib import Path
from html import escape
import json
import re
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "live" / "nfdrs_multisource"
MANIFEST = BASE / "integration_manifest.csv"
OUTPUT = ROOT / "nfdrs_multisource_dashboard_station_info.html"
METRICS = ["BI", "ERC", "SC", "IC", "GSI", "KBDI"]
SOURCE_NAMES = {
    "open_meteo_gfs": "Open-Meteo GFS · forecast",
    "noaa_hrrr": "NOAA HRRR · forecast",
    "iem_asos": "IEM ASOS · observed (estimated solar)",
    "synoptic_hybrid": "Synoptic + Open-Meteo · observed hybrid",
}
CITY_COORDS = {
    "Los Angeles": (34.0522,-118.2437), "Ventura": (34.2805,-119.2945),
    "Riverside": (33.9806,-117.3755), "San Bernardino": (34.1083,-117.2898),
    "Santa Ana": (33.7455,-117.8677), "San Diego": (32.7157,-117.1611),
}
CITY_NAMES = ["Los Angeles", "Ventura", "Riverside", "San Bernardino", "Santa Ana", "San Diego"]

def load_data():
    if not MANIFEST.is_file():
        raise SystemExit(f"Missing {MANIFEST}. Run integrate_weather_sources_nfdrs.py --run first.")
    m = pd.read_csv(MANIFEST).fillna("")
    records, errors = [], []
    for row in m.to_dict("records"):
        source = str(row.get("source", ""))
        location = str(row.get("location", ""))
        config = str(row.get("config_file", ""))
        status = str(row.get("status", ""))
        if not config or not status.startswith("NFDRS4 PASS"):
            errors.append(f"{source} / {location}: {status or 'missing status'}")
            continue
        cfg = ROOT / config
        index_file = cfg.with_name(cfg.stem + "_indexes.csv")
        if not index_file.is_file():
            errors.append(f"{source} / {location}: missing {index_file.name}")
            continue
        try:
            d = pd.read_csv(index_file, low_memory=False)
            if "DateTime" not in d or not any(x in d for x in METRICS):
                raise ValueError("Missing DateTime or index columns")
            d["DateTime"] = pd.to_datetime(d["DateTime"], utc=True, errors="coerce")
            d = d.dropna(subset=["DateTime"]).sort_values("DateTime")
            if d.empty:
                raise ValueError("No valid timestamps")
            for metric in METRICS:
                if metric not in d:
                    d[metric] = float("nan")
                else:
                    d[metric] = pd.to_numeric(d[metric], errors="coerce")
            records.append({"source":source, "location":location, "station":str(row.get("station_id","")),
                            "method":str(row.get("method","")), "weather_rows":row.get("rows",""),
                            "df":d, "status":status, "config_file":config})
        except Exception as exc:
            errors.append(f"{source} / {location}: {exc}")
    if not records:
        raise SystemExit("No valid NFDRS4 index files found. Check manifest and run outputs.")
    return records, errors

def resolve_local_path(value):
    """Resolve a path saved in an NFDRS4 config on the same Windows project."""
    value = str(value).strip().strip('"').replace("\\", "/")
    candidate = Path(value)
    if candidate.is_file():
        return candidate
    if not candidate.is_absolute():
        candidate = ROOT / value
        if candidate.is_file():
            return candidate
    # Old absolute paths can change when a project folder is moved.
    for marker in ("/live/", "/data/"):
        if marker in value.lower():
            suffix = value[value.lower().index(marker)+1:]
            candidate = ROOT / suffix
            if candidate.is_file():
                return candidate
    return None


def station_settings(record):
    """Read only settings actually present in the run's init file."""
    config = resolve_local_path(record.get("config_file", ""))
    result = {"fuel_model": "Unknown (init file unavailable)",
              "calibration": "Not verified — research preview",
              "init_file": "Not found", "slope_class": "Unknown"}
    if config is None:
        return result
    config_text = config.read_text(encoding="utf-8-sig", errors="replace")
    init_match = re.search(r'^\s*initFile\s*=\s*"([^"]+)"', config_text, re.I | re.M)
    if not init_match:
        return result
    init = resolve_local_path(init_match.group(1))
    if init is None:
        return result
    result["init_file"] = str(init.relative_to(ROOT)) if init.is_relative_to(ROOT) else str(init)
    content = init.read_text(encoding="utf-8-sig", errors="replace")
    for field, output in (("fuelModel", "fuel_model"), ("slopeClass", "slope_class")):
        match = re.search(r'^\s*' + field + r'\s*=\s*"([^"]+)"', content, re.I | re.M)
        if match:
            result[output] = match.group(1)
    return result


def figure_for(r):
    d = r["df"]
    fig = go.Figure()
    for i, metric in enumerate(METRICS):
        fig.add_trace(go.Scatter(x=d["DateTime"], y=d[metric], mode="lines",
                                 name=metric, visible=(i==0), line={"width":2}))
    buttons = []
    for i, metric in enumerate(METRICS):
        buttons.append({"label":metric, "method":"update",
                        "args":[{"visible":[j==i for j in range(len(METRICS))]},
                                {"yaxis":{"title":metric,"autorange":True}}]})
    fig.update_layout(
        template="plotly_white", height=370, margin=dict(l=55,r=20,t=55,b=55),
        title={"text":"", "font":{"size":16}},
        xaxis={"title":"Timestamp (UTC)"},
        yaxis={"title":"Burning Index (BI)", "rangemode":"tozero"},
        updatemenus=[{"buttons":buttons,"direction":"right","x":0,"y":1.17,"xanchor":"left",
                      "showactive":True, "bgcolor":"#edf2f9"}],
        hovermode="x unified", showlegend=False,
    )
    return fig.to_json()

def main():
    records, errors = load_data()
    # Station coordinates come from the saved IEM catalog; project cities use
    # the established six project reference coordinates.
    coords = dict(CITY_COORDS)
    catalog = ROOT / "live/california_sources/iem/iem_california_station_catalog.csv"
    if catalog.is_file():
        cat = pd.read_csv(catalog)
        for row in cat.to_dict("records"):
            try:
                coords[str(row["station_name"])] = (float(row["latitude"]), float(row["longitude"]))
            except (ValueError, TypeError, KeyError):
                continue
    figures = []
    summary = []
    for i,r in enumerate(records):
        d = r["df"]
        figures.append({"id":f"chart-{i}", "source":r["source"], "location":r["location"],
                        "plot":json.loads(figure_for(r))})
        summary.append({"source":r["source"],"location":r["location"],"rows":len(d),
                        "start":d["DateTime"].min().strftime("%Y-%m-%d %H:%M UTC"),
                        "end":d["DateTime"].max().strftime("%Y-%m-%d %H:%M UTC"),
                        "method":r["method"]})
    sources = list(dict.fromkeys(x["source"] for x in figures))
    locs = list(dict.fromkeys(x["location"] for x in figures))
    # Keep comparison payload small. One record per timestamp, retaining all
    # index metrics. Never interpolate or fabricate overlapping timestamps.
    compare_data = []
    for r in records:
        d = r["df"][["DateTime"] + METRICS].copy()
        d = d.drop_duplicates(subset=["DateTime"], keep="last")
        points = []
        for values in d.itertuples(index=False, name=None):
            ts = values[0].strftime("%Y-%m-%dT%H:%M:%SZ")
            metrics = [None if pd.isna(v) else float(v) for v in values[1:]]
            points.append([ts] + metrics)
        compare_data.append({"source":r["source"], "location":r["location"], "points":points})
    station_info = {}
    for r in records:
        loc = r["location"]
        settings = station_settings(r)
        kind = {"open_meteo_gfs":"Forecast (modeled)",
                "noaa_hrrr":"Forecast (modeled)",
                "iem_asos":"Observed (solar radiation estimated)",
                "synoptic_hybrid":"Hybrid (observed + modeled precipitation/solar)"}.get(r["source"], "Unclassified")
        station_info.setdefault(loc, []).append({
            "source": SOURCE_NAMES.get(r["source"], r["source"]),
            "data_type":kind, "fuel_model":settings["fuel_model"],
            "slope_class":settings["slope_class"],
            "calibration":settings["calibration"], "init_file":settings["init_file"]})
    map_fig = go.Figure()
    map_rows = []
    for loc in locs:
        if loc not in coords:
            continue
        lat, lon = coords[loc]
        n_sources = len({r["source"] for r in records if r["location"] == loc})
        map_rows.append((loc,lat,lon,n_sources))
    if map_rows:
        map_fig.add_trace(go.Scattergeo(
            lat=[r[1] for r in map_rows], lon=[r[2] for r in map_rows],
            text=[f"{r[0]}<br>{r[3]} source(s)" for r in map_rows],
            customdata=[[r[0],r[3],"Original project location" if r[0] in CITY_COORDS else "IEM station"] for r in map_rows],
            mode="markers", hovertemplate="%{text}<extra>Click for details</extra>",
            marker=dict(size=[11 if r[0] in CITY_COORDS else 8 for r in map_rows],
                        color=["#dc6a26" if r[0] in CITY_COORDS else "#3274b9" for r in map_rows],
                        line=dict(width=1,color="white"))
        ))
    map_fig.update_layout(
        title=dict(text="California NFDRS4 project locations", x=0.02),
        geo=dict(projection_type="mercator", center=dict(lat=37.1,lon=-119.5),
                 lonaxis=dict(range=[-125.2,-113.5]), lataxis=dict(range=[32,42.3]),
                 showland=True, landcolor="#e9f0e6", showocean=True,
                 oceancolor="#dcecf7", showlakes=True, lakecolor="#d6e8f7",
                 showsubunits=True, subunitcolor="#c4ced8", showcoastlines=True,
                 coastlinecolor="#a9bdc8", bgcolor="white"),
        margin=dict(l=0,r=0,t=55,b=0), height=650,
        paper_bgcolor="white", showlegend=False
    )
    source_opts = "".join(f'<option value="{escape(s)}">{escape(SOURCE_NAMES.get(s,s))}</option>' for s in sources)
    location_opts = "".join(f'<option value="{escape(s)}">{escape(s)}</option>' for s in locs)
    table_rows = "".join(
        "<tr><td>"+escape(SOURCE_NAMES.get(x["source"],x["source"]))+"</td><td>"+escape(x["location"])+
        "</td><td>"+str(x["rows"])+"</td><td>"+escape(x["start"])+"</td><td>"+escape(x["end"])+
        "</td></tr>" for x in summary)
    warn_rows = "".join("<li>"+escape(e)+"</li>" for e in errors)
    compare_location_opts = "".join(f'<option value="{escape(s)}">{escape(s)}</option>' for s in sorted(locs, key=lambda loc: (loc != "Los Angeles", loc.lower())))
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>California NFDRS4 · Multi-source Results</title>
<style>
:root{font-family:Arial,sans-serif;color:#162337;background:#f4f7fb}
*{box-sizing:border-box}body{margin:0}header{background:#102844;color:#fff;padding:28px max(24px,calc((100vw - 1200px)/2))}
h1{margin:0 0 9px;font-size:29px}header p{color:#cad7e6;margin:0}
main{max-width:1200px;margin:25px auto;padding:0 18px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card,.panel{background:white;border-radius:12px;box-shadow:0 2px 12px #112b4a12;padding:18px;margin-bottom:17px}
.card b{display:block;font-size:28px;margin:8px 0}.small{font-size:13px;color:#66768b}
.filters{display:flex;gap:15px;flex-wrap:wrap;align-items:end}label{font-size:13px;font-weight:bold}
select{display:block;padding:11px;border:1px solid #cbd5e1;border-radius:7px;margin-top:7px;min-width:245px;background:#fff}
#charts{display:grid;grid-template-columns:1fr 1fr;gap:14px}.chart-card{background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px #112b4a12}
.plot{height:390px}.caption{font-size:12px;color:#64748b;padding:0 17px 14px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #e5eaf0}
th{background:#f5f8fc} .scroll{overflow-x:auto}.warning{background:#fff8e8;border-left:4px solid #d38a17;padding:14px;line-height:1.55;border-radius:5px}
#empty{display:none;padding:30px;color:#64748b}
@media(max-width:900px){#charts{grid-template-columns:1fr}.stats{grid-template-columns:1fr}}
</style></head><body>
<header><h1>California Wildfire Research · NFDRS4</h1><p>Actual multi-source fire-danger calculations from local NFDRS4 outputs</p></header>
<main><div class="stats">
<div class="card"><span class="small">Successful output streams loaded</span><b>__STREAMS__</b></div>
<div class="card"><span class="small">Distinct weather sources</span><b>__SOURCES__</b></div>
<div class="card"><span class="small">NFDRS4 index records</span><b>__RECORDS__</b></div>
</div>
<div class="panel warning"><b>Research interpretation:</b> These are separate NFDRS4 runs using sample initialization parameters, not calibrated ignition probabilities. The source streams have different dates, locations, and lengths. IEM uses estimated solar radiation; Synoptic is a hybrid using Open-Meteo precipitation and radiation. Do not compare their values as if all inputs, periods, and station conditions were identical. Very short HRRR runs have limited fuel-moisture spin-up.</div>
<div class="panel"><h2>California station map</h2><p class="small">Orange: original six Southern California locations. Blue: IEM station sites included in the successful runs. This is a location map, not a wildfire-risk heatmap.</p><div id="stationMap" style="width:100%;height:650px"></div><div id="stationDetails" class="small" style="padding:12px;border:1px solid #d9e4f0;border-radius:8px;margin-top:12px" aria-live="polite">Click a station marker to view coordinates, weather data types, fuel models, and calibration status.</div></div>
<div class="panel"><h2>Side-by-side source comparison</h2>
<p class="small">Only exact matching timestamps are compared; mismatched periods are not merged or interpolated. Different weather inputs and initialization parameters still limit scientific comparability.</p>
<div class="filters">
<label>Location<select id="compareLocation">__COMPARE_LOCATION_OPTIONS__</select></label>
<label>NFDRS4 index<select id="compareMetric"><option>BI</option><option>ERC</option><option>SC</option><option>IC</option><option>GSI</option><option>KBDI</option></select></label>
</div><div id="comparisonNotice" class="small" style="padding:12px 0"></div>
<div id="comparisonPlot" style="width:100%;height:390px"></div></div>
<div class="panel"><h2>Explore NFDRS4 indices</h2>
<div class="filters"><label>Weather source<select id="sourceFilter"><option value="">All sources</option>__SOURCE_OPTIONS__</select></label>
<label>Location<select id="locationFilter"><option value="">All locations</option>__LOCATION_OPTIONS__</select></label></div>
<p class="small">Use BI, ERC, SC, IC, GSI or KBDI above each chart to switch the displayed NFDRS4 index. Hover to inspect values.</p></div>
<div id="charts"></div><div id="empty" class="panel">No matching outputs. Choose another source or location.</div>
<div class="panel"><h2>Processing coverage</h2><div class="scroll"><table><thead><tr><th>Source</th><th>Location</th><th>Index rows</th><th>First record</th><th>Last record</th></tr></thead><tbody>__TABLE_ROWS__</tbody></table></div></div>
__WARNINGS__
<div class="panel"><h2>What this demonstration proves</h2><p>All four weather-source workflows have generated readable NFDRS4 index outputs. It does <b>not</b> establish station-specific calibration, predictive accuracy, or equal comparability between weather providers. Next steps are quality control, warm-up periods, consistent station/time matching, and validation against independent fire observations.</p></div>
</main><script>__PLOTLY_JS__</script><script>
const plots=__FIGURES__;
const comparison=__COMPARISON_DATA__;
const stationInfo=__STATION_INFO__;
const metricNames=["BI","ERC","SC","IC","GSI","KBDI"];
Plotly.newPlot("stationMap",__MAP_DATA__,__MAP_LAYOUT__,{responsive:true,displaylogo:false}).then(()=>{
 document.getElementById("stationMap").on("plotly_click",event=>{
   const p=event.points && event.points[0];
   if(!p || !p.customdata)return;
   const [location,count,type]=p.customdata;
   const streams=stationInfo[location]||[];
   const details=document.getElementById("stationDetails");
   details.replaceChildren();
   const heading=document.createElement("h3");heading.textContent=location;heading.style.margin="0 0 8px";details.appendChild(heading);
   const coordinates=document.createElement("p");
   coordinates.textContent=`${type} | Latitude: ${Number(p.lat).toFixed(4)} | Longitude: ${Number(p.lon).toFixed(4)} | ${count} source(s)`;
   details.appendChild(coordinates);
   streams.forEach(s=>{
     const entry=document.createElement("div");entry.style.cssText="border-top:1px solid #d9e4f0;padding:10px 0;line-height:1.6";
     const title=document.createElement("strong");title.textContent=s.source;entry.appendChild(title);
     [["Weather data",s.data_type],["NFDRS4 fuel model",s.fuel_model],
      ["Slope class",s.slope_class],["Calibration",s.calibration],
      ["Initialization file",s.init_file]].forEach(([label,value])=>{
       const line=document.createElement("div");line.textContent=label+": "+value;entry.appendChild(line);
     });
     details.appendChild(entry);
   });
   if(!streams.length){const line=document.createElement("p");line.textContent="No processed NFDRS4 streams found for this marker.";details.appendChild(line);}
   const choice=document.getElementById("compareLocation");
   if([...choice.options].some(o=>o.value===location)){choice.value=location;renderComparison();}
 });
});
function renderComparison(){
 const location=document.getElementById("compareLocation").value;
 const metric=document.getElementById("compareMetric").value;
 const idx=metricNames.indexOf(metric)+1;
 const streams=comparison.filter(x=>x.location===location);
 const notice=document.getElementById("comparisonNotice");
 if(!streams.length){notice.textContent="No data for this location.";Plotly.purge("comparisonPlot");return;}
 const sourceSet=new Set(streams.map(x=>x.source));
 if(sourceSet.size<2){
  notice.textContent="Only one source has an NFDRS4 run at this location. Showing its time series; no cross-source comparison is possible.";
 }else{
  notice.textContent="Multiple sources available. Only shared timestamps appear below.";
 }
 let matched=null;
 if(sourceSet.size>=2){
   streams.forEach(s=>{
     const keys=new Set(s.points.map(p=>p[0]));
     matched=matched===null?keys:new Set([...matched].filter(k=>keys.has(k)));
   });
 }
 const useMatch=sourceSet.size>=2;
 const traces=streams.map(s=>{
   const points=s.points.filter(p=>!useMatch||matched.has(p[0]));
   return {x:points.map(p=>p[0]),y:points.map(p=>p[idx]),mode:"lines+markers",
           name:__SOURCE_NAMES__[s.source]||s.source,connectgaps:false};
 });
 if(useMatch && matched.size===0){
   notice.textContent="No exact shared timestamps between these sources. Comparison is intentionally withheld; select another location or align their periods first.";
 }
 Plotly.react("comparisonPlot",traces,{
  template:"plotly_white",height:390,margin:{l:60,r:20,t:40,b:55},
  xaxis:{title:"UTC time"},yaxis:{title:metric},
  hovermode:"x unified",legend:{orientation:"h",y:-0.3}
 },{responsive:true,displaylogo:false});
}
document.getElementById("compareLocation").addEventListener("change",renderComparison);
document.getElementById("compareMetric").addEventListener("change",renderComparison);
renderComparison();
const container=document.getElementById("charts");
const src=document.getElementById("sourceFilter");
const loc=document.getElementById("locationFilter");
function render(){
 const chosen=plots.filter(p=>(!src.value||p.source===src.value)&&(!loc.value||p.location===loc.value));
 container.innerHTML="";
 document.getElementById("empty").style.display=chosen.length?"none":"block";
 chosen.forEach(p=>{
  const wrap=document.createElement("div");wrap.className="chart-card";
  const heading=document.createElement("div");heading.style.cssText="font-weight:bold;padding:16px 18px 0";heading.textContent=p.location+" · "+(__SOURCE_NAMES__[p.source]||p.source);wrap.appendChild(heading);
  const div=document.createElement("div");div.className="plot";wrap.appendChild(div);
  container.appendChild(wrap);
  Plotly.newPlot(div,p.plot.data,p.plot.layout,{responsive:true,displaylogo:false});
 });
}
src.addEventListener("change",render);loc.addEventListener("change",render);render();
</script></body></html>"""
    warnings = ('<div class="panel"><h2>Skipped or missing outputs</h2><ul>'+warn_rows+'</ul></div>') if errors else ''
    html = (html.replace("__STREAMS__",str(len(records)))
            .replace("__SOURCES__",str(len(sources)))
            .replace("__RECORDS__",f"{sum(x['rows'] for x in summary):,}")
            .replace("__SOURCE_OPTIONS__",source_opts)
            .replace("__COMPARE_LOCATION_OPTIONS__",compare_location_opts)
            .replace("__COMPARISON_DATA__",json.dumps(compare_data,separators=(",",":")))
            .replace("__STATION_INFO__",json.dumps(station_info,separators=(",",":")))
            .replace("__SOURCE_NAMES__","("+json.dumps(SOURCE_NAMES)+")")
            .replace("__MAP_DATA__",map_fig.to_json().split('"data":',1)[1].split(',"layout":',1)[0])
            .replace("__MAP_LAYOUT__",json.dumps(map_fig.to_plotly_json()["layout"]))
            .replace("__LOCATION_OPTIONS__",location_opts)
            .replace("__TABLE_ROWS__",table_rows)
            .replace("__WARNINGS__",warnings)
            .replace("__PLOTLY_JS__",get_plotlyjs())
            .replace("__FIGURES__",json.dumps(figures,separators=(",",":"))))
    OUTPUT.write_text(html,encoding="utf-8")
    print(f"Created: {OUTPUT}")
    print(f"Loaded: {len(records)} streams; {len(sources)} sources; {sum(x['rows'] for x in summary):,} NFDRS4 index records")
    if errors:
        print(f"Warnings: {len(errors)} missing/failed streams (shown in dashboard)")
if __name__=="__main__":
    main()
