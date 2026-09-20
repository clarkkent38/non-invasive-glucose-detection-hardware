/*
 * ============================================================================
 * Non-Invasive Glucose Prediction System — ESP32-S3 Interactive Sensor Node
 * ============================================================================
 *
 * COMPLETE STEP-BY-STEP FLOW:
 *   1. Press tactile button (or dashboard triggers remotely)
 *   2. ST7789 shows step-by-step sensor collection progress
 *   3. Sensors collected → row inserted to Supabase with status='pending'
 *   4. Display shows "Open dashboard — enter your details"
 *   5. User opens Streamlit dashboard, fills in name/age/etc, clicks Submit
 *   6. Dashboard runs prediction, patches row to status='complete'
 *   7. ESP32 polls Supabase every 5s for status='complete' on its row
 *   8. When complete: display shows patient name, glucose, Clarke zone, category
 *   9. Returns to home screen after 30s, ready for next reading
 *
 * WIRING:
 *   ┌─ I2C ────────────────────┬─ SPI Display ──────────┬─ Analog/Digital ─┐
 *   │ MAX30102  SDA → GPIO8    │ ST7789  VCC → 3.3V     │ pH probe → GPIO4 │
 *   │           SCL → GPIO9    │         GND → GND      │ Button   → GPIO0 │
 *   │ TMP117    SDA → GPIO8    │         SCL → GPIO18   │                  │
 *   │           SCL → GPIO9    │         SDA → GPIO23   │                  │
 *   └──────────────────────────┤         RES → GPIO2    │                  │
 *                              │         DC  → GPIO15   │                  │
 *                              │         BLK → GPIO21   │                  │
 *                              └────────────────────────┴──────────────────┘
 *
 * REQUIRED LIBRARIES (Arduino Library Manager):
 *   Adafruit ST7789 · Adafruit GFX · SparkFun MAX3010x · SparkFun TMP117
 *   ArduinoJson >= 6.x  (WiFi / HTTPClient / WebServer built into ESP32 core)
 * ============================================================================
 */

// ── User Configuration ───────────────────────────────────────────────────────
#define WIFI_SSID         "YOUR_WIFI_SSID"      // ← your network name
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"  // ← your password

#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID         "esp32_node_01"

// ── Pin Configuration ────────────────────────────────────────────────────────
#define I2C_SDA   8
#define I2C_SCL   9
#define TFT_SCL   18
#define TFT_SDA   23
#define TFT_RES   2
#define TFT_DC    15
#define TFT_BLK   21
#define PH_ADC_PIN 4
#define BUTTON_PIN 0

// ── Sensor Parameters ────────────────────────────────────────────────────────
#define PH_SLOPE           -0.0017f
#define PH_INTERCEPT        14.0f
#define PPG_SAMPLE_MS       5000
#define PPG_RATE_HZ         100
#define PPG_SAMPLES_NEEDED  (PPG_RATE_HZ * PPG_SAMPLE_MS / 1000)   // 500
#define POLL_INTERVAL_MS    5000    // how often to check Supabase for 'complete'
#define POLL_TIMEOUT_MS     300000  // 5 minutes max wait for user details
#define WEB_SERVER_PORT     80

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

// ── Hardware Objects ─────────────────────────────────────────────────────────
MAX30105          ppgSensor;
TMP117            tmpSensor;
Adafruit_ST7789   tft = Adafruit_ST7789(TFT_DC, TFT_RES, TFT_SDA, TFT_SCL);
WebServer         server(WEB_SERVER_PORT);

// ── Display Colour Palette ───────────────────────────────────────────────────
#define C_BG       0x0000   // black
#define C_WHITE    0xFFFF
#define C_CYAN     0x07FF
#define C_YELLOW   0xFFE0
#define C_GREEN    0x07E0
#define C_RED      0xF800
#define C_ORANGE   0xFD20
#define C_BLUE     0x001F
#define C_LBLUE    0x867F   // light blue / progress bar
#define C_GREY     0x7BEF
#define C_DGREY    0x39E7   // dark grey border

