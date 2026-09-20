/*
 * ============================================================================
 * Non-Invasive Glucose Prediction System — ESP32-S3 Interactive Sensor Node
 * ============================================================================
 *
 * BUTTON-DRIVEN STEP-BY-STEP FLOW:
 *
 *   HOME SCREEN
 *     └─ [Button 1] ──► pH COLLECTION
 *                            collect 32 ADC samples over 3s
 *                            final value = MEDIAN (rejects electrode noise)
 *                            display result, prompt next press
 *                        [Button 2] ──► TEMPERATURE COLLECTION
 *                            collect 10 TMP117 readings over 2s
 *                            final value = TRIMMED MEAN (drop top/bottom 2)
 *                            display result, prompt next press
 *                        [Button 3] ──► PPG / HEART RATE COLLECTION
 *                            collect 5-second MAX30102 IR buffer (~500 samples)
 *                            DC  = mean of all samples  (stable baseline)
 *                            AC  = 95th pct − 5th pct   (robust p2p, rejects spikes)
 *                            HR  = peak count with adaptive threshold
 *                            PI  = AC/DC × 100
 *                            PW  = 60000 / HR
 *                            display all values, prompt final press
 *                        [Button 4] ──► UPLOAD to Supabase (status='pending')
 *                            display "Open dashboard, enter details"
 *                            poll every 5s for status='complete'
 *                            when complete → show results screen (30s)
 *                            return to HOME
 *
 * WIRING:
 *   MAX30102   SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND
 *   TMP117     SDA→GPIO8  SCL→GPIO9  VCC→3.3V  GND→GND
 *   pH probe   VOUT→GPIO4  GND→GND  (signal-conditioned 0–3.3V)
 *   ST7789     SCL→GPIO18  SDA→GPIO23  RES→GPIO2  DC→GPIO15  BLK→GPIO21
 *   Button     GPIO0 → GND  (internal pull-up)
 *
 * LIBRARIES (Arduino Library Manager):
 *   Adafruit ST7789 · Adafruit GFX · SparkFun MAX3010x · SparkFun TMP117
 *   ArduinoJson >= 6   (WiFi / HTTPClient / WebServer — built into ESP32 core)
 * ============================================================================
 */

// ── User Configuration ───────────────────────────────────────────────────────
#define WIFI_SSID         "YOUR_WIFI_SSID"
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"
#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID         "esp32_node_01"

// ── Pin Configuration ────────────────────────────────────────────────────────
#define I2C_SDA     8
#define I2C_SCL     9
#define TFT_SCL    18
#define TFT_SDA    23
#define TFT_RES     2
#define TFT_DC     15
#define TFT_BLK    21
#define PH_ADC_PIN  4
#define BUTTON_PIN  0

// ── pH Calibration ───────────────────────────────────────────────────────────
// Measure ADC at pH 4.0 and pH 7.0 buffer solutions, then calculate:
//   PH_SLOPE     = (7.0 - 4.0) / (adc_at_7 - adc_at_4)
//   PH_INTERCEPT = 7.0 - PH_SLOPE * adc_at_7
#define PH_SLOPE      -0.0017f   // replace with calibrated value
#define PH_INTERCEPT   14.0f     // replace with calibrated value

// ── Timing ───────────────────────────────────────────────────────────────────
#define PPG_COLLECT_MS    5000    // 5 seconds of PPG data
#define PPG_RATE_HZ        100    // MAX30102 sample rate
#define PPG_SAMPLES       (PPG_RATE_HZ * PPG_COLLECT_MS / 1000)  // 500
#define POLL_INTERVAL_MS  5000    // how often ESP32 checks Supabase for 'complete'
#define POLL_TIMEOUT_MS  300000   // give up after 5 minutes
#define BTN_DEBOUNCE_MS    250

// ── Libraries ────────────────────────────────────────────────────────────────
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <SparkFunTMP117.h>
#include <algorithm>   // std::sort, std::nth_element

// ── Hardware Objects ─────────────────────────────────────────────────────────
MAX30105        ppg;
TMP117          tmp;
Adafruit_ST7789 tft = Adafruit_ST7789(TFT_DC, TFT_RES, TFT_SDA, TFT_SCL);
WebServer       server(80);

