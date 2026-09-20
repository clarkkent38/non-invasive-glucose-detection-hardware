import pandas as pd
import numpy as np
import pickle
import json
from pathlib import Path

import sys
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

with open(BASE_DIR / 'models' / 'production_model_full_sensor_stacked.pkl', 'rb') as f:
    bundle = pickle.load(f)

with open(BASE_DIR / 'models' / 'scaler_full_sensor.pkl', 'rb') as f:
    scaler = pickle.load(f)

with open(BASE_DIR / 'reports' / 'features_manifest_full_sensor.json') as f:
    manifest = json.load(f)

def prepare_features(raw_dict):
    raw_dc = float(raw_dict.get('ppg_raw_dc_baseline', 174000.0))
    raw_ac = float(raw_dict.get('ppg_raw_ac_p2p', 2500.0))
    sys_peak = float(raw_dict.get('ppg_systolic_peak', raw_dc + 0.5 * raw_ac))
    dias_peak = float(raw_dict.get('ppg_diastolic_peak', raw_dc - 0.5 * raw_ac + 0.46 * raw_ac))
    trough = float(raw_dict.get('ppg_trough', raw_dc - 0.5 * raw_ac))
    hr = float(raw_dict.get('hr_bpm', 75.0))
    saliva_ph = float(raw_dict.get('saliva_ph', 7.25))
    temp_c = float(raw_dict.get('temperature_c', 36.6))
    age = float(raw_dict.get('age', 45.0))
    bmi = float(raw_dict.get('bmi', 26.5))

    ph_mean_base = manifest.get('ph_mean_train_baseline', 7.2474)
    ph_dev = float(raw_dict.get('ph_deviation_from_mean', saliva_ph - ph_mean_base))

    # APG / VPG defaults
    apg_a = float(raw_dict.get('apg_a', 74.4))
    apg_b = float(raw_dict.get('apg_b', -67.1))
    apg_c = float(raw_dict.get('apg_c', 18.5))
    apg_d = float(raw_dict.get('apg_d', -18.6))
    apg_e = float(raw_dict.get('apg_e', 11.2))

    apg_ba = float(raw_dict.get('apg_b_a_ratio', apg_b / max(1e-4, apg_a) if 'apg_b' in raw_dict else -0.90))
    apg_ai = float(raw_dict.get('apg_aging_index', (apg_b - apg_c - apg_d - apg_e) / max(1e-4, apg_a) if 'apg_b' in raw_dict else -1.31))

    vpg_max = float(raw_dict.get('vpg_max', raw_ac * 1.65 * (hr / 60.0)))
    vpg_min = float(raw_dict.get('vpg_min', -raw_ac * 1.35 * (hr / 60.0)))

    ac_half = 0.5 * raw_ac
    ppg_energy = float(raw_dict.get('ppg_signal_energy', 0.5 * (ac_half ** 2)))

    hrv_sdnn = float(raw_dict.get('hrv_sdnn', 42.5))
    hrv_rmssd = float(raw_dict.get('hrv_rmssd', 49.7))
    hrv_pnn50 = float(raw_dict.get('hrv_pnn50', 0.45 * hrv_sdnn))
    hrv_lf_hf = float(raw_dict.get('hrv_lf_hf_ratio', 1.80))
    hrv_hf = float(raw_dict.get('hrv_hf', hrv_rmssd * 14.0))
    hrv_lf = float(raw_dict.get('hrv_lf', hrv_hf * hrv_lf_hf))

    raw_numeric = {
        'ppg_raw_dc_baseline': raw_dc,
        'ppg_raw_ac_p2p': raw_ac,
        'ppg_systolic_peak': sys_peak,
        'ppg_diastolic_peak': dias_peak,
        'ppg_trough': trough,
        'perfusion_index': float(raw_dict.get('perfusion_index', (raw_ac / max(1.0, raw_dc)) * 100.0)),
        'ppg_signal_energy': ppg_energy,
        'pulse_pressure': float(raw_dict.get('pulse_pressure', sys_peak - dias_peak)),
        'hr_bpm': hr,
        'ppg_hr_bpm': float(raw_dict.get('ppg_hr_bpm', hr)),
        'pulse_width_ms': float(raw_dict.get('pulse_width_ms', 240.0)),
        'trough_to_trough_ms': float(raw_dict.get('trough_to_trough_ms', (60000.0 / max(30.0, hr)))),
        'dicrotic_notch_amp': float(raw_dict.get('dicrotic_notch_amp', trough + raw_ac * 0.33)),
        'dicrotic_ratio': float(raw_dict.get('dicrotic_ratio', 0.46)),
        'vpg_max': vpg_max,
        'vpg_min': vpg_min,
        'apg_a': apg_a,
        'apg_b': apg_b,
        'apg_c': apg_c,
        'apg_d': apg_d,
        'apg_e': apg_e,
        'apg_b_a_ratio': apg_ba,
        'apg_aging_index': apg_ai,
        'hrv_sdnn': hrv_sdnn,
        'hrv_rmssd': hrv_rmssd,
        'hrv_pnn50': hrv_pnn50,
        'hrv_lf': hrv_lf,
        'hrv_hf': hrv_hf,
        'hrv_lf_hf_ratio': hrv_lf_hf,
        'saliva_ph': saliva_ph,
        'ph_deviation_from_mean': ph_dev,
        'temperature_c': temp_c,
        'age': age,
        'bmi': bmi
    }

    raw_num_df = pd.DataFrame([raw_numeric])[scaler.feature_names_in_]
    scaled_num_vals = scaler.transform(raw_num_df)[0]
    scaled_num_cols = manifest['scaled_numeric_features']
    feat_dict = {col: scaled_num_vals[i] for i, col in enumerate(scaled_num_cols)}

    diag = str(raw_dict.get('diabetes_diagnosis', 'None')).strip()
    feat_dict['diag_none'] = 1 if diag in ['None', 'Healthy', 'none'] else 0
    feat_dict['diag_prediabetes'] = 1 if diag.lower() == 'prediabetes' else 0
    feat_dict['diag_type_1'] = 1 if 'type 1' in diag.lower() or 'type1' in diag.lower() else 0
    feat_dict['diag_type_2'] = 1 if 'type 2' in diag.lower() or 'type2' in diag.lower() else 0

    feat_dict['bmi_cat_underweight'] = 1 if bmi < 18.5 else 0
    feat_dict['bmi_cat_normal'] = 1 if 18.5 <= bmi < 25.0 else 0
    feat_dict['bmi_cat_overweight'] = 1 if 25.0 <= bmi < 30.0 else 0
    feat_dict['bmi_cat_obese'] = 1 if bmi >= 30.0 else 0
    feat_dict['bmi_cat_missing'] = 0

    feat_dict['med_taking_insulin'] = int(raw_dict.get('med_taking_insulin', 0))
    feat_dict['med_taking_oral'] = int(raw_dict.get('med_taking_oral', 0))
    feat_dict['med_taking_any'] = int(raw_dict.get('med_taking_any', feat_dict['med_taking_insulin'] | feat_dict['med_taking_oral']))

    gender = str(raw_dict.get('gender', 'male')).lower()
    feat_dict['gender_male'] = 1 if gender in ['male', 'm', '1', 1] else 0
    feat_dict['family_history'] = int(raw_dict.get('family_history', 0))
    feat_dict['smoking'] = int(raw_dict.get('smoking', 0))
    feat_dict['fasting'] = int(raw_dict.get('fasting', 1))

    ordered_cols = manifest['scaled_numeric_features'] + manifest['categorical_and_binary_features']
    return pd.DataFrame([feat_dict])[ordered_cols]

