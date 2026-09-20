"""
Non-Invasive Blood Glucose Prediction System
Live Sensor Dashboard — Supabase-connected extension

This file adds a "Live Sensor Mode" on top of the existing dashboard UI.
It DOES NOT modify app/dashboard.py or scripts/predict.py.

Architecture:
  ESP32-S3 → Supabase REST (INSERT) → this dashboard (SELECT) → predict()

Secrets (set in .streamlit/secrets.toml locally, or in Streamlit Cloud):
  [supabase]
  url  = "https://YOUR_PROJECT.supabase.co"
  key  = "YOUR_ANON_KEY"

Run locally:   streamlit run app/live_dashboard.py
Deploy:        point a second Streamlit Cloud app at this file in the same repo
"""

from __future__ import annotations

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# ── path setup (same as dashboard.py) ────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from predict import GlucosePredictor
from app.supabase_client import create_client, get_latest_reading, get_reading_history, patch_reading

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Non-Invasive Glucose — Live Sensor Dashboard",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── shared CSS (same palette as dashboard.py) ─────────────────────────────────
st.html("""
<style>
    .glucose-card {
        background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
        border-radius: 15px;
        border: 2px solid #64748b;
        padding: 20px;
        margin: 15px 0;
        text-align: center;
        box-shadow: 0 8px 25px rgba(0,0,0,0.3);
    }
    .glucose-normal { border-color: #22c55e !important; }
    .glucose-prediabetic { border-color: #f59e0b !important; }
    .glucose-diabetic { border-color: #ef4444 !important; }
    .metric-card { 
        background: #1e293b; 
        border: 1px solid #475569; 
        border-radius: 10px;
        padding: 15px; 
        margin: 8px 0;
        text-align: center;
    }
    .sensor-value { font-size: 1.2em; font-weight: bold; color: #e2e8f0; }
    .sensor-unit { font-size: 0.9em; color: #94a3b8; margin-left: 5px; }
    .ts-small { font-size: 0.85em; color: #94a3b8; }
    .esp32-status {
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 12px;
        margin: 10px 0;
    }
    .status-online { border-color: #22c55e !important; }
    .status-offline { border-color: #ef4444 !important; }
    .status-reading { border-color: #3b82f6 !important; }
</style>
""")

