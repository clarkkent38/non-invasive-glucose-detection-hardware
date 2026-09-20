# Hardware + Supabase Setup Guide

End-to-end guide for connecting the ESP32-S3 sensor node to the live
Streamlit dashboard via Supabase.

---

## Prerequisites

- A **Supabase account** — free tier is sufficient (500 MB, 2 projects):
  https://supabase.com/dashboard
- **Arduino IDE 2.x** with the ESP32-S3 board package installed
- The three sensors wired to the ESP32-S3 (see Section 4)
- Python 3.9+ with the project's `requirements.txt` installed

---

## Step 1 — Run the SQL Schema in Supabase

1. Open your Supabase project dashboard.
2. In the left sidebar click **SQL Editor**.
3. Click **New query**.
4. Open the file `supabase/schema.sql` from this repository and paste the
   entire contents into the editor.
5. Click **Run** (Ctrl+Enter).
6. Expected output: no errors, and `Success. No rows returned.`
7. Verify: click **Table Editor** in the sidebar — you should see a `readings`
   table with all the columns listed.

> **What the script does:** Creates the `readings` table, adds two indexes
> (for fast latest-reading queries and per-patient history), enables Row Level
> Security, and adds policies allowing the anonymous key to INSERT and SELECT.

---

## Step 2 — Find Your Supabase URL and Anon Key

1. In the Supabase dashboard, click **Project Settings** (gear icon, bottom-left).
2. Click **API** in the settings sidebar.
3. Note down two values:
   - **Project URL** — looks like `https://abcdefghijklmnop.supabase.co`
   - **anon / public** key — a long JWT string starting with `eyJ`

You will use these in Steps 3 and 5.

---

## Step 3 — Configure the ESP32 Firmware

1. Open `firmware/esp32_sensor_node.ino` in Arduino IDE.
2. At the top of the file, fill in your values in the `#define` section:

```cpp
#define WIFI_SSID         "YourNetworkName"
#define WIFI_PASSWORD     "YourNetworkPassword"
#define SUPABASE_PROJECT  "abcdefghijklmnop"   // just the ref, not full URL
#define SUPABASE_ANON_KEY "eyJ..."             // full anon key
#define DEVICE_ID         "esp32_node_01"      // any unique label
```

3. Install the required libraries via **Tools → Manage Libraries**:
   - `SparkFun MAX3010x Pulse and Proximity Sensor Library`
   - `SparkFun TMP117`
   - `ArduinoJson` (version 6.x)
   - WiFi and HTTPClient are built into the ESP32 board package.

4. Select board: **Tools → Board → esp32 → ESP32S3 Dev Module**

5. Select the correct COM port.

6. Click **Upload**.

7. Open the **Serial Monitor** (115200 baud). You should see:
   ```
   === ESP32-S3 Glucose Sensor Node ===
   [OK] MAX30102 initialised
   [OK] TMP117 initialised
   [OK] pH ADC on GPIO4
   [OK] Trigger button on GPIO0 (press to measure)
   [NET] Connected — IP: 192.168.x.x
   ```

---

## Step 4 — Sensor Wiring Reference

All three sensors share the same I2C bus. Wire them as follows:

### I2C Bus (shared by MAX30102 and TMP117)

| ESP32-S3 Pin | Signal | MAX30102 Pin | TMP117 Pin |
|---|---|---|---|
| GPIO 8 | SDA | SDA | SDA |
| GPIO 9 | SCL | SCL | SCL |
| 3.3V | Power | VIN / 3.3V | VIN |
| GND | Ground | GND | GND |

> Both sensors have fixed I2C addresses: MAX30102 = **0x57**, TMP117 = **0x48**.
> They coexist on the same bus without conflict.

### pH Probe (analog)

| ESP32-S3 Pin | Signal | pH Module Pin |
|---|---|---|
| GPIO 4 | Analog input | VOUT / AO |
| 3.3V | Power (if module needs it) | VCC |
| GND | Ground | GND |

> ⚠️ **Important:** The pH electrode outputs a small millivolt signal. A
> signal-conditioning module (e.g. using LM324 or similar op-amp) is required
> to convert it to the 0–3.3V range that the ESP32 ADC accepts. **Never apply
> more than 3.3V to an ESP32 GPIO.** A simple and inexpensive module is the
> "Gravity: Analog pH Sensor" by DFRobot, which includes the conditioning
> circuit.

### pH Calibration (do this before first use)

1. In `firmware/esp32_sensor_node.ino` find:
   ```cpp
   #define PH_SLOPE      -0.0017f
   #define PH_INTERCEPT  14.0f
   ```
2. Dip the probe in **pH 7.0 buffer solution**. Note the raw ADC value printed
   in Serial Monitor: `[pH] ADC=2048  saliva_ph=X.XX`
3. Dip the probe in **pH 4.0 buffer solution**. Note the ADC value.
4. Calculate slope and intercept:
   ```
   slope     = (7.0 - 4.0) / (adc_at_7 - adc_at_4)
   intercept = 7.0 - slope * adc_at_7
   ```