// ── Colours ──────────────────────────────────────────────────────────────────
#define C_BG     0x0000
#define C_WHITE  0xFFFF
#define C_CYAN   0x07FF
#define C_YELLOW 0xFFE0
#define C_GREEN  0x07E0
#define C_RED    0xF800
#define C_ORANGE 0xFD20
#define C_BLUE   0x001F
#define C_LBLUE  0x867F
#define C_GREY   0x7BEF
#define C_DGREY  0x39E7

// ── State Machine ────────────────────────────────────────────────────────────
enum Step {
  STEP_HOME = 0,
  STEP_PH,
  STEP_TEMP,
  STEP_PPG,
  STEP_UPLOAD
};

Step        currentStep = STEP_HOME;
bool        stepReady   = false;   // set true when button pressed
long        currentRowId = -1;
unsigned long lastBtnTime = 0;

// Collected sensor values (filled one step at a time)
float g_ph    = 0;
float g_temp  = 0;
float g_dc    = 0;
float g_ac    = 0;
float g_hr    = 0;
float g_pi    = 0;
float g_pw    = 0;

// =============================================================================
// ── DISPLAY UTILITIES ────────────────────────────────────────────────────────
// =============================================================================

void tftClear() { tft.fillScreen(C_BG); }

// Double-border chrome with title bar
void chrome(const char* title, uint16_t barBg, uint16_t barFg) {
  tft.drawRect(0, 0, 240, 240, C_WHITE);
  tft.drawRect(1, 1, 238, 238, C_DGREY);
  tft.fillRect(2, 2, 236, 28, barBg);
  tft.drawRect(2, 2, 236, 28, C_WHITE);
  tft.setTextSize(2);
  tft.setTextColor(barFg);
  int tw = strlen(title) * 12;
  tft.setCursor((240 - tw) / 2, 9);
  tft.print(title);
}

void hline(int y) { tft.drawFastHLine(4, y, 232, C_DGREY); }

// Progress stepper  e.g.  [1] ─── [2] ─── [3] ─── [4]
void stepBar(int active) {
  int xs[] = {20, 80, 140, 200};
  for (int i = 0; i < 4; i++) {
    uint16_t col = (i + 1 < active)  ? C_GREEN  :
                   (i + 1 == active) ? C_YELLOW : C_DGREY;
    tft.fillCircle(xs[i], 220, 8, col);
    tft.setTextColor(C_BG); tft.setTextSize(1);
    tft.setCursor(xs[i] - 3, 216);
    tft.print(i + 1);
    if (i < 3) {
      uint16_t lc = (i + 2 <= active) ? C_GREEN : C_DGREY;
      tft.drawFastHLine(xs[i] + 9, 220, xs[i+1] - xs[i] - 18, lc);
    }
  }
}

// Large value display row:  label (left)   value  unit (right)
void bigVal(int y, const char* label, float val, const char* unit,
            int decimals = 2, uint16_t valCol = C_GREEN) {
  tft.setTextSize(1); tft.setTextColor(C_GREY);
  tft.setCursor(10, y); tft.print(label);
  tft.setTextColor(valCol); tft.setTextSize(2);
  char buf[16];
  if (decimals == 0) snprintf(buf, sizeof(buf), "%.0f", val);
  else if (decimals == 1) snprintf(buf, sizeof(buf), "%.1f", val);
  else snprintf(buf, sizeof(buf), "%.2f", val);
  tft.setCursor(10, y + 10); tft.print(buf);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.print(" "); tft.print(unit);
}

// "Press button to continue" prompt at bottom
void pressPrompt(const char* msg = "Press button to continue") {
  tft.fillRect(4, 196, 232, 16, C_BG);
  tft.setTextColor(C_CYAN); tft.setTextSize(1);
  int tw = strlen(msg) * 6;
  tft.setCursor((240 - tw) / 2, 198);
  tft.print(msg);
}

// =============================================================================
// ── SCREENS ──────────────────────────────────────────────────────────────────
// =============================================================================

void screenHome() {
  tftClear();
  chrome("  Glucose Monitor", 0x000F, C_CYAN);
  hline(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Device : ");
  tft.setTextColor(C_CYAN); tft.print(DEVICE_ID);

  tft.setTextColor(C_WHITE); tft.setCursor(10, 56); tft.print("Network: ");
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_GREEN); tft.print("Connected");
    tft.setTextColor(C_DGREY); tft.setCursor(10, 70);
    tft.print(WiFi.localIP().toString().c_str());
  } else {
    tft.setTextColor(C_RED); tft.print("No WiFi");
  }

  hline(84);

  tft.setTextColor(C_YELLOW); tft.setTextSize(2);
  tft.setCursor(30, 100); tft.print("Press Button");
  tft.setCursor(42, 122); tft.print("to Begin");

  hline(148);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 156); tft.print("3 sensors, 1 button press each:");
  tft.setTextColor(C_CYAN);
  tft.setCursor(14, 170); tft.print("1. pH Probe");
  tft.setCursor(14, 182); tft.print("2. Temperature");
  tft.setCursor(14, 194); tft.print("3. PPG / Heart Rate");

  stepBar(1);
}