# ── ESP32 Device Control Functions ──────────────────────────────────────────
def get_esp32_status(device_ip: str) -> Dict[str, Any]:
    """Get ESP32 device status via HTTP"""
    try:
        response = requests.get(f"http://{device_ip}/status", timeout=5)
        if response.status_code == 200:
            return {"online": True, "data": response.json()}
        else:
            return {"online": False, "error": f"HTTP {response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"online": False, "error": str(e)}

def trigger_esp32_reading(device_ip: str) -> Dict[str, Any]:
    """Trigger a new sensor reading on ESP32"""
    try:
        response = requests.post(f"http://{device_ip}/start_reading", timeout=10)
        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        elif response.status_code == 409:
            return {"success": False, "error": "Reading already in progress"}
        else:
            return {"success": False, "error": f"HTTP {response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}

def render_esp32_control_panel(sb):
    """Render ESP32 device control panel"""
    st.markdown("#### 📡 ESP32 Device Control")
    
    # Device IP input (saved in session state)
    if "esp32_ip" not in st.session_state:
        st.session_state.esp32_ip = "192.168.1.100"  # Default IP
    
    col_ip, col_status, col_trigger = st.columns([2, 2, 2])
    
    with col_ip:
        device_ip = st.text_input("ESP32 IP Address:", 
                                 value=st.session_state.esp32_ip,
                                 key="esp32_ip_input",
                                 help="Enter the ESP32's local network IP address")
        st.session_state.esp32_ip = device_ip
    
    with col_status:
        check_status_btn = st.button("🔍 Check Device Status")
        
    with col_trigger:
        trigger_reading_btn = st.button("🚀 Start Remote Reading", type="primary")
    
    # Status display
    if check_status_btn or "esp32_status" not in st.session_state:
        with st.spinner(f"Checking device at {device_ip}..."):
            status = get_esp32_status(device_ip)
            st.session_state.esp32_status = status
    
    if "esp32_status" in st.session_state:
        status = st.session_state.esp32_status
        
        if status["online"]:
            data = status["data"]
            status_class = "status-reading" if data.get("reading_in_progress") else "status-online"
            
            st.markdown(f"""
            <div class="esp32-status {status_class}">
                <strong>🟢 Device Online</strong> — {device_ip}<br>
                Device ID: {data.get('device_id', '—')}<br>
                Reading in progress: {"Yes" if data.get('reading_in_progress') else "No"}<br>
                WiFi: {"Connected" if data.get('wifi_connected') else "Disconnected"}
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="esp32-status status-offline">
                <strong>🔴 Device Offline</strong><br>
                Error: {status.get('error', 'Unknown error')}<br>
                Check IP address and network connection
            </div>
            """, unsafe_allow_html=True)
    
    # Remote reading trigger
    if trigger_reading_btn:
        if "esp32_status" not in st.session_state or not st.session_state.esp32_status["online"]:
            st.error("❌ Cannot trigger reading: Device is offline. Check device status first.")
        else:
            with st.spinner(f"Triggering reading on {device_ip}..."):
                result = trigger_esp32_reading(device_ip)
                
                if result["success"]:
                    st.success("✅ Reading started successfully! The ESP32 will collect sensor data and upload to Supabase.")
                    st.info("💡 **Tip:** Wait 10-15 seconds, then click 'Fetch Latest Reading' to see the new data.")
                    # Clear cached status to force refresh
                    if "esp32_status" in st.session_state:
                        del st.session_state.esp32_status
                else:
                    st.error(f"❌ Failed to start reading: {result['error']}")
    
    st.markdown("---")

# ── original CSS (combined above) ─────────────────────────────────────────────
st.markdown("""
<style>
  .main-title  { font-size:2.1rem; font-weight:800; color:#1e3a8a; }
  .sub-title   { font-size:1.0rem; color:#475569; margin-bottom:1rem; }
  .live-badge  { background:linear-gradient(135deg,#16a34a,#4ade80);
                 color:#fff; padding:6px 14px; border-radius:8px;
                 font-weight:700; font-size:.88rem; display:inline-block; }
  .manual-badge{ background:linear-gradient(135deg,#1e3a8a,#0284c7);
                 color:#fff; padding:6px 14px; border-radius:8px;
                 font-weight:700; font-size:.88rem; display:inline-block; }
  .sensor-card { background:#0f172a; border:1px solid #334155;
                 border-radius:10px; padding:14px 18px; margin-bottom:8px; }
  .sensor-label{ color:#94a3b8; font-size:.78rem; text-transform:uppercase;
                 letter-spacing:.05em; }
  .sensor-value{ color:#f1f5f9; font-size:1.4rem; font-weight:700; }
  .sensor-unit { color:#64748b; font-size:.82rem; margin-left:4px; }
  .ts-small    { color:#64748b; font-size:.80rem; }
  .ood-banner  { background:#7f1d1d; border-left:4px solid #ef4444;
                 padding:10px 14px; border-radius:6px; color:#fca5a5;
                 margin:8px 0; font-size:.88rem; }
</style>
""", unsafe_allow_html=True)

# ── predictor singleton ───────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading model…")
def load_predictor() -> GlucosePredictor:
    return GlucosePredictor()

predictor = load_predictor()

# ── Supabase client (cached; None if secrets missing) ─────────────────────────
@st.cache_resource(show_spinner=False)
def load_supabase_client():
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as exc:
        return None   # secrets not configured — live mode will show an error

sb = load_supabase_client()

# =============================================================================
# HEADER
# =============================================================================
st.markdown("<div class='main-title'>📡 Non-Invasive Blood Glucose — Live Sensor Dashboard</div>",
            unsafe_allow_html=True)
st.markdown("<div class='sub-title'>Real-time inference from ESP32-S3 sensor node via Supabase</div>",
            unsafe_allow_html=True)

# ── Mode toggle ───────────────────────────────────────────────────────────────
mode = st.radio(
    "Input Mode",
    options=["🔴 Live Sensor Mode (ESP32 → Supabase)", "✏️ Manual Entry (same as main dashboard)"],
    horizontal=True,
    help="Live mode fetches the latest reading inserted by the ESP32. Manual mode lets you type values directly.",
)
is_live = mode.startswith("🔴")

st.markdown("---")

# =============================================================================
# ── SIDEBAR: patient context (both modes) ─────────────────────────────────────
# =============================================================================
with st.sidebar:
    st.markdown("### 🧑‍⚕️ Patient Context")
    st.caption("These demographic fields supplement sensor data for the prediction model. "
               "They are stored with each reading (Live mode) or used locally (Manual mode).")

    patient_name_input = st.text_input("Patient / Subject Name", value="",
                                       placeholder="Jane Doe")
    age_input     = st.number_input("Age (years)",  min_value=18, max_value=100, value=45)
    gender_input  = st.selectbox("Biological Gender", ["Male", "Female"])
    height_input  = st.number_input("Height (cm)", min_value=100.0, max_value=220.0, value=170.0, step=0.5)
    weight_input  = st.number_input("Weight (kg)", min_value=30.0,  max_value=200.0, value=70.0, step=0.5)
    computed_bmi  = round(weight_input / ((height_input / 100) ** 2), 1)
    st.markdown(f"**BMI:** {computed_bmi} kg/m²")

    diagnosis_input = st.selectbox(
        "Clinical Diagnosis",
        ["None / Unknown", "Prediabetes", "Type 1 Diabetes", "Type 2 Diabetes"],
    )
    diag_map = {"None / Unknown": "None", "Prediabetes": "Prediabetes",
                "Type 1 Diabetes": "Type 1", "Type 2 Diabetes": "Type 2"}
    diag_clean = diag_map[diagnosis_input]

    fasting_input    = st.selectbox("Fasting State", ["Fasting", "Non-Fasting"])
    fasting_val      = 1 if fasting_input == "Fasting" else 0
    insulin_input    = st.checkbox("Taking Insulin")
    oral_input       = st.checkbox("Taking Oral Medication")
    family_hist      = st.checkbox("Family History of Diabetes")
    smoking_input    = st.checkbox("Smoker")

    st.markdown("---")
    st.caption("⚠️ Synthetic-data model only. Not for clinical use.")

# Build demographic context dict (merged into payload for both modes)
demo_context: Dict[str, Any] = {
    "age":               float(age_input),
    "bmi":               float(computed_bmi),
    "gender":            gender_input.lower(),
    "diabetes_diagnosis": diag_clean,
    "fasting":           fasting_val,
    "med_taking_insulin": int(insulin_input),
    "med_taking_oral":   int(oral_input),
    "med_taking_any":    int(insulin_input or oral_input),
    "family_history":    int(family_hist),
    "smoking":           int(smoking_input),
}

# =============================================================================
# ── LIVE MODE ─────────────────────────────────────────────────────────────────
# =============================================================================
if is_live:
    if sb is None:
        st.error(
            "**Supabase not configured.** "
            "Add your credentials to `.streamlit/secrets.toml`:\n\n"
            "```toml\n[supabase]\nurl = \"https://YOUR_PROJECT.supabase.co\"\n"
            "key = \"YOUR_ANON_KEY\"\n```\n\n"
            "See SETUP_HARDWARE.md for full instructions."
        )
        st.stop()

    st.markdown("<div class='live-badge'>🟢 LIVE — Connected to Supabase</div>",
                unsafe_allow_html=True)
    st.markdown("")

    # ── Refresh controls ──────────────────────────────────────────────────────
    col_refresh, col_auto, col_spacer = st.columns([2, 3, 5])
    with col_refresh:
        fetch_btn = st.button("🔄 Fetch Latest Reading", type="primary")
    with col_auto:
        # streamlit-autorefresh: installs via requirements.txt
        # Default is OFF (manual refresh) to avoid hammering Supabase during demos.
        try:
            from streamlit_autorefresh import st_autorefresh
            auto_on = st.checkbox("⏱ Auto-refresh every 30 s", value=False)
            if auto_on:
                st_autorefresh(interval=30_000, key="live_autorefresh")
        except ImportError:
            st.caption("*(install streamlit-autorefresh to enable auto-refresh)*")

    # ── ESP32 Device Control Panel ────────────────────────────────────────────
    render_esp32_control_panel(sb)

    # ── Fetch reading ─────────────────────────────────────────────────────────
    # Fetch on button press OR on first load (session_state["live_reading"] absent)
    if fetch_btn or "live_reading" not in st.session_state:
        with st.spinner("Fetching latest reading from Supabase…"):
            raw_row = get_latest_reading(sb)
        if raw_row:
            st.session_state["live_reading"] = raw_row
        else:
            st.warning("No readings in the database yet. Press the ESP32 button to take a measurement.")
            st.stop()

    raw_row: Optional[dict] = st.session_state.get("live_reading")
    if not raw_row:
        st.info("Press **Fetch Latest Reading** to load a sensor reading.")
        st.stop()

    # ── Display raw sensor values ─────────────────────────────────────────────
    ts_raw = raw_row.get("created_at", "")
    try:
        ts_dt  = pd.to_datetime(ts_raw, utc=True).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        ts_dt  = str(ts_raw)

    st.markdown(f"<div class='ts-small'>📅 Last reading received: <b>{ts_dt}</b> &nbsp;·&nbsp; "
                f"Device: <b>{raw_row.get('device_id','—')}</b> &nbsp;·&nbsp; "
                f"Row ID: <b>{raw_row.get('id','—')}</b></div>",
                unsafe_allow_html=True)
    st.markdown("")

    st.markdown("#### 🔬 Raw Sensor Values (read-only, from ESP32)")

    # Six sensor cards in two rows of three
    def sensor_card(label: str, value, unit: str, fmt: str = ".2f") -> str:
        if value is None:
            disp = "<span style='color:#475569'>—  (fallback)</span>"
        else:
            try:
                disp = f"{float(value):{fmt}}"
            except Exception:
                disp = str(value)
        return (
            f"<div class='sensor-card'>"
            f"<div class='sensor-label'>{label}</div>"
            f"<div class='sensor-value'>{disp}"
            f"<span class='sensor-unit'>{unit}</span></div>"
            f"</div>"
        )

    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.markdown(sensor_card("Saliva pH",        raw_row.get("saliva_ph"),            "pH",   ".3f"), unsafe_allow_html=True)
    r1c2.markdown(sensor_card("Heart Rate",        raw_row.get("hr_bpm"),               "BPM",  ".1f"), unsafe_allow_html=True)
    r1c3.markdown(sensor_card("Temperature",       raw_row.get("temperature_c"),        "°C",   ".2f"), unsafe_allow_html=True)
    r1c4.markdown(sensor_card("Perfusion Index",   raw_row.get("perfusion_index"),      "%",    ".3f"), unsafe_allow_html=True)

    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.markdown(sensor_card("PPG DC Baseline",   raw_row.get("ppg_raw_dc_baseline"),  "ADC",  ".0f"), unsafe_allow_html=True)
    r2c2.markdown(sensor_card("PPG AC Amplitude",  raw_row.get("ppg_raw_ac_p2p"),       "ADC",  ".1f"), unsafe_allow_html=True)
    r2c3.markdown(sensor_card("Pulse Width",       raw_row.get("pulse_width_ms"),       "ms",   ".1f"), unsafe_allow_html=True)

    # HRV note
    hrv_present = any(raw_row.get(f) is not None for f in
                      ["hrv_sdnn","hrv_rmssd","hrv_pnn50","hrv_lf_hf_ratio"])
    if not hrv_present:
        r2c4.info("HRV not yet sent by firmware. predict.py uses training-mean fallbacks.")
    else:
        r2c4.markdown(sensor_card("HRV SDNN", raw_row.get("hrv_sdnn"), "ms"), unsafe_allow_html=True)

    st.markdown("---")

    # ── Build prediction payload ──────────────────────────────────────────────
    # Merge ESP32 sensor values + sidebar demographic context.
    # Only include non-None sensor fields — let predict.py handle missing ones
    # via its documented fallbacks (never pass None explicitly).
    sensor_payload: Dict[str, Any] = {}
    sensor_fields = [
        "saliva_ph", "hr_bpm", "ppg_raw_dc_baseline", "ppg_raw_ac_p2p",
        "perfusion_index", "pulse_width_ms", "temperature_c",
        "hrv_sdnn", "hrv_rmssd", "hrv_pnn50", "hrv_lf", "hrv_hf", "hrv_lf_hf_ratio",
    ]
    for field in sensor_fields:
        val = raw_row.get(field)
        if val is not None:
            sensor_payload[field] = float(val)

    prediction_payload = {**demo_context, **sensor_payload}

    # ── Run prediction ────────────────────────────────────────────────────────
    with st.spinner("Running inference…"):
        result = predictor.predict_full_sensor(prediction_payload)

    # Write prediction back to Supabase row (fire-and-forget)
    row_id = raw_row.get("id")
    if row_id:
        patch_reading(sb, row_id, {
            "predicted_bgl_mg_dl": result.get("predicted_bgl_mg_dl"),
            "clarke_zone":         result.get("clarke_zone", ""),
            "is_ood":              result.get("is_out_of_distribution", False),
            "patient_name":        patient_name_input.strip() or None,
        })

    # ── Display prediction results (same UI block as dashboard.py style) ──────
    _render_prediction_output(result, patient_name_input, ts_dt)

    st.markdown("---")

    # ── Trend chart ───────────────────────────────────────────────────────────
    st.markdown("#### 📈 Patient Reading History")
    history_df = get_reading_history(sb, patient_name=patient_name_input, limit=30)

    if history_df.empty:
        st.info("No history found. Readings will appear here after the first prediction is stored.")
    elif "predicted_bgl_mg_dl" not in history_df.columns or history_df["predicted_bgl_mg_dl"].dropna().empty:
        st.info("Readings exist but have no stored predictions yet — run a prediction first.")
    else:
        _render_trend_chart(history_df)

# =============================================================================
# ── MANUAL MODE ───────────────────────────────────────────────────────────────
# =============================================================================
else:
    st.markdown("<div class='manual-badge'>✏️ MANUAL ENTRY MODE</div>", unsafe_allow_html=True)
    st.markdown("")
    st.info(
        "**Manual Entry** lets you type in sensor values directly — identical to the main dashboard "
        "(`app/dashboard.py`), which remains untouched.  "
        "Use this mode for testing or when no ESP32 is connected."
    )

    with st.form("manual_sensor_form"):
        st.markdown("#### Sensor Readings")
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            m_ph    = st.number_input("Saliva pH",           min_value=4.0,  max_value=10.0, value=7.25,     step=0.01)
            m_hr    = st.number_input("Heart Rate (BPM)",    min_value=30.0, max_value=200.0, value=72.0,    step=0.5)
            m_temp  = st.number_input("Temperature (°C)",    min_value=34.0, max_value=42.0,  value=36.6,    step=0.1)
        with mc2:
            m_dc    = st.number_input("PPG DC Baseline",     min_value=50000.0, max_value=250000.0, value=175000.0, step=1000.0)
            m_ac    = st.number_input("PPG AC Amplitude",    min_value=100.0,   max_value=8000.0,   value=1200.0,   step=50.0)
            m_pi    = st.number_input("Perfusion Index (%)", min_value=0.1,     max_value=10.0,     value=0.69,     step=0.01)
        with mc3:
            m_pw    = st.number_input("Pulse Width (ms)",    min_value=100.0, max_value=500.0, value=280.0, step=5.0)
            m_sdnn  = st.number_input("HRV SDNN (ms)",       min_value=0.0,   max_value=200.0, value=42.0,  step=1.0)
            m_rmssd = st.number_input("HRV RMSSD (ms)",      min_value=0.0,   max_value=200.0, value=34.0,  step=1.0)

        ref_bgl = st.number_input(
            "Reference / Fingerstick BGL (mg/dL, optional — for Clarke zone validation)",
            min_value=0.0, max_value=600.0, value=0.0, step=1.0,
        )
        submitted = st.form_submit_button("⚡ Run Prediction", type="primary")

    if submitted:
        manual_payload: Dict[str, Any] = {**demo_context,
            "saliva_ph":           m_ph,
            "hr_bpm":              m_hr,
            "temperature_c":       m_temp,
            "ppg_raw_dc_baseline": m_dc,
            "ppg_raw_ac_p2p":      m_ac,
            "perfusion_index":     m_pi,
            "pulse_width_ms":      m_pw,
            "hrv_sdnn":            m_sdnn,
            "hrv_rmssd":           m_rmssd,
        }
        if ref_bgl > 0:
            manual_payload["reference_bgl_mg_dl"] = ref_bgl

        with st.spinner("Running inference…"):
            result = predictor.predict_full_sensor(manual_payload)

        _render_prediction_output(result, patient_name_input, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))


# =============================================================================
# ── Shared rendering helpers (used by both modes) ────────────────────────────
# =============================================================================

def _render_prediction_output(result: dict, patient_name: str, timestamp: str):
    """Display BGL, CI, Clarke zone, OOD warning — same visual style as dashboard.py."""
    pred_bgl = result.get("predicted_bgl_mg_dl")
    if pred_bgl is None:
        # predict() routed to tabular model — no sensor data sufficient
        risk_band = result.get("risk_band", "unknown")
        st.warning(f"Insufficient sensor data for BGL prediction. Risk band (demographics only): **{risk_band}**")
        return

    ci       = result.get("confidence_interval_5th_95th", [pred_bgl - 20, pred_bgl + 20])
    zone_str = result.get("clarke_zone", "")
    is_ood   = result.get("is_out_of_distribution", False)
    ood_warn = result.get("ood_warning", "")

    # OOD banner
    if is_ood:
        st.markdown(
            f"<div class='ood-banner'>⚠️ <b>OUT-OF-DISTRIBUTION INPUT:</b><br/>{ood_warn}</div>",
            unsafe_allow_html=True,
        )

    # Main result
    col_bgl, col_ci, col_zone = st.columns(3)

    # Colour by glucose range
    if pred_bgl < 70:
        bgl_color = "#ef4444"     # red — hypoglycemia
    elif pred_bgl < 140:
        bgl_color = "#22c55e"     # green — normal
    elif pred_bgl < 180:
        bgl_color = "#f59e0b"     # amber — elevated
    else:
        bgl_color = "#ef4444"     # red — hyperglycemia

    with col_bgl:
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.85rem;color:#94a3b8;text-transform:uppercase;'>"
            f"Predicted Blood Glucose</div>"
            f"<div style='font-size:3.0rem;font-weight:800;color:{bgl_color};'>"
            f"{pred_bgl:.1f}</div>"
            f"<div style='color:#64748b;font-size:.85rem;'>mg/dL</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with col_ci:
        ci_low, ci_high = ci[0], ci[1]
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.85rem;color:#94a3b8;text-transform:uppercase;'>"
            f"90% Confidence Interval</div>"
            f"<div style='font-size:1.8rem;font-weight:700;color:#f1f5f9;'>"
            f"{ci_low:.1f} – {ci_high:.1f}</div>"
            f"<div style='color:#64748b;font-size:.85rem;'>mg/dL</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with col_zone:
        # Clarke zone colour
        zone_letter = next((c for c in "ABCDE" if f"Zone {c}" in zone_str), "—")
        zone_colour = {"A":"#22c55e","B":"#84cc16","C":"#f59e0b","D":"#f97316","E":"#ef4444"}.get(zone_letter,"#94a3b8")
        st.markdown(
            f"<div style='text-align:center'>"
            f"<div style='font-size:.85rem;color:#94a3b8;text-transform:uppercase;'>"
            f"Clarke Zone</div>"
            f"<div style='font-size:3.0rem;font-weight:800;color:{zone_colour};'>"
            f"{zone_letter}</div>"
            f"<div style='color:#64748b;font-size:.80rem;'>{zone_str}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Confidence interval gauge
    ci_fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=pred_bgl,
        number={"suffix": " mg/dL", "font": {"size": 24}},
        gauge={
            "axis": {"range": [40, 400], "tickwidth": 1},
            "bar": {"color": bgl_color, "thickness": 0.3},
            "steps": [
                {"range": [40,  70],  "color": "#fca5a5"},  # hypo
                {"range": [70,  140], "color": "#bbf7d0"},  # normal
                {"range": [140, 180], "color": "#fef08a"},  # elevated
                {"range": [180, 400], "color": "#fca5a5"},  # hyper
            ],
            "threshold": {"line": {"color": "#f1f5f9","width":2},
                          "thickness": 0.75, "value": pred_bgl},
        },
        title={"text": f"Confidence interval: {ci_low:.0f}–{ci_high:.0f} mg/dL",
               "font": {"size": 13, "color": "#94a3b8"}},
    ))
    ci_fig.update_layout(height=220, margin=dict(t=20,b=10,l=10,r=10),
                         paper_bgcolor="rgba(0,0,0,0)", font_color="#f1f5f9")
    st.plotly_chart(ci_fig, use_container_width=True, key=f"gauge_{timestamp}")

    # Glucose range interpretation
    if pred_bgl < 70:
        st.error("🚨 **Hypoglycemia detected** — BGL below 70 mg/dL. Immediate clinical attention required.")
    elif pred_bgl < 100:
        st.success("✅ **Normal fasting range** — BGL 70–99 mg/dL.")
    elif pred_bgl < 126:
        st.warning("⚠️ **Prediabetes range** — BGL 100–125 mg/dL. Follow-up HbA1c test recommended.")
    elif pred_bgl < 180:
        st.warning("⚠️ **Elevated range** — BGL 126–179 mg/dL.")
    else:
        st.error("🚨 **Hyperglycemia** — BGL ≥ 180 mg/dL. Medication review recommended.")

    diag_conf = result.get("diagnosis_stratum_confidence", "")
    if diag_conf:
        st.caption(f"ℹ️ {diag_conf}")

    st.caption(
        f"⚠️ Validation: synthetic-data only — not for clinical use. "
        f"Patient: {patient_name or '—'} | {timestamp}"
    )


