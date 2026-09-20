/*
 * ============================================================================
 * Non-Invasive Glucose Prediction System — ESP32-S3 DevKitC 1
 * ============================================================================
 *
 * DISPLAY OPTIONS — set one flag below:
 *   #define DISPLAY_OLED   → 0.96" SSD1306 128×64 (I2C 0x3C)  ← coming soon
 *   #define DISPLAY_NONE   → Serial Monitor only (current mode)
 *
 * Everything is always printed to Serial Monitor at 115200 baud regardless
 * of which display option is active.  Open Tools → Serial Monitor to follow
 * the full flow even without a display wired up.
 *
 * BUTTON-DRIVEN STEP FLOW:
 *   Press 1 → show pH instructions → Press 2 → collect pH (32 samples, MEDIAN)
 *   Press 3 → show Temp instructions → Press 4 → collect Temp (10 readings, trimmed mean)
 *   Press 5 → show PPG instructions → Press 6 → collect PPG (500 samples, 5 sec)
 *   Press 7 → upload to Supabase (status=pending) → wait for dashboard
 *   Dashboard: user enters details → Submit → prediction patched to row
 *   ESP32 polls every 5s → receives result → shows on display + Serial
 *
 * WIRING (ESP32-S3 DevKitC 1):
 *   MAX30102   SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND
 *   TMP117     SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND  (shared I2C bus)
 *   pH module  AO →GPIO4  VCC→3.3V  GND→GND
 *   SSD1306    SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND  (same I2C bus, addr 0x3C)
 *   Tactile    GPIO14 → GND  (internal pull-up, NOT GPIO0 which is BOOT)
 *
 * LIBRARIES (Arduino Library Manager):
 *   Adafruit SSD1306 · Adafruit GFX Library
 *   SparkFun MAX3010x · SparkFun TMP117
 *   ArduinoJson >= 6   (WiFi / HTTPClient / WebServer built into ESP32 core)
 * ============================================================================
 */

// ── Display Mode ─────────────────────────────────────────────────────────────
// Uncomment ONE of these.  Serial Monitor output is ALWAYS active.
// #define DISPLAY_OLED      // 0.96" SSD1306 128x64 via I2C (enable when wired)
#define DISPLAY_NONE         // No display hardware — Serial Monitor only

// ── User Configuration ───────────────────────────────────────────────────────
#define WIFI_SSID         "YOUR_WIFI_SSID"
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"
#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID         "esp32_node_01"

// ── Pin Configuration (ESP32-S3 DevKitC 1) ───────────────────────────────────
#define I2C_SDA      8    // GPIO8  — labelled SDA on board
#define I2C_SCL      9    // GPIO9  — labelled SCL on board
#define PH_ADC_PIN   4    // GPIO4  — ADC1_3, no strapping function
#define BUTTON_PIN  14    // GPIO14 — safe general input (NOT GPIO0 = BOOT pin)

// SSD1306 OLED uses the same I2C bus as MAX30102 + TMP117 (addr 0x3C)
// No extra pins needed — just wire SDA/SCL/VCC/GND

// ── pH-4502C Module Wiring ───────────────────────────────────────────────────
// Pin header (top to bottom): To  Do  Po  G  V+
//   V+  → ESP32 5V pin     (module needs 5V, NOT 3.3V)
//   G   → GND
//   Po  → voltage divider → GPIO4  (Po can output up to 3.5V — MUST divide!)
//   Do  → not connected   (digital threshold output, unused)
//   To  → not connected   (NTC thermistor output, unused)
//
// REQUIRED voltage divider on Po pin (protects ESP32 ADC from >3.3V):
//
//   Po ──[10kΩ]──┬──── GPIO4
//                [10kΩ]
//                │
//               GND
//
// This halves the Po voltage: 0–5V becomes 0–2.5V at GPIO4 (safe for ESP32)
// The calibration formula below already accounts for the 2× divider factor.
//
// ── pH Calibration ───────────────────────────────────────────────────────────
// The pH-4502C outputs ~2.5V at pH 7.0.
// With the 1:2 voltage divider, GPIO4 sees ~1.25V (ADC raw ~1550 at 12-bit).
// Calibrate with pH 4.0 and pH 7.0 buffer solutions:
//   1. Dip probe in pH 7.0 buffer → note raw ADC from Serial Monitor
//   2. Dip probe in pH 4.0 buffer → note raw ADC
//   3. Calculate:
//      PH_SLOPE     = (7.0 - 4.0) / (adc_at_7 - adc_at_4)
//      PH_INTERCEPT = 7.0 - PH_SLOPE * adc_at_7
// Default values are approximate — REPLACE with your measured values.
#define PH_SLOPE      -0.0034f   // approx for 4502C with 1:2 divider
#define PH_INTERCEPT   12.26f    // approx for 4502C with 1:2 divider