// ── Sensor Data ──────────────────────────────────────────────────────────────
struct SensorData {
  float saliva_ph          = 0;
  float hr_bpm             = 0;
  float ppg_raw_dc_baseline= 0;
  float ppg_raw_ac_p2p     = 0;
  float temperature_c      = 0;
  float perfusion_index    = 0;
  float pulse_width_ms     = 0;
  bool  valid              = false;
};

// ── Global State ─────────────────────────────────────────────────────────────
SensorData  latest;
bool        readingInProgress = false;
bool        triggerReading    = false;
long        currentRowId      = -1;   // Supabase row id of the current reading
unsigned long lastButtonTime  = 0;

// =============================================================================
// ── DISPLAY HELPERS ──────────────────────────────────────────────────────────
// =============================================================================

void tftClear() { tft.fillScreen(C_BG); }

// Outer double-border with title bar
void drawChrome(const char* title, uint16_t titleBg, uint16_t titleFg) {
  // outer border
  tft.drawRect(0, 0, 240, 240, C_WHITE);
  tft.drawRect(1, 1, 238, 238, C_DGREY);
  // title bar
  tft.fillRect(2, 2, 236, 28, titleBg);
  tft.drawRect(2, 2, 236, 28, C_WHITE);
  tft.setTextColor(titleFg);
  tft.setTextSize(2);
  int tw = strlen(title) * 12;
  tft.setCursor((240 - tw) / 2, 9);
  tft.print(title);
}

// Horizontal divider line
void hLine(int y) { tft.drawFastHLine(4, y, 232, C_DGREY); }

// Key-value row
void kv(int y, const char* key, const char* val, uint16_t valColor = C_GREEN) {
  tft.setTextSize(1);
  tft.setTextColor(C_GREY);
  tft.setCursor(10, y);
  tft.print(key);
  tft.setTextColor(valColor);
  tft.setCursor(130, y);
  tft.print(val);
}

// Progress bar (x,y,w,h, 0-100 percent, colour)
void progressBar(int x, int y, int w, int h, int pct, uint16_t col) {
  tft.drawRect(x, y, w, h, C_GREY);
  int fill = (w - 2) * pct / 100;
  if (fill > 0) tft.fillRect(x + 1, y + 1, fill, h - 2, col);
}

// =============================================================================
// ── SCREENS ──────────────────────────────────────────────────────────────────
// =============================================================================

void screenHome() {
  tftClear();
  drawChrome("  Glucose Monitor", 0x000F /*dark navy*/, C_CYAN);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42);  tft.print("Device : "); tft.setTextColor(C_CYAN);  tft.print(DEVICE_ID);
  tft.setTextColor(C_WHITE); tft.setCursor(10, 56);  tft.print("Network: ");
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_GREEN); tft.print("Connected");
    tft.setTextColor(C_GREY);  tft.setCursor(10, 70); tft.print(WiFi.localIP().toString().c_str());
  } else {
    tft.setTextColor(C_RED); tft.print("No WiFi");
  }

  hLine(82);

  // Big "Press button" prompt
  tft.setTextColor(C_YELLOW); tft.setTextSize(2);
  tft.setCursor(18, 98);  tft.print("Press Button");
  tft.setCursor(28, 118); tft.print("to Start Test");

  hLine(140);

  tft.setTextSize(1); tft.setTextColor(C_GREY);
  tft.setCursor(10, 150); tft.print("Or trigger from dashboard:");
  tft.setTextColor(C_CYAN);
  // Show IP for dashboard remote trigger
  String ip = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : "---";
  tft.setCursor(10, 163); tft.printf("http://%s/start_reading", ip.c_str());

  hLine(178);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 186); tft.print("Status: Ready");
  tft.setCursor(10, 200); tft.print("Last row ID: ");
  tft.setTextColor(C_CYAN);
  if (currentRowId > 0) tft.print(currentRowId);
  else tft.print("None");
}

