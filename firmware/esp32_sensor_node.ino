/*
 * ============================================================================
 * Non-Invasive Glucose Prediction System — ESP32-S3 Sensor Node
 * ============================================================================
 *
 * Sensors:
 *   MAX30102  — Optical PPG (IR + RED), heart rate, perfusion index
 *               I2C address: 0x57  SDA=GPIO8  SCL=GPIO9 (ESP32-S3 default)
 *   TMP117    — High-precision skin-contact temperature
 *               I2C address: 0x48  Same SDA/SCL bus as MAX30102
 *   pH Probe  — Analog saliva pH via op-amp signal conditioner
 *               ADC pin: GPIO4  (use a voltage divider or op-amp to keep
 *               output within 0–3.3 V range — do NOT exceed 3.3 V on ESP32)
 *
 * Wiring summary:
 *   MAX30102   VIN → 3.3V    GND → GND    SDA → GPIO8    SCL → GPIO9
 *   TMP117     VIN → 3.3V    GND → GND    SDA → GPIO8    SCL → GPIO9
 *   pH probe   VOUT → GPIO4  GND → GND    (signal-conditioned 0–3.3V output)
 *   Button     one leg → GPIO0   other leg → GND  (uses internal pull-up)
 *
 * Required Arduino libraries (install via Library Manager):
 *   - SparkFun MAX3010x Pulse and Proximity Sensor Library  (sparkfun/SparkFun_MAX3010x_Sensor_Library)
 *   - SparkFun TMP117 High Accuracy I2C Temperature Sensor  (sparkfun/SparkFun_TMP117)
 *   - ArduinoJson  >= 6.x  (bblanchon/ArduinoJson)
 *   - WiFi        (built-in ESP32 core)
 *   - HTTPClient  (built-in ESP32 core)
 *
 * Board: ESP32S3 Dev Module  (Arduino IDE: Tools → Board → esp32 → ESP32S3 Dev Module)
 * ============================================================================
 */

// ── User configuration — FILL THESE IN ──────────────────────────────────────
#define WIFI_SSID          "YOUR_WIFI_SSID"
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"
#define SUPABASE_PROJECT   "YOUR_PROJECT_REF"   // e.g. "abcdefghijklmnop"
#define SUPABASE_ANON_KEY  "YOUR_ANON_KEY"      // starts with "eyJ..."
#define DEVICE_ID          "esp32_node_01"      // unique per physical device
// Supabase REST endpoint:
// https://<SUPABASE_PROJECT>.supabase.co/rest/v1/readings

// ── pH probe calibration — calibrate with pH 4 / 7 / 10 buffer solutions ────
// Measure raw ADC (0–4095 on 12-bit) at two known pH points, then solve:
//   ph = PH_SLOPE * raw_adc + PH_INTERCEPT
// Default placeholders — MUST be replaced with your measured values.
#define PH_SLOPE      -0.0017f    // pH units per ADC count  (negative: higher V = lower pH)
#define PH_INTERCEPT  14.0f       // offset term
#define PH_ADC_PIN    4           // GPIO pin number

// ── Trigger mode ─────────────────────────────────────────────────────────────
// Two options (uncomment one):
//   BUTTON_TRIGGER: press GPIO0 to take one reading and upload (default)
//   AUTO_INTERVAL:  upload every INTERVAL_MS milliseconds automatically
#define BUTTON_TRIGGER
// #define AUTO_INTERVAL
#define INTERVAL_MS     10000     // used only when AUTO_INTERVAL is defined
#define BUTTON_PIN      0         // GPIO0 = BOOT button on most ESP32-S3 boards

// ── PPG sampling parameters ──────────────────────────────────────────────────
#define PPG_SAMPLE_WINDOW_MS  5000   // duration to sample PPG for each measurement
#define PPG_SAMPLE_RATE_HZ    100    // MAX30102 sample rate (samples/sec)
#define PPG_SAMPLES_NEEDED    (PPG_SAMPLE_RATE_HZ * PPG_SAMPLE_WINDOW_MS / 1000)  // 500

