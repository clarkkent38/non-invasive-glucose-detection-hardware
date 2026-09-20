"""
Non-Invasive Blood Glucose - Live Sensor Dashboard
====================================================
Architecture:
  ESP32 button -> sensors collected -> Supabase row (status=pending)
  Dashboard polls -> detects pending -> user fills form -> Submit
  Dashboard runs predict() -> patches row (status=complete)
  ESP32 polls -> finds complete -> shows result on TFT display

Key design decisions:
  - Prediction result stored in st.session_state["last_result"] so that
    the 8-second autorefresh NEVER erases it.  A "Done / New Reading"
    button explicitly clears it.
  - Clarke zone text is shortened to "Zone A" etc. for metric cards;
    full text is shown in the detail block below.
  - Separate "Audit Log" tab shows all completed readings with a
    patient selector and full chart suite for any selected patient.
"""
from __future__ import annotations

import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from predict import GlucosePredictor
from app.supabase_client import (
    create_client, get_pending_reading,
    get_latest_reading, get_reading_history, patch_reading,
)

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Glucose Monitor - Live",
    page_icon="🩸",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CUSTOM_CSS = """
<style>
.main-title  { font-size:2.1rem; font-weight:800; color:#1e3a8a; margin-bottom:.1rem; }
.sub-title   { font-size:1.0rem; color:#475569; margin-bottom:1rem; }
.live-badge  { background:linear-gradient(135deg,#16a34a,#4ade80);
               color:#fff; padding:6px 14px; border-radius:8px;
               font-weight:700; font-size:.88rem; display:inline-block; }
.pending-badge { background:linear-gradient(135deg,#92400e,#d97706);
               color:#fff; padding:6px 14px; border-radius:8px;
               font-weight:700; font-size:.88rem; display:inline-block; }
.manual-badge { background:linear-gradient(135deg,#1e3a8a,#0284c7);
               color:#fff; padding:6px 14px; border-radius:8px;
               font-weight:700; font-size:.88rem; display:inline-block; }
.result-badge { background:linear-gradient(135deg,#0369a1,#38bdf8);
               color:#fff; padding:6px 14px; border-radius:8px;
               font-weight:700; font-size:.88rem; display:inline-block;
               margin-bottom:.6rem; }
.model-badge-fs { background:linear-gradient(135deg,#1e3a8a,#0284c7);
               color:#fff; padding:6px 14px; border-radius:8px;
               font-weight:700; font-size:.88rem; display:inline-block;
               margin-bottom:.6rem; }
.badge-normal { background-color:#dcfce7; color:#166534;
               padding:4px 12px; border-radius:9999px;
               font-weight:700; border:1px solid #86efac; display:inline-block; }
.badge-prediabetes { background-color:#fef9c3; color:#854d0e;
               padding:4px 12px; border-radius:9999px;
               font-weight:700; border:1px solid #fde047; display:inline-block; }
.badge-diabetes { background-color:#fee2e2; color:#991b1b;
               padding:4px 12px; border-radius:9999px;
               font-weight:700; border:1px solid #fca5a5; display:inline-block; }
.badge-hypo { background-color:#e0e7ff; color:#3730a3;
               padding:4px 12px; border-radius:9999px;
               font-weight:700; border:1px solid #a5b4fc; display:inline-block; }
.disclaimer-banner { background-color:#fffbeb; border-left:5px solid #f59e0b;
               padding:.8rem 1.1rem; border-radius:6px;
               margin-top:.8rem; color:#92400e; font-size:.90rem; }
.disclaimer-critical { background-color:#fef2f2; border-left:5px solid #ef4444;
               padding:.8rem 1.1rem; border-radius:6px;
               margin-top:.8rem; color:#b91c1c; font-size:.90rem; }
.sensor-card { background:#0f172a; border:1px solid #334155;
               border-radius:10px; padding:12px 16px; margin-bottom:6px; }
.sensor-label { color:#94a3b8; font-size:.75rem; text-transform:uppercase;
               letter-spacing:.05em; margin-bottom:2px; }
.sensor-value { color:#f1f5f9; font-size:1.35rem; font-weight:700; }
.sensor-unit  { color:#64748b; font-size:.78rem; margin-left:3px; }
.step-done    { color:#22c55e; font-weight:700; }
.step-active  { color:#fbbf24; font-weight:700; }
.step-waiting { color:#475569; }
.ts-small     { color:#64748b; font-size:.78rem; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# =============================================================================
# SINGLETONS
# =============================================================================

@st.cache_resource(show_spinner="Loading prediction model...")
def load_predictor() -> GlucosePredictor:
    return GlucosePredictor()

@st.cache_resource(show_spinner=False)
def load_sb():
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception:
        return None

predictor = load_predictor()
sb        = load_sb()

# =============================================================================
# CHART HELPERS
# =============================================================================

def plot_ci_gauge(bgl: float, ci_low: float, ci_high: float) -> go.Figure:
    fig = go.Figure()
    for x0, x1, col, lbl in [
        (40,  70,  "#dbeafe", "Hypo (<70)"),
        (70,  140, "#dcfce7", "Normal (70-140)"),
        (140, 200, "#fef9c3", "Elevated"),
        (200, 360, "#fee2e2", "Severe (>=200)"),
    ]:
        fig.add_vrect(x0=x0, x1=x1, fillcolor=col, opacity=0.45, layer="below",
                      line_width=0, annotation_text=lbl, annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=[ci_low, ci_high], y=[0, 0], mode="lines",
        line=dict(color="#0284c7", width=14),
        name="90% Prediction Interval",
        hovertext=f"CI: {ci_low:.1f} - {ci_high:.1f} mg/dL",
    ))
    fig.add_trace(go.Scatter(
        x=[bgl], y=[0], mode="markers+text",
        marker=dict(color="#1e3a8a", size=18, symbol="diamond",
                    line=dict(color="white", width=2)),
        text=[f"<b>{bgl:.1f} mg/dL</b>"], textposition="top center",
        name="Predicted BGL",
    ))
    fig.update_layout(
        title="<b>Prediction Interval vs Clinical Safety Zones</b>",
        xaxis=dict(title="Blood Glucose (mg/dL)", range=[40, 360], zeroline=False),
        yaxis=dict(showticklabels=False, showgrid=False, range=[-0.5, 0.5]),
        height=210, margin=dict(l=15, r=15, t=35, b=25), showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.4, xanchor="center", x=0.5),
    )
    return fig


def plot_sensor_bars(row: dict) -> go.Figure:
    sensors = [
        ("Saliva pH",    row.get("saliva_ph"),           7.0,  7.4,  "pH"),
        ("Temp (C)",     row.get("temperature_c"),       36.1, 37.2, "C"),
        ("HR (BPM)",     row.get("hr_bpm"),              60.0, 80.0, "BPM"),
        ("Perfusion %",  row.get("perfusion_index"),      0.5,  5.0, "%"),
    ]
    names, values, colors, texts = [], [], [], []
    for label, val, lo, hi, unit in sensors:
        if val is None:
            continue
        val = float(val)
        names.append(label)
        values.append(val)
        if val < lo:
            colors.append("#0284c7")
            texts.append(f"{val:.2f} {unit}  (below {lo}-{hi})")
        elif val > hi:
            colors.append("#dc2626")
            texts.append(f"{val:.2f} {unit}  (above {lo}-{hi})")
        else:
            colors.append("#16a34a")
            texts.append(f"{val:.2f} {unit}  (normal {lo}-{hi})")
    fig = go.Figure(go.Bar(x=names, y=values, marker=dict(color=colors),
                           text=texts, textposition="auto"))
    fig.update_layout(
        title="<b>Sensor Readings vs Normal Reference Ranges</b>",
        yaxis=dict(title="Value"),
        height=260, margin=dict(l=15, r=15, t=35, b=25),
    )
    return fig


def plot_trend(df: pd.DataFrame, patient_name: str) -> Optional[go.Figure]:
    df2 = df.dropna(subset=["predicted_bgl_mg_dl"]).sort_values("created_at")
    if len(df2) < 1:
        return None
    color_map = []
    for cz in df2.get("clarke_zone", pd.Series(["Zone A"] * len(df2))):
        s = str(cz)
        if "Zone A" in s:   color_map.append("#16a34a")
        elif "Zone B" in s: color_map.append("#d97706")
        else:               color_map.append("#dc2626")
    fig = go.Figure()
    fig.add_hrect(y0=70, y1=140, fillcolor="#dcfce7", opacity=0.35,
                  layer="below", line_width=0,
                  annotation_text="Target (70-140 mg/dL)", annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=df2["created_at"], y=df2["predicted_bgl_mg_dl"],
        mode="lines+markers",
        line=dict(color="#0284c7", width=2.5, shape="spline"),
        marker=dict(color=color_map, size=11, line=dict(color="white", width=1.5)),
        name="Blood Glucose (mg/dL)",
        hovertemplate="%{x|%Y-%m-%d %H:%M}<br>BGL: %{y:.1f} mg/dL<extra></extra>",
    ))
    fig.update_layout(
        title=f"<b>Glycemic History — {patient_name}</b>",
        xaxis=dict(title="Time", showgrid=True),
        yaxis=dict(title="Predicted BGL (mg/dL)",
                   range=[40, max(260.0, df2["predicted_bgl_mg_dl"].max() + 30)]),
        height=300, margin=dict(l=15, r=15, t=40, b=25),
    )
    return fig


# =============================================================================
# SHARED HELPERS  (defined ABOVE all UI code so runpy never hits NameError)
# =============================================================================

def glucose_category(bgl: float) -> str:
    if bgl < 70:   return "Hypoglycemia"
    if bgl < 100:  return "Normal"
    if bgl < 126:  return "Prediabetes"
    if bgl < 180:  return "Elevated"
    return "Hyperglycemia"


def zone_short(zone_str: str) -> str:
    """Return 'Zone A', 'Zone B' etc. — strips the parenthetical description."""
    for z in "ABCDE":
        if f"Zone {z}" in zone_str:
            return f"Zone {z}"
    return zone_str[:10]


def render_result_block(result: dict, patient_name: str, ts: str,
                        row: Optional[dict] = None, key_suffix: str = ""):
    """
    Full prediction output block.  Called both after Submit and from the
    Audit Log tab.  key_suffix keeps plotly chart keys unique.
    """
    pred_bgl = result.get("predicted_bgl_mg_dl")
    if pred_bgl is None:
        st.warning(f"No BGL prediction available.  Risk band: {result.get('risk_band','—')}")
        return

    ci       = result.get("confidence_interval_5th_95th", [pred_bgl-20, pred_bgl+20])
    zone_str = result.get("clarke_zone", "Zone A")
    is_ood   = result.get("is_out_of_distribution", False)
    ood_warn = result.get("ood_warning", "")
    cat      = glucose_category(pred_bgl)

    st.markdown("<div class='model-badge-fs'>MODEL A: Full-Sensor Multi-Modal Ensemble (R²=0.8528)</div>",
                unsafe_allow_html=True)

    if is_ood:
        st.markdown(f"<div class='disclaimer-critical'>⚠️ <b>OUT-OF-DISTRIBUTION INPUT</b><br/>{ood_warn}</div>",
                    unsafe_allow_html=True)

    # Clinical tier badge
    if pred_bgl < 70:
        badge = "<span class='badge-hypo'>⚠️ ACUTE HYPOGLYCEMIA (&lt;70 mg/dL)</span>"
        msg   = "Critically low. Rapid-acting carbohydrate intake recommended immediately."
    elif pred_bgl < 100:
        badge = "<span class='badge-normal'>✅ NORMAL FASTING (70-99 mg/dL)</span>"
        msg   = "Glycemic levels within healthy fasting baseline."
    elif pred_bgl < 126:
        badge = "<span class='badge-prediabetes'>⚠️ PREDIABETES RANGE (100-125 mg/dL)</span>"
        msg   = "Impaired fasting glucose. HbA1c follow-up recommended."
    elif pred_bgl < 180:
        badge = "<span class='badge-diabetes'>🔶 ELEVATED (126-179 mg/dL)</span>"
        msg   = "Elevated blood glucose consistent with diabetic threshold."
    else:
        badge = "<span class='badge-diabetes'>🔴 HYPERGLYCEMIA (&ge;180 mg/dL)</span>"
        msg   = "Marked hyperglycemia. Clinical evaluation recommended."

    st.markdown(f"#### Clinical Status: {badge}", unsafe_allow_html=True)
    st.caption(f"**Interpretation:** {msg}")

    # 4-column metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Blood Glucose",      f"{pred_bgl:.1f} mg/dL")
    m2.metric("90% CI",             f"{ci[0]:.1f} – {ci[1]:.1f} mg/dL",
              delta=f"Width {ci[1]-ci[0]:.1f}", delta_color="off")
    m3.metric("Clarke Zone",         zone_short(zone_str))
    m4.metric("Category",            cat)

    st.caption(f"Full zone: {zone_str}")

    # CI gauge
    st.markdown("#### 🎯 Prediction Interval vs Safety Zones")
    st.plotly_chart(plot_ci_gauge(pred_bgl, ci[0], ci[1]),
                    use_container_width=True, key=f"ci_{key_suffix}")

    # Sensor bars if row data available
    if row:
        st.markdown("#### 🩺 Sensor Readings vs Normal Reference Ranges")
        st.plotly_chart(plot_sensor_bars(row),
                        use_container_width=True, key=f"bars_{key_suffix}")

    # Clinical messages
    if pred_bgl < 70:
        st.error("🚨 Hypoglycemia — below 70 mg/dL. Immediate attention required.")
    elif pred_bgl < 100:
        st.success("✅ Normal fasting range (70-99 mg/dL).")
    elif pred_bgl < 126:
        st.warning("⚠️ Prediabetes range (100-125 mg/dL).")
    elif pred_bgl < 180:
        st.warning("⚠️ Elevated range (126-179 mg/dL).")
    else:
        st.error("🚨 Hyperglycemia — ≥ 180 mg/dL.")

    diag_conf = result.get("diagnosis_stratum_confidence", "")
    if diag_conf:
        st.caption(f"ℹ️ {diag_conf}")

    st.markdown(
        "<div class='disclaimer-banner'>"
        "🔬 <b>Research Prototype:</b> Synthetic-data model only. "
        "Not a certified medical device. Not a substitute for clinical lab testing."
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Patient: {patient_name or '—'} · {ts}")


def sc(label: str, val, unit: str, fmt: str = ".2f") -> str:
    """Returns HTML for a dark sensor card."""
    if val is None:
        body = "<span style='color:#475569'>— (fallback)</span>"
    else:
        try:    body = f"{float(val):{fmt}}"
        except: body = str(val)
    return (f"<div class='sensor-card'>"
            f"<div class='sensor-label'>{label}</div>"
            f"<div class='sensor-value'>{body}"
            f"<span class='sensor-unit'>{unit}</span></div></div>")


def pipeline_ui(active: int):
    labels = ["① Sensor Collection", "② Enter User Details", "③ Prediction Result"]
    cols = st.columns(3)
    for i, (col, lbl) in enumerate(zip(cols, labels), start=1):
        if i < active:
            col.markdown(f"<div class='step-done'>✅ {lbl}</div>", unsafe_allow_html=True)
        elif i == active:
            col.markdown(f"<div class='step-active'>▶ {lbl}</div>", unsafe_allow_html=True)
        else:
            col.markdown(f"<div class='step-waiting'>○ {lbl}</div>", unsafe_allow_html=True)
    st.markdown("")


def esp32_status(ip: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"http://{ip}/status", timeout=5)
        return {"online": True, "data": r.json()} if r.status_code == 200 \
               else {"online": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"online": False, "error": str(e)}


def esp32_trigger(ip: str) -> Dict[str, Any]:
    try:
        r = requests.post(f"http://{ip}/start_reading", timeout=10)
        if r.status_code == 200: return {"ok": True}
        if r.status_code == 409: return {"ok": False, "error": "Already reading"}
        return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# PAGE HEADER + TABS
# =============================================================================
st.markdown("<div class='main-title'>🩸 Non-Invasive Blood Glucose — Live Sensor Dashboard</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-title'>ESP32-S3 → Supabase → real-time prediction → device display</div>",
            unsafe_allow_html=True)

tab_live, tab_manual, tab_audit = st.tabs([
    "🔴 Live Sensor Mode",
    "✏️ Manual Entry",
    "📋 Audit Log & Patient History",
])

# =============================================================================
# TAB 1 — LIVE SENSOR MODE
# =============================================================================
with tab_live:
    if sb is None:
        st.error("Supabase not configured. Add `[supabase]` to `.streamlit/secrets.toml`.")
        st.stop()

    # ── Device Control Panel ──────────────────────────────────────────────────
    with st.expander("📡 ESP32 Device Control (remote trigger)", expanded=False):
        if "esp32_ip" not in st.session_state:
            st.session_state.esp32_ip = "192.168.1.100"
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            ip = st.text_input("ESP32 IP Address", value=st.session_state.esp32_ip, key="ip_in")
            st.session_state.esp32_ip = ip
        with c2:
            if st.button("🔍 Check Status"):
                st.session_state.dev_status = esp32_status(ip)
        with c3:
            if st.button("🚀 Start Remote Reading", type="primary"):
                res = esp32_trigger(ip)
                if res["ok"]:
                    st.success("Reading started on device!")
                    # Clear stale result so dashboard shows the new reading
                    st.session_state.pop("last_result", None)
                else:
                    st.error(f"Failed: {res.get('error')}")
        if "dev_status" in st.session_state:
            s = st.session_state.dev_status
            if s["online"]:
                d = s["data"]
                st.markdown(
                    f"🟢 **Online** — `{d.get('device_id','?')}` | "
                    f"Step: `{d.get('step','?')}` | Last row: `{d.get('row_id','—')}`"
                )
            else:
                st.error(f"🔴 Offline — {s.get('error')}")

    # ── Auto-refresh (only when no result locked in session_state) ────────────
    # We deliberately suppress autorefresh while a result is shown so that
    # the page does not clear the prediction output.
    if "last_result" not in st.session_state:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=8_000, key="live_poll")
        except ImportError:
            st.caption("_(install streamlit-autorefresh for automatic polling)_")

    # =========================================================================
    # STATE: result already computed — show it until user clicks Done
    # =========================================================================
    if "last_result" in st.session_state:
        r = st.session_state.last_result

        pipeline_ui(3)
        st.markdown("<div class='result-badge'>✅ STEP 3 — Prediction Complete</div>",
                    unsafe_allow_html=True)
        st.markdown("")

        col_done, col_new, col_encyclopedia = st.columns([2, 4, 2])
        with col_done:
            if st.button("✅ Done — Start New Reading", type="primary"):
                st.session_state.pop("last_result", None)
                st.session_state.pop("last_pending_row", None)
                st.rerun()
        
        with col_encyclopedia:
            if st.button("📖 Generate Technical Encyclopedia", help="Create comprehensive technical PDF documentation"):
                with st.spinner("Generating comprehensive technical documentation..."):
                    try:
                        # First try the comprehensive version
                        import subprocess
                        result = subprocess.run([
                            sys.executable, "scripts/generate_comprehensive_encyclopedia.py"
                        ], capture_output=True, text=True, cwd=".")
                        
                        if result.returncode == 0:
                            # Find the generated PDF path from output
                            lines = result.stdout.split('\n')
                            pdf_path = None
                            for line in lines:
                                if "COMPREHENSIVE_Technical_Encyclopedia_" in line and line.endswith('.pdf'):
                                    pdf_path = line.split(': ')[-1]
                                    break
                            
                            if pdf_path and Path(pdf_path).exists():
                                with open(pdf_path, "rb") as f:
                                    pdf_bytes = f.read()
                                st.download_button(
                                    label="📥 Download COMPREHENSIVE Encyclopedia PDF",
                                    data=pdf_bytes,
                                    file_name=Path(pdf_path).name,
                                    mime="application/pdf",
                                    use_container_width=True
                                )
                                st.success(f"📖 COMPREHENSIVE Technical Encyclopedia generated! ({len(pdf_bytes):,} bytes)")
                            else:
                                st.success("✅ COMPREHENSIVE Encyclopedia generated! Check reports/ folder.")
                        else:
                            # Fallback to original version
                            st.warning("Comprehensive version failed, generating standard encyclopedia...")
                            result_fallback = subprocess.run([
                                sys.executable, "scripts/generate_technical_encyclopedia.py"
                            ], capture_output=True, text=True, cwd=".")
                            
                            if result_fallback.returncode == 0:
                                st.success("✅ Technical Encyclopedia generated! Check reports/ folder.")
                            else:
                                st.error(f"Generation failed: {result_fallback.stderr}")
                    except Exception as e:
                        st.error(f"Error generating encyclopedia: {e}")

        st.markdown("---")
        st.markdown("### 📊 Prediction & Clinical Decision Output")

        render_result_block(
            result       = r["result"],
            patient_name = r["patient_name"],
            ts           = r["ts"],
            row          = r.get("sensor_row"),
            key_suffix   = f"live_{r.get('row_id','0')}",
        )

        # Longitudinal trend for this patient
        hist = get_reading_history(sb, patient_name=r["patient_name"], limit=30)
        if not hist.empty:
            fig_t = plot_trend(hist, r["patient_name"])
            if fig_t:
                st.markdown("#### 📈 Patient Glycemic History")
                st.plotly_chart(fig_t, use_container_width=True,
                                key=f"trend_live_{r.get('row_id','0')}")

        st.stop()

    # =========================================================================
    # POLL SUPABASE for pending row
    # =========================================================================
    pending = get_pending_reading(sb)

    # ── STEP 1: no pending row ────────────────────────────────────────────────
    if pending is None:
        pipeline_ui(1)
        st.markdown("<div class='pending-badge'>⏳ WAITING — Press ESP32 Button to Start</div>",
                    unsafe_allow_html=True)
        st.markdown("")
        st.info(
            "**How to start a reading:**\n\n"
            "- **Physical button:** Press the tactile button on the ESP32 device\n"
            "- **Remote trigger:** Use the Device Control panel above\n\n"
            "The dashboard detects the reading within 8 seconds "
            "and prompts you to fill in patient details."
        )
        # Show most recent completed reading (compact)
        last = get_latest_reading(sb)
        if last and last.get("status") == "complete":
            bgl  = last.get("predicted_bgl_mg_dl")
            name = last.get("patient_name") or "—"
            zone = zone_short(last.get("clarke_zone") or "—")
            cat  = last.get("glucose_category") or "—"
            st.markdown("---")
            st.markdown("#### 📋 Most Recent Completed Reading")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Patient",       name)
            c2.metric("Blood Glucose", f"{float(bgl):.1f} mg/dL" if bgl else "—")
            c3.metric("Clarke Zone",   zone)
            c4.metric("Category",      cat)
            try:
                ts_str = pd.to_datetime(last["created_at"], utc=True).strftime("%Y-%m-%d %H:%M UTC")
                st.caption(f"Row ID: {last.get('id')} · {ts_str} · Device: {last.get('device_id','—')}")
            except Exception:
                pass
        st.stop()

    # ── STEP 2: pending row found — show sensor data + form ───────────────────
    pipeline_ui(2)
    st.markdown(
        "<div class='pending-badge'>"
        "📋 STEP 2 — Sensor data received! Enter patient details to generate prediction."
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    try:
        ts = pd.to_datetime(pending.get("created_at", ""), utc=True).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        ts = str(pending.get("created_at", ""))

    st.markdown(
        f"<div class='ts-small'>📅 Reading: <b>{ts}</b> &nbsp;·&nbsp; "
        f"Device: <b>{pending.get('device_id','—')}</b> &nbsp;·&nbsp; "
        f"Row ID: <b>{pending.get('id','—')}</b></div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # Sensor cards
    st.markdown("#### 🔬 Collected Sensor Readings (from ESP32)")
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.markdown(sc("Saliva pH",        pending.get("saliva_ph"),           "pH",  ".3f"), unsafe_allow_html=True)
    r1c2.markdown(sc("Heart Rate",       pending.get("hr_bpm"),              "BPM", ".1f"), unsafe_allow_html=True)
    r1c3.markdown(sc("Temperature",      pending.get("temperature_c"),       "°C",  ".2f"), unsafe_allow_html=True)
    r1c4.markdown(sc("Perfusion Index",  pending.get("perfusion_index"),     "%",   ".3f"), unsafe_allow_html=True)

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.markdown(sc("PPG DC Baseline",  pending.get("ppg_raw_dc_baseline"), "ADC", ".0f"), unsafe_allow_html=True)
    r2c2.markdown(sc("PPG AC Amplitude", pending.get("ppg_raw_ac_p2p"),      "ADC", ".1f"), unsafe_allow_html=True)
    r2c3.markdown(sc("Pulse Width",      pending.get("pulse_width_ms"),      "ms",  ".1f"), unsafe_allow_html=True)
    r2c4.markdown(sc("Row ID",           pending.get("id"),                  "",    "d"),   unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 👤 Enter Patient Details")
    st.caption("Fill in the fields and click **Submit** — prediction runs and results appear on the ESP32 display within 5 seconds.")

    with st.form("patient_form"):
        fc1, fc2 = st.columns(2)
        with fc1:
            name_in   = st.text_input("Patient / Subject Name *", placeholder="e.g. John Doe")
            age_in    = st.number_input("Age (years)", 18, 100, 45)
            gender_in = st.selectbox("Biological Gender", ["Male", "Female"])
            diag_in   = st.selectbox("Clinical Diagnosis",
                                     ["None / Unknown", "Prediabetes",
                                      "Type 1 Diabetes", "Type 2 Diabetes"])
        with fc2:
            height_in = st.number_input("Height (cm)", 100.0, 220.0, 170.0, 0.5)
            weight_in = st.number_input("Weight (kg)",  30.0, 200.0,  70.0, 0.5)
            bmi_calc  = round(weight_in / ((height_in / 100) ** 2), 1)
            st.markdown(f"**Computed BMI:** {bmi_calc} kg/m²")
            fasting_in = st.selectbox("Fasting State", ["Fasting", "Non-Fasting"])
            insulin_in = st.checkbox("Taking Insulin")
            oral_in    = st.checkbox("Taking Oral Medication")
            family_in  = st.checkbox("Family History of Diabetes")
            smoke_in   = st.checkbox("Smoker")

        submitted = st.form_submit_button("⚡ Submit & Generate Prediction",
                                          type="primary", use_container_width=True)

    if submitted:
        if not name_in.strip():
            st.warning("Please enter a patient name.")
            st.stop()

        diag_map = {"None / Unknown": "None", "Prediabetes": "Prediabetes",
                    "Type 1 Diabetes": "Type 1", "Type 2 Diabetes": "Type 2"}

        demo: Dict[str, Any] = {
            "age":                float(age_in),
            "bmi":                float(bmi_calc),
            "gender":             gender_in.lower(),
            "diabetes_diagnosis": diag_map[diag_in],
            "fasting":            1 if fasting_in == "Fasting" else 0,
            "med_taking_insulin": int(insulin_in),
            "med_taking_oral":    int(oral_in),
            "med_taking_any":     int(insulin_in or oral_in),
            "family_history":     int(family_in),
            "smoking":            int(smoke_in),
        }
        sensor_fields = [
            "saliva_ph", "hr_bpm", "ppg_raw_dc_baseline", "ppg_raw_ac_p2p",
            "perfusion_index", "pulse_width_ms", "temperature_c",
            "hrv_sdnn", "hrv_rmssd", "hrv_pnn50", "hrv_lf", "hrv_hf", "hrv_lf_hf_ratio",
        ]
        sensor = {f: float(pending[f]) for f in sensor_fields if pending.get(f) is not None}
        payload = {**demo, **sensor}

        with st.spinner("Running prediction model..."):
            result = predictor.predict_full_sensor(payload)

        pred_bgl = result.get("predicted_bgl_mg_dl", 0.0)
        ci       = result.get("confidence_interval_5th_95th", [pred_bgl-20, pred_bgl+20])
        zone_str = result.get("clarke_zone", "Zone A")
        is_ood   = result.get("is_out_of_distribution", False)
        ood_warn = result.get("ood_warning", "")
        cat      = glucose_category(pred_bgl)

        # Patch row in Supabase
        patch_reading(sb, pending["id"], {
            "status":               "complete",
            "patient_name":         name_in.strip(),
            "age":                  float(age_in),
            "bmi":                  float(bmi_calc),
            "gender":               gender_in.lower(),
            "diabetes_diagnosis":   diag_map[diag_in],
            "fasting":              1 if fasting_in == "Fasting" else 0,
            "med_taking_insulin":   int(insulin_in),
            "med_taking_oral":      int(oral_in),
            "family_history":       int(family_in),
            "smoking":              int(smoke_in),
            "predicted_bgl_mg_dl":  pred_bgl,
            "ci_low_mg_dl":         ci[0],
            "ci_high_mg_dl":        ci[1],
            "clarke_zone":          zone_str,
            "glucose_category":     cat,
            "is_ood":               is_ood,
            "ood_warning":          ood_warn,
        })

        # ── Lock result in session_state so autorefresh never erases it ───────
        st.session_state["last_result"] = {
            "result":      result,
            "patient_name": name_in.strip(),
            "ts":           ts,
            "sensor_row":   dict(pending),
            "row_id":       pending.get("id"),
        }
        st.rerun()   # triggers immediate redisplay via the "last_result" branch above

# =============================================================================
# TAB 2 — MANUAL ENTRY
# =============================================================================
with tab_manual:
    st.markdown("<div class='manual-badge'>✏️ MANUAL ENTRY MODE</div>", unsafe_allow_html=True)
    st.markdown("")

    man_c1, man_c2 = st.columns([1, 2])
    with man_c1:
        st.markdown("#### Patient Details")
        m_name   = st.text_input("Name", key="m_name", placeholder="Jane Doe")
        m_age    = st.number_input("Age", 18, 100, 45, key="m_age")
        m_gender = st.selectbox("Gender", ["Male", "Female"], key="m_gender")
        m_h      = st.number_input("Height (cm)", 100.0, 220.0, 170.0, 0.5, key="m_h")
        m_w      = st.number_input("Weight (kg)",  30.0, 200.0,  70.0, 0.5, key="m_w")
        m_bmi    = round(m_w / ((m_h / 100) ** 2), 1)
        st.markdown(f"**BMI:** {m_bmi}")
        m_diag   = st.selectbox("Diagnosis",
                                 ["None / Unknown","Prediabetes","Type 1 Diabetes","Type 2 Diabetes"],
                                 key="m_diag")
        m_fast   = st.selectbox("Fasting", ["Fasting","Non-Fasting"], key="m_fast")
        m_ins    = st.checkbox("Insulin",        key="m_ins")
        m_oral   = st.checkbox("Oral meds",      key="m_oral")
        m_fam    = st.checkbox("Family history", key="m_fam")
        m_smoke  = st.checkbox("Smoker",         key="m_smoke")

    with man_c2:
        st.markdown("#### Sensor Readings")
        m_ph   = st.number_input("Saliva pH",        4.0,  10.0,  7.25, 0.01, key="m_ph")
        m_hr   = st.number_input("Heart Rate (BPM)", 30.0, 200.0, 72.0, 0.5,  key="m_hr")
        m_tmp  = st.number_input("Temperature (C)",  34.0,  42.0, 36.6, 0.1,  key="m_tmp")
        m_dc   = st.number_input("PPG DC Baseline",  50000.0, 250000.0, 175000.0, 1000.0, key="m_dc")
        m_ac   = st.number_input("PPG AC Amplitude",   100.0,   8000.0,   1200.0,   50.0, key="m_ac")
        m_pi   = st.number_input("Perfusion %",          0.1,     10.0,      0.69,   0.01, key="m_pi")
        m_pw   = st.number_input("Pulse Width (ms)",  100.0, 500.0, 280.0, 5.0,  key="m_pw")
        m_sdnn = st.number_input("HRV SDNN (ms)",       0.0, 200.0,  42.0, 1.0,  key="m_sdnn")
        m_rmss = st.number_input("HRV RMSSD (ms)",      0.0, 200.0,  34.0, 1.0,  key="m_rmss")

        if st.button("⚡ Run Prediction", type="primary", use_container_width=True, key="man_run"):
            diag_map = {"None / Unknown":"None","Prediabetes":"Prediabetes",
                        "Type 1 Diabetes":"Type 1","Type 2 Diabetes":"Type 2"}
            payload = {
                "age": float(m_age), "bmi": float(m_bmi), "gender": m_gender.lower(),
                "diabetes_diagnosis": diag_map[m_diag],
                "fasting": 1 if m_fast == "Fasting" else 0,
                "med_taking_insulin": int(m_ins), "med_taking_oral": int(m_oral),
                "med_taking_any": int(m_ins or m_oral),
                "family_history": int(m_fam), "smoking": int(m_smoke),
                "saliva_ph": m_ph, "hr_bpm": m_hr, "temperature_c": m_tmp,
                "ppg_raw_dc_baseline": m_dc, "ppg_raw_ac_p2p": m_ac,
                "perfusion_index": m_pi, "pulse_width_ms": m_pw,
                "hrv_sdnn": m_sdnn, "hrv_rmssd": m_rmss,
            }
            with st.spinner("Running inference..."):
                result = predictor.predict_full_sensor(payload)

            ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            st.markdown("---")
            st.markdown("### 📊 Prediction Output")
            render_result_block(
                result, m_name, ts_now,
                row={"saliva_ph": m_ph, "hr_bpm": m_hr,
                     "temperature_c": m_tmp, "perfusion_index": m_pi},
                key_suffix=f"manual_{ts_now}",
            )

# =============================================================================
# TAB 3 — AUDIT LOG & PATIENT HISTORY
# =============================================================================
with tab_audit:
    st.markdown("### 📋 Audit Log — All Completed Readings")

    if sb is None:
        st.error("Supabase not configured.")
        st.stop()

    # Load all completed readings (up to 100)
    all_readings = get_reading_history(sb, patient_name=None, limit=100)

    if all_readings.empty:
        st.info("No completed readings yet. Complete a reading cycle to populate the audit log.")
        st.stop()

    # ── Summary table ─────────────────────────────────────────────────────────
    st.markdown(f"**{len(all_readings)} completed readings on record.**")

    display_cols = ["id", "created_at", "patient_name", "predicted_bgl_mg_dl",
                    "clarke_zone", "glucose_category", "device_id"]
    available = [c for c in display_cols if c in all_readings.columns]
    df_show = all_readings[available].copy()

    # Format timestamp
    if "created_at" in df_show.columns:
        df_show["created_at"] = pd.to_datetime(df_show["created_at"], utc=True).dt.strftime("%Y-%m-%d %H:%M UTC")

    # Shorten Clarke zone in table
    if "clarke_zone" in df_show.columns:
        df_show["clarke_zone"] = df_show["clarke_zone"].apply(
            lambda z: zone_short(str(z)) if pd.notna(z) else "—"
        )

    if "predicted_bgl_mg_dl" in df_show.columns:
        df_show["predicted_bgl_mg_dl"] = df_show["predicted_bgl_mg_dl"].apply(
            lambda v: f"{float(v):.1f}" if pd.notna(v) else "—"
        )

    st.dataframe(df_show, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Patient selector for detailed view ────────────────────────────────────
    st.markdown("#### 👤 View Patient Detail & Charts")

    patient_names = sorted([n for n in all_readings["patient_name"].dropna().unique() if n])
    if not patient_names:
        st.info("No readings with patient names yet.")
        st.stop()

    selected_patient = st.selectbox("Select patient:", patient_names, key="audit_patient")

    if selected_patient:
        patient_rows = all_readings[
            all_readings["patient_name"] == selected_patient
        ].sort_values("created_at", ascending=False)

        st.markdown(f"**{len(patient_rows)} reading(s) for {selected_patient}**")

        # Longitudinal trend
        fig_t = plot_trend(patient_rows, selected_patient)
        if fig_t:
            st.plotly_chart(fig_t, use_container_width=True,
                            key=f"audit_trend_{selected_patient}")
        else:
            st.info("Need at least 2 readings to draw trend line.")

        st.markdown("#### Reading Details (expand to see full results)")

        # Each reading as an expander with full charts
        for idx, row_data in patient_rows.iterrows():
            bgl  = row_data.get("predicted_bgl_mg_dl")
            ts_r = row_data.get("created_at", "")
            zone = zone_short(str(row_data.get("clarke_zone", "—")))
            cat  = row_data.get("glucose_category", "—")

            label = f"Row {row_data.get('id')} · {ts_r} · BGL: {float(bgl):.1f} mg/dL · {zone} · {cat}" \
                    if bgl else f"Row {row_data.get('id')} · {ts_r}"

            with st.expander(label, expanded=False):
                # Re-run prediction from stored values to get full result dict
                # (Supabase only stores the headline numbers, not the full dict)
                sensor_fields = [
                    "saliva_ph", "hr_bpm", "ppg_raw_dc_baseline", "ppg_raw_ac_p2p",
                    "perfusion_index", "pulse_width_ms", "temperature_c",
                ]
                demo_fields = [
                    "age", "bmi", "gender", "diabetes_diagnosis",
                    "fasting", "med_taking_insulin", "med_taking_oral", "family_history", "smoking",
                ]

                has_sensor = any(pd.notna(row_data.get(f)) for f in sensor_fields)

                if has_sensor:
                    payload: Dict[str, Any] = {}
                    for f in demo_fields:
                        v = row_data.get(f)
                        if pd.notna(v):
                            payload[f] = v
                    # Set defaults for required fields
                    payload.setdefault("age",                45.0)
                    payload.setdefault("bmi",                26.5)
                    payload.setdefault("gender",             "male")
                    payload.setdefault("diabetes_diagnosis", "None")
                    payload.setdefault("fasting",            1)
                    payload.setdefault("med_taking_insulin", 0)
                    payload.setdefault("med_taking_oral",    0)
                    payload.setdefault("med_taking_any",     0)
                    payload.setdefault("family_history",     0)
                    payload.setdefault("smoking",            0)

                    for f in sensor_fields:
                        v = row_data.get(f)
                        if pd.notna(v):
                            payload[f] = float(v)

                    with st.spinner("Loading..."):
                        try:
                            full_result = predictor.predict_full_sensor(payload)
                        except Exception as e:
                            st.error(f"Prediction error: {e}")
                            continue

                    render_result_block(
                        result       = full_result,
                        patient_name = selected_patient,
                        ts           = str(ts_r),
                        row          = dict(row_data),
                        key_suffix   = f"audit_{row_data.get('id', idx)}",
                    )
                else:
                    # Show stored values only
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Patient",       selected_patient)
                    c2.metric("Blood Glucose", f"{float(bgl):.1f} mg/dL" if bgl else "—")
                    c3.metric("Clarke Zone",   zone)
                    c4.metric("Category",      cat)
                    st.caption("Sensor data not available for this row — stored values shown.")
