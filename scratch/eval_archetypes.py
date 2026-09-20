import pandas as pd
import pickle
import json
import numpy as np

# Load model, scaler, manifest
with open('models/production_model_full_sensor_stacked.pkl', 'rb') as f:
    model = pickle.load(f)
with open('models/scaler_full_sensor.pkl', 'rb') as f:
    scaler = pickle.load(f)
with open('reports/features_manifest_full_sensor.json', 'r') as f:
    manifest = json.load(f)

# Load synthetic data
df = pd.read_parquet('data/interim/synthetic_features.parquet')

# Filter to hypo
hypo_df = df[df['glycemic_state_at_reading'] == 'hypoglycemic'].copy()

print("="*80)
print("EVALUATING MODEL ON SYNTHETIC HYPOGLYCEMIA ARCHETYPES")
print("="*80)

for arch in sorted(hypo_df['archetype'].unique()):
    sub_df = hypo_df[hypo_df['archetype'] == arch].copy()
    if len(sub_df) == 0: continue
    
    # Need to generate the exact same features as the training script
    # Let's map it the same way predict.py does, or better yet, just extract the 50 columns
    
    # 1. Scaled Numeric
    raw_num = sub_df[scaler.feature_names_in_]
    scaled_num = scaler.transform(raw_num)
    scaled_num_df = pd.DataFrame(scaled_num, columns=manifest['scaled_numeric_features'], index=sub_df.index)
    
    # 2. Categorical
    cat_df = pd.DataFrame(index=sub_df.index)
    
    # diag_none, diag_prediabetes, diag_type_1, diag_type_2
    cat_df['diag_none'] = (sub_df['diabetes_diagnosis'] == 'None').astype(int)
    cat_df['diag_prediabetes'] = (sub_df['diabetes_diagnosis'] == 'Prediabetes').astype(int)
    cat_df['diag_type_1'] = (sub_df['diabetes_diagnosis'] == 'Type 1').astype(int)
    cat_df['diag_type_2'] = (sub_df['diabetes_diagnosis'] == 'Type 2').astype(int)
    
    # bmi_cat
    bmi = sub_df['bmi']
    cat_df['bmi_cat_underweight'] = (bmi < 18.5).astype(int)
    cat_df['bmi_cat_normal'] = ((bmi >= 18.5) & (bmi < 25.0)).astype(int)
    cat_df['bmi_cat_overweight'] = ((bmi >= 25.0) & (bmi < 30.0)).astype(int)
    cat_df['bmi_cat_obese'] = (bmi >= 30.0).astype(int)
    cat_df['bmi_cat_missing'] = 0
    
    # meds
    cat_df['med_taking_insulin'] = sub_df['medication'].apply(lambda x: 1 if 'insulin' in str(x).lower() else 0)
    cat_df['med_taking_oral'] = sub_df['medication'].apply(lambda x: 1 if 'metformin' in str(x).lower() else 0)
    cat_df['med_taking_any'] = cat_df['med_taking_insulin'] | cat_df['med_taking_oral']
    
    # other
    cat_df['gender_male'] = (sub_df['gender'] == 'male').astype(int)
    cat_df['family_history'] = sub_df['family_history'].astype(int)
    cat_df['smoking'] = sub_df['smoking'].astype(int)
    cat_df['fasting'] = sub_df['fasting'].astype(int)
    
    # combine
    X = pd.concat([scaled_num_df, cat_df], axis=1)
    
    # Make sure columns exactly match manifest
    ordered_cols = manifest['scaled_numeric_features'] + manifest['categorical_and_binary_features']
    X = X[ordered_cols]
    
    # Predict
    preds = model.predict(X)
    
    ref = sub_df['bgl_mg_dl'].values
    errors = np.abs(preds - ref)
    mae = np.mean(errors)
    
    print(f"\\nArchetype: {arch} (N={len(sub_df)})")
    print(f"  Mean Ref: {ref.mean():.1f} mg/dL | Mean Pred: {preds.mean():.1f} mg/dL")
    print(f"  MAE: {mae:.1f} mg/dL")
