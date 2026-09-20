"""
Non-Invasive Blood Glucose Prediction System
Live Sensor Dashboard - ESP32-S3 + Supabase + Real-Time Prediction

Architecture:
  ESP32-S3 -> Supabase (status=pending) -> This dashboard -> predict() -> Supabase (status=complete)
  ESP32 polls Supabase for status=complete -> shows results on display

Run: streamlit run app/live_dashboard.py
"""
from __future__ import annotations

import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np
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
    page_title="Glucose Monitor - Live Sensor Dashboard",
    page_icon="🩸",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# CSS - matches original dashboard palette exactly
# =============================================================================
CUSTOM_CSS = """
<style>
    .main-title {
        font-size: 2.1rem;
        font-weight: 800;
        color: #1e3a8a;
        margin-bottom: 0.1rem;
    }
    .sub-title {
        font-size: 1.0rem;
        color: #475569;
        margin-bottom: 1.1rem;
    }
    .live-badge {
        background: linear-gradient(135deg, #16a34a 0%, #4ade80 100%);
        color: #fff;
        padding: 6px 14px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.88rem;
        display: inline-block;
        margin-bottom: 0.6rem;
    }
    .pending-badge {
        background: linear-gradient(135deg, #92400e 0%, #d97706 100%);
        color: #fff;
        padding: 6px 14px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.88rem;
        display: inline-block;
        margin-bottom: 0.6rem;
    }
    .manual-badge {
        background: linear-gradient(135deg, #1e3a8a 0%, #0284c7 100%);
        color: #fff;
        padding: 6px 14px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.88rem;
        display: inline-block;
        margin-bottom: 0.6rem;
    }
    .badge-normal {
        background-color: #dcfce7;
        color: #166534;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: 700;
        border: 1px solid #86efac;
        display: inline-block;
    }
    .badge-prediabetes {
        background-color: #fef9c3;
        color: #854d0e;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: 700;
        border: 1px solid #fde047;
        display: inline-block;
    }
    .badge-diabetes {
        background-color: #fee2e2;
        color: #991b1b;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: 700;
        border: 1px solid #fca5a5;
        display: inline-block;
    }
    .badge-hypo {
        background-color: #e0e7ff;
        color: #3730a3;
        padding: 4px 12px;
        border-radius: 9999px;
        font-weight: 700;
        border: 1px solid #a5b4fc;
        display: inline-block;
    }
    .disclaimer-banner {
        background-color: #fffbeb;
        border-left: 5px solid #f59e0b;
        padding: 0.8rem 1.1rem;
        border-radius: 6px;
        margin-top: 0.8rem;
        color: #92400e;
        font-size: 0.90rem;
    }
    .disclaimer-critical {
        background-color: #fef2f2;
        border-left: 5px solid #ef4444;
        padding: 0.8rem 1.1rem;
        border-radius: 6px;
        margin-top: 0.8rem;
        color: #b91c1c;
        font-size: 0.90rem;
    }
    .sensor-card {
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 6px;
    }
    .sensor-label {
        color: #94a3b8;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 2px;
    }
    .sensor-value {
        color: #f1f5f9;
        font-size: 1.35rem;
        font-weight: 700;
    }
    .sensor-unit {
        color: #64748b;
        font-size: 0.78rem;
        margin-left: 3px;
    }
    .step-done    { color: #22c55e; font-weight: 700; }
    .step-active  { color: #fbbf24; font-weight: 700; }
    .step-waiting { color: #475569; }
    .ts-small { color: #64748b; font-size: 0.78rem; }
    .model-badge-fs {
        background: linear-gradient(135deg, #1e3a8a 0%, #0284c7 100%);
        color: #ffffff;
        padding: 6px 14px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.88rem;
        display: inline-block;
        margin-bottom: 0.6rem;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# =============================================================================
# SINGLETONS
# =============================================================================

@st.cache_resource(show_spinner="Loading model...")
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
sb = load_sb()

# =============================================================================
# CHART HELPERS - same style as dashboard.py
# =============================================================================

def plot_ci_gauge(bgl: float, ci_low: float, ci_high: float, ts: str) -> go.Figure:
    fig = go.Figure()
    fig.add_vrect(x0=40,  x1=70,  fillcolor="#dbeafe", opacity=0.45, layer="below", line_width=0, annotation_text="Hypo (<70)",      annotation_position="top left")
    fig.add_vrect(x0=70,  x1=140, fillcolor="#dcfce7", opacity=0.45, layer="below", line_width=0, annotation_text="Normal (70-140)", annotation_position="top left")
    fig.add_vrect(x0=140, x1=200, fillcolor="#fef9c3", opacity=0.45, layer="below", line_width=0, annotation_text="Elevated",        annotation_position="top left")
    fig.add_vrect(x0=200, x1=360, fillcolor="#fee2e2", opacity=0.45, layer="below", line_width=0, annotation_text="Severe (>=200)",  annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=[ci_low, ci_high], y=[0, 0], mode="lines",
        line=dict(color="#0284c7", width=14),
        name="90% Prediction Interval",
        hovertext=f"CI: [{ci_low:.1f} - {ci_high:.1f}] mg/dL",
    ))
    fig.add_trace(go.Scatter(
        x=[bgl], y=[0], mode="markers+text",
        marker=dict(color="#1e3a8a", size=18, symbol="diamond", line=dict(color="white", width=2)),
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


def plot_trend(df: pd.DataFrame, patient_name: str) -> Optional[go.Figure]:
    df2 = df.dropna(subset=["predicted_bgl_mg_dl"]).sort_values("created_at")
    if df2.empty or len(df2) < 2:
        return None
    color_map = []
    for cz in df2.get("clarke_zone", pd.Series(["Zone A"] * len(df2))):
        if "Zone A" in str(cz): color_map.append("#16a34a")
        elif "Zone B" in str(cz): color_map.append("#d97706")
        else: color_map.append("#dc2626")
    fig = go.Figure()
    fig.add_hrect(y0=70, y1=140, fillcolor="#dcfce7", opacity=0.35, layer="below", line_width=0,
                  annotation_text="Target Range (70-140 mg/dL)", annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=df2["created_at"], y=df2["predicted_bgl_mg_dl"],
        mode="lines+markers",
        line=dict(color="#0284c7", width=2.5, shape="spline"),
        marker=dict(color=color_map, size=11, line=dict(color="white", width=1.5)),
        name="Blood Glucose (mg/dL)",
    ))
    fig.update_layout(
        title=f"<b>Glycemic History for {patient_name}</b>",
        xaxis=dict(title="Time", showgrid=True),
        yaxis=dict(title="Predicted BGL (mg/dL)", range=[40, max(260.0, df2["predicted_bgl_mg_dl"].max() + 30)]),
        height=280, margin=dict(l=15, r=15, t=35, b=25),
    )
    return fig


def plot_sensor_bars(row: dict) -> go.Figure:
    sensors = [
        ("Saliva pH",   row.get("saliva_ph"),        7.0,  7.4,  "pH"),
        ("Temp (C)",    row.get("temperature_c"),    36.1, 37.2, "C"),
        ("HR (BPM)",    row.get("hr_bpm"),           60.0, 80.0, "BPM"),
        ("Perfusion %", row.get("perfusion_index"),   0.5,  5.0, "%"),
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
            texts.append(f"{val:.2f} {unit} (below normal {lo}-{hi})")
        elif val > hi:
            colors.append("#dc2626")
            texts.append(f"{val:.2f} {unit} (above normal {lo}-{hi})")
        else:
            colors.append("#16a34a")
            texts.append(f"{val:.2f} {unit} (normal {lo}-{hi})")
    fig = go.Figure(go.Bar(x=names, y=values, marker=dict(color=colors),
                           text=texts, textposition="auto"))
    fig.update_layout(
        title="<b>Sensor Readings vs Normal Reference Ranges</b>",
        yaxis=dict(title="Value"),
        height=260, margin=dict(l=15, r=15, t=35, b=25),
    )
    return fig


# =============================================================================
# HELPER FUNCTIONS - defined BEFORE any UI code calls them
# =============================================================================

def glucose_category(bgl: float) -> str:
    if bgl < 70:   return "Hypoglycemia"
    if bgl < 100:  return "Normal"
    if bgl < 126:  return "Prediabetes"
    if bgl < 180:  return "Elevated"
    return "Hyperglycemia"


def render_result_block(result: dict, patient_name: str, ts: str,
                        row: Optional[dict] = None):
    """Full prediction result display matching original dashboard.py style."""
    pred_bgl = result.get("predicted_bgl_mg_dl")

    if pred_bgl is None:
        risk_band = result.get("risk_band", "unknown")
        st.warning(f"Insufficient sensor data for BGL prediction. Demographic risk band: **{risk_band}**")
        return

    ci        = result.get("confidence_interval_5th_95th", [pred_bgl - 20, pred_bgl + 20])
    zone_str  = result.get("clarke_zone", "Zone A")
    is_ood    = result.get("is_out_of_distribution", False)
    ood_warn  = result.get("ood_warning", "")
    trend     = result.get("trend", "Stable")
    cat       = glucose_category(pred_bgl)

    # Model badge
    st.markdown("<div class='model-badge-fs'>MODEL A: Full-Sensor Multi-Modal Ensemble (R2=0.8528)</div>",
                unsafe_allow_html=True)

    # OOD warning
    if is_ood:
        st.markdown(f"<div class='disclaimer-critical'>⚠️ <b>OUT-OF-DISTRIBUTION INPUT:</b><br/>{ood_warn}</div>",
                    unsafe_allow_html=True)

    # Clinical status tier badge
    if pred_bgl < 70:
        tier_badge = "<span class='badge-hypo'>⚠️ ACUTE HYPOGLYCEMIA (&lt;70 mg/dL)</span>"
        tier_msg   = "Blood glucose critically low. Rapid-acting carbohydrate intake recommended."
    elif pred_bgl < 100:
        tier_badge = "<span class='badge-normal'>✅ NORMAL FASTING (70-99 mg/dL)</span>"
        tier_msg   = "Glycemic levels within healthy fasting baseline."
    elif pred_bgl < 126:
        tier_badge = "<span class='badge-prediabetes'>⚠️ PREDIABETES RANGE (100-125 mg/dL)</span>"
        tier_msg   = "Impaired fasting glucose. HbA1c follow-up recommended."
    elif pred_bgl < 180:
        tier_badge = "<span class='badge-diabetes'>🔶 ELEVATED (126-179 mg/dL)</span>"
        tier_msg   = "Elevated blood glucose consistent with diabetic threshold."
    else:
        tier_badge = "<span class='badge-diabetes'>🔴 SEVERE HYPERGLYCEMIA (&ge;180 mg/dL)</span>"
        tier_msg   = "Marked hyperglycemia. Clinical evaluation recommended."

    st.markdown(f"#### Clinical Status: {tier_badge}", unsafe_allow_html=True)
    st.caption(f"**Interpretation:** {tier_msg}")

    # 4-column metrics
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        st.metric("Estimated Blood Glucose", f"{pred_bgl:.1f} mg/dL")
    with col_m2:
        st.metric("90% Confidence Interval",
                  f"[{ci[0]:.1f}, {ci[1]:.1f}] mg/dL",
                  delta=f"Width: {ci[1]-ci[0]:.1f} mg/dL",
                  delta_color="off")
    with col_m3:
        zone_short = zone_str.split("(")[0].strip() if "(" in zone_str else zone_str
        st.metric("Clarke Zone", zone_short)
    with col_m4:
        st.metric("Category", cat)

    # CI Gauge chart
    st.markdown("#### 🎯 Prediction Interval vs Safety Zones")
    st.plotly_chart(plot_ci_gauge(pred_bgl, ci[0], ci[1], ts),
                    use_container_width=True, key=f"ci_{ts}")

    # Sensor bar chart (if sensor data available)
    if row is not None:
        st.markdown("#### 🩺 Sensor Readings vs Normal Ranges")
        st.plotly_chart(plot_sensor_bars(row), use_container_width=True, key=f"bars_{ts}")

    # Clinical interpretation messages
    if pred_bgl < 70:
        st.error("🚨 Hypoglycemia - below 70 mg/dL. Immediate attention required.")
    elif pred_bgl < 100:
        st.success("✅ Normal fasting range (70-99 mg/dL).")
    elif pred_bgl < 126:
        st.warning("⚠️ Prediabetes range (100-125 mg/dL). HbA1c follow-up recommended.")
    elif pred_bgl < 180:
        st.warning("⚠️ Elevated range (126-179 mg/dL).")
    else:
        st.error("🚨 Hyperglycemia - >= 180 mg/dL. Medication review recommended.")

    diag_conf = result.get("diagnosis_stratum_confidence", "")
    if diag_conf:
        st.caption(f"ℹ️ {diag_conf}")

    st.markdown(
        "<div class='disclaimer-banner'>"
        "🔬 <b>Research Prototype Notice:</b> Synthetic-data model only. "
        "Not a certified medical device. Not a substitute for clinical laboratory blood testing."
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Patient: {patient_name or '-'} · {ts}")


def show_completed_summary(row: dict):
    """Compact card for last completed reading shown on home screen."""
    bgl = row.get("predicted_bgl_mg_dl")
    if bgl is None:
        st.info("No prediction stored for this row.")
        return

    bgl = float(bgl)
    cat = glucose_category(bgl)

    col_colors = {"Normal": "#22c55e", "Prediabetes": "#f59e0b",
                  "Elevated": "#f97316", "Hyperglycemia": "#ef4444", "Hypoglycemia": "#3b82f6"}
    col_b = col_colors.get(cat, "#94a3b8")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Patient",     row.get("patient_name") or "-")
    c2.metric("Blood Glucose", f"{bgl:.1f} mg/dL")
    c3.metric("Clarke Zone", (row.get("clarke_zone") or "-").split("(")[0].strip())
    c4.metric("Category",    cat)

    try:
        ts_str = pd.to_datetime(row["created_at"], utc=True).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        ts_str = str(row.get("created_at", ""))
    st.caption(f"Row ID: {row.get('id')} · {ts_str} · Device: {row.get('device_id', '-')}")


def render_trend_section(patient_name: str, sb_client):
    """Render longitudinal trend chart for a patient."""
    if not patient_name or not patient_name.strip():
        return
    hist = get_reading_history(sb_client, patient_name=patient_name.strip(), limit=30)
    if hist.empty:
        st.info(f"No history yet for {patient_name}. Future readings will appear here.")
        return
    fig = plot_trend(hist, patient_name)
    if fig:
        st.markdown("#### 📈 Patient Glycemic History")
        st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# ESP32 HTTP HELPERS
# =============================================================================

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
# PAGE HEADER
# =============================================================================
st.markdown("<div class='main-title'>🩸 Non-Invasive Blood Glucose - Live Sensor Dashboard</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-title'>ESP32-S3 sensor node → Supabase → real-time prediction → device display</div>",
            unsafe_allow_html=True)

mode = st.radio("Mode", ["🔴 Live Sensor Mode", "✏️ Manual Entry"], horizontal=True)
is_live = mode.startswith("🔴")
st.markdown("---")

# =============================================================================
# LIVE SENSOR MODE
# =============================================================================
if is_live:
    if sb is None:
        st.error("Supabase not configured. Add `[supabase]` block to `.streamlit/secrets.toml`.")
        st.stop()

    # ── Step pipeline indicator ───────────────────────────────────────────────
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
                    st.session_state.pop("pending_row", None)
                else:
                    st.error(f"Failed: {res.get('error')}")

        if "dev_status" in st.session_state:
            s = st.session_state.dev_status
            if s["online"]:
                d = s["data"]
                st.markdown(
                    f"🟢 **Online** — `{d.get('device_id','?')}` | "
                    f"Step: `{d.get('step','?')}` | "
                    f"Last row: `{d.get('row_id','—')}`"
                )
            else:
                st.error(f"🔴 Offline — {s.get('error')}")

    st.markdown("")

    # ── Auto-refresh every 8s ─────────────────────────────────────────────────
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=8_000, key="live_poll")
    except ImportError:
        st.caption("_(install streamlit-autorefresh for automatic polling)_")

    # ── Check Supabase for pending row ────────────────────────────────────────
    pending = get_pending_reading(sb)

    # =========================================================================
    # STEP 1 — No pending row: waiting for ESP32 button press
    # =========================================================================
    if pending is None:
        pipeline_ui(1)
        st.markdown("<div class='pending-badge'>⏳ WAITING — Press ESP32 Button to Start</div>",
                    unsafe_allow_html=True)
        st.markdown("")

        st.info(
            "**How to start a reading:**\n\n"
            "- **Physical button:** Press the tactile button on the ESP32 device  \n"
            "- **Remote trigger:** Use the Device Control panel above  \n\n"
            "The dashboard detects the reading within 8 seconds and prompts for patient details."
        )

        # Show last completed reading if any
        last = get_latest_reading(sb)
        if last and last.get("status") == "complete":
            st.markdown("---")
            st.markdown("#### 📋 Most Recent Completed Reading")
            show_completed_summary(last)

            # Show trend for last patient
            last_name = last.get("patient_name", "")
            if last_name:
                render_trend_section(last_name, sb)

        st.stop()

    # =========================================================================
    # STEP 2 — Pending row found: show sensor values + user details form
    # =========================================================================
    pipeline_ui(2)
    st.markdown(
        "<div class='pending-badge'>📋 STEP 2 — Sensor data received! Enter patient details to generate prediction.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # Timestamp
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

    def sc(label, val, unit, fmt=".2f"):
        if val is None:
            body = "<span style='color:#475569'>— (fallback)</span>"
        else:
            try:    body = f"{float(val):{fmt}}"
            except: body = str(val)
        return (f"<div class='sensor-card'><div class='sensor-label'>{label}</div>"
                f"<div class='sensor-value'>{body}<span class='sensor-unit'>{unit}</span></div></div>")

    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.markdown(sc("Saliva pH",       pending.get("saliva_ph"),            "pH",   ".3f"), unsafe_allow_html=True)
    r1c2.markdown(sc("Heart Rate",      pending.get("hr_bpm"),               "BPM",  ".1f"), unsafe_allow_html=True)
    r1c3.markdown(sc("Temperature",     pending.get("temperature_c"),        "°C",   ".2f"), unsafe_allow_html=True)
    r1c4.markdown(sc("Perfusion Index", pending.get("perfusion_index"),      "%",    ".3f"), unsafe_allow_html=True)

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.markdown(sc("PPG DC Baseline", pending.get("ppg_raw_dc_baseline"),  "ADC",  ".0f"), unsafe_allow_html=True)
    r2c2.markdown(sc("PPG AC Amplitude",pending.get("ppg_raw_ac_p2p"),       "ADC",  ".1f"), unsafe_allow_html=True)
    r2c3.markdown(sc("Pulse Width",     pending.get("pulse_width_ms"),       "ms",   ".1f"), unsafe_allow_html=True)
    r2c4.markdown(sc("Row ID",          pending.get("id"),                   "",     "d"),   unsafe_allow_html=True)

    st.markdown("---")

    # ── Patient Details Form ──────────────────────────────────────────────────
    st.markdown("#### 👤 Enter Patient Details")
    st.caption(
        "Fill in the fields below and click **Submit** — "
        "the prediction will run and results appear on the ESP32 display within seconds."
    )

    with st.form("patient_form", clear_on_submit=False):
        fc1, fc2 = st.columns(2)

        with fc1:
            name_in    = st.text_input("Patient / Subject Name *", placeholder="e.g. John Doe")
            age_in     = st.number_input("Age (years)", 18, 100, 45)
            gender_in  = st.selectbox("Biological Gender", ["Male", "Female"])
            diag_in    = st.selectbox(
                "Clinical Diagnosis",
                ["None / Unknown", "Prediabetes", "Type 1 Diabetes", "Type 2 Diabetes"],
            )
        with fc2:
            height_in  = st.number_input("Height (cm)",  100.0, 220.0, 170.0, 0.5)
            weight_in  = st.number_input("Weight (kg)",   30.0, 200.0,  70.0, 0.5)
            bmi_calc   = round(weight_in / ((height_in / 100) ** 2), 1)
            st.markdown(f"**Computed BMI:** {bmi_calc} kg/m²")
            fasting_in = st.selectbox("Fasting State", ["Fasting", "Non-Fasting"])
            insulin_in = st.checkbox("Taking Insulin")
            oral_in    = st.checkbox("Taking Oral Medication")
            family_in  = st.checkbox("Family History of Diabetes")
            smoke_in   = st.checkbox("Smoker")

        submitted = st.form_submit_button(
            "⚡ Submit & Generate Prediction",
            type="primary", use_container_width=True,
        )

    # ── On Submit ─────────────────────────────────────────────────────────────
    if submitted:
        if not name_in.strip():
            st.warning("Please enter a patient name before submitting.")
            st.stop()

        diag_map = {
            "None / Unknown": "None", "Prediabetes": "Prediabetes",
            "Type 1 Diabetes": "Type 1", "Type 2 Diabetes": "Type 2",
        }

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
        sensor: Dict[str, Any] = {
            f: float(pending[f]) for f in sensor_fields if pending.get(f) is not None
        }

        payload = {**demo, **sensor}

        with st.spinner("Running prediction model..."):
            result = predictor.predict_full_sensor(payload)

        pred_bgl  = result.get("predicted_bgl_mg_dl", 0.0)
        ci        = result.get("confidence_interval_5th_95th", [pred_bgl - 20, pred_bgl + 20])
        zone_str  = result.get("clarke_zone", "Zone A")
        is_ood    = result.get("is_out_of_distribution", False)
        ood_warn  = result.get("ood_warning", "")
        cat       = glucose_category(pred_bgl)

        # Patch Supabase row to complete
        patch_ok = patch_reading(sb, pending["id"], {
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

        if patch_ok:
            st.success("✅ Results uploaded — the ESP32 display will update within 5 seconds.")
        else:
            st.warning("Prediction complete but Supabase update failed. Showing results below.")

        pipeline_ui(3)
        st.markdown("---")
        st.markdown("### 📊 Prediction & Clinical Decision Output")
        render_result_block(result, name_in.strip(), ts, row=pending)

        # Trend chart
        st.markdown("---")
        render_trend_section(name_in.strip(), sb)

        st.stop()

    st.stop()

# =============================================================================
# MANUAL ENTRY MODE
# =============================================================================
else:
    st.markdown("<div class='manual-badge'>✏️ MANUAL ENTRY MODE</div>", unsafe_allow_html=True)
    st.markdown("")

    with st.sidebar:
        st.markdown("### 🧑‍⚕️ Patient Context")
        sid_name   = st.text_input("Name", placeholder="Jane Doe")
        sid_age    = st.number_input("Age", 18, 100, 45)
        sid_gender = st.selectbox("Gender", ["Male", "Female"])
        sid_h      = st.number_input("Height (cm)", 100.0, 220.0, 170.0, 0.5)
        sid_w      = st.number_input("Weight (kg)",  30.0, 200.0,  70.0, 0.5)
        sid_bmi    = round(sid_w / ((sid_h / 100) ** 2), 1)
        st.markdown(f"**BMI:** {sid_bmi}")
        sid_diag   = st.selectbox("Diagnosis",
                                   ["None / Unknown", "Prediabetes", "Type 1 Diabetes", "Type 2 Diabetes"])
        sid_fast   = st.selectbox("Fasting", ["Fasting", "Non-Fasting"])
        sid_ins    = st.checkbox("Insulin")
        sid_oral   = st.checkbox("Oral meds")
        sid_fam    = st.checkbox("Family history")
        sid_smoke  = st.checkbox("Smoker")
        st.caption("⚠️ Research prototype. Not for clinical use.")

    diag_map = {
        "None / Unknown": "None", "Prediabetes": "Prediabetes",
        "Type 1 Diabetes": "Type 1", "Type 2 Diabetes": "Type 2",
    }

    with st.form("manual_form"):
        st.markdown("#### Sensor Readings")
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            m_ph   = st.number_input("Saliva pH",        4.0,  10.0,  7.25, 0.01)
            m_hr   = st.number_input("Heart Rate (BPM)", 30.0, 200.0, 72.0, 0.5)
            m_tmp  = st.number_input("Temperature (C)",  34.0,  42.0, 36.6, 0.1)
        with mc2:
            m_dc   = st.number_input("PPG DC Baseline",  50000.0, 250000.0, 175000.0, 1000.0)
            m_ac   = st.number_input("PPG AC Amplitude",   100.0,   8000.0,   1200.0,   50.0)
            m_pi   = st.number_input("Perfusion Index %",    0.1,     10.0,      0.69,   0.01)
        with mc3:
            m_pw   = st.number_input("Pulse Width (ms)", 100.0, 500.0, 280.0, 5.0)
            m_sdnn = st.number_input("HRV SDNN (ms)",      0.0, 200.0,  42.0, 1.0)
            m_rmss = st.number_input("HRV RMSSD (ms)",     0.0, 200.0,  34.0, 1.0)
        ok = st.form_submit_button("⚡ Run Prediction", type="primary", use_container_width=True)

    if ok:
        payload = {
            "age":                float(sid_age),
            "bmi":                float(sid_bmi),
            "gender":             sid_gender.lower(),
            "diabetes_diagnosis": diag_map[sid_diag],
            "fasting":            1 if sid_fast == "Fasting" else 0,
            "med_taking_insulin": int(sid_ins),
            "med_taking_oral":    int(sid_oral),
            "med_taking_any":     int(sid_ins or sid_oral),
            "family_history":     int(sid_fam),
            "smoking":            int(sid_smoke),
            "saliva_ph":          m_ph,
            "hr_bpm":             m_hr,
            "temperature_c":      m_tmp,
            "ppg_raw_dc_baseline": m_dc,
            "ppg_raw_ac_p2p":     m_ac,
            "perfusion_index":    m_pi,
            "pulse_width_ms":     m_pw,
            "hrv_sdnn":           m_sdnn,
            "hrv_rmssd":          m_rmss,
        }
        with st.spinner("Running inference..."):
            result = predictor.predict_full_sensor(payload)

        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        st.markdown("---")
        st.markdown("### 📊 Prediction & Clinical Decision Output")
        render_result_block(result, sid_name, ts_now,
                            row={"saliva_ph": m_ph, "hr_bpm": m_hr,
                                 "temperature_c": m_tmp, "perfusion_index": m_pi})