void screenPHReady() {
  tftClear();
  chrome("  Step 1: pH", 0x2800, C_YELLOW);
  hline(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Saliva pH Measurement");

  tft.setTextColor(C_GREY);
  tft.setCursor(10, 60);  tft.print("1. Place pH probe in saliva");
  tft.setCursor(10, 74);  tft.print("2. Hold probe steady");
  tft.setCursor(10, 88);  tft.print("3. Press button to collect");

  hline(104);

  tft.setTextColor(C_CYAN); tft.setTextSize(1);
  tft.setCursor(10, 112); tft.print("Method: 32 samples, MEDIAN");
  tft.setCursor(10, 126); tft.print("(rejects electrode noise)");

  stepBar(1);
  pressPrompt("Press button to start pH read");
}

void screenPHCollecting() {
  // Just update the status line — chrome already drawn
  tft.fillRect(4, 155, 232, 30, C_BG);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 158); tft.print("Collecting 32 samples...");
  tft.setCursor(10, 172); tft.print("Hold probe steady in saliva");
}

void screenPHDone(float ph) {
  tftClear();
  chrome("  pH — Done", 0x0410, C_GREEN);
  hline(34);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Collected: 32 samples (median)");

  tft.setTextColor(C_GREEN); tft.setTextSize(1);
  tft.setCursor(10, 58); tft.print("Saliva pH:");
  tft.setTextColor(C_WHITE); tft.setTextSize(3);
  char buf[8]; snprintf(buf, sizeof(buf), "%.2f", ph);
  tft.setCursor(30, 72); tft.print(buf);
  tft.setTextColor(C_GREY); tft.setTextSize(1); tft.print(" pH");

  hline(110);

  // pH interpretation
  tft.setTextColor(C_GREY); tft.setCursor(10, 118); tft.print("Interpretation:");
  uint16_t col = C_GREEN;
  const char* interp = "Normal range";
  if      (ph < 5.5f) { col = C_RED;    interp = "Very Acidic"; }
  else if (ph < 6.2f) { col = C_ORANGE; interp = "Acidic"; }
  else if (ph < 7.4f) { col = C_GREEN;  interp = "Normal (6.2–7.4)"; }
  else if (ph < 8.0f) { col = C_YELLOW; interp = "Slightly Alkaline"; }
  else                { col = C_ORANGE; interp = "Alkaline"; }
  tft.setTextColor(col); tft.setCursor(10, 132); tft.print(interp);

  hline(146);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 154); tft.print("Next: Temperature sensor");

  stepBar(2);
  pressPrompt("Press button for Temperature");
}

void screenTempReady() {
  tftClear();
  chrome("Step 2: Temp", 0x2800, C_YELLOW);
  hline(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Skin Temperature Measurement");

  tft.setTextColor(C_GREY);
  tft.setCursor(10, 60);  tft.print("1. Place TMP117 sensor on skin");
  tft.setCursor(10, 74);  tft.print("   (wrist or fingertip)");
  tft.setCursor(10, 88);  tft.print("2. Hold still for 3 seconds");
  tft.setCursor(10, 102); tft.print("3. Press button to collect");

  hline(118);

  tft.setTextColor(C_CYAN); tft.setTextSize(1);
  tft.setCursor(10, 126); tft.print("Method: 10 readings, trimmed mean");
  tft.setCursor(10, 140); tft.print("(drops highest & lowest 2)");

  stepBar(2);
  pressPrompt("Press button to start Temp read");
}

void screenTempCollecting() {
  tft.fillRect(4, 155, 232, 30, C_BG);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 158); tft.print("Collecting 10 readings...");
  tft.setCursor(10, 172); tft.print("Hold sensor still on skin");
}

