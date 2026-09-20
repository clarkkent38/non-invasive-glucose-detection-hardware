"""
Non-Invasive Blood Glucose â€” Live Sensor Dashboard
====================================================
Step-by-step flow:
  1. ESP32 presses button â†’ collects sensors â†’ inserts row with status='pending'
  2. This dashboard detects the pending row and shows a user-details form
  3. User fills name / age / BMI / etc. and clicks Submit
  4. Dashboard runs predict(), patches row to status='complete' with results
  5. ESP32 polls Supabase, finds status='complete', shows results on TFT display

Run locally:   streamlit run app/live_dashboard.py
Deploy:        streamlit run streamlit_app.py  (Streamlit Cloud entry point)
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

# â”€â”€ Page config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
st.set_page_config(
    page_title="Glucose Monitor â€” Live",
    page_icon="ðŸ’‰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# â”€â”€ CSS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
st.markdown("""
<style>
  /* Page */
  .main-title  { font-size:2rem; font-weight:800; color:#0ea5e9; }
  .sub-title   { font-size:.95rem; color:#64748b; margin-bottom:.5rem; }

  /* Status badges */
  .badge-pending  { background:#92400e; color:#fef3c7;
                    padding:6px 14px; border-radius:6px;
                    font-weight:700; font-size:.85rem; display:inline-block; }
  .badge-live     { background:#065f46; color:#d1fae5;
                    padding:6px 14px; border-radius:6px;
                    font-weight:700; font-size:.85rem; display:inline-block; }
  .badge-manual   { background:#1e3a8a; color:#dbeafe;
                    padding:6px 14px; border-radius:6px;
                    font-weight:700; font-size:.85rem; display:inline-block; }

  /* Sensor reading cards */
  .sc { background:#0f172a; border:1px solid #334155; border-radius:10px;
        padding:12px 16px; margin-bottom:6px; }
  .sl { color:#94a3b8; font-size:.75rem; text-transform:uppercase;
        letter-spacing:.05em; margin-bottom:2px; }
  .sv { color:#f1f5f9; font-size:1.35rem; font-weight:700; }
  .su { color:#64748b; font-size:.78rem; margin-left:3px; }

  /* Step pipeline display */
  .step-done    { color:#22c55e; font-weight:700; }
  .step-active  { color:#fbbf24; font-weight:700; }
  .step-waiting { color:#475569; }

  /* Result headline */
  .result-box { background:#0f172a; border:2px solid #334155;
                border-radius:14px; padding:22px; margin:12px 0;
                text-align:center; }
  .result-normal   { border-color:#22c55e !important; }
  .result-pre      { border-color:#f59e0b !important; }
  .result-high     { border-color:#f97316 !important; }
  .result-danger   { border-color:#ef4444 !important; }

  /* OOD */
  .ood-warn { background:#7f1d1d; border-left:4px solid #ef4444;
              padding:10px 14px; border-radius:6px; color:#fca5a5;
              margin:8px 0; font-size:.88rem; }
  .ts-small { color:#64748b; font-size:.78rem; }
</style>
""", unsafe_allow_html=True)

# â”€â”€ Singletons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@st.cache_resource(show_spinner="Loading modelâ€¦")
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

# â”€â”€ ESP32 HTTP helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        if r.status_code == 200:   return {"ok": True}
        if r.status_code == 409:   return {"ok": False, "error": "Already reading"}
        return {"ok": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# â”€â”€ Glucose category helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def glucose_category(bgl: float) -> str:
    if bgl < 70:   return "Hypoglycemia"
    if bgl < 100:  return "Normal"
    if bgl < 126:  return "Prediabetes"
    if bgl < 180:  return "Elevated"
    return "Hyperglycemia"

def bgl_color(bgl: float) -> str:
    if bgl < 70 or bgl >= 180: return "#ef4444"
    if bgl >= 126:              return "#f97316"
    if bgl >= 100:              return "#f59e0b"
    return "#22c55e"

# =============================================================================
# â”€â”€ SHARED RENDER HELPERS â€” defined here so runpy sees them before any call â”€â”€â”€
# =============================================================================

def _render_result(result: dict, patient_name: str, ts: str,
                   pred_bgl: float, ci: list, zone_str: str,
                   cat: str, is_ood: bool, ood_warn: str):
    """Full results block â€” same style for both live and manual modes."""
    if is_ood:
        st.markdown(f"<div class='ood-warn'>âš ï¸ <b>Out-of-distribution input</b><br>{ood_warn}</div>",
                    unsafe_allow_html=True)

    col_bgl, col_ci, col_zone = st.columns(3)
    col_b = bgl_color(pred_bgl)

    with col_bgl:
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.82rem;color:#94a3b8;text-transform:uppercase;'>Predicted BGL</div>"
            f"<div style='font-size:3.2rem;font-weight:800;color:{col_b};'>{pred_bgl:.1f}</div>"
            f"<div style='color:#64748b;font-size:.82rem;'>mg/dL</div>"
            f"<div style='color:#64748b;font-size:.80rem;margin-top:4px;'>{cat}</div>"
            "</div>", unsafe_allow_html=True)

    with col_ci:
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.82rem;color:#94a3b8;text-transform:uppercase;'>90% CI</div>"
            f"<div style='font-size:1.9rem;font-weight:700;color:#f1f5f9;'>{ci[0]:.1f} â€“ {ci[1]:.1f}</div>"
            f"<div style='color:#64748b;font-size:.82rem;'>mg/dL</div>"
            "</div>", unsafe_allow_html=True)

    with col_zone:
        zl = next((c for c in "ABCDE" if f"Zone {c}" in zone_str), "A")
        zc = {"A":"#22c55e","B":"#84cc16","C":"#f59e0b","D":"#f97316","E":"#ef4444"}.get(zl,"#94a3b8")
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.82rem;color:#94a3b8;text-transform:uppercase;'>Clarke Zone</div>"
            f"<div style='font-size:3.2rem;font-weight:800;color:{zc};'>{zl}</div>"
            f"<div style='color:#64748b;font-size:.78rem;'>{zone_str}</div>"
            "</div>", unsafe_allow_html=True)

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=pred_bgl,
        number={"suffix":" mg/dL","font":{"size":22}},
        gauge={
            "axis": {"range":[40,400],"tickwidth":1},
            "bar":  {"color":col_b,"thickness":0.28},
            "steps":[
                {"range":[40,70],   "color":"rgba(239,68,68,.20)"},
                {"range":[70,100],  "color":"rgba(34,197,94,.18)"},
                {"range":[100,126], "color":"rgba(245,158,11,.18)"},
                {"range":[126,180], "color":"rgba(249,115,22,.18)"},
                {"range":[180,400], "color":"rgba(239,68,68,.20)"},
            ],
        },
        title={"text":f"CI: {ci[0]:.0f}â€“{ci[1]:.0f} mg/dL","font":{"size":12,"color":"#94a3b8"}},
    ))
    fig.update_layout(height=200, margin=dict(t=20,b=10,l=10,r=10),
                      paper_bgcolor="rgba(0,0,0,0)", font_color="#f1f5f9")
    st.plotly_chart(fig, use_container_width=True, key=f"gauge_{ts}")

    if pred_bgl < 70:
        st.error("ðŸš¨ Hypoglycemia â€” below 70 mg/dL. Immediate attention required.")
    elif pred_bgl < 100:
        st.success("âœ… Normal fasting range (70â€“99 mg/dL).")
    elif pred_bgl < 126:
        st.warning("âš ï¸ Prediabetes range (100â€“125 mg/dL). HbA1c follow-up recommended.")
    elif pred_bgl < 180:
        st.warning("âš ï¸ Elevated (126â€“179 mg/dL).")
    else:
        st.error("ðŸš¨ Hyperglycemia â€” â‰¥ 180 mg/dL. Medication review recommended.")

    st.caption(f"âš ï¸ Synthetic-data model â€” not for clinical use. "
               f"Patient: {patient_name or 'â€”'} Â· {ts}")


def _show_completed_result(row: dict):
    """Compact display of a completed row's prediction."""
    bgl = row.get("predicted_bgl_mg_dl")
    if bgl is None:
        st.info("No prediction stored for this row.")
        return
    col_b = bgl_color(bgl)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Patient",  row.get("patient_name") or "â€”")
    c2.markdown(f"<div class='sc'><div class='sl'>BGL</div>"
                f"<div class='sv' style='color:{col_b}'>{bgl:.1f}<span class='su'>mg/dL</span></div></div>",
                unsafe_allow_html=True)
    c3.metric("Clarke Zone", row.get("clarke_zone", "â€”"))
    c4.metric("Category",    row.get("glucose_category", "â€”"))
    try:
        ts = pd.to_datetime(row["created_at"], utc=True).strftime("%Y-%m-%d %H:%M UTC")
        st.caption(f"Row ID: {row.get('id')} Â· {ts} Â· Device: {row.get('device_id','â€”')}")
    except Exception:
        pass


def _render_trend(df: pd.DataFrame):
    df2 = df.dropna(subset=["predicted_bgl_mg_dl"]).sort_values("created_at")
    if df2.empty:
        return
    fig = go.Figure()
    for y0, y1, col, lbl in [
        (40,  70,  "rgba(239,68,68,.15)",  "Hypo"),
        (70,  100, "rgba(34,197,94,.12)",  "Normal"),
        (100, 126, "rgba(245,158,11,.12)", "Prediabetes"),
        (126, 180, "rgba(249,115,22,.12)", "Elevated"),
        (180, 400, "rgba(239,68,68,.12)",  "Hyper"),
    ]:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=col, line_width=0,
                      annotation_text=lbl, annotation_font_size=9,
                      annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=df2["created_at"], y=df2["predicted_bgl_mg_dl"],
        mode="lines+markers", name="BGL",
        line=dict(color="#38bdf8", width=2), marker=dict(size=6),
        hovertemplate="%{x|%H:%M:%S}<br>%{y:.1f} mg/dL<extra></extra>",
    ))
    fig.update_layout(
        title="Glucose Prediction History",
        xaxis_title="Time", yaxis_title="Predicted BGL (mg/dL)",
        yaxis=dict(range=[40, 400]),
        height=350, paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.8)", font_color="#f1f5f9",
        margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

# =============================================================================
# â”€â”€ HEADER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# =============================================================================
st.markdown("<div class='main-title'>ðŸ’‰ Non-Invasive Blood Glucose â€” Live Dashboard</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-title'>ESP32-S3 sensor node â†’ Supabase â†’ real-time prediction</div>",
            unsafe_allow_html=True)

mode = st.radio("Mode", ["ðŸ”´ Live Sensor Mode", "âœï¸ Manual Entry"],
                horizontal=True)
is_live = mode.startswith("ðŸ”´")
st.markdown("---")

# =============================================================================
# â”€â”€ LIVE MODE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# =============================================================================
if is_live:
    if sb is None:
        st.error("Supabase not configured. Add credentials to `.streamlit/secrets.toml`.")
        st.code("[supabase]\nurl = \"https://mjcwhnkyojfaezydvpsp.supabase.co\"\n"
                "key = \"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...\"", language="toml")
        st.stop()

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # STEP PIPELINE INDICATOR
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def pipeline_ui(active_step: int):
        """active_step: 1=waiting for sensor, 2=enter details, 3=done"""
        steps = ["â‘  Sensor Collection", "â‘¡ Enter User Details", "â‘¢ Prediction Result"]
        cols = st.columns(3)
        for i, (col, label) in enumerate(zip(cols, steps), start=1):
            if i < active_step:
                col.markdown(f"<div class='step-done'>âœ… {label}</div>", unsafe_allow_html=True)
            elif i == active_step:
                col.markdown(f"<div class='step-active'>â–¶ {label}</div>", unsafe_allow_html=True)
            else:
                col.markdown(f"<div class='step-waiting'>â—‹ {label}</div>", unsafe_allow_html=True)
        st.markdown("")

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # DEVICE CONTROL PANEL (collapsible)
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    with st.expander("ðŸ“¡ ESP32 Device Control (remote trigger)", expanded=False):
        if "esp32_ip" not in st.session_state:
            st.session_state.esp32_ip = "192.168.1.100"

        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            ip = st.text_input("ESP32 IP Address", value=st.session_state.esp32_ip,
                               key="ip_input")
            st.session_state.esp32_ip = ip
        with c2:
            if st.button("ðŸ” Check Status"):
                st.session_state.dev_status = esp32_status(ip)
        with c3:
            if st.button("ðŸš€ Start Remote Reading", type="primary"):
                res = esp32_trigger(ip)
                if res["ok"]:
                    st.success("Reading started on device!")
                    # clear any cached pending row so we pick up the new one
                    st.session_state.pop("pending_row", None)
                else:
                    st.error(f"Failed: {res.get('error')}")

        if "dev_status" in st.session_state:
            s = st.session_state.dev_status
            if s["online"]:
                d = s["data"]
                busy = d.get("reading_in_progress", False)
                st.markdown(
                    f"ðŸŸ¢ **Online** â€” Device: `{d.get('device_id','?')}` &nbsp;|&nbsp; "
                    f"Reading: {'â³ In progress' if busy else 'âœ… Idle'} &nbsp;|&nbsp; "
                    f"Last row: `{d.get('last_row_id','â€”')}`"
                )
            else:
                st.error(f"ðŸ”´ Offline â€” {s.get('error')}")

    st.markdown("")

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # AUTO-REFRESH every 8 seconds so pending row is detected automatically
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=8_000, key="live_poll")
    except ImportError:
        st.caption("*(install streamlit-autorefresh for automatic polling)*")

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # CHECK FOR PENDING ROW
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    pending = get_pending_reading(sb)

    # =====================================================================
    # STEP 1 â€” No pending row: waiting for ESP32
    # =====================================================================
    if pending is None:
        pipeline_ui(1)
        st.markdown("<div class='badge-pending'>â³ WAITING â€” Press ESP32 Button to Start</div>",
                    unsafe_allow_html=True)
        st.markdown("")

        st.info(
            "**How to start a reading:**\n\n"
            "â€¢ **Physical button:** Press the tactile button on the ESP32 device\n\n"
            "â€¢ **Remote trigger:** Use the Device Control panel above\n\n"
            "The dashboard will automatically detect the reading within 8 seconds "
            "and prompt you to fill in patient details."
        )

        # Show last completed reading if any
        last = get_latest_reading(sb)
        if last and last.get("status") == "complete":
            st.markdown("---")
            st.markdown("#### ðŸ“‹ Most Recent Completed Reading")
            _show_completed_result(last)

        st.stop()

    # =====================================================================
    # STEP 2 â€” Pending row found: show sensor values + user details form
    # =====================================================================
    pipeline_ui(2)
    st.markdown("<div class='badge-pending'>ðŸ“‹ STEP 2 â€” Enter Patient Details to Generate Prediction</div>",
                unsafe_allow_html=True)
    st.markdown("")

    # Timestamp
    ts_raw = pending.get("created_at", "")
    try:
        ts = pd.to_datetime(ts_raw, utc=True).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        ts = str(ts_raw)

    st.markdown(
        f"<div class='ts-small'>ðŸ“… Reading received: <b>{ts}</b> &nbsp;Â·&nbsp; "
        f"Device: <b>{pending.get('device_id','â€”')}</b> &nbsp;Â·&nbsp; "
        f"Row ID: <b>{pending.get('id','â€”')}</b></div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # â”€â”€ Sensor values (read-only display) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    st.markdown("#### ðŸ”¬ Collected Sensor Readings")

    def sc(label, val, unit, fmt=".2f"):
        if val is None:
            body = "<span style='color:#475569'>â€” (fallback)</span>"
        else:
            try:    body = f"{float(val):{fmt}}"
            except: body = str(val)
        return (f"<div class='sc'><div class='sl'>{label}</div>"
                f"<div class='sv'>{body}<span class='su'>{unit}</span></div></div>")

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(sc("Saliva pH",       pending.get("saliva_ph"),            "pH",   ".3f"), unsafe_allow_html=True)
    c2.markdown(sc("Heart Rate",      pending.get("hr_bpm"),               "BPM",  ".1f"), unsafe_allow_html=True)
    c3.markdown(sc("Temperature",     pending.get("temperature_c"),        "Â°C",   ".2f"), unsafe_allow_html=True)
    c4.markdown(sc("Perfusion Index", pending.get("perfusion_index"),      "%",    ".3f"), unsafe_allow_html=True)

    c5, c6, c7, c8 = st.columns(4)
    c5.markdown(sc("PPG DC Baseline", pending.get("ppg_raw_dc_baseline"),  "ADC",  ".0f"), unsafe_allow_html=True)
    c6.markdown(sc("PPG AC Amplitude",pending.get("ppg_raw_ac_p2p"),       "ADC",  ".1f"), unsafe_allow_html=True)
    c7.markdown(sc("Pulse Width",     pending.get("pulse_width_ms"),       "ms",   ".1f"), unsafe_allow_html=True)
    c8.markdown(sc("Row ID",          pending.get("id"),                   "",     "d"),   unsafe_allow_html=True)

    st.markdown("---")

    # â”€â”€ User details form â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    st.markdown("#### ðŸ‘¤ Enter Patient Details")
    st.caption("Fill in the fields below and click **Submit** â€” the prediction will run "
               "and results will appear on the ESP32 display within seconds.")

    with st.form("patient_details_form", clear_on_submit=False):
        fc1, fc2 = st.columns(2)

        with fc1:
            name_in    = st.text_input("Patient / Subject Name *",
                                       placeholder="e.g. John Doe")
            age_in     = st.number_input("Age (years)",  18, 100, 45)
            gender_in  = st.selectbox("Biological Gender", ["Male", "Female"])
            diag_in    = st.selectbox("Clinical Diagnosis",
                                      ["None / Unknown", "Prediabetes",
                                       "Type 1 Diabetes", "Type 2 Diabetes"])

        with fc2:
            height_in  = st.number_input("Height (cm)",  100.0, 220.0, 170.0, 0.5)
            weight_in  = st.number_input("Weight (kg)",   30.0, 200.0,  70.0, 0.5)
            bmi_calc   = round(weight_in / ((height_in / 100) ** 2), 1)
            st.markdown(f"**Computed BMI:** {bmi_calc} kg/mÂ²")
            fasting_in = st.selectbox("Fasting State", ["Fasting", "Non-Fasting"])
            insulin_in = st.checkbox("Taking Insulin")
            oral_in    = st.checkbox("Taking Oral Medication")
            family_in  = st.checkbox("Family History of Diabetes")
            smoke_in   = st.checkbox("Smoker")

        submitted = st.form_submit_button("âš¡ Submit & Generate Prediction",
                                          type="primary", use_container_width=True)

    # â”€â”€ On Submit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if submitted:
        if not name_in.strip():
            st.warning("Please enter a patient name before submitting.")
            st.stop()

        diag_map = {"None / Unknown":"None","Prediabetes":"Prediabetes",
                    "Type 1 Diabetes":"Type 1","Type 2 Diabetes":"Type 2"}

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

        sensor_fields = ["saliva_ph","hr_bpm","ppg_raw_dc_baseline","ppg_raw_ac_p2p",
                         "perfusion_index","pulse_width_ms","temperature_c",
                         "hrv_sdnn","hrv_rmssd","hrv_pnn50","hrv_lf","hrv_hf","hrv_lf_hf_ratio"]
        sensor: Dict[str, Any] = {
            f: float(pending[f]) for f in sensor_fields if pending.get(f) is not None
        }

        payload = {**demo, **sensor}

        with st.spinner("Running prediction modelâ€¦"):
            result = predictor.predict_full_sensor(payload)

        pred_bgl  = result.get("predicted_bgl_mg_dl", 0.0)
        ci        = result.get("confidence_interval_5th_95th", [pred_bgl - 20, pred_bgl + 20])
        zone_str  = result.get("clarke_zone", "Zone A")
        is_ood    = result.get("is_out_of_distribution", False)
        ood_warn  = result.get("ood_warning", "")
        cat       = glucose_category(pred_bgl)

        # Patch Supabase row â†’ status='complete'
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
            st.success("âœ… Results uploaded â€” the ESP32 display will update within 5 seconds.")
        else:
            st.warning("Prediction complete but Supabase update failed. Results shown below.")

        # Show results on dashboard
        pipeline_ui(3)
        _render_result(result, name_in.strip(), ts,
                       pred_bgl, ci, zone_str, cat, is_ood, ood_warn)

        # Trend chart
        st.markdown("---")
        st.markdown("#### ðŸ“ˆ Patient History")
        hist = get_reading_history(sb, patient_name=name_in.strip(), limit=30)
        if not hist.empty and "predicted_bgl_mg_dl" in hist.columns:
            _render_trend(hist)

        st.stop()

    st.stop()


# =============================================================================
# â”€â”€ MANUAL ENTRY MODE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# =============================================================================
else:
    st.markdown("<div class='badge-manual'>âœï¸ MANUAL ENTRY</div>", unsafe_allow_html=True)
    st.markdown("")

    with st.sidebar:
        st.markdown("### ðŸ§‘â€âš•ï¸ Patient Context")
        sid_name    = st.text_input("Name", placeholder="Jane Doe")
        sid_age     = st.number_input("Age", 18, 100, 45)
        sid_gender  = st.selectbox("Gender", ["Male", "Female"])
        sid_h       = st.number_input("Height (cm)", 100.0, 220.0, 170.0, 0.5)
        sid_w       = st.number_input("Weight (kg)",  30.0, 200.0,  70.0, 0.5)
        sid_bmi     = round(sid_w / ((sid_h / 100) ** 2), 1)
        st.markdown(f"**BMI:** {sid_bmi}")
        sid_diag    = st.selectbox("Diagnosis",
                                   ["None / Unknown","Prediabetes","Type 1 Diabetes","Type 2 Diabetes"])
        sid_fast    = st.selectbox("Fasting", ["Fasting", "Non-Fasting"])
        sid_ins     = st.checkbox("Insulin")
        sid_oral    = st.checkbox("Oral meds")
        sid_fam     = st.checkbox("Family history")
        sid_smoke   = st.checkbox("Smoker")
        st.caption("âš ï¸ Research prototype. Not for clinical use.")

    diag_map = {"None / Unknown":"None","Prediabetes":"Prediabetes",
                "Type 1 Diabetes":"Type 1","Type 2 Diabetes":"Type 2"}

    with st.form("manual_form"):
        st.markdown("#### Sensor Readings")
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            m_ph   = st.number_input("Saliva pH",        4.0,  10.0,  7.25, 0.01)
            m_hr   = st.number_input("Heart Rate (BPM)", 30.0, 200.0, 72.0, 0.5)
            m_tmp  = st.number_input("Temperature (Â°C)", 34.0,  42.0, 36.6, 0.1)
        with mc2:
            m_dc   = st.number_input("PPG DC Baseline",  50000.0, 250000.0, 175000.0, 1000.0)
            m_ac   = st.number_input("PPG AC Amplitude",   100.0,   8000.0,   1200.0,   50.0)
            m_pi   = st.number_input("Perfusion Index (%",   0.1,     10.0,      0.69,   0.01)
        with mc3:
            m_pw   = st.number_input("Pulse Width (ms)", 100.0, 500.0, 280.0, 5.0)
            m_sdnn = st.number_input("HRV SDNN (ms)",      0.0, 200.0,  42.0, 1.0)
            m_rmss = st.number_input("HRV RMSSD (ms)",     0.0, 200.0,  34.0, 1.0)
        ok = st.form_submit_button("âš¡ Run Prediction", type="primary",
                                   use_container_width=True)

    if ok:
        payload = {
            "age": float(sid_age), "bmi": float(sid_bmi),
            "gender": sid_gender.lower(),
            "diabetes_diagnosis": diag_map[sid_diag],
            "fasting": 1 if sid_fast == "Fasting" else 0,
            "med_taking_insulin": int(sid_ins), "med_taking_oral": int(sid_oral),
            "med_taking_any": int(sid_ins or sid_oral),
            "family_history": int(sid_fam), "smoking": int(sid_smoke),
            "saliva_ph": m_ph, "hr_bpm": m_hr, "temperature_c": m_tmp,
            "ppg_raw_dc_baseline": m_dc, "ppg_raw_ac_p2p": m_ac,
            "perfusion_index": m_pi, "pulse_width_ms": m_pw,
            "hrv_sdnn": m_sdnn, "hrv_rmssd": m_rmss,
        }
        with st.spinner("Running inferenceâ€¦"):
            result = predictor.predict_full_sensor(payload)

        pred_bgl = result.get("predicted_bgl_mg_dl", 0.0)
        ci       = result.get("confidence_interval_5th_95th", [pred_bgl-20, pred_bgl+20])
        zone_str = result.get("clarke_zone", "Zone A")
        is_ood   = result.get("is_out_of_distribution", False)
        ood_warn = result.get("ood_warning", "")
        cat      = glucose_category(pred_bgl)
        ts_now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        _render_result(result, sid_name, ts_now,
                       pred_bgl, ci, zone_str, cat, is_ood, ood_warn)