// ── Timing ───────────────────────────────────────────────────────────────────
#define PPG_COLLECT_MS   5000
#define PPG_RATE_HZ       100
#define PPG_SAMPLES       (PPG_RATE_HZ * PPG_COLLECT_MS / 1000)   // 500
#define POLL_INTERVAL_MS 5000    // poll Supabase every 5s
#define POLL_TIMEOUT_MS  300000  // 5 minutes max wait
#define BTN_DEBOUNCE_MS   250

// ── Libraries ────────────────────────────────────────────────────────────────
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <SparkFunTMP117.h>
#include <algorithm>

#ifdef DISPLAY_OLED
  #include <Adafruit_GFX.h>
  #include <Adafruit_SSD1306.h>
  #define OLED_W       128
  #define OLED_H        64
  #define OLED_ADDR   0x3C
  Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);
  bool oledReady = false;
#endif

// ── Hardware Objects ─────────────────────────────────────────────────────────
MAX30105  ppg;
TMP117    tmp;
WebServer server(80);

// ── State Machine ─────────────────────────────────────────────────────────────
enum Step { STEP_HOME=0, STEP_PH, STEP_TEMP, STEP_PPG, STEP_UPLOAD };
Step          currentStep = STEP_HOME;
long          currentRowId = -1;
unsigned long lastBtnTime  = 0;

// Collected values
float g_ph=0, g_temp=0, g_dc=0, g_ac=0, g_hr=0, g_pi=0, g_pw=0;

// =============================================================================
// ── DISPLAY ABSTRACTION ───────────────────────────────────────────────────────
// All UI calls go through these functions.
// Serial is ALWAYS written. OLED is written when DISPLAY_OLED is defined.
// =============================================================================

// divider line in serial
void serialDivider(char c = '-') {
  for (int i = 0; i < 40; i++) Serial.print(c);
  Serial.println();
}

#ifdef DISPLAY_OLED
void oledClear() {
  if (!oledReady) return;
  oled.clearDisplay();
}
void oledShow() {
  if (!oledReady) return;
  oled.display();
}
void oledText(int x, int y, const char* txt, uint8_t sz = 1) {
  if (!oledReady) return;
  oled.setTextSize(sz);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(x, y);
  oled.print(txt);
}
void oledTextF(int x, int y, uint8_t sz, const char* fmt, ...) {
  if (!oledReady) return;
  char buf[64];
  va_list args; va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  oledText(x, y, buf, sz);
}
// Step progress indicator  [1][2][3][4]
void oledStepBar(int active) {
  if (!oledReady) return;
  int xs[] = {4, 36, 68, 100};
  for (int i = 0; i < 4; i++) {
    if (i + 1 <= active)
      oled.fillRect(xs[i], 55, 28, 9, SSD1306_WHITE);
    else
      oled.drawRect(xs[i], 55, 28, 9, SSD1306_WHITE);
    oled.setTextColor(i + 1 <= active ? SSD1306_BLACK : SSD1306_WHITE);
    oled.setTextSize(1);
    oled.setCursor(xs[i] + 11, 57);
    oled.print(i + 1);
  }
}
#endif

// ── Unified screen functions (write to Serial + OLED if available) ────────────

void showHome() {
  serialDivider('=');
  Serial.println("  GLUCOSE MONITOR — HOME");
  serialDivider('=');
  Serial.printf("  Device : %s\n", DEVICE_ID);
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("  Network: Connected  IP: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("  Network: Not connected");
  serialDivider();
  Serial.println("  Steps: pH → Temp → PPG → Upload");
  Serial.println("  >>> Press button to begin <<<");
  serialDivider('=');

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "GLUCOSE MONITOR", 1);
  oledText(0, 12, "Press button", 1);
  oledText(0, 22, "to begin", 1);
  if (WiFi.status() == WL_CONNECTED) {
    oledText(0, 36, WiFi.localIP().toString().c_str(), 1);
  } else {
    oledText(0, 36, "No WiFi", 1);
  }
  oledStepBar(1);
  oledShow();
  #endif
}