def _render_trend_chart(history_df: pd.DataFrame):
    """Longitudinal BGL trend from Supabase reading history."""
    df = history_df.dropna(subset=["predicted_bgl_mg_dl"]).sort_values("created_at")
    if df.empty:
        return

    fig = go.Figure()

    # Background zones
    for y0, y1, colour, label in [
        (40,  70,  "rgba(252,165,165,0.15)", "Hypoglycemia"),
        (70,  140, "rgba(187,247,208,0.12)", "Normal"),
        (140, 180, "rgba(254,240,138,0.15)", "Elevated"),
        (180, 400, "rgba(252,165,165,0.12)", "Hyperglycemia"),
    ]:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=colour, line_width=0,
                      annotation_text=label, annotation_font_size=10,
                      annotation_position="top left")

    # BGL line
    fig.add_trace(go.Scatter(
        x=df["created_at"], y=df["predicted_bgl_mg_dl"],
        mode="lines+markers",
        name="Predicted BGL",
        line=dict(color="#38bdf8", width=2),
        marker=dict(size=6),
        hovertemplate="%{x|%H:%M:%S}<br>BGL: %{y:.1f} mg/dL<extra></extra>",
    ))

    # OOD points
    ood_df = df[df.get("is_ood", False) == True] if "is_ood" in df.columns else pd.DataFrame()
    if not ood_df.empty:
        fig.add_trace(go.Scatter(
            x=ood_df["created_at"], y=ood_df["predicted_bgl_mg_dl"],
            mode="markers", name="OOD flagged",
            marker=dict(color="#f97316", size=10, symbol="x"),
        ))

    fig.update_layout(
        title="Glucose Prediction History",
        xaxis_title="Time",
        yaxis_title="Predicted BGL (mg/dL)",
        yaxis=dict(range=[40, 400]),
        legend=dict(orientation="h", y=-0.2),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.8)",
        font_color="#f1f5f9",
        margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