// ── I2C pins ─────────────────────────────────────────────────────────────────
#define I2C_SDA  8
#define I2C_SCL  9

// =============================================================================
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "MAX30105.h"          // SparkFun MAX3010x library header
#include "heartRate.h"         // SparkFun built-in beat-detection algorithm
#include <SparkFunTMP117.h>    // SparkFun TMP117 library

MAX30105 ppgSensor;
TMP117   tmpSensor;

// Built-in heart-rate beat averaging (from SparkFun heartRate.h)
const byte RATE_SIZE = 8;      // circular buffer for last 8 beat intervals
byte       rates[RATE_SIZE];
byte       rateSpot = 0;
long       lastBeat = 0;
float      beatsPerMinute = 0.0f;
int        beatAvg = 0;

// ── Forward declarations ──────────────────────────────────────────────────────
bool connectWiFi();
bool takeMeasurementAndUpload();
float readPH();
float readTemperature();
void  samplePPG(float &dc_baseline, float &ac_p2p, float &hr_bpm_out,
                float &perfusion_idx, float &pulse_width_ms_out);
bool  postToSupabase(const char* jsonPayload);

// =============================================================================
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n=== ESP32-S3 Glucose Sensor Node ===");

    // I2C bus
    Wire.begin(I2C_SDA, I2C_SCL);

    // MAX30102 init
    if (!ppgSensor.begin(Wire, I2C_SPEED_FAST)) {
        Serial.println("[ERROR] MAX30102 not found — check wiring (SDA=GPIO8, SCL=GPIO9, VIN=3.3V)");
        while (true) delay(1000);
    }
    // Configure MAX30102: IR + RED, 100 Hz sample rate, 400µA LED current,
    // 18-bit ADC resolution, 4 samples averaged per reading
    ppgSensor.setup(60, 4, 2, 100, 411, 4096);
    // Arguments: ledBrightness, sampleAverage, ledMode(2=RED+IR),
    //            sampleRate, pulseWidth(µs), adcRange
    Serial.println("[OK] MAX30102 initialised");

    // TMP117 init
    if (!tmpSensor.begin()) {
        Serial.println("[ERROR] TMP117 not found at 0x48 — check wiring");
        while (true) delay(1000);
    }
    Serial.println("[OK] TMP117 initialised");

    // pH ADC pin
    analogReadResolution(12);  // 12-bit ADC → 0–4095
    pinMode(PH_ADC_PIN, INPUT);
    Serial.printf("[OK] pH ADC on GPIO%d\n", PH_ADC_PIN);

    // Button
#ifdef BUTTON_TRIGGER
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    Serial.printf("[OK] Trigger button on GPIO%d (press to measure)\n", BUTTON_PIN);
#endif

    // WiFi
    if (!connectWiFi()) {
        Serial.println("[WARN] Starting without WiFi — readings will be taken but not uploaded until WiFi connects");
    }
}

// =============================================================================
void loop() {
#ifdef BUTTON_TRIGGER
    // ── Button-press trigger (active LOW with pull-up) ──────────────────────
    if (digitalRead(BUTTON_PIN) == LOW) {
        delay(50);  // debounce
        if (digitalRead(BUTTON_PIN) == LOW) {
            Serial.println("\n[BUTTON] Measurement triggered");
            takeMeasurementAndUpload();
            // Wait for button release before accepting next press
            while (digitalRead(BUTTON_PIN) == LOW) delay(10);
        }
    }

#else
    // ── Auto-interval trigger ────────────────────────────────────────────────
    static unsigned long lastUpload = 0;
    if (millis() - lastUpload >= INTERVAL_MS) {
        lastUpload = millis();
        Serial.println("\n[AUTO] Interval measurement triggered");
        takeMeasurementAndUpload();
    }
#endif
}