void screenStep(const char* sensor, int step, int total, bool done = false) {
  if (step == 1) {
    tftClear();
    drawChrome("Collecting Sensors", 0x0010 /*dark blue*/, C_CYAN);
  }

  // Progress bar at top
  int pct = done ? (step * 100 / total) : ((step - 1) * 100 / total);
  tft.fillRect(4, 32, 232, 10, C_BG);
  progressBar(4, 32, 232, 10, pct, C_LBLUE);

  // Step counter
  tft.fillRect(4, 44, 232, 12, C_BG);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 46);
  tft.printf("Step %d of %d", step, total);

  // Current sensor row (offset by step so they stack)
  int rowY = 62 + (step - 1) * 20;
  tft.fillRect(4, rowY - 2, 232, 18, C_BG);
  if (done) {
    tft.setTextColor(C_GREEN);
    tft.setCursor(10, rowY); tft.print("[OK] ");
  } else {
    tft.setTextColor(C_YELLOW);
    tft.setCursor(10, rowY); tft.print("[..] ");
  }
  tft.setTextColor(C_WHITE); tft.print(sensor);
}

void screenWaitingUser(long rowId) {
  tftClear();
  drawChrome(" Awaiting Details", 0x3000 /*dark amber*/, C_YELLOW);

  hLine(34);

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 42); tft.print("Sensors: ");
  tft.setTextColor(C_GREEN); tft.print("Collected & uploaded");

  tft.setTextColor(C_GREY); tft.setCursor(10, 56); tft.printf("Row ID: %ld", rowId);

  hLine(68);

  // Big instruction
  tft.setTextColor(C_YELLOW); tft.setTextSize(2);
  tft.setCursor(14, 80); tft.print("Open Dashboard");

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 104); tft.print("Enter your details:");
  tft.setTextColor(C_CYAN); tft.setTextSize(1);
  tft.setCursor(14, 118); tft.print(" Name, Age, BMI, Diagnosis");
  tft.setCursor(14, 132); tft.print(" Fasting state, Medications");

  tft.setTextColor(C_WHITE); tft.setCursor(10, 148); tft.print("Then press");
  tft.setTextColor(C_GREEN);  tft.print("  Submit");

  hLine(162);

  // Spinner / waiting indicator
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 170); tft.print("Waiting for prediction...");

  // WiFi IP for easy access
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_DGREY);
    tft.setCursor(10, 186);
    tft.printf("IP: %s", WiFi.localIP().toString().c_str());
  }

  tft.setTextColor(C_GREY);
  tft.setCursor(10, 200); tft.print("Polling Supabase every 5s");
}

void screenPolling(long rowId, int secondsElapsed) {
  // Only update the dynamic parts (avoid full redraw flicker)
  // Refresh the "seconds waiting" and pulse dots
  tft.fillRect(4, 194, 232, 18, C_BG);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 196);
  tft.printf("Waiting %ds... (max 300s)", secondsElapsed);

  // Animated dot indicator
  static int dot = 0;
  dot = (dot + 1) % 4;
  tft.fillRect(4, 210, 232, 14, C_BG);
  tft.setTextColor(C_LBLUE); tft.setCursor(10, 212);
  for (int i = 0; i < 4; i++) tft.print(i < dot ? ">" : " ");
}

void screenError(const char* line1, const char* line2 = "") {
  tftClear();
  drawChrome("   !! ERROR !!", C_RED, C_WHITE);

  tft.setTextColor(C_RED); tft.setTextSize(2);
  tft.setCursor(20, 80);  tft.print("Reading");
  tft.setCursor(20, 100); tft.print("Failed");

  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.setCursor(10, 140); tft.print(line1);
  if (strlen(line2) > 0) { tft.setCursor(10, 156); tft.print(line2); }

  tft.setTextColor(C_GREY);
  tft.setCursor(10, 188); tft.print("Press button to retry");
  delay(3000);
}