void screenTempDone(float temp) {
  tftClear();
  chrome(" Temp — Done", 0x0410, C_GREEN);
  hline(34);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Collected: 10 readings (trimmed mean)");

  tft.setTextColor(C_GREEN); tft.setTextSize(1);
  tft.setCursor(10, 58); tft.print("Skin Temperature:");
  tft.setTextColor(C_WHITE); tft.setTextSize(3);
  char buf[8]; snprintf(buf, sizeof(buf), "%.2f", temp);
  tft.setCursor(10, 72); tft.print(buf);
  tft.setTextColor(C_GREY); tft.setTextSize(1); tft.print(" C");

  hline(110);

  tft.setTextColor(C_GREY); tft.setCursor(10, 118); tft.print("Normal skin: 34.0–37.5 °C");
  uint16_t col = (temp >= 34.0f && temp <= 37.5f) ? C_GREEN : C_ORANGE;
  tft.setTextColor(col); tft.setCursor(10, 132);
  tft.print((temp >= 34.0f && temp <= 37.5f) ? "In normal range" : "Outside normal range");

  hline(146);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 154); tft.print("Next: PPG / Heart Rate sensor");

  stepBar(3);
  pressPrompt("Press button for PPG");
}

void screenPPGReady() {
  tftClear();
  chrome("Step 3: PPG/HR", 0x2800, C_YELLOW);
  hline(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("PPG & Heart Rate Measurement");

  tft.setTextColor(C_GREY);
  tft.setCursor(10, 60);  tft.print("1. Place fingertip firmly on");
  tft.setCursor(10, 74);  tft.print("   MAX30102 sensor");
  tft.setCursor(10, 88);  tft.print("2. Keep VERY STILL for 5 sec");
  tft.setCursor(10, 102); tft.print("3. Press button to collect");

  hline(118);

  tft.setTextColor(C_CYAN); tft.setTextSize(1);
  tft.setCursor(10, 126); tft.print("Method: 500 samples / 5 seconds");
  tft.setCursor(10, 140); tft.print("DC=mean  AC=pct95-pct5  HR=peaks");

  stepBar(3);
  pressPrompt("Press button to start PPG (5s)");
}

void screenPPGProgress(int pct) {
  // Progress bar that updates during collection — no full redraw
  tft.fillRect(4, 155, 232, 10, C_BG);
  tft.drawRect(4, 155, 232, 10, C_GREY);
  int fw = 230 * pct / 100;
  if (fw > 0) tft.fillRect(5, 156, fw, 8, C_LBLUE);
  tft.fillRect(4, 168, 232, 20, C_BG);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 170);
  tft.printf("Collecting... %d%%  keep still!", pct);
}

void screenPPGDone(float dc, float ac, float hr, float pi, float pw) {
  tftClear();
  chrome(" PPG — Done", 0x0410, C_GREEN);
  hline(34);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 40); tft.print("Collected: 500 samples (5 sec)");
  hline(52);

  bigVal(58,  "Heart Rate:", hr, "BPM", 1, C_CYAN);
  bigVal(90,  "Perfusion :", pi, "%",   2, C_GREEN);
  bigVal(122, "PPG DC    :", dc, "ADC", 0, C_GREY);
  bigVal(150, "PPG AC    :", ac, "ADC", 1, C_GREY);

  hline(178);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 184); tft.print("All 3 sensors complete!");
  tft.setTextColor(C_YELLOW);
  tft.setCursor(10, 198); tft.print("Press button to UPLOAD & get result");

  stepBar(4);
}

void screenUploading() {
  tftClear();
  chrome("  Uploading...", 0x0010, C_CYAN);
  hline(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 44); tft.print("Sending sensor data to cloud...");

  tft.setTextColor(C_GREY);
  char pbuf[20];
  snprintf(pbuf, sizeof(pbuf), "pH:   %.2f", g_ph);   tft.setCursor(20, 68);  tft.print(pbuf);
  snprintf(pbuf, sizeof(pbuf), "Temp: %.2f C", g_temp); tft.setCursor(20, 82);  tft.print(pbuf);
  snprintf(pbuf, sizeof(pbuf), "HR:   %.1f BPM", g_hr); tft.setCursor(20, 96);  tft.print(pbuf);
  snprintf(pbuf, sizeof(pbuf), "PI:   %.2f %%", g_pi);  tft.setCursor(20, 110); tft.print(pbuf);

  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 134); tft.print("Please wait...");

  stepBar(4);
}