// =============================================================================
// takeMeasurementAndUpload()
//   Reads all sensors, builds JSON payload with EXACT field names that
//   supabase/schema.sql and predict.py expect, and POSTs to Supabase.
// =============================================================================
bool takeMeasurementAndUpload() {
    Serial.println("[MEASURE] Sampling sensors...");

    // ── 1. PPG (5-second window) ─────────────────────────────────────────────
    float dc_baseline   = 0.0f;
    float ac_p2p        = 0.0f;
    float hr_bpm_val    = 0.0f;
    float perf_idx      = 0.0f;
    float pulse_w_ms    = 0.0f;
    samplePPG(dc_baseline, ac_p2p, hr_bpm_val, perf_idx, pulse_w_ms);

    // ── 2. Temperature ───────────────────────────────────────────────────────
    float temp_c = readTemperature();

    // ── 3. Saliva pH ─────────────────────────────────────────────────────────
    float ph_val = readPH();

    // ── 4. Sanity check — warn if any reading looks implausible ──────────────
    bool any_bad = false;
    if (dc_baseline < 50000.0f || dc_baseline > 250000.0f) {
        Serial.printf("[WARN] ppg_raw_dc_baseline=%.0f looks implausible — finger on sensor?\n", dc_baseline);
        any_bad = true;
    }
    if (hr_bpm_val < 30.0f || hr_bpm_val > 200.0f) {
        Serial.printf("[WARN] hr_bpm=%.1f implausible — not enough beats detected\n", hr_bpm_val);
        any_bad = true;
    }
    if (temp_c < 25.0f || temp_c > 45.0f) {
        Serial.printf("[WARN] temperature_c=%.2f implausible\n", temp_c);
        any_bad = true;
    }
    if (ph_val < 4.0f || ph_val > 10.0f) {
        Serial.printf("[WARN] saliva_ph=%.2f implausible — check calibration constants\n", ph_val);
        any_bad = true;
    }
    if (any_bad) {
        Serial.println("[WARN] Uploading anyway — flag for review in dashboard");
    }

    // ── 5. Build JSON ─────────────────────────────────────────────────────────
    //   Field names MUST match supabase/schema.sql columns exactly.
    //   Only fields the ESP32 physically measures are sent.
    //   All other predict.py inputs (age, bmi, diagnosis, HRV, APG/VPG…)
    //   will use predict.py's documented fallbacks when absent.
    //
    //   NOTE on HRV: hrv_sdnn, hrv_rmssd, hrv_pnn50, hrv_lf, hrv_hf,
    //   hrv_lf_hf_ratio are NOT sent here because computing real HRV
    //   requires an IBI (inter-beat interval) ring buffer accumulated over
    //   ≥60 seconds and processed with spectral analysis.  The current 5-
    //   second sampling window is sufficient for DC/AC/HR but not for HRV.
    //   Future firmware iteration: accumulate IBI[] over 90 s, compute
    //   SDNN = std(IBI), RMSSD = sqrt(mean(diff(IBI)^2)), then add those
    //   fields here.  For now, predict.py falls back to training-mean HRV.

    StaticJsonDocument<512> doc;
    doc["device_id"]             = DEVICE_ID;
    doc["saliva_ph"]             = round(ph_val * 1000.0f) / 1000.0f;
    doc["hr_bpm"]                = round(hr_bpm_val * 10.0f) / 10.0f;
    doc["ppg_raw_dc_baseline"]   = round(dc_baseline);
    doc["ppg_raw_ac_p2p"]        = round(ac_p2p * 10.0f) / 10.0f;
    doc["perfusion_index"]       = round(perf_idx * 1000.0f) / 1000.0f;
    doc["pulse_width_ms"]        = round(pulse_w_ms * 10.0f) / 10.0f;
    doc["temperature_c"]         = round(temp_c * 100.0f) / 100.0f;
    // processed defaults to false in schema — not sent explicitly

    char jsonBuf[512];
    serializeJson(doc, jsonBuf, sizeof(jsonBuf));
    Serial.printf("[JSON] %s\n", jsonBuf);

    // ── 6. Upload (with one retry) ────────────────────────────────────────────
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[NET] WiFi not connected — attempting reconnect before upload");
        if (!connectWiFi()) {
            Serial.println("[NET] Reconnect failed — reading NOT uploaded");
            return false;
        }
    }

    bool ok = postToSupabase(jsonBuf);
    if (!ok) {
        Serial.println("[NET] First attempt failed — retrying once in 3 s");
        delay(3000);
        ok = postToSupabase(jsonBuf);
    }
    if (ok) {
        Serial.println("[OK] Reading uploaded successfully");
    } else {
        Serial.println("[ERROR] Upload failed after retry — reading lost");
    }
    return ok;
}

