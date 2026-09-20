import sys
import pickle
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load training data
train_df = pd.read_csv(BASE_DIR / "data" / "processed" / "full_sensor_train_features.csv")
test_df = pd.read_csv(BASE_DIR / "data" / "processed" / "full_sensor_test_features.csv")

with open(BASE_DIR / "reports" / "features_manifest_full_sensor.json", "r") as f:
    manifest = json.load(f)

feature_cols = manifest["scaled_numeric_features"] + manifest["categorical_and_binary_features"]
X_train = train_df[feature_cols]
y_train = train_df["bgl_mg_dl"]
X_test = test_df[feature_cols]
y_test = test_df["bgl_mg_dl"]

print(f"Loaded Train: {len(X_train)} samples, Test: {len(X_test)} samples")

# Test original vs boosted capacity
configs = [
    {"name": "Original (n=100, d=4, lr=0.05)", "n_estimators": 100, "max_depth": 4, "lr": 0.05},
    {"name": "Doubled Capacity (n=200, d=6, lr=0.05)", "n_estimators": 200, "max_depth": 6, "lr": 0.05},
    {"name": "Deeper (n=250, d=8, lr=0.03)", "n_estimators": 250, "max_depth": 8, "lr": 0.03}
]

for cfg in configs:
    print(f"\n--- Testing {cfg['name']} ---")
    q05 = GradientBoostingRegressor(loss="quantile", alpha=0.05, n_estimators=cfg["n_estimators"], max_depth=cfg["max_depth"], learning_rate=cfg["lr"], random_state=42)
    q95 = GradientBoostingRegressor(loss="quantile", alpha=0.95, n_estimators=cfg["n_estimators"], max_depth=cfg["max_depth"], learning_rate=cfg["lr"], random_state=42)
    
    q05.fit(X_train, y_train)
    q95.fit(X_train, y_train)
    
    p05_test = q05.predict(X_test)
    p95_test = q95.predict(X_test)
    
    coverage = np.mean((y_test >= p05_test) & (y_test <= p95_test)) * 100.0
    mean_width = np.mean(p95_test - p05_test)
    print(f"Test Coverage: {coverage:.2f}% (Target ~90%) | Mean CI Width: {mean_width:.2f} mg/dL")