// Final results screen — shown after prediction comes back
void screenResults(const char* patientName,
                   float bgl, float ciLow, float ciHigh,
                   const char* zone, const char* category,
                   bool isOod) {
  tftClear();
  drawChrome("   Results", 0x0410 /*dark green*/, C_GREEN);

  // Patient name
  hLine(34);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 38); tft.print("Patient: ");
  tft.setTextColor(C_WHITE); tft.setTextSize(1);
  tft.print(patientName[0] ? patientName : "—");
  hLine(50);

  // Big glucose reading
  uint16_t bglColor = C_GREEN;
  if (bgl < 70 || bgl >= 180) bglColor = C_RED;
  else if (bgl >= 126)         bglColor = C_ORANGE;
  else if (bgl >= 100)         bglColor = C_YELLOW;

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 58); tft.print("Blood Glucose (predicted):");

  tft.setTextColor(bglColor); tft.setTextSize(3);
  char bglStr[16];
  snprintf(bglStr, sizeof(bglStr), "%.1f", bgl);
  int bglW = strlen(bglStr) * 18;
  tft.setCursor((240 - bglW - 36) / 2, 72);
  tft.print(bglStr);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.print(" mg/dL");

  // Confidence interval
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 106);
  tft.printf("90%% CI: %.1f – %.1f mg/dL", ciLow, ciHigh);

  hLine(118);

  // Clarke zone badge
  uint16_t zoneCol = C_GREEN;
  if (zone[5] == 'B') zoneCol = C_YELLOW;
  else if (zone[5] == 'C' || zone[5] == 'D' || zone[5] == 'E') zoneCol = C_ORANGE;

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 126); tft.print("Clarke Zone:");
  tft.setTextColor(zoneCol); tft.setTextSize(2);
  tft.setCursor(110, 122); tft.print(zone);

  hLine(146);

  // Category
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 152); tft.print("Category:");
  tft.setTextColor(bglColor); tft.setCursor(80, 152); tft.print(category);

  // OOD warning
  if (isOod) {
    tft.setTextColor(C_ORANGE);
    tft.setCursor(10, 166); tft.print("! Input outside training range");
  }

  hLine(178);

  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 184); tft.print("Screen clears in 30s");
  tft.setCursor(10, 198); tft.print("Row ID: "); tft.setTextColor(C_CYAN); tft.print(currentRowId);

  // Research disclaimer
  tft.setTextColor(0x4A49);  // very dim grey
  tft.setCursor(10, 214); tft.print("Research only. Not clinical.");
}

// =============================================================================
// ── SENSOR READING ───────────────────────────────────────────────────────────
// =============================================================================

bool initSensors() {
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!ppgSensor.begin()) { Serial.println("ERR: MAX30102 not found"); return false; }
  ppgSensor.setup();
  ppgSensor.setPulseAmplitudeRed(0x0A);
  ppgSensor.setPulseAmplitudeIR(0x1F);
  if (!tmpSensor.begin()) { Serial.println("ERR: TMP117 not found"); return false; }
  Serial.println("Sensors OK");
  return true;
}

float readPH() {
  long sum = 0;
  for (int i = 0; i < 16; i++) { sum += analogRead(PH_ADC_PIN); delay(5); }
  int raw = sum / 16;
  return PH_SLOPE * raw + PH_INTERCEPT;
}

float readTemp() {
  return tmpSensor.dataReady() ? tmpSensor.readTempC() : 36.6f;
}