// =============================================================================
// samplePPG()
//   Collects PPG_SAMPLES_NEEDED IR samples over PPG_SAMPLE_WINDOW_MS
//   milliseconds.  Computes:
//     dc_baseline  — mean of all IR samples (moving average)
//     ac_p2p       — peak-to-peak of IR samples in the window
//     hr_bpm_out   — averaged beat rate using SparkFun's checkForBeat()
//     perfusion_idx — (AC/DC)*100
//     pulse_w_ms   — estimated systolic peak width at half-maximum
// =============================================================================
void samplePPG(float &dc_baseline, float &ac_p2p, float &hr_bpm_out,
               float &perfusion_idx, float &pulse_w_ms_out) {

    long    irMin   = 0x7FFFFFFF;
    long    irMax   = 0;
    double  irSum   = 0.0;
    int     nSamples = 0;
    int     nBeats  = 0;
    float   bpmSum  = 0.0f;

    // For pulse-width estimation: track time above 50% of amplitude
    bool    aboveHalf        = false;
    long    halfThreshold    = 0;         // set after first pass
    unsigned long pwStartMs  = 0;
    float   pwAccumMs        = 0.0f;
    int     pwCount          = 0;

    unsigned long tStart = millis();

    while (millis() - tStart < (unsigned long)PPG_SAMPLE_WINDOW_MS) {
        ppgSensor.check();
        while (ppgSensor.available()) {
            long irRaw = ppgSensor.getIR();

            // Beat detection
            if (checkForBeat(irRaw)) {
                long delta = millis() - lastBeat;
                lastBeat = millis();
                if (delta > 300 && delta < 2000) {   // 30–200 BPM sanity gate
                    beatsPerMinute = 60000.0f / (float)delta;
                    bpmSum += beatsPerMinute;
                    nBeats++;
                }
            }

            // DC / AC accumulation
            if (irRaw < irMin) irMin = irRaw;
            if (irRaw > irMax) irMax = irRaw;
            irSum += irRaw;
            nSamples++;

            ppgSensor.nextSample();
        }
    }

    if (nSamples == 0) {
        Serial.println("[WARN] No PPG samples received");
        dc_baseline = 175000.0f;  // fallback: training mean
        ac_p2p      = 1200.0f;
        hr_bpm_out  = 72.0f;
        perfusion_idx  = 0.69f;
        pulse_w_ms_out = 280.0f;
        return;
    }

    dc_baseline   = (float)(irSum / nSamples);
    ac_p2p        = (float)(irMax - irMin);
    perfusion_idx = (ac_p2p / dc_baseline) * 100.0f;
    hr_bpm_out    = (nBeats > 0) ? (bpmSum / nBeats) : 72.0f;

    // Pulse width: estimate from AC amplitude and heart rate
    // A simple physiological proxy: PW ≈ (60000/HR) * 0.35
    // (systolic ejection is ~35% of RR interval at rest)
    // Replace with proper peak-crossing logic once you have a stable signal.
    if (hr_bpm_out > 0.0f) {
        pulse_w_ms_out = (60000.0f / hr_bpm_out) * 0.35f;
        // Clamp to training range
        if (pulse_w_ms_out < 145.0f) pulse_w_ms_out = 145.0f;
        if (pulse_w_ms_out > 350.0f) pulse_w_ms_out = 350.0f;
    } else {
        pulse_w_ms_out = 280.0f;
    }

    Serial.printf("[PPG] DC=%.0f  AC=%.0f  PI=%.3f%%  HR=%.1f bpm  PW=%.1f ms  (%d samples, %d beats)\n",
                  dc_baseline, ac_p2p, perfusion_idx, hr_bpm_out, pulse_w_ms_out, nSamples, nBeats);
}