void showPHReady() {
  serialDivider('=');
  Serial.println("  STEP 1 — pH PROBE");
  serialDivider();
  Serial.println("  1. Place probe in saliva");
  Serial.println("  2. Hold steady");
  Serial.println("  >>> Press button to collect pH <<<");
  serialDivider();
  Serial.println("  Method: 32 ADC samples → MEDIAN");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "STEP 1: pH", 1);
  oledText(0, 12, "Place probe in", 1);
  oledText(0, 22, "saliva & hold", 1);
  oledText(0, 34, "Press to collect", 1);
  oledStepBar(1);
  oledShow();
  #endif
}

void showPHCollecting(int sample, int total) {
  if (sample % 8 == 0) {  // print every 8th sample to avoid flooding serial
    Serial.printf("  [pH] Collecting... %d/%d\r", sample, total);
  }

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "Collecting pH...", 1);
  char buf[20]; snprintf(buf, sizeof(buf), "%d / %d samples", sample, total);
  oledText(0, 14, buf, 1);
  oledText(0, 26, "Hold still!", 1);
  // progress bar
  oled.drawRect(0, 40, 128, 8, SSD1306_WHITE);
  int fw = 126 * sample / total;
  if (fw > 0) oled.fillRect(1, 41, fw, 6, SSD1306_WHITE);
  oledStepBar(1);
  oledShow();
  #endif
}

void showPHDone(float ph) {
  Serial.println();
  serialDivider();
  Serial.printf("  [pH] DONE\n");
  Serial.printf("  Saliva pH : %.3f\n", ph);
  const char* interp =
    (ph < 5.5f) ? "Very Acidic" :
    (ph < 6.2f) ? "Acidic" :
    (ph < 7.4f) ? "Normal (6.2-7.4)" :
    (ph < 8.0f) ? "Slightly Alkaline" : "Alkaline";
  Serial.printf("  Interp    : %s\n", interp);
  serialDivider();
  Serial.println("  >>> Press button for Temperature <<<");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "pH DONE", 1);
  char buf[20]; snprintf(buf, sizeof(buf), "pH: %.3f", ph);
  oledText(0, 14, buf, 2);
  oledText(0, 36, interp, 1);
  oledText(0, 46, "Press for Temp->", 1);
  oledStepBar(2);
  oledShow();
  #endif
}

void showTempReady() {
  serialDivider('=');
  Serial.println("  STEP 2 — TEMPERATURE");
  serialDivider();
  Serial.println("  1. Place TMP117 on skin (wrist/fingertip)");
  Serial.println("  2. Hold still for ~3 seconds");
  Serial.println("  >>> Press button to collect Temp <<<");
  serialDivider();
  Serial.println("  Method: 10 readings → TRIMMED MEAN (drop top/bottom 2)");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "STEP 2: TEMP", 1);
  oledText(0, 12, "Place sensor on", 1);
  oledText(0, 22, "skin & hold still", 1);
  oledText(0, 34, "Press to collect", 1);
  oledStepBar(2);
  oledShow();
  #endif
}

void showTempCollecting(int reading, int total) {
  Serial.printf("  [Temp] Reading %d/%d...\r", reading, total);

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "Collecting Temp", 1);
  char buf[20]; snprintf(buf, sizeof(buf), "%d / %d readings", reading, total);
  oledText(0, 14, buf, 1);
  oledText(0, 26, "Hold still!", 1);
  oled.drawRect(0, 40, 128, 8, SSD1306_WHITE);
  int fw = 126 * reading / total;
  if (fw > 0) oled.fillRect(1, 41, fw, 6, SSD1306_WHITE);
  oledStepBar(2);
  oledShow();
  #endif
}

void showTempDone(float temp) {
  Serial.println();
  serialDivider();
  Serial.printf("  [Temp] DONE\n");
  Serial.printf("  Temperature : %.2f C\n", temp);
  const char* status = (temp >= 34.0f && temp <= 37.5f) ? "Normal range" : "Outside normal";
  Serial.printf("  Status      : %s (normal 34.0-37.5 C)\n", status);
  serialDivider();
  Serial.println("  >>> Press button for PPG <<<");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "TEMP DONE", 1);
  char buf[20]; snprintf(buf, sizeof(buf), "%.2f C", temp);
  oledText(0, 14, buf, 2);
  oledText(0, 36, status, 1);
  oledText(0, 46, "Press for PPG ->", 1);
  oledStepBar(3);
  oledShow();
  #endif
}

