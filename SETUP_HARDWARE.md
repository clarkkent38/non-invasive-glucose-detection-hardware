# Hardware + Supabase Setup Guide

End-to-end guide for connecting the ESP32-S3 sensor node to the live
Streamlit dashboard via Supabase.

---

## Prerequisites

- A **Supabase account** — free tier is sufficient (500 MB, 2 projects):
  https://supabase.com/dashboard
  **Project already created:** https://mjcwhnkyojfaezydvpsp.supabase.co
- **Hardware GitHub repo:** https://github.com/clarkkent38/non-invasive-glucose-detection-hardware
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

## Step 2 — Your Supabase Credentials (already set up)

Your Supabase project is live. The values below are already wired into all
project files:

| Value | What it is |
|---|---|
| **Project URL** | `https://mjcwhnkyojfaezydvpsp.supabase.co` |
| **Project ref** | `mjcwhnkyojfaezydvpsp` (the subdomain) |
| **Anon / public key** | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…` (in firmware + secrets.toml) |

> ⚠️ **Keep your secret key (`sb_secret_...`) out of all files and repos.**
> It should only be used in the Supabase dashboard or trusted server environments.
> The anon key above is safe for device firmware and client-side code.

---

## Step 3 — Configure the ESP32 Firmware

1. Open `firmware/esp32_sensor_node.ino` in Arduino IDE.
2. The Supabase credentials are already filled in. You only need to set your
   WiFi password:

```cpp
#define WIFI_SSID         "YourNetworkName"    // ← your WiFi network name
#define WIFI_PASSWORD     "YourWiFiPassword"   // ← your WiFi password
// These are already set:
#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIs..."
#define DEVICE_ID         "esp32_node_01"
```

3. Install the required libraries via **Tools → Manage Libraries**:
   - `Adafruit ST7789` (for TFT display)
   - `Adafruit GFX Library` (graphics support)  
   - `SparkFun MAX3010x Pulse and Proximity Sensor Library`
   - `SparkFun TMP117`
   - `ArduinoJson` (version 6.x)
   - WiFi, HTTPClient, and WebServer are built into the ESP32 board package.

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

## Step 4 — Hardware Wiring (Updated with Display)

This system now includes an interactive ST7789 TFT display that shows 
real-time sensor readings and progress.

### Complete Wiring Table

| Component | ESP32-S3 Pin | Signal | Notes |
|---|---|---|---|
| **MAX30102** | GPIO 8 | SDA | I2C data |
| | GPIO 9 | SCL | I2C clock |
| | 3.3V | VIN | Power |
| | GND | GND | Ground |
| **TMP117** | GPIO 8 | SDA | Share I2C bus with MAX30102 |
| | GPIO 9 | SCL | Share I2C bus |  
| | 3.3V | VIN | Power |
| | GND | GND | Ground |
| **pH Probe Module** | GPIO 4 | VOUT | Analog signal (0-3.3V) |
| | 3.3V | VCC | Power (if needed) |
| | GND | GND | Ground |
| **ST7789 Display** | GPIO 18 | SCL | SPI clock |
| | GPIO 23 | SDA | SPI data (MOSI) |
| | GPIO 2 | RES | Reset pin |
| | GPIO 15 | DC | Data/Command |  
| | GPIO 21 | BLK | Backlight control |
| | 3.3V | VCC | Power |
| | GND | GND | Ground |
| **Tactile Button** | GPIO 0 | Button | Connect to GND (internal pull-up) |

### Visual Wiring Diagram

```
ESP32-S3                     ST7789 Display (240x240)
┌─────────────┐             ┌─────────────────────────────┐
│    3.3V  ●──┼─────────────┤ VCC                         │
│     GND  ●──┼─────────────┤ GND                         │  
│  GPIO18  ●──┼─────────────┤ SCL (SPI Clock)             │
│  GPIO23  ●──┼─────────────┤ SDA (SPI Data/MOSI)         │
│   GPIO2  ●──┼─────────────┤ RES (Reset)                 │
│  GPIO15  ●──┼─────────────┤ DC  (Data/Command)          │
│  GPIO21  ●──┼─────────────┤ BLK (Backlight)             │
│             │             └─────────────────────────────┘
│             │
│   GPIO8  ●──┼──┬── MAX30102 (SDA) + TMP117 (SDA) 
│   GPIO9  ●──┼──┼── MAX30102 (SCL) + TMP117 (SCL)
│             │  │
│   GPIO4  ●──┼──┼── pH Probe Signal Conditioner (VOUT)
│             │  │
│   GPIO0  ●──┼──┼── Tactile Button (other leg → GND)
│             │  │
│    3.3V  ●──┼──┴── Sensor Power (MAX30102, TMP117, pH module)
│     GND  ●──┼───── Common Ground
└─────────────┘
```

### I2C Address Verification

Both I2C sensors have fixed addresses and won't conflict:
- **MAX30102:** I2C address `0x57` 
- **TMP117:** I2C address `0x48`

### pH Calibration (Critical Step)

⚠️ **Must be done before first use:**

1. Get pH buffer solutions: 4.0, 7.0, and 10.0 (available from electronics suppliers)
2. In firmware, find these calibration constants:
   ```cpp
   #define PH_SLOPE      -0.0017f    // Replace with your values
   #define PH_INTERCEPT  14.0f       // Replace with your values  
   ```
3. Dip probe in **pH 7.0**, note the raw ADC value from Serial Monitor
4. Dip probe in **pH 4.0**, note the raw ADC value  
5. Calculate: 
   ```
   slope = (7.0 - 4.0) / (adc_at_7 - adc_at_4)
   intercept = 7.0 - slope * adc_at_7
   ```
6. Update firmware with calculated values and re-upload
7. Verify with pH 10.0 buffer (should read ≈10.0 ± 0.2)

### Interactive Operation

The device now has two ways to trigger readings:

1. **Manual:** Press the tactile button → device runs collection sequence
2. **Remote:** Use dashboard's "Start Remote Reading" button → device responds via WiFi

The ST7789 display shows:
- Welcome screen with WiFi status and device IP
- Step-by-step progress during sensor collection  
- Final results with all sensor values
- Upload status (success/error)
- Beautiful bordered interface like a professional medical device
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

1. The secrets file already exists at `.streamlit/secrets.toml` with your
   Supabase credentials filled in (it's gitignored and won't be committed).

2. Run the live dashboard:
   ```
   streamlit run app/live_dashboard.py
   ```

3. **New Interactive Features:**
   - Select **🔴 Live Sensor Mode** at the top
   - **ESP32 Device Control** panel: Enter your ESP32's IP address
   - **Check Device Status:** Verify ESP32 is online and ready  
   - **Start Remote Reading:** Trigger sensor collection remotely from dashboard
   - **Fetch Latest Reading:** Get the newest data from Supabase

4. **Two ways to take readings:**
   - **Manual:** Press tactile button on ESP32 → watch progress on display
   - **Remote:** Click "Start Remote Reading" → ESP32 responds automatically
   
5. The ESP32 display shows step-by-step progress and results beautifully.

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
   url = "https://mjcwhnkyojfaezydvpsp.supabase.co"
   key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
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