bool readPPG(SensorData& d) {
  while (ppgSensor.available()) { ppgSensor.getIR(); ppgSensor.getRed(); }
  uint32_t buf[PPG_SAMPLES_NEEDED];
  int n = 0;
  unsigned long t0 = millis();
  while (n < PPG_SAMPLES_NEEDED && (millis() - t0) < PPG_SAMPLE_MS + 1000) {
    if (ppgSensor.available()) {
      buf[n++] = ppgSensor.getIR();
      ppgSensor.getRed();
    }
    delay(9);
  }
  if (n < PPG_SAMPLES_NEEDED / 2) return false;

  uint32_t sum = 0, mn = 0xFFFFFFFF, mx = 0;
  for (int i = 0; i < n; i++) {
    sum += buf[i];
    if (buf[i] < mn) mn = buf[i];
    if (buf[i] > mx) mx = buf[i];
  }
  d.ppg_raw_dc_baseline = (float)sum / n;
  d.ppg_raw_ac_p2p      = (float)(mx - mn);
  d.perfusion_index     = d.ppg_raw_ac_p2p / d.ppg_raw_dc_baseline * 100.0f;

  // Peak-detection heart rate
  int beats = 0; bool wasHigh = false;
  float thresh = d.ppg_raw_dc_baseline + d.ppg_raw_ac_p2p * 0.3f;
  for (int i = 0; i < n; i++) {
    bool high = buf[i] > thresh;
    if (high && !wasHigh) beats++;
    wasHigh = high;
  }
  d.hr_bpm       = beats > 0 ? (beats * 60000.0f / PPG_SAMPLE_MS) : 70.0f;
  d.pulse_width_ms = d.hr_bpm > 0 ? (60000.0f / d.hr_bpm) : 857.0f;
  return true;
}

SensorData collectAll() {
  SensorData d;

  screenStep("pH Probe",           1, 4, false);
  delay(400);
  d.saliva_ph = readPH();
  screenStep("pH Probe",           1, 4, true);
  delay(300);

  screenStep("Temperature Sensor", 2, 4, false);
  delay(400);
  d.temperature_c = readTemp();
  screenStep("Temperature Sensor", 2, 4, true);
  delay(300);

  screenStep("PPG (5 sec...)",     3, 4, false);
  if (!readPPG(d)) {
    screenError("PPG collection failed", "Keep finger on sensor");
    return d;
  }
  screenStep("PPG Sensor",         3, 4, true);
  delay(300);

  screenStep("Validating...",      4, 4, false);
  delay(600);
  d.valid = (d.saliva_ph > 4.0f && d.saliva_ph < 9.5f &&
             d.temperature_c > 30.0f && d.temperature_c < 45.0f);
  screenStep("Validation",         4, 4, true);
  delay(500);
  return d;
}

// =============================================================================
// ── SUPABASE HELPERS ─────────────────────────────────────────────────────────
// =============================================================================

String supabaseUrl(const char* path) {
  return String("https://") + SUPABASE_PROJECT + ".supabase.co/rest/v1/" + path;
}