void screenWaiting(long rowId, int elapsed) {
  // Called once at first, then updateWaitingTimer() refreshes only the counter
  tftClear();
  chrome(" Awaiting User", 0x3000, C_YELLOW);
  hline(34);

  tft.setTextColor(C_GREEN); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Uploaded! Row ID: ");
  tft.setTextColor(C_CYAN); tft.print(rowId);

  hline(54);

  tft.setTextColor(C_WHITE); tft.setTextSize(2);
  tft.setCursor(16, 66); tft.print("Open Dashboard");

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 92);  tft.print("Fill in your details:");
  tft.setTextColor(C_CYAN);
  tft.setCursor(16, 106); tft.print("Name, Age, BMI");
  tft.setCursor(16, 120); tft.print("Diagnosis, Medications");
  tft.setTextColor(C_WHITE);
  tft.setCursor(10, 136); tft.print("Then click ");
  tft.setTextColor(C_GREEN); tft.print("Submit");

  hline(150);

  // WiFi IP for quick access
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_DGREY); tft.setTextSize(1);
    tft.setCursor(10, 158); tft.print("Dashboard IP polling active...");
  }

  stepBar(4);
}

void updateWaitingTimer(int elapsed) {
  tft.fillRect(4, 172, 232, 20, C_BG);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 174);
  tft.printf("Waiting %ds / 300s for result...", elapsed);
  // Pulse dots
  static int dot = 0; dot = (dot + 1) % 4;
  tft.fillRect(4, 188, 232, 14, C_BG);
  tft.setTextColor(C_LBLUE);
  tft.setCursor(100, 190);
  for (int i = 0; i < 4; i++) tft.print(i < dot ? ">" : ".");
}

void screenError(const char* l1, const char* l2 = "") {
  tftClear();
  chrome("   ERROR", C_RED, C_WHITE);
  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 80);  tft.print(l1);
  if (l2[0]) { tft.setCursor(10, 96); tft.print(l2); }
  tft.setTextColor(C_GREY);
  tft.setCursor(10, 130); tft.print("Press button to restart");
  delay(2000);
}

void screenResults(const char* name, float bgl, float ciLo, float ciHi,
                   const char* zone, const char* cat, bool ood) {
  tftClear();
  chrome("    Results", 0x0410, C_GREEN);

  // Patient
  hline(34);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 38); tft.print("Patient: ");
  tft.setTextColor(C_WHITE); tft.print(name[0] ? name : "—");
  hline(50);

  // BGL colour coding
  uint16_t bc = C_GREEN;
  if      (bgl < 70)  bc = C_RED;
  else if (bgl >= 180) bc = C_RED;
  else if (bgl >= 126) bc = C_ORANGE;
  else if (bgl >= 100) bc = C_YELLOW;

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 58); tft.print("Blood Glucose:");

  tft.setTextColor(bc); tft.setTextSize(3);
  char bb[12]; snprintf(bb, sizeof(bb), "%.1f", bgl);
  int bw = strlen(bb) * 18;
  tft.setCursor((240 - bw) / 2 - 18, 70);
  tft.print(bb);
  tft.setTextColor(C_GREY); tft.setTextSize(1); tft.print(" mg/dL");

  // CI
  tft.setTextColor(C_GREY); tft.setCursor(10, 106);
  tft.printf("90%% CI:  %.1f – %.1f mg/dL", ciLo, ciHi);

  hline(118);

  // Zone
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 126); tft.print("Clarke Zone:");
  uint16_t zc = C_GREEN;
  if (zone[5]=='B') zc=C_YELLOW;
  else if (zone[5]>'B') zc=C_ORANGE;
  tft.setTextColor(zc); tft.setTextSize(2);
  tft.setCursor(120, 122); tft.print(zone);

  hline(148);

  // Category
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 156); tft.print("Category: ");
  tft.setTextColor(bc); tft.print(cat);

  if (ood) {
    tft.setTextColor(C_ORANGE);
    tft.setCursor(10, 170); tft.print("! Input outside training range");
  }

  hline(180);
  tft.setTextColor(C_DGREY); tft.setTextSize(1);
  tft.setCursor(10, 186); tft.print("Screen resets in 30s");
  tft.setCursor(10, 200); tft.print("Research use only. Not clinical.");

  stepBar(5); // all done
}

// =============================================================================
// ── SMART SENSOR COLLECTION ──────────────────────────────────────────────────
// =============================================================================