void showPPGReady() {
  serialDivider('=');
  Serial.println("  STEP 3 — PPG / HEART RATE");
  serialDivider();
  Serial.println("  1. Place fingertip FIRMLY on MAX30102");
  Serial.println("  2. Keep VERY STILL for 5 seconds");
  Serial.println("  >>> Press button to start 5-sec collection <<<");
  serialDivider();
  Serial.println("  Method:");
  Serial.println("    DC  = mean of 500 IR samples (baseline)");
  Serial.println("    AC  = 95th pct - 5th pct    (robust amplitude)");
  Serial.println("    HR  = adaptive threshold peak count");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "STEP 3: PPG/HR", 1);
  oledText(0, 12, "Finger on sensor", 1);
  oledText(0, 22, "VERY still 5 sec", 1);
  oledText(0, 34, "Press to collect", 1);
  oledStepBar(3);
  oledShow();
  #endif
}

void showPPGProgress(int pct) {
  // Overwrite same line in serial
  Serial.printf("  [PPG] Collecting... %3d%%\r", pct);

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "PPG Collecting", 1);
  oledText(0, 12, "Keep STILL!", 1);
  char buf[16]; snprintf(buf, sizeof(buf), "%d%%", pct);
  oledText(50, 24, buf, 2);
  oled.drawRect(0, 44, 128, 8, SSD1306_WHITE);
  int fw = 126 * pct / 100;
  if (fw > 0) oled.fillRect(1, 45, fw, 6, SSD1306_WHITE);
  oledStepBar(3);
  oledShow();
  #endif
}

void showPPGDone(float dc, float ac, float hr, float pi, float pw) {
  Serial.println();
  serialDivider();
  Serial.printf("  [PPG] DONE\n");
  Serial.printf("  Heart Rate  : %.1f BPM\n",  hr);
  Serial.printf("  Perf. Index : %.3f %%\n",   pi);
  Serial.printf("  PPG DC      : %.0f ADC\n",  dc);
  Serial.printf("  PPG AC      : %.1f ADC\n",  ac);
  Serial.printf("  Pulse Width : %.1f ms\n",   pw);
  serialDivider();
  Serial.println("  All 3 sensors done!");
  Serial.println("  >>> Press button to UPLOAD & get result <<<");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "PPG DONE", 1);
  char h[20]; snprintf(h, sizeof(h), "HR: %.1f BPM", hr);
  oledText(0, 12, h, 1);
  char p[20]; snprintf(p, sizeof(p), "PI: %.2f%%", pi);
  oledText(0, 22, p, 1);
  oledText(0, 34, "Press to UPLOAD", 1);
  oledStepBar(4);
  oledShow();
  #endif
}

void showUploading() {
  serialDivider('=');
  Serial.println("  UPLOADING TO SUPABASE...");
  serialDivider();
  Serial.printf("  pH   : %.3f\n", g_ph);
  Serial.printf("  Temp : %.2f C\n", g_temp);
  Serial.printf("  HR   : %.1f BPM\n", g_hr);
  Serial.printf("  PI   : %.3f %%\n", g_pi);
  Serial.printf("  DC   : %.0f  AC: %.1f\n", g_dc, g_ac);

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "UPLOADING...", 1);
  char buf[24]; snprintf(buf, sizeof(buf), "pH:%.2f T:%.1fC", g_ph, g_temp);
  oledText(0, 14, buf, 1);
  snprintf(buf, sizeof(buf), "HR:%.0f PI:%.2f%%", g_hr, g_pi);
  oledText(0, 24, buf, 1);
  oledText(0, 36, "Please wait...", 1);
  oledStepBar(4);
  oledShow();
  #endif
}

void showWaiting(long rowId) {
  serialDivider('=');
  Serial.println("  WAITING FOR USER DETAILS");
  serialDivider();
  Serial.printf("  Supabase row ID : %ld\n", rowId);
  Serial.println("  Action required:");
  Serial.println("  1. Open the Streamlit dashboard");
  Serial.println("  2. Switch to LIVE SENSOR MODE");
  Serial.println("  3. Fill in Name, Age, BMI, Diagnosis");
  Serial.println("  4. Click SUBMIT");
  serialDivider();
  Serial.println("  Polling every 5s for status=complete...");

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "OPEN DASHBOARD", 1);
  oledText(0, 12, "Enter details &", 1);
  oledText(0, 22, "click Submit", 1);
  char buf[20]; snprintf(buf, sizeof(buf), "Row: %ld", rowId);
  oledText(0, 36, buf, 1);
  oledText(0, 46, "Polling...", 1);
  oledShow();
  #endif
}

