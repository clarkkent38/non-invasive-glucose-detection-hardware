"""
Check current serving path compatibility with the 5 benchmark presets and extreme test cases
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from predict import GlucosePredictor
from scripts.train_models import clarke_error_grid_zone

predictor = GlucosePredictor()

test_cases = [
    {
        "name": "Benchmark 1: Healthy Adult",
        "ref": 88.0,
        "data": {
            "saliva_ph": 7.35, "temperature_c": 36.6, "spo2_pct": 99.0,
            "hr_bpm": 66.0, "ppg_raw_dc_baseline": 178000.0, "ppg_raw_ac_p2p": 1450.0,
            "perfusion_index": 0.81, "pulse_width_ms": 285.0, "vpg_max": 4800.0,
            "apg_a": 85.0, "apg_b": -55.0, "hrv_sdnn": 58.0, "hrv_rmssd": 52.0,
            "hrv_pnn50": 26.0, "hrv_lf_hf_ratio": 1.10, "age": 34.0, "bmi": 21.0,
            "diabetes_diagnosis": "None", "fasting": 1, "family_history": 0, "smoking": 0,
            "reference_bgl_mg_dl": 88.0
        }
    },
    {
        "name": "Benchmark 2: Prediabetes",
        "ref": 114.0,
        "data": {
            "saliva_ph": 6.95, "temperature_c": 36.5, "spo2_pct": 98.0,
            "hr_bpm": 76.0, "ppg_raw_dc_baseline": 172000.0, "ppg_raw_ac_p2p": 1180.0,
            "perfusion_index": 0.68, "pulse_width_ms": 275.0, "vpg_max": 4400.0,
            "apg_a": 78.0, "apg_b": -62.0, "hrv_sdnn": 36.0, "hrv_rmssd": 28.0,
            "hrv_pnn50": 10.0, "hrv_lf_hf_ratio": 1.65, "age": 52.0, "bmi": 27.4,
            "diabetes_diagnosis": "Prediabetes", "fasting": 1, "family_history": 1, "smoking": 0,
            "reference_bgl_mg_dl": 114.0
        }
    },
    {
        "name": "Benchmark 3: Type 2 Post-Prandial",
        "ref": 172.0,
        "data": {
            "saliva_ph": 6.60, "temperature_c": 36.9, "spo2_pct": 97.0,
            "hr_bpm": 84.0, "ppg_raw_dc_baseline": 164000.0, "ppg_raw_ac_p2p": 1850.0,
            "perfusion_index": 1.12, "pulse_width_ms": 298.0, "vpg_max": 5200.0,
            "apg_a": 72.0, "apg_b": -48.0, "hrv_sdnn": 26.0, "hrv_rmssd": 18.0,
            "hrv_pnn50": 6.0, "hrv_lf_hf_ratio": 2.20, "age": 59.0, "bmi": 29.7,
            "diabetes_diagnosis": "Type 2", "med_taking_oral": 1, "fasting": 0, "family_history": 1, "smoking": 1,
            "reference_bgl_mg_dl": 172.0
        }
    },
    {
        "name": "Benchmark 4: Severe Hyperglycemia",
        "ref": 265.0,
        "data": {
            "saliva_ph": 6.15, "temperature_c": 37.2, "spo2_pct": 96.0,
            "hr_bpm": 98.0, "ppg_raw_dc_baseline": 188000.0, "ppg_raw_ac_p2p": 2950.0,
            "perfusion_index": 1.57, "pulse_width_ms": 325.0, "vpg_max": 6100.0,
            "apg_a": 64.0, "apg_b": -38.0, "hrv_sdnn": 14.0, "hrv_rmssd": 8.0,
            "hrv_pnn50": 1.0, "hrv_lf_hf_ratio": 3.80, "age": 48.0, "bmi": 32.4,
            "diabetes_diagnosis": "Type 1", "med_taking_insulin": 1, "fasting": 0, "family_history": 1, "smoking": 1,
            "reference_bgl_mg_dl": 265.0
        }
    },
    {
        "name": "Benchmark 5: Acute Hypoglycemia",
        "ref": 62.0,
        "data": {
            "saliva_ph": 7.42, "temperature_c": 36.1, "spo2_pct": 99.0,
            "hr_bpm": 88.0, "ppg_raw_dc_baseline": 172000.0, "ppg_raw_ac_p2p": 1150.0,
            "perfusion_index": 0.66, "pulse_width_ms": 260.0, "vpg_max": 4100.0,
            "apg_a": 88.0, "apg_b": -75.0, "hrv_sdnn": 48.0, "hrv_rmssd": 44.0,
            "hrv_pnn50": 20.0, "hrv_lf_hf_ratio": 1.30, "age": 28.0, "bmi": 21.6,
            "diabetes_diagnosis": "Type 1", "med_taking_insulin": 1, "fasting": 1, "family_history": 0, "smoking": 0,
            "reference_bgl_mg_dl": 62.0
        }
    }
]

print("="*80)
print("TESTING CURRENT SERVING PATH (predict.py)")
print("="*80)

for tc in test_cases:
    res = predictor.predict_full_sensor(tc["data"])
    pred = res["predicted_bgl_mg_dl"]
    ref = tc["ref"]
    err = abs(pred - ref)
    zone = clarke_error_grid_zone(ref, pred)
    print(f"{tc['name']}:")
    print(f"  Reference: {ref:.1f} mg/dL | Predicted: {pred:.1f} mg/dL | Abs Error: {err:.1f} mg/dL | Clarke Zone: Zone {zone}")
    print(f"  CI: {res['confidence_interval_5th_95th']} | Width: {res['interval_width_mg_dl']}")