// ------ pH: 32 samples → MEDIAN -------
// Median is the best choice for electrochemical sensors because:
// • pH electrodes produce occasional large spikes from electrode movement
// • Median completely ignores outliers; mean would be pulled by them
float collectPH() {
  screenPHCollecting();
  const int N = 32;
  int samples[N];
  for (int i = 0; i < N; i++) {
    samples[i] = analogRead(PH_ADC_PIN);
    delay(90);  // ~3 seconds total
  }
  // Partial sort to find median
  std::sort(samples, samples + N);
  int medianRaw = (samples[N/2 - 1] + samples[N/2]) / 2;
  float ph = PH_SLOPE * medianRaw + PH_INTERCEPT;
  Serial.printf("[pH] median ADC=%d  pH=%.2f\n", medianRaw, ph);
  return ph;
}

// ------ Temperature: 10 readings → TRIMMED MEAN -------
// Drop the 2 highest and 2 lowest, average the middle 6.
// Trimmed mean handles warm-up transients at start and
// any momentary contact breaks at end.
float collectTemp() {
  screenTempCollecting();
  const int N = 10;
  float samples[N];
  for (int i = 0; i < N; i++) {
    while (!tmp.dataReady()) delay(10);
    samples[i] = tmp.readTempC();
    delay(200);
  }
  std::sort(samples, samples + N);
  // Drop bottom 2 and top 2 → average middle 6
  float sum = 0;
  for (int i = 2; i < N - 2; i++) sum += samples[i];
  float temp = sum / (N - 4);
  Serial.printf("[Temp] trimmed mean=%.2f C\n", temp);
  return temp;
}