void addHeaders(HTTPClient& http) {
  http.addHeader("Content-Type",  "application/json");
  http.addHeader("apikey",         SUPABASE_ANON_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
}

// Insert sensor row with status='pending'; returns the new row id or -1
long insertPendingRow(const SensorData& d) {
  HTTPClient http;
  http.begin(supabaseUrl("readings") + "?select=id");
  addHeaders(http);
  http.addHeader("Prefer", "return=representation");   // so Supabase returns the row

  StaticJsonDocument<512> doc;
  doc["device_id"]            = DEVICE_ID;
  doc["status"]               = "pending";
  doc["saliva_ph"]            = d.saliva_ph;
  doc["hr_bpm"]               = d.hr_bpm;
  doc["ppg_raw_dc_baseline"]  = d.ppg_raw_dc_baseline;
  doc["ppg_raw_ac_p2p"]       = d.ppg_raw_ac_p2p;
  doc["temperature_c"]        = d.temperature_c;
  doc["perfusion_index"]      = d.perfusion_index;
  doc["pulse_width_ms"]       = d.pulse_width_ms;

  String body;
  serializeJson(doc, body);

  int code = http.POST(body);
  long rowId = -1;

  if (code == 201) {
    String resp = http.getString();
    DynamicJsonDocument rdoc(256);
    if (!deserializeJson(rdoc, resp)) {
      // Response is a JSON array [ { "id": N } ]
      rowId = rdoc[0]["id"].as<long>();
    }
    Serial.printf("Inserted row id=%ld status=pending\n", rowId);
  } else {
    Serial.printf("Insert failed HTTP %d: %s\n", code, http.getString().c_str());
  }
  http.end();
  return rowId;
}

// Poll for status='complete' on our row; returns true and fills result if ready
bool pollForResult(long rowId,
                   char* patientName, size_t nameLen,
                   float& bgl, float& ciLow, float& ciHigh,
                   char* zone, size_t zoneLen,
                   char* category, size_t catLen,
                   bool& isOod) {
  HTTPClient http;
  String url = supabaseUrl("readings") +
               "?id=eq." + String(rowId) +
               "&select=status,patient_name,predicted_bgl_mg_dl,"
               "ci_low_mg_dl,ci_high_mg_dl,clarke_zone,"
               "glucose_category,is_ood";
  http.begin(url);
  addHeaders(http);
  http.addHeader("Accept", "application/json");

  int code = http.GET();
  bool done = false;

  if (code == 200) {
    String resp = http.getString();
    DynamicJsonDocument doc(512);
    if (!deserializeJson(doc, resp) && doc.size() > 0) {
      const char* st = doc[0]["status"] | "pending";
      if (strcmp(st, "complete") == 0) {
        strlcpy(patientName, doc[0]["patient_name"] | "", nameLen);
        bgl    = doc[0]["predicted_bgl_mg_dl"] | 0.0f;
        ciLow  = doc[0]["ci_low_mg_dl"]        | (bgl - 20.0f);
        ciHigh = doc[0]["ci_high_mg_dl"]        | (bgl + 20.0f);
        strlcpy(zone,     doc[0]["clarke_zone"]     | "Zone A", zoneLen);
        strlcpy(category, doc[0]["glucose_category"]| "Normal", catLen);
        isOod  = doc[0]["is_ood"] | false;
        done = true;
        Serial.printf("Poll: complete! BGL=%.1f zone=%s\n", bgl, zone);
      }
    }
  } else {
    Serial.printf("Poll HTTP %d\n", code);
  }
  http.end();
  return done;
}

// =============================================================================
// ── WEB SERVER (dashboard remote trigger) ────────────────────────────────────
// =============================================================================

void handleStartReading() {
  if (readingInProgress) {
    server.send(409, "application/json", "{\"error\":\"Reading already in progress\"}");
    return;
  }
  server.send(200, "application/json",
              "{\"status\":\"started\",\"message\":\"Sensor reading initiated\"}");
  triggerReading = true;
}

void handleStatus() {
  StaticJsonDocument<300> doc;
  doc["device_id"]          = DEVICE_ID;
  doc["reading_in_progress"]= readingInProgress;
  doc["wifi_connected"]     = (WiFi.status() == WL_CONNECTED);
  doc["ip_address"]         = WiFi.localIP().toString();
  doc["last_row_id"]        = currentRowId;
  String r; serializeJson(doc, r);
  server.send(200, "application/json", r);
}

// =============================================================================
// ── MAIN READING FLOW ────────────────────────────────────────────────────────
// =============================================================================

void doReading() {
  readingInProgress = true;
  triggerReading    = false;

  Serial.println("=== Starting reading sequence ===");

  // ── Phase 1: collect sensors ──
  SensorData d = collectAll();
  if (!d.valid) {
    screenError("Sensor data invalid", "Check connections & retry");
    readingInProgress = false;
    delay(3000);
    screenHome();
    return;
  }

  // ── Phase 2: upload to Supabase (status=pending) ──
  tft.fillRect(4, 180, 232, 30, C_BG);
  tft.setTextColor(C_YELLOW); tft.setTextSize(1);
  tft.setCursor(10, 182); tft.print("Uploading to cloud...");

  long rowId = insertPendingRow(d);
  if (rowId < 0) {
    screenError("Supabase upload failed", "Check WiFi & credentials");
    readingInProgress = false;
    delay(3000);
    screenHome();
    return;
  }
  currentRowId = rowId;

  // ── Phase 3: show "waiting for user details" screen ──
  delay(500);
  screenWaitingUser(rowId);

  // ── Phase 4: poll until dashboard sets status='complete' ──
  unsigned long pollStart = millis();
  int elapsed = 0;

  char patientName[64] = "";
  float bgl = 0, ciLow = 0, ciHigh = 0;
  char zone[32] = "Zone A";
  char category[32] = "Normal";
  bool isOod = false;
  bool gotResult = false;

  while ((millis() - pollStart) < POLL_TIMEOUT_MS) {
    delay(POLL_INTERVAL_MS);
    elapsed = (millis() - pollStart) / 1000;

    screenPolling(rowId, elapsed);

    if (WiFi.status() != WL_CONNECTED) {
      WiFi.reconnect();
      delay(2000);
      continue;
    }

    gotResult = pollForResult(rowId,
                              patientName, sizeof(patientName),
                              bgl, ciLow, ciHigh,
                              zone, sizeof(zone),
                              category, sizeof(category),
                              isOod);
    if (gotResult) break;
  }

  // ── Phase 5: show results ──
  if (gotResult) {
    Serial.printf("Result: %s  BGL=%.1f  Zone=%s\n", patientName, bgl, zone);
    screenResults(patientName, bgl, ciLow, ciHigh, zone, category, isOod);
    delay(30000);  // show results for 30 seconds
  } else {
    screenError("Timeout: no response", "User did not submit in 5min");
    delay(4000);
  }

  readingInProgress = false;
  screenHome();
  Serial.println("=== Reading cycle complete ===");
}

// =============================================================================
// ── SETUP ────────────────────────────────────────────────────────────────────
// =============================================================================
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== ESP32-S3 Glucose Monitor ===");

  // Display first so user sees feedback immediately
  pinMode(TFT_BLK, OUTPUT);
  digitalWrite(TFT_BLK, HIGH);
  tft.init(240, 240);
  tft.setRotation(0);
  tftClear();

  // Startup screen
  drawChrome("Initialising...", 0x000F, C_CYAN);
  tft.setTextColor(C_GREY); tft.setTextSize(1);
  tft.setCursor(10, 48); tft.print("Starting sensors...");

  // Button
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  // Sensors
  if (!initSensors()) {
    screenError("Sensor init failed", "Check I2C wiring");
    while (true) delay(1000);
  }
  tft.setTextColor(C_GREEN); tft.setCursor(10, 62); tft.print("[OK] Sensors ready");

  // WiFi
  tft.setTextColor(C_GREY); tft.setCursor(10, 78); tft.print("Connecting WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 24) { delay(500); Serial.print("."); }

  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(C_GREEN); tft.setCursor(10, 92);  tft.print("[OK] WiFi connected");
    tft.setTextColor(C_CYAN);  tft.setCursor(10, 106); tft.print(WiFi.localIP().toString().c_str());
    Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());

    // Web server
    server.on("/start_reading", HTTP_POST, handleStartReading);
    server.on("/status",        HTTP_GET,  handleStatus);
    server.begin();
    Serial.println("Web server started");
    tft.setTextColor(C_GREEN); tft.setCursor(10, 120); tft.print("[OK] Web server ready");
  } else {
    tft.setTextColor(C_ORANGE); tft.setCursor(10, 92); tft.print("[!] WiFi failed — offline");
    Serial.println("\nWiFi failed");
  }

  delay(1500);
  screenHome();
  Serial.println("Setup complete — waiting for button press");
}

// =============================================================================
// ── LOOP ─────────────────────────────────────────────────────────────────────
// =============================================================================
void loop() {
  server.handleClient();

  // Debounced button check
  if (!readingInProgress) {
    bool btnDown = (digitalRead(BUTTON_PIN) == LOW);
    if (btnDown && (millis() - lastButtonTime > 300)) {
      lastButtonTime = millis();
      triggerReading = true;
      Serial.println("Button pressed");
    }
  }

  if (triggerReading && !readingInProgress) {
    doReading();
  }

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
  }

  delay(50);
}
