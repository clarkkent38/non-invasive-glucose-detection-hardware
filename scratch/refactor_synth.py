import sys
import os

filepath = 'scripts/synth_generator.py'
with open(filepath, 'r') as f:
    content = f.read()

# 1. Update the feature coupling logic inside the loop
old_coupling = """            # 7. Multi-Modal PPG & HRV Feature Coupling
            pw_noise = np.random.normal(0, 12.0)
            reading_pw = p_base_pw + 24.0 * glucose_z + pw_noise
            reading_pw = float(np.clip(reading_pw, 145.0, 350.0))

            hr_noise = np.random.normal(0, 6.0)
            reading_hr = p_base_hr + 3.5 * (1 - is_fasting) + 4.8 * glucose_z + hr_noise
            reading_hr = float(np.clip(reading_hr, 52.0, 130.0))
            t2t_ms = float(60000.0 / reading_hr)

            dicrotic_ratio = float(np.clip(0.48 - 0.032 * glucose_z + np.random.normal(0, 0.070), 0.22, 0.65))
            raw_dia_peak = float(np.round(raw_trough + raw_ac * dicrotic_ratio, 1))
            dicrotic_notch_amp = float(np.round(raw_trough + raw_ac * (dicrotic_ratio * 0.72), 1))

            sdnn_shift = -15.0 * glucose_z + np.random.normal(0, 7.0)
            reading_sdnn = float(np.clip(p_base_sdnn + sdnn_shift, 6.0, 140.0))

            rmssd_shift = -14.0 * glucose_z + np.random.normal(0, 6.5)
            reading_rmssd = float(np.clip(p_base_rmssd + rmssd_shift, 5.0, 135.0))
            reading_pnn50 = float(np.clip(0.45 * reading_sdnn + np.random.normal(0, 5.0), 0.0, 65.0))"""

new_coupling = """            # 7. Multi-Modal PPG & HRV Feature Coupling
            
            # --- ARCHETYPE INJECTION FOR EXTREME VALUES ---
            archetype_label = "normal"
            if bgl < 70.0:
                # Hypoglycemia Archetypes (with percentages)
                rand_hypo = np.random.uniform(0, 1)
                if rand_hypo < 0.36:  # ~40% of 90% (scaled to make room for unawareness) -> roughly 36%
                    archetype_label = "hypo_sympathetic"
                elif rand_hypo < 0.63: # ~30% of 90% -> 27%
                    archetype_label = "hypo_parasympathetic"
                elif rand_hypo < 0.76: # ~15% of 90% -> 13%
                    archetype_label = "hypo_exercise"
                elif rand_hypo < 0.90: # ~15% of 90% -> 14%
                    archetype_label = "hypo_nocturnal"
                else: # 10%
                    archetype_label = "hypo_unawareness"
            elif bgl > 250.0:
                # Severe Hyperglycemia Archetypes
                rand_hyper = np.random.uniform(0, 1)
                if rand_hyper < 0.50:
                    archetype_label = "hyper_dehydrated"
                elif rand_hyper < 0.80:
                    archetype_label = "hyper_gradual"
                else:
                    archetype_label = "hyper_stress"
            
            # Apply Archetypes to Physiological Variables
            if archetype_label == "hypo_sympathetic":
                reading_hr = float(np.random.uniform(95, 120))
                reading_sdnn = float(np.random.uniform(15, 30))
                reading_rmssd = float(np.random.uniform(10, 25))
                # Adjust perfusion and AC amplitude downwards
                perfusion_index = float(np.random.uniform(0.4, 0.9))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hypo_parasympathetic":
                reading_hr = float(np.random.uniform(55, 70))
                reading_sdnn = float(np.random.uniform(50, 90))
                reading_rmssd = float(np.random.uniform(45, 80))
                perfusion_index = float(np.random.uniform(1.0, 1.8))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hypo_exercise":
                reading_hr = float(np.random.uniform(100, 120))
                reading_sdnn = float(np.random.uniform(15, 40))
                reading_rmssd = float(np.random.uniform(15, 35))
                perfusion_index = float(np.random.uniform(2.0, 3.5))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hypo_nocturnal":
                reading_hr = float(np.random.uniform(45, 60))
                reading_sdnn = float(np.random.uniform(75, 120))
                reading_rmssd = float(np.random.uniform(70, 110))
                perfusion_index = float(np.random.uniform(1.5, 2.5))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hypo_unawareness":
                reading_hr = float(np.random.uniform(65, 85))
                reading_sdnn = float(np.random.uniform(35, 55))
                reading_rmssd = float(np.random.uniform(30, 50))
                perfusion_index = float(np.random.uniform(1.0, 1.6))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hyper_dehydrated":
                reading_hr = float(np.random.uniform(95, 115))
                reading_sdnn = float(np.random.uniform(20, 40))
                reading_rmssd = float(np.random.uniform(15, 30))
                perfusion_index = float(np.random.uniform(0.5, 1.0))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hyper_gradual":
                reading_hr = float(np.random.uniform(75, 90))
                reading_sdnn = float(np.random.uniform(35, 60))
                reading_rmssd = float(np.random.uniform(30, 50))
                perfusion_index = float(np.random.uniform(1.2, 2.0))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            elif archetype_label == "hyper_stress":
                reading_hr = float(np.random.uniform(90, 110))
                reading_sdnn = float(np.random.uniform(15, 35))
                reading_rmssd = float(np.random.uniform(10, 25))
                perfusion_index = float(np.random.uniform(1.5, 2.5))
                raw_ac = (perfusion_index / 100.0) * raw_dc
            else:
                # Normal standard generation logic for non-extreme values
                hr_noise = np.random.normal(0, 6.0)
                reading_hr = p_base_hr + 3.5 * (1 - is_fasting) + 4.8 * glucose_z + hr_noise
                reading_hr = float(np.clip(reading_hr, 52.0, 130.0))
                
                sdnn_shift = -15.0 * glucose_z + np.random.normal(0, 7.0)
                reading_sdnn = float(np.clip(p_base_sdnn + sdnn_shift, 6.0, 140.0))

                rmssd_shift = -14.0 * glucose_z + np.random.normal(0, 6.5)
                reading_rmssd = float(np.clip(p_base_rmssd + rmssd_shift, 5.0, 135.0))

            # Remaining dependent variables
            pw_noise = np.random.normal(0, 12.0)
            reading_pw = p_base_pw + 24.0 * glucose_z + pw_noise
            reading_pw = float(np.clip(reading_pw, 145.0, 350.0))
            t2t_ms = float(60000.0 / reading_hr)

            dicrotic_ratio = float(np.clip(0.48 - 0.032 * glucose_z + np.random.normal(0, 0.070), 0.22, 0.65))
            raw_dia_peak = float(np.round(raw_trough + raw_ac * dicrotic_ratio, 1))
            dicrotic_notch_amp = float(np.round(raw_trough + raw_ac * (dicrotic_ratio * 0.72), 1))

            reading_pnn50 = float(np.clip(0.45 * reading_sdnn + np.random.normal(0, 5.0), 0.0, 65.0))"""