// ------ PPG / Heart Rate: 500 samples (5s) → robust statistics -------
// DC  = MEAN of all samples           → stable perfusion baseline
// AC  = 95th PERCENTILE − 5th PERCENTILE  → robust peak-to-peak
//       (rejects motion spikes better than raw min/max)
// HR  = adaptive threshold peak counting
// PI  = AC/DC × 100
// PW  = 60000 / HR
bool collectPPG() {
  // Clear FIFO
  while (ppg.available()) { ppg.getIR(); ppg.getRed(); }

  uint32_t buf[PPG_SAMPLES];
  int n = 0;
  unsigned long t0 = millis();
  int lastPct = 0;

  while (n < PPG_SAMPLES && (millis() - t0) < PPG_COLLECT_MS + 1000) {
    if (ppg.available()) {
      buf[n++] = ppg.getIR();
      ppg.getRed();   // discard Red, we use IR only
    }
    // Update progress bar roughly every 10%
    int pct = n * 100 / PPG_SAMPLES;
    if (pct != lastPct && pct % 10 == 0) {
      screenPPGProgress(pct);
      lastPct = pct;
    }
    delay(9);
  }

  if (n < PPG_SAMPLES / 2) {
    Serial.println("[PPG] Insufficient samples");
    return false;
  }

  // ── DC: simple mean ──────────────────────────────────────────────────────
  uint64_t sumV = 0;
  for (int i = 0; i < n; i++) sumV += buf[i];
  g_dc = (float)sumV / n;

  // ── AC: 95th − 5th percentile (robust amplitude) ─────────────────────────
  // Copy to a sortable array
  uint32_t sorted[PPG_SAMPLES];
  memcpy(sorted, buf, n * sizeof(uint32_t));
  std::sort(sorted, sorted + n);
  int p5  = sorted[(int)(n * 0.05f)];
  int p95 = sorted[(int)(n * 0.95f)];
  g_ac = (float)(p95 - p5);

  // ── HR: adaptive threshold peak counting ─────────────────────────────────
  // Threshold = DC + 30% of AC amplitude
  float thresh = g_dc + 0.30f * g_ac;
  int beats = 0;
  bool above = false;
  for (int i = 0; i < n; i++) {
    bool cur = buf[i] > thresh;
    if (cur && !above) beats++;
    above = cur;
  }
  g_hr = (beats > 0) ? ((float)beats * 60000.0f / PPG_COLLECT_MS) : 70.0f;
  // Sanity clamp
  if (g_hr < 30.0f) g_hr = 30.0f;
  if (g_hr > 220.0f) g_hr = 220.0f;

  // ── PI and PW ─────────────────────────────────────────────────────────────
  g_pi = (g_ac / g_dc) * 100.0f;
  g_pw = (g_hr > 0) ? (60000.0f / g_hr) : 857.0f;

  Serial.printf("[PPG] n=%d  DC=%.0f  AC=%.0f  HR=%.1f  PI=%.2f  PW=%.1f\n",
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
  doc["device_id"]            = DEVICE_ID;
  doc["status"]               = "pending";
  doc["saliva_ph"]            = g_ph;
  doc["hr_bpm"]               = g_hr;
  doc["ppg_raw_dc_baseline"]  = g_dc;
  doc["ppg_raw_ac_p2p"]       = g_ac;
  doc["temperature_c"]        = g_temp;
  doc["perfusion_index"]      = g_pi;
  doc["pulse_width_ms"]       = g_pw;

  String body; serializeJson(doc, body);
  int code = http.POST(body);
  long rowId = -1;

  if (code == 201) {
    DynamicJsonDocument rdoc(256);
    deserializeJson(rdoc, http.getString());
    rowId = rdoc[0]["id"].as<long>();
    Serial.printf("[Supabase] Inserted row id=%ld\n", rowId);
  } else {
    Serial.printf("[Supabase] Insert failed HTTP %d: %s\n",
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
             "ci_low_mg_dl,ci_high_mg_dl,clarke_zone,"
             "glucose_category,is_ood");
  sbHeaders(http);
  http.addHeader("Accept", "application/json");

  int code = http.GET();
  bool done = false;
  if (code == 200) {
    DynamicJsonDocument doc(512);
    if (!deserializeJson(doc, http.getString()) && doc.size() > 0) {
      if (strcmp(doc[0]["status"] | "pending", "complete") == 0) {
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
  }
  http.end();
  return done;
}

// =============================================================================
// ── WEB SERVER (remote trigger from dashboard) ───────────────────────────────
// =============================================================================

void onStartReading() {
  if (currentStep != STEP_HOME) {
    server.send(409,"application/json",
                "{\"error\":\"Reading already in progress\"}");
    return;
  }
  server.send(200,"application/json",
              "{\"status\":\"started\"}");
  currentStep = STEP_PH;
  stepReady   = true;
}

void onStatus() {
  StaticJsonDocument<200> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["step"]         = (int)currentStep;
  doc["row_id"]       = currentRowId;
  doc["wifi"]         = (WiFi.status() == WL_CONNECTED);
  doc["ip"]           = WiFi.localIP().toString();
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
      // Wait for release to avoid double-fire
      while (digitalRead(BUTTON_PIN) == LOW) delay(10);
      delay(50);
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
  Serial.println("\n=== ESP32-S3 Glucose Monitor ===");

  // Display
  pinMode(TFT_BLK, OUTPUT); digitalWrite(TFT_BLK, HIGH);
  tft.init(240, 240); tft.setRotation(0); tftClear();

  chrome("Initialising...", 0x000F, C_CYAN);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 48); tft.print("Starting...");

  // Button
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  // I2C + Sensors
  Wire.begin(I2C_SDA, I2C_SCL);

  tft.setCursor(10, 62); tft.print("MAX30102...");
  if (!ppg.begin()) {
    screenError("MAX30102 not found", "Check I2C wiring GPIO8/9");
    while (true) delay(1000);
  }
  ppg.setup();
  ppg.setPulseAmplitudeRed(0x0A);
  ppg.setPulseAmplitudeIR(0x1F);
  tft.setTextColor(C_GREEN); tft.setCursor(10, 62); tft.print("[OK] MAX30102");

  tft.setTextColor(C_GREY); tft.setCursor(10, 76); tft.print("TMP117...");
  if (!tmp.begin()) {
    screenError("TMP117 not found", "Check I2C wiring GPIO8/9");
    while (true) delay(1000);
  }
  tft.setTextColor(C_GREEN); tft.setCursor(10, 76); tft.print("[OK] TMP117");

  tft.setTextColor(C_GREY); tft.setCursor(10, 90); tft.print("Connecting WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t++ < 24) { delay(500); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_GREEN); tft.setCursor(10, 90); tft.print("[OK] WiFi");
    tft.setTextColor(C_CYAN);  tft.setCursor(10, 104); tft.print(WiFi.localIP().toString().c_str());
    server.on("/start_reading", HTTP_POST, onStartReading);
    server.on("/status",        HTTP_GET,  onStatus);
    server.begin();
    Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    tft.setTextColor(C_ORANGE); tft.setCursor(10, 90); tft.print("[!] WiFi failed");
  }

  delay(1200);
  screenHome();
}

// =============================================================================
// ── LOOP — STATE MACHINE ─────────────────────────────────────────────────────
// =============================================================================

void loop() {
  server.handleClient();

  bool btn = buttonPressed();
  if (btn) Serial.printf("[BTN] step=%d\n", currentStep);

  switch (currentStep) {

    // ── HOME: wait for button to begin ────────────────────────────────────
    case STEP_HOME:
      if (btn) {
        currentStep = STEP_PH;
        screenPHReady();
      }
      break;

    // ── STEP 1: show pH ready screen; button → collect ────────────────────
    case STEP_PH:
      if (btn) {
        g_ph = collectPH();
        screenPHDone(g_ph);
        currentStep = STEP_TEMP;
      }
      break;

    // ── STEP 2: show Temp ready screen; button → collect ──────────────────
    case STEP_TEMP:
      if (btn) {
        g_temp = collectTemp();
        screenTempDone(g_temp);
        currentStep = STEP_PPG;
        // Show the PPG ready screen immediately after Temp done screen
        // (user needs to re-position finger)
        // We wait for another button press on the Temp done screen
        // before showing PPG ready — handled by falling into STEP_PPG below
      }
      break;

    // ── STEP 3: show PPG ready screen; button → collect ───────────────────
    case STEP_PPG:
      if (btn) {
        // First press on "Temp done → PPG" screen triggers PPG ready
        // But if we just transitioned, the "Temp done" screen IS the ready
        // screen for PPG — so this single button press starts collection.
        screenPPGReady();
        // Wait for next button press to start actual collection
        // Re-enter loop; next press handled here
        currentStep = STEP_PPG;   // stay here
        // We need a sub-state: show ready, then collect on next press
        // Use a local flag via nested wait
        bool collected = false;
        while (!collected) {
          server.handleClient();
          if (buttonPressed()) {
            // Start 5-second collection
            tftClear();
            chrome("Step 3: PPG/HR", 0x2800, C_YELLOW);
            tft.setTextColor(C_YELLOW); tft.setTextSize(1);
            tft.setCursor(10, 42); tft.print("Collecting — KEEP STILL!");
            screenPPGProgress(0);
            stepBar(3);

            if (!collectPPG()) {
              screenError("PPG failed", "Keep finger still & retry");
              currentStep = STEP_PPG;
              screenPPGReady();
              break;
            }
            screenPPGDone(g_dc, g_ac, g_hr, g_pi, g_pw);
            currentStep = STEP_UPLOAD;
            collected = true;
          }
        }
      }
      break;

    // ── STEP 4: button → upload, then poll for result ─────────────────────
    case STEP_UPLOAD:
      if (btn) {
        screenUploading();

        if (WiFi.status() != WL_CONNECTED) {
          screenError("No WiFi", "Reconnecting...");
          WiFi.reconnect();
          delay(3000);
          if (WiFi.status() != WL_CONNECTED) {
            currentStep = STEP_HOME;
            screenHome();
            break;
          }
        }

        long rowId = insertPending();
        if (rowId < 0) {
          screenError("Upload failed", "Check WiFi & Supabase");
          currentStep = STEP_HOME;
          delay(3000);
          screenHome();
          break;
        }
        currentRowId = rowId;

        // Show waiting screen and poll
        screenWaiting(rowId, 0);

        char name[64]="", zone[32]="Zone A", cat[32]="Normal";
        float bgl=0, ciLo=0, ciHi=0;
        bool ood=false;
        bool got=false;
        unsigned long t0 = millis();

        while ((millis()-t0) < POLL_TIMEOUT_MS) {
          delay(POLL_INTERVAL_MS);
          int elapsed = (millis()-t0)/1000;
          updateWaitingTimer(elapsed);
          if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(1000); continue; }
          server.handleClient();
          got = pollResult(rowId, name,sizeof(name),
                           bgl, ciLo, ciHi,
                           zone,sizeof(zone),
                           cat, sizeof(cat), ood);
          if (got) break;
        }

        if (got) {
          screenResults(name, bgl, ciLo, ciHi, zone, cat, ood);
          delay(30000);
        } else {
          screenError("Timeout: no result", "Dashboard not submitted in 5min");
          delay(3000);
        }

        // Reset for next reading
        currentStep = STEP_HOME;
        g_ph=0; g_temp=0; g_dc=0; g_ac=0; g_hr=0; g_pi=0; g_pw=0;
        screenHome();
      }
      break;
  }

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  delay(20);
}