void showPolling(int elapsed) {
  Serial.printf("  [Poll] %ds elapsed... checking Supabase\r", elapsed);

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0,  "Waiting for", 1);
  oledText(0, 10, "dashboard...", 1);
  char buf[24]; snprintf(buf, sizeof(buf), "%ds / 300s", elapsed);
  oledText(0, 24, buf, 1);
  oledText(0, 36, "Polling Supabase", 1);
  // animated dots
  static int dot = 0; dot = (dot + 1) % 4;
  for (int i = 0; i < dot; i++) {
    oled.fillCircle(10 + i * 14, 54, 3, SSD1306_WHITE);
  }
  oledShow();
  #endif
}

void showResults(const char* name, float bgl, float ciLo, float ciHi,
                 const char* zone, const char* cat, bool ood) {
  Serial.println();
  serialDivider('=');
  Serial.println("  *** PREDICTION RESULT ***");
  serialDivider('=');
  Serial.printf("  Patient         : %s\n",    name[0] ? name : "—");
  Serial.printf("  Blood Glucose   : %.1f mg/dL\n", bgl);
  Serial.printf("  90%% CI          : %.1f – %.1f mg/dL\n", ciLo, ciHi);
  Serial.printf("  Clarke Zone     : %s\n",    zone);
  Serial.printf("  Category        : %s\n",    cat);
  if (ood) Serial.println("  !! INPUT OUTSIDE TRAINING RANGE !!");
  serialDivider('=');
  Serial.println("  Showing for 30 seconds then returning to home.");
  serialDivider('=');

  #ifdef DISPLAY_OLED
  oledClear();
  char buf[24];
  oledText(0, 0, name[0] ? name : "---", 1);
  oled.drawFastHLine(0, 10, 128, SSD1306_WHITE);
  snprintf(buf, sizeof(buf), "%.1f mg/dL", bgl);
  oledText(0, 14, buf, 2);
  snprintf(buf, sizeof(buf), "CI:%.0f-%.0f", ciLo, ciHi);
  oledText(0, 34, buf, 1);
  snprintf(buf, sizeof(buf), "%s  %s", zone, cat);
  oledText(0, 44, buf, 1);
  if (ood) { oled.drawFastHLine(0, 53, 128, SSD1306_WHITE); oledText(0, 55, "!OOD", 1); }
  oledShow();
  #endif
}

void showError(const char* l1, const char* l2 = "") {
  serialDivider('!');
  Serial.printf("  ERROR: %s\n", l1);
  if (l2[0]) Serial.printf("         %s\n", l2);
  Serial.println("  Press button to restart");
  serialDivider('!');

  #ifdef DISPLAY_OLED
  oledClear();
  oledText(0, 0, "!! ERROR !!", 1);
  oledText(0, 14, l1, 1);
  if (l2[0]) oledText(0, 24, l2, 1);
  oledText(0, 46, "Press to restart", 1);
  oledShow();
  #endif
}

// =============================================================================
// ── SMART SENSOR COLLECTION ──────────────────────────────────────────────────
// =============================================================================

float collectPH() {
  const int N = 32;
  int samples[N];
  for (int i = 0; i < N; i++) {
    samples[i] = analogRead(PH_ADC_PIN);
    showPHCollecting(i + 1, N);
    delay(90);   // ~3 seconds total
  }
  std::sort(samples, samples + N);
  int medianRaw = (samples[N/2 - 1] + samples[N/2]) / 2;

  // Voltage at GPIO4 (after 1:2 divider): raw * 3.3 / 4095
  // Actual Po voltage from module: GPIO4_voltage * 2
  float gpio4V  = medianRaw * (3.3f / 4095.0f);
  float moduleV = gpio4V * 2.0f;  // undo the 1:2 divider
  float ph = PH_SLOPE * medianRaw + PH_INTERCEPT;

  Serial.printf("\n  [pH] Median ADC = %d\n", medianRaw);
  Serial.printf("  [pH] GPIO4 voltage  = %.3f V\n", gpio4V);
  Serial.printf("  [pH] Module Po volt = %.3f V  (after undoing divider)\n", moduleV);
  Serial.printf("  [pH] Calculated pH  = %.3f\n", ph);
  Serial.println("  [pH] NOTE: Use pH 4/7 buffers to calibrate PH_SLOPE & PH_INTERCEPT");
  return ph;
}