if old_coupling not in content:
    print("Could not find old coupling block")
    sys.exit(1)
content = content.replace(old_coupling, new_coupling)

# 2. Add 'archetype' to the generated rows
old_row = """                "data_source": "synthetic",
                "training_branch": "full_sensor",
                "placement": "fingertip",
                # Non-conflated Diagnosis & Glycemic States"""
new_row = """                "data_source": "synthetic",
                "training_branch": "full_sensor",
                "placement": "fingertip",
                "archetype": archetype_label,
                # Non-conflated Diagnosis & Glycemic States"""

if old_row not in content:
    print("Could not find old row block")
    sys.exit(1)
content = content.replace(old_row, new_row)

# 3. Add inter-archetype separation checks to validate_and_report
old_val = """    # Perfusion Index
    pi = df["perfusion_index"]
    assert pi.min() >= 0.50 and pi.max() <= 5.00, "PI out of bounds!"
    print(f">>> PERFUSION INDEX PASSED: mean={pi.mean():.3f}%, min={pi.min():.3f}%, max={pi.max():.3f}%")"""
new_val = """    # Perfusion Index
    pi = df["perfusion_index"]
    assert pi.min() >= 0.35 and pi.max() <= 5.00, "PI out of bounds!"
    print(f">>> PERFUSION INDEX PASSED: mean={pi.mean():.3f}%, min={pi.min():.3f}%, max={pi.max():.3f}%")
    
    # Archetype Diversity Check
    print("\\n>>> EXTREME GLUCOSE ARCHETYPE VALIDATION:")
    hypo_df = df[df["glycemic_state_at_reading"] == "hypoglycemic"]
    if len(hypo_df) > 0:
        print(f"  Hypoglycemia (N={len(hypo_df)}):")
        for arch in sorted(hypo_df['archetype'].unique()):
            sub = hypo_df[hypo_df['archetype'] == arch]
            print(f"    {arch:22s} (N={len(sub):3d}): HR={sub['hr_bpm'].mean():5.1f} | RMSSD={sub['hrv_rmssd'].mean():5.1f} | PI={sub['perfusion_index'].mean():4.2f}")
        
        # Check standard deviations to ensure wide spread overall
        hr_std = hypo_df['hr_bpm'].std()
        print(f"  Hypo HR Overall StdDev: {hr_std:.1f} (Must be >15 to ensure diversity)")
        assert hr_std > 15.0, "Hypo HR lacks physiological diversity (StdDev < 15)!"

    hyper_df = df[df["glycemic_state_at_reading"] == "very_high"]
    if len(hyper_df) > 0:
        print(f"  Severe Hyperglycemia (N={len(hyper_df)}):")
        for arch in sorted(hyper_df['archetype'].unique()):
            sub = hyper_df[hyper_df['archetype'] == arch]
            print(f"    {arch:22s} (N={len(sub):3d}): HR={sub['hr_bpm'].mean():5.1f} | RMSSD={sub['hrv_rmssd'].mean():5.1f} | PI={sub['perfusion_index'].mean():4.2f}")"""

if old_val not in content:
    print("Could not find old validation block")
    sys.exit(1)
content = content.replace(old_val, new_val)

with open(filepath, 'w') as f:
    f.write(content)

print("Successfully refactored synth_generator.py")