from scratch.test_serving_compatibility import test_cases
from scripts.train_models import clarke_error_grid_zone

print('='*80)
print('EVALUATING TEST CASES WITH FIXED PREPROCESSING')
print('='*80)

for tc in test_cases:
    X_df = prepare_features(tc['data'])
    base_preds = []
    for name in bundle['model_names']:
        m = bundle['base_models'][name]
        p = float(m.predict(X_df)[0])
        base_preds.append(p)
    stacked = float(bundle['meta_learner'].predict([base_preds])[0])
    rf_p = base_preds[1]
    xgb_p = base_preds[2]
    ref = tc['ref']
    print(f"{tc['name']} (Ref: {ref:.1f} mg/dL):")
    print(f"  Random Forest: {rf_p:.1f} mg/dL | Abs Err: {abs(rf_p-ref):.1f} | Zone: Zone {clarke_error_grid_zone(ref, rf_p)}")
    print(f"  XGBoost:       {xgb_p:.1f} mg/dL | Abs Err: {abs(xgb_p-ref):.1f} | Zone: Zone {clarke_error_grid_zone(ref, xgb_p)}")
    print(f"  Stacked Model: {stacked:.1f} mg/dL | Abs Err: {abs(stacked-ref):.1f} | Zone: Zone {clarke_error_grid_zone(ref, stacked)}")