float collectTemp() {
  const int N = 10;
  float samples[N];
  for (int i = 0; i < N; i++) {
    while (!tmp.dataReady()) delay(10);
    samples[i] = tmp.readTempC();
    showTempCollecting(i + 1, N);
    Serial.printf("  [Temp] Reading %d = %.3f C\n", i+1, samples[i]);
    delay(200);
  }
  std::sort(samples, samples + N);
  float sum = 0;
  for (int i = 2; i < N - 2; i++) sum += samples[i];
  float temp = sum / (N - 4);
  Serial.printf("\n  [Temp] Trimmed mean = %.3f C  (sorted: %.2f...%.2f)\n",
                temp, samples[0], samples[N-1]);
  return temp;
}

bool collectPPG() {
  while (ppg.available()) { ppg.getIR(); ppg.getRed(); }

  uint32_t buf[PPG_SAMPLES];
  int n = 0;
  int lastPct = 0;
  unsigned long t0 = millis();

  while (n < PPG_SAMPLES && (millis() - t0) < PPG_COLLECT_MS + 1000) {
    if (ppg.available()) {
      buf[n++] = ppg.getIR();
      ppg.getRed();
    }
    int pct = n * 100 / PPG_SAMPLES;
    if (pct != lastPct && pct % 5 == 0) {
      showPPGProgress(pct);
      lastPct = pct;
    }
    delay(9);
  }

  if (n < PPG_SAMPLES / 2) {
    Serial.printf("\n  [PPG] Only %d samples (need %d) — failed\n", n, PPG_SAMPLES/2);
    return false;
  }

  // DC = mean
  uint64_t sv = 0;
  for (int i = 0; i < n; i++) sv += buf[i];
  g_dc = (float)sv / n;

  // AC = 95th − 5th percentile
  uint32_t sorted[PPG_SAMPLES];
  memcpy(sorted, buf, n * sizeof(uint32_t));
  std::sort(sorted, sorted + n);
  int p5  = sorted[(int)(n * 0.05f)];
  int p95 = sorted[(int)(n * 0.95f)];
  g_ac = (float)(p95 - p5);

  // HR via adaptive threshold (DC + 30% AC)
  float thresh = g_dc + 0.30f * g_ac;
  int beats = 0; bool above = false;
  for (int i = 0; i < n; i++) {
    bool cur = buf[i] > thresh;
    if (cur && !above) beats++;
    above = cur;
  }
  g_hr = (beats > 0) ? ((float)beats * 60000.0f / PPG_COLLECT_MS) : 70.0f;
  if (g_hr < 30.0f)  g_hr = 30.0f;
  if (g_hr > 220.0f) g_hr = 220.0f;

  g_pi = (g_ac / g_dc) * 100.0f;
  g_pw = (g_hr > 0) ? (60000.0f / g_hr) : 857.0f;

  Serial.printf("\n  [PPG] n=%d  DC=%.0f  AC=%.0f  HR=%.1f  PI=%.3f  PW=%.1f\n",
                n, g_dc, g_ac, g_hr, g_pi, g_pw);
  return true;
}

// =============================================================================
// ── SUPABASE ─────────────────────────────────────────────────────────────────
// =============================================================================

void sbHeaders(HTTPClient& h) {
  h.addHeader("Content-Type",  "application/json");
  h.addHeader("apikey",         SUPABASE_ANON_KEY);
  h.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
}

long insertPending() {
  HTTPClient http;
  http.begin(String("https://") + SUPABASE_PROJECT +
             ".supabase.co/rest/v1/readings?select=id");
  sbHeaders(http);
  http.addHeader("Prefer", "return=representation");

  StaticJsonDocument<512> doc;
  doc["device_id"]           = DEVICE_ID;
  doc["status"]              = "pending";
  doc["saliva_ph"]           = g_ph;
  doc["hr_bpm"]              = g_hr;
  doc["ppg_raw_dc_baseline"] = g_dc;
  doc["ppg_raw_ac_p2p"]      = g_ac;
  doc["temperature_c"]       = g_temp;
  doc["perfusion_index"]     = g_pi;
  doc["pulse_width_ms"]      = g_pw;

  String body; serializeJson(doc, body);
  Serial.println("  [Supabase] Sending POST...");
  Serial.println("  Payload: " + body);

  int code = http.POST(body);
  long rowId = -1;
  if (code == 201) {
    DynamicJsonDocument rdoc(256);
    deserializeJson(rdoc, http.getString());
    rowId = rdoc[0]["id"].as<long>();
    Serial.printf("  [Supabase] Insert OK — row id=%ld\n", rowId);
  } else {
    Serial.printf("  [Supabase] Insert FAILED HTTP %d: %s\n",
                  code, http.getString().c_str());
  }
  http.end();
  return rowId;
}

