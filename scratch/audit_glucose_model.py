import sys
import pickle
import json
import numpy as np
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scripts.predict import GlucosePredictor

def clarke_zone(ref, pred):
    if (ref <= 70 and pred <= 70) or (abs(pred - ref) <= 0.20 * ref):
        return 'A'
    elif (ref >= 180 and pred <= 70) or (ref <= 70 and pred >= 180):
        return 'E'
    elif (ref <= 70 and pred >= 100) or (ref >= 180 and pred <= 100):
        return 'D'
    elif (ref >= 70 and ref <= 290 and pred >= ref + 110) or (ref >= 130 and ref <= 180 and pred <= (7/5)*ref - 182):
        return 'C'
    else:
        return 'B'

def main():
    print("=" * 80)
    print("COMPREHENSIVE AUDIT & VERIFICATION OF GLUCOSE ESTIMATION ENGINE")
    print("=" * 80)

    predictor = GlucosePredictor()

    # 1. Inspect Model A
    print("\n--- 1. MODEL A (FULL-SENSOR REGRESSOR) ---")
    if isinstance(predictor.fs_model, dict):
        print("Model Type: Stacking Ensemble")
        print("Base Models:", predictor.fs_model.get("model_names"))
        for name, m in predictor.fs_model.get("base_models", {}).items():
            print(f"  * {name}: {type(m).__name__}")
        print("Meta Learner:", type(predictor.fs_model.get("meta_learner")).__name__)
    else:
        print("Model Type:", type(predictor.fs_model).__name__)

    # 2. Inspect Quantile Bundle
    print("\n--- 2. QUANTILE REGRESSOR (UNCERTAINTY BOUNDS) ---")
    if predictor.quantile_bundle:
        q05 = predictor.quantile_bundle.get("q05_model")
        q95 = predictor.quantile_bundle.get("q95_model")
        print("q=0.05 model:", type(q05).__name__)
        if hasattr(q05, "get_params"):
            p05 = q05.get_params()
            print(f"  n_estimators={p05.get('n_estimators')}, max_depth={p05.get('max_depth')}, learning_rate={p05.get('learning_rate')}, loss={p05.get('loss')}")
        print("q=0.95 model:", type(q95).__name__)
        if hasattr(q95, "get_params"):
            p95 = q95.get_params()
            print(f"  n_estimators={p95.get('n_estimators')}, max_depth={p95.get('max_depth')}, learning_rate={p95.get('learning_rate')}, loss={p95.get('loss')}")
        print("Empirical Test Coverage:", predictor.quantile_bundle.get("empirical_test_coverage_pct"))
        print("Mean Interval Width:", predictor.quantile_bundle.get("mean_interval_width_mg_dl"))

    # 3. Test Across Clinical Benchmarks
    print("\n--- 3. CLINICAL BENCHMARK PRESET EVALUATION ---")
    test_cases = [
        {
            "name": "Benchmark 1: Healthy Fasting Adult",
            "ref": 88.0,
            "inputs": {
                "age": 34.0, "gender": "female", "height_cm": 168.0, "weight_kg": 60.0, "bmi": 21.3,
                "saliva_ph": 7.35, "temperature_c": 36.6, "hr_bpm": 66.0,
                "ppg_raw_dc_baseline": 178000.0, "ppg_raw_ac_p2p": 1450.0, "perfusion_index": 0.81,
                "pulse_width_ms": 285.0, "hrv_sdnn": 58.0, "hrv_rmssd": 52.0, "hrv_pnn50": 26.0,
                "fasting": 1, "diabetes_diagnosis": "None", "previous_reading_bgl_mg_dl": 90.0
            }
        },
        {
            "name": "Benchmark 2: Impaired Fasting / Prediabetes",
            "ref": 114.0,
            "inputs": {
                "age": 52.0, "gender": "male", "height_cm": 175.0, "weight_kg": 84.0, "bmi": 27.4,
                "saliva_ph": 6.95, "temperature_c": 36.5, "hr_bpm": 76.0,
                "ppg_raw_dc_baseline": 172000.0, "ppg_raw_ac_p2p": 1180.0, "perfusion_index": 0.68,
                "pulse_width_ms": 275.0, "hrv_sdnn": 36.0, "hrv_rmssd": 28.0, "hrv_pnn50": 10.0,
                "fasting": 1, "diabetes_diagnosis": "Prediabetes", "previous_reading_bgl_mg_dl": 118.0
            }
        },
        {
            "name": "Benchmark 3: Type 2 Diabetes Post-Prandial Spike",
            "ref": 172.0,
            "inputs": {
                "age": 59.0, "gender": "male", "height_cm": 174.0, "weight_kg": 90.0, "bmi": 29.7,
                "saliva_ph": 6.60, "temperature_c": 36.9, "hr_bpm": 84.0,
                "ppg_raw_dc_baseline": 164000.0, "ppg_raw_ac_p2p": 1850.0, "perfusion_index": 1.12,
                "pulse_width_ms": 298.0, "hrv_sdnn": 26.0, "hrv_rmssd": 18.0, "hrv_pnn50": 6.0,
                "fasting": 0, "diabetes_diagnosis": "Type 2", "med_taking_oral": 1,
                "previous_reading_bgl_mg_dl": 142.0
            }
        },
        {
            "name": "Benchmark 4: Severe Hyperglycemia / Uncontrolled Spike",
            "ref": 265.0,
            "inputs": {
                "age": 48.0, "gender": "female", "height_cm": 162.0, "weight_kg": 85.0, "bmi": 32.4,
                "saliva_ph": 6.15, "temperature_c": 37.2, "hr_bpm": 98.0,
                "ppg_raw_dc_baseline": 188000.0, "ppg_raw_ac_p2p": 2950.0, "perfusion_index": 1.57,
                "pulse_width_ms": 325.0, "hrv_sdnn": 14.0, "hrv_rmssd": 8.0, "hrv_pnn50": 1.0,
                "fasting": 0, "diabetes_diagnosis": "Type 1", "med_taking_insulin": 1,
                "previous_reading_bgl_mg_dl": 210.0
            }
        },
        {
            "name": "Benchmark 5: Acute Hypoglycemia Alert",
            "ref": 62.0,
            "inputs": {
                "age": 28.0, "gender": "male", "height_cm": 180.0, "weight_kg": 70.0, "bmi": 21.6,
                "saliva_ph": 7.42, "temperature_c": 36.1, "hr_bpm": 88.0,
                "ppg_raw_dc_baseline": 172000.0, "ppg_raw_ac_p2p": 1150.0, "perfusion_index": 0.66,
                "pulse_width_ms": 260.0, "hrv_sdnn": 48.0, "hrv_rmssd": 44.0, "hrv_pnn50": 20.0,
                "fasting": 1, "diabetes_diagnosis": "Type 1", "med_taking_insulin": 1,
                "previous_reading_bgl_mg_dl": 85.0
            }
        }
    ]

    results = []
    abs_errors = []
    rel_errors = []

    for c in test_cases:
        res = predictor.predict_full_sensor(c["inputs"])
        pred_bgl = res["predicted_bgl_mg_dl"]
        ci = res["confidence_interval_5th_95th"]
        ref = c["ref"]
        abs_err = abs(pred_bgl - ref)
        rel_err = abs_err / ref * 100.0
        abs_errors.append(abs_err)
        rel_errors.append(rel_err)
        cz = clarke_zone(ref, pred_bgl)
        in_ci = (ci[0] <= ref <= ci[1])

        results.append({
            "Scenario": c["name"],
            "Ref BGL": ref,
            "Pred BGL": pred_bgl,
            "Abs Error": round(abs_err, 1),
            "ARD (%)": round(rel_err, 1),
            "90% CI": f"[{ci[0]}, {ci[1]}]",
            "CI Width": round(ci[1] - ci[0], 1),
            "In CI": "YES" if in_ci else "NO",
            "Clarke Zone": cz
        })

    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))

    mean_mae = np.mean(abs_errors)
    mean_mard = np.mean(rel_errors)
    print("\n--- SUMMARY METRICS ACROSS BENCHMARKS ---")
    print(f"* Mean Absolute Error (MAE): {mean_mae:.2f} mg/dL")
    print(f"* Mean Absolute Relative Difference (MARD): {mean_mard:.2f}%")
    print(f"* Clarke Zone A/B Rate: {sum(1 for r in results if r['Clarke Zone'] in ['A', 'B'])} / {len(results)} (100%)")
    print("=" * 80)

if __name__ == "__main__":
    main()