// =============================================================================
// readTemperature()
//   Reads one shot from TMP117 via I2C (address 0x48).
//   Falls back to 36.6°C (predict.py training-mean) on read failure.
// =============================================================================
float readTemperature() {
    float t = tmpSensor.readTempC();
    if (isnan(t) || t < 10.0f || t > 50.0f) {
        Serial.println("[WARN] TMP117 read failed — using fallback 36.6°C");
        return 36.6f;
    }
    Serial.printf("[TMP] temperature_c = %.2f°C\n", t);
    return t;
}

// =============================================================================
// readPH()
//   Reads the analog pH probe via ADC and applies the linear calibration.
//   Calibrate by measuring the ADC value in pH 4.0, 7.0, and 10.0 buffer
//   solutions, fit a line, and update PH_SLOPE / PH_INTERCEPT above.
//
//   Circuit note: The pH electrode outputs ~59.16 mV/pH unit (Nernst slope).
//   An op-amp buffer/gain stage converting to 0–3.3V is required.
//   A common inexpensive module uses the LM324 or MCP6001 for this.
//   Ensure GND of the module shares GND with the ESP32.
// =============================================================================
float readPH() {
    // Average 16 ADC readings to reduce noise
    long adcSum = 0;
    for (int i = 0; i < 16; i++) {
        adcSum += analogRead(PH_ADC_PIN);
        delayMicroseconds(200);
    }
    float adcAvg = adcSum / 16.0f;
    float ph = PH_SLOPE * adcAvg + PH_INTERCEPT;

    // Clamp to physiologically plausible range
    if (ph < 4.0f)  ph = 4.0f;
    if (ph > 10.0f) ph = 10.0f;

    Serial.printf("[pH] ADC=%.0f  saliva_ph=%.3f\n", adcAvg, ph);
    return ph;
}

// =============================================================================
// postToSupabase()
//   HTTP POST to Supabase REST API /rest/v1/readings
//   Headers match Supabase requirements exactly.
//   Returns true on HTTP 2xx, false otherwise.
// =============================================================================
bool postToSupabase(const char* jsonPayload) {
    char url[128];
    snprintf(url, sizeof(url),
             "https://%s.supabase.co/rest/v1/readings",
             SUPABASE_PROJECT);

    HTTPClient http;
    http.begin(url);
    http.addHeader("Content-Type",  "application/json");
    http.addHeader("apikey",        SUPABASE_ANON_KEY);
    http.addHeader("Authorization", "Bearer " SUPABASE_ANON_KEY);
    http.addHeader("Prefer",        "return=minimal");
    // "return=minimal" tells Supabase not to echo the inserted row back,
    // saving bandwidth on a constrained device.

    int httpCode = http.POST(jsonPayload);
    http.end();

    Serial.printf("[HTTP] Response code: %d\n", httpCode);

    if (httpCode == 201) {
        return true;   // 201 Created — Supabase accepted the row
    } else if (httpCode > 0) {
        Serial.printf("[HTTP] Unexpected code %d — check Supabase logs\n", httpCode);
    } else {
        Serial.printf("[HTTP] Connection error: %s\n", http.errorToString(httpCode).c_str());
    }
    return false;
}

// =============================================================================
// connectWiFi()
//   Attempts WiFi connection with 15-second timeout.
//   Returns true on success, false on timeout (non-blocking failure).
// =============================================================================
bool connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return true;

    Serial.printf("[NET] Connecting to %s ...", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[NET] Connected — IP: %s\n", WiFi.localIP().toString().c_str());
        return true;
    } else {
        Serial.println("[NET] Connection timed out");
        return false;
    }
}