bool pollResult(long rowId,
                char* name, size_t nLen,
                float& bgl, float& ciLo, float& ciHi,
                char* zone, size_t zLen,
                char* cat,  size_t cLen,
                bool& ood) {
  HTTPClient http;
  http.begin(String("https://") + SUPABASE_PROJECT +
             ".supabase.co/rest/v1/readings"
             "?id=eq." + String(rowId) +
             "&select=status,patient_name,predicted_bgl_mg_dl,"
             "ci_low_mg_dl,ci_high_mg_dl,clarke_zone,glucose_category,is_ood");
  sbHeaders(http);
  http.addHeader("Accept", "application/json");

  int code = http.GET();
  bool done = false;
  if (code == 200) {
    DynamicJsonDocument doc(512);
    if (!deserializeJson(doc, http.getString()) && doc.size() > 0) {
      const char* st = doc[0]["status"] | "pending";
      Serial.printf("  [Poll] Row %ld status = %s\n", rowId, st);
      if (strcmp(st, "complete") == 0) {
        strlcpy(name, doc[0]["patient_name"] | "", nLen);
        bgl  = doc[0]["predicted_bgl_mg_dl"] | 0.0f;
        ciLo = doc[0]["ci_low_mg_dl"]        | (bgl - 20.0f);
        ciHi = doc[0]["ci_high_mg_dl"]        | (bgl + 20.0f);
        strlcpy(zone, doc[0]["clarke_zone"]      | "Zone A", zLen);
        strlcpy(cat,  doc[0]["glucose_category"] | "Normal", cLen);
        ood  = doc[0]["is_ood"] | false;
        done = true;
      }
    }
  } else {
    Serial.printf("  [Poll] HTTP %d\n", code);
  }
  http.end();
  return done;
}

// =============================================================================
// ── WEB SERVER ───────────────────────────────────────────────────────────────
// =============================================================================

void onStartReading() {
  if (currentStep != STEP_HOME) {
    server.send(409,"application/json","{\"error\":\"Reading in progress\"}");
    return;
  }
  server.send(200,"application/json","{\"status\":\"started\"}");
  Serial.println("  [Web] Remote start triggered from dashboard");
  currentStep = STEP_PH;
  showPHReady();
}

void onStatus() {
  StaticJsonDocument<200> doc;
  doc["device_id"] = DEVICE_ID;
  doc["step"]      = (int)currentStep;
  doc["row_id"]    = currentRowId;
  doc["wifi"]      = (WiFi.status() == WL_CONNECTED);
  doc["ip"]        = WiFi.localIP().toString();
  String r; serializeJson(doc,r);
  server.send(200,"application/json",r);
}

// =============================================================================
// ── BUTTON ───────────────────────────────────────────────────────────────────
// =============================================================================

bool buttonPressed() {
  if (digitalRead(BUTTON_PIN) == LOW) {
    if (millis() - lastBtnTime > BTN_DEBOUNCE_MS) {
      lastBtnTime = millis();
      while (digitalRead(BUTTON_PIN) == LOW) delay(10);
      delay(50);
      Serial.printf("  [BTN] Pressed at step %d\n", (int)currentStep);
      return true;
    }
  }
  return false;
}

// =============================================================================
// ── SETUP ────────────────────────────────────────────────────────────────────
// =============================================================================