5. Update `PH_SLOPE` and `PH_INTERCEPT` with your computed values, then
   re-upload the firmware.
6. Verify with pH 10.0 buffer: the printed value should be ≈ 10.0 ± 0.2.

### Taking a Reading

Press the **BOOT button (GPIO0)** on the ESP32-S3 development board. The
firmware will:
1. Sample PPG for 5 seconds (keep finger still on sensor)
2. Read temperature
3. Read pH (average of 16 ADC samples)
4. POST the JSON to Supabase

Serial output on success:
```
[BUTTON] Measurement triggered
[MEASURE] Sampling sensors...
[PPG] DC=175000  AC=1350  PI=0.771%  HR=72.3 bpm  PW=290.4 ms  (500 samples, 6 beats)
[TMP] temperature_c = 36.72°C
[pH] ADC=2050  saliva_ph=7.260
[JSON] {"device_id":"esp32_node_01","saliva_ph":7.26,...}
[HTTP] Response code: 201
[OK] Reading uploaded successfully
```

---

## Step 5 — Configure Streamlit Locally

1. Copy the example secrets file:
   ```
   cp .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
   (`secrets.toml` is in `.gitignore` — it will never be committed.)

2. Edit `.streamlit/secrets.toml`:
   ```toml
   [supabase]
   url = "https://abcdefghijklmnop.supabase.co"
   key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
   ```

3. Run the live dashboard:
   ```
   streamlit run app/live_dashboard.py
   ```

4. Select **🔴 Live Sensor Mode** at the top. Press **Fetch Latest Reading**.
   The reading taken in Step 4 should appear.

---

## Step 6 — Deploy to Streamlit Cloud (second app, same repo)

The existing `app/dashboard.py` Streamlit Cloud deployment is not affected.
You deploy `app/live_dashboard.py` as a **separate app** from the same repo.

1. Go to https://share.streamlit.io and click **New app**.
2. Connect to your GitHub repository.
3. Set **Main file path** to: `app/live_dashboard.py`
4. Click **Advanced settings → Secrets** and paste:
   ```toml
   [supabase]
   url = "https://abcdefghijklmnop.supabase.co"
   key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
   ```
5. Click **Deploy**.

Both apps (the original manual dashboard and the new live sensor dashboard)
now run from the same repo and the same ML model, with no shared state between
them.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────┐
│                 ESP32-S3 Sensor Node                      │
│                                                           │
│  MAX30102 (I2C 0x57) ─┐                                   │
│  TMP117   (I2C 0x48) ─┼─ firmware/esp32_sensor_node.ino  │
│  pH Probe (ADC GPIO4)─┘         │                         │
│                                  │  JSON POST              │
│                                  │  /rest/v1/readings     │
└──────────────────────────────────┼──────────────────────── ┘
                                   │
                                   ▼ HTTPS
┌──────────────────────────────────────────────────────────┐
│                    Supabase Cloud                          │
│                                                           │
│  public.readings table (supabase/schema.sql)             │
│  Row Level Security: anon INSERT + SELECT                 │
└──────────────────────────────────┬──────────────────────── ┘
                                   │ supabase-py SELECT
                                   ▼
┌──────────────────────────────────────────────────────────┐
│             app/live_dashboard.py (Streamlit)             │
│                                                           │
│  app/supabase_client.py → get_latest_reading()           │
│         │                                                 │
│         ▼                                                 │
│  predict.predict_full_sensor(sensor_dict + demographics) │
│         │                                                 │
│         ▼                                                 │
│  Display: BGL, CI, Clarke zone, OOD warning, trend chart │
│  Writes back: predicted_bgl_mg_dl, clarke_zone, is_ood   │
└──────────────────────────────────────────────────────────┘

Original app/dashboard.py and scripts/predict.py: UNTOUCHED
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `MAX30102 not found` | Wiring or power | Check SDA=GPIO8, SCL=GPIO9, VIN=3.3V |
| `TMP117 not found at 0x48` | Wiring | Same I2C bus, check connections |
| HTTP code 401 | Wrong anon key | Re-copy key from Supabase API settings |
| HTTP code 403 | RLS policy issue | Re-run schema.sql; check anon policies exist |
| `saliva_ph=14.0` (constant) | Not calibrated | Run pH calibration procedure above |
| Streamlit: "Supabase not configured" | Missing secrets.toml | Create `.streamlit/secrets.toml` |
| Predictions show `72.0 BPM` always | Not enough beats in 5s | Hold finger still, ensure good contact |

---

## Files Added by This Feature (original codebase untouched)

```
supabase/schema.sql              SQL to run once in Supabase SQL Editor
firmware/esp32_sensor_node.ino   ESP32-S3 Arduino firmware
app/supabase_client.py           Python helper for Supabase queries
app/live_dashboard.py            New Streamlit app with Live Sensor Mode
.streamlit/secrets.toml.example  Template for credentials (gitignored)
SETUP_HARDWARE.md                This file
```

Files confirmed untouched:
```
app/dashboard.py        ← original manual dashboard, NOT modified
scripts/predict.py      ← production inference engine, NOT modified
models/                 ← all model artifacts, NOT modified
```