void setup() {
  Serial.begin(115200);
  delay(500);
  serialDivider('=');
  Serial.println("  ESP32-S3 Glucose Monitor — Starting");
  serialDivider('=');

  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.println("  I2C bus started on SDA=8, SCL=9");

  // ── OLED init (only when DISPLAY_OLED defined) ────────────────────────────
  #ifdef DISPLAY_OLED
  Serial.println("  OLED: Initialising SSD1306 0.96\" at 0x3C...");
  if (oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    oledReady = true;
    oled.clearDisplay();
    oled.setTextColor(SSD1306_WHITE);
    oled.setTextSize(1);
    oled.setCursor(0, 0); oled.print("Glucose Monitor");
    oled.setCursor(0,12); oled.print("Initialising...");
    oled.display();
    Serial.println("  OLED: OK");
  } else {
    Serial.println("  OLED: FAILED (check wiring / I2C address)");
  }
  #else
  Serial.println("  Display: SERIAL MONITOR ONLY (DISPLAY_NONE active)");
  Serial.println("  To enable OLED: uncomment #define DISPLAY_OLED");
  #endif

  // ── MAX30102 ──────────────────────────────────────────────────────────────
  Serial.println("  MAX30102: Initialising...");
  if (!ppg.begin()) {
    showError("MAX30102 not found", "Check I2C SDA=8 SCL=9");
    while (true) delay(1000);
  }
  ppg.setup();
  ppg.setPulseAmplitudeRed(0x0A);
  ppg.setPulseAmplitudeIR(0x1F);
  Serial.println("  MAX30102: OK");

  // ── TMP117 ────────────────────────────────────────────────────────────────
  Serial.println("  TMP117:   Initialising...");
  if (!tmp.begin()) {
    showError("TMP117 not found", "Check I2C SDA=8 SCL=9");
    while (true) delay(1000);
  }
  Serial.println("  TMP117:   OK");

  // ── WiFi ──────────────────────────────────────────────────────────────────
  Serial.printf("  WiFi: Connecting to '%s'...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t++ < 24) {
    delay(500); Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("  WiFi: Connected — IP: %s\n", WiFi.localIP().toString().c_str());
    server.on("/start_reading", HTTP_POST, onStartReading);
    server.on("/status",        HTTP_GET,  onStatus);
    server.begin();
    Serial.println("  Web server: started");
  } else {
    Serial.println("  WiFi: FAILED — running offline");
  }

  serialDivider('=');
  Serial.println("  Setup complete. Ready.");
  serialDivider('=');
  showHome();
}

// =============================================================================
// ── LOOP — STATE MACHINE ─────────────────────────────────────────────────────
// =============================================================================

void loop() {
  server.handleClient();
  bool btn = buttonPressed();

  switch (currentStep) {

    case STEP_HOME:
      if (btn) {
        currentStep = STEP_PH;
        showPHReady();
      }
      break;

    case STEP_PH:
      if (btn) {
        g_ph = collectPH();
        showPHDone(g_ph);
        currentStep = STEP_TEMP;
        delay(800);
        showTempReady();
      }
      break;

    case STEP_TEMP:
      if (btn) {
        g_temp = collectTemp();
        showTempDone(g_temp);
        currentStep = STEP_PPG;
        delay(800);
        showPPGReady();
      }
      break;

    case STEP_PPG:
      if (btn) {
        // Show "collecting" header then start
        serialDivider('=');
        Serial.println("  [PPG] Starting 5-second collection — KEEP STILL");
        serialDivider();
        showPPGProgress(0);
        if (!collectPPG()) {
          showError("PPG failed", "Keep finger still, retry");
          currentStep = STEP_PPG;
          delay(2000);
          showPPGReady();
          break;
        }
        showPPGDone(g_dc, g_ac, g_hr, g_pi, g_pw);
        currentStep = STEP_UPLOAD;
      }
      break;

    case STEP_UPLOAD:
      if (btn) {
        showUploading();

        if (WiFi.status() != WL_CONNECTED) {
          Serial.println("  WiFi lost — reconnecting...");
          WiFi.reconnect();
          delay(3000);
          if (WiFi.status() != WL_CONNECTED) {
            showError("No WiFi", "Check credentials");
            currentStep = STEP_HOME;
            delay(3000);
            showHome();
            break;
          }
        }

        long rowId = insertPending();
        if (rowId < 0) {
          showError("Upload failed", "Check WiFi/Supabase");
          currentStep = STEP_HOME;
          delay(3000);
          showHome();
          break;
        }
        currentRowId = rowId;
        showWaiting(rowId);

        // Poll loop
        char name[64]="", zone[32]="Zone A", cat[32]="Normal";
        float bgl=0, ciLo=0, ciHi=0;
        bool ood=false, got=false;
        unsigned long t0 = millis();

        while ((millis()-t0) < POLL_TIMEOUT_MS) {
          delay(POLL_INTERVAL_MS);
          int elapsed = (millis()-t0) / 1000;
          showPolling(elapsed);
          if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(1000); continue; }
          server.handleClient();
          got = pollResult(rowId, name, sizeof(name),
                           bgl, ciLo, ciHi,
                           zone, sizeof(zone), cat, sizeof(cat), ood);
          if (got) break;
        }

        if (got) {
          showResults(name, bgl, ciLo, ciHi, zone, cat, ood);
          delay(30000);
        } else {
          showError("Timeout", "Dashboard not submitted");
          delay(3000);
        }

        // Reset for next cycle
        currentStep = STEP_HOME;
        g_ph=0; g_temp=0; g_dc=0; g_ac=0; g_hr=0; g_pi=0; g_pw=0;
        showHome();
      }
      break;
  }

  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  delay(20);
}
