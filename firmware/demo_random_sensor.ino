/*
 * ============================================================================
 * DEMO: Random Sensor Values — No Hardware Sensors Required
 * ============================================================================
 * Demonstrates the complete button-driven flow with Supabase upload.
 * No sensors needed — random realistic values are generated instead.
 *
 * FLOW:
 *   Press 1 → pH step ready
 *   Press 2 → simulate pH collection → show value in Serial Monitor
 *   Press 3 → Temperature step ready
 *   Press 4 → simulate Temp collection → show value
 *   Press 5 → PPG step ready
 *   Press 6 → simulate 5-sec PPG → show HR, PI, DC, AC values
 *   Press 7 → pack JSON → upload to Supabase (status=pending)
 *          → poll every 5s → show prediction result when dashboard submits
 *
 * WIRING: Only a tactile button on GPIO14 → GND. Nothing else.
 *
 * LIBRARIES NEEDED:
 *   ArduinoJson (by Benoit Blanchon) — install from Library Manager
 *   WiFi / HTTPClient / WebServer    — built into ESP32 Arduino core
 * ============================================================================
 */

// ── Config ───────────────────────────────────────────────────────────────────
#define WIFI_SSID         "YOUR_WIFI_SSID"
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"
#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID         "esp32_demo_01"
#define BUTTON_PIN        14
#define DEBOUNCE_MS       250
#define POLL_INTERVAL_MS  5000
#define POLL_TIMEOUT_MS   300000

// ── Libraries ────────────────────────────────────────────────────────────────
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// ── Globals ───────────────────────────────────────────────────────────────────
WebServer     server(80);
unsigned long lastBtn = 0;
long          rowId   = -1;
float         s_ph, s_temp, s_dc, s_ac, s_hr, s_pi, s_pw;

enum Step { HOME=0, PH_READY, PH_DONE, TEMP_READY, TEMP_DONE,
            PPG_READY, PPG_DONE, UPLOAD };
Step step = HOME;

// =============================================================================
// ── UTILITIES ────────────────────────────────────────────────────────────────
// =============================================================================

void divider(char c = '-') {
  for (int i = 0; i < 44; i++) Serial.print(c);
  Serial.println();
}

bool btnPressed() {
  if (digitalRead(BUTTON_PIN) == LOW && (millis() - lastBtn) > DEBOUNCE_MS) {
    lastBtn = millis();
    while (digitalRead(BUTTON_PIN) == LOW) delay(10);
    delay(50);
    Serial.printf("[BTN] step=%d\n", (int)step);
    return true;
  }
  return false;
}

// =============================================================================
// ── RANDOM GENERATORS (realistic physiological ranges) ───────────────────────
// =============================================================================

float randPH()   { return 6.0f + random(0, 200) / 100.0f; }  // 6.00 – 8.00
float randTemp() { return 35.5f + random(0, 200) / 100.0f; } // 35.5 – 37.5 °C
float randHR()   { return 55.0f + random(0, 400) / 10.0f; }  // 55 – 95 BPM

void genPPG(float hr) {
  s_dc = 140000.0f + random(0, 60000);
  s_pi = 0.5f + random(0, 150) / 100.0f;
  s_ac = s_dc * s_pi / 100.0f;
  s_pw = 60000.0f / hr;
}

// =============================================================================
// ── SERIAL SCREEN FUNCTIONS  (defined BEFORE they are called) ────────────────
// =============================================================================

void screenHome() {
  divider('=');
  Serial.println("  DEMO — Glucose Monitor");
  divider();
  Serial.printf("  Device : %s\n", DEVICE_ID);
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("  WiFi   : %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("  WiFi   : not connected");
  divider();
  Serial.println("  [DEMO MODE — No sensors required]");
  Serial.println("  Random values in realistic physiological ranges.");
  divider();
  Serial.println("  >>> PRESS BUTTON to begin <<<");
  divider('=');
}

void screenPHReady() {
  divider('=');
  Serial.println("  STEP 1 — SALIVA pH");
  divider();
  Serial.println("  [DEMO] Pretend to place pH probe in saliva");
  Serial.println("  >>> PRESS BUTTON to simulate pH collection <<<");
  divider('=');
}

void screenPHDone() {
  Serial.printf("\n  Simulating 32 ADC samples");
  for (int i = 0; i < 8; i++) { delay(200); Serial.print("."); }
  Serial.println();
  divider();
  Serial.printf("  [pH] Result : %.3f pH\n", s_ph);
  if      (s_ph < 6.2f) Serial.println("  Interp : Acidic");
  else if (s_ph < 7.4f) Serial.println("  Interp : Normal (6.2–7.4)");
  else                   Serial.println("  Interp : Slightly Alkaline");
  divider();
  Serial.println("  >>> PRESS BUTTON for Temperature <<<");
}

void screenTempReady() {
  divider('=');
  Serial.println("  STEP 2 — TEMPERATURE");
  divider();
  Serial.println("  [DEMO] Pretend to place TMP117 on skin");
  Serial.println("  >>> PRESS BUTTON to simulate Temp collection <<<");
  divider('=');
}

void screenTempDone() {
  Serial.printf("\n  Simulating 10 temperature readings:\n");
  for (int i = 0; i < 10; i++) {
    float v = s_temp + (random(-10, 10) / 100.0f);
    delay(150);
    Serial.printf("    Reading %2d: %.3f C\n", i + 1, v);
  }
  divider();
  Serial.printf("  [Temp] Trimmed mean : %.3f C\n", s_temp);
  divider();
  Serial.println("  >>> PRESS BUTTON for PPG / Heart Rate <<<");
}

void screenPPGReady() {
  divider('=');
  Serial.println("  STEP 3 — PPG / HEART RATE");
  divider();
  Serial.println("  [DEMO] Pretend to place finger on MAX30102");
  Serial.println("  >>> PRESS BUTTON to simulate 5-sec PPG <<<");
  divider('=');
}

void screenPPGDone() {
  Serial.println("\n  Simulating 5-second PPG collection...");
  for (int p = 0; p <= 100; p += 10) {
    Serial.printf("  Progress: %3d%%\r", p);
    delay(500);
  }
  Serial.println();
  divider();
  Serial.printf("  [PPG] Heart Rate    : %.1f BPM\n", s_hr);
  Serial.printf("  [PPG] Perfusion Idx : %.3f %%\n",  s_pi);
  Serial.printf("  [PPG] PPG DC        : %.0f ADC\n", s_dc);
  Serial.printf("  [PPG] PPG AC        : %.1f ADC\n", s_ac);
  Serial.printf("  [PPG] Pulse Width   : %.1f ms\n",  s_pw);
  divider();
  Serial.println("  All 3 sensors simulated!");
  Serial.println("  >>> PRESS BUTTON to PACK JSON and UPLOAD <<<");
}

void screenResults(const char* name, float bgl, float ciLo, float ciHi,
                   const char* zone, const char* cat, bool ood) {
  divider('=');
  Serial.println("  *** PREDICTION RESULT ***");
  divider('=');
  Serial.printf("  Patient       : %s\n", (name && name[0]) ? name : "—");
  Serial.printf("  Blood Glucose : %.1f mg/dL\n", bgl);
  Serial.printf("  90%% CI        : %.1f – %.1f mg/dL\n", ciLo, ciHi);
  Serial.printf("  Clarke Zone   : %s\n", zone);
  Serial.printf("  Category      : %s\n", cat);
  if (ood) Serial.println("  [!] Out-of-distribution input flag set");
  divider('=');
  Serial.println("  Demo complete. Restarting in 30 seconds...");
}

// =============================================================================
// ── SUPABASE ─────────────────────────────────────────────────────────────────
// =============================================================================

void addHeaders(HTTPClient& h) {
  h.addHeader("Content-Type",  "application/json");
  h.addHeader("apikey",         SUPABASE_ANON_KEY);
  h.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
}

long uploadPending() {
  HTTPClient http;
  http.begin(String("https://") + SUPABASE_PROJECT +
             ".supabase.co/rest/v1/readings?select=id");
  addHeaders(http);
  http.addHeader("Prefer", "return=representation");

  StaticJsonDocument<512> doc;
  doc["device_id"]           = DEVICE_ID;
  doc["status"]              = "pending";
  doc["saliva_ph"]           = s_ph;
  doc["hr_bpm"]              = s_hr;
  doc["ppg_raw_dc_baseline"] = s_dc;
  doc["ppg_raw_ac_p2p"]      = s_ac;
  doc["temperature_c"]       = s_temp;
  doc["perfusion_index"]     = s_pi;
  doc["pulse_width_ms"]      = s_pw;

  String body;
  serializeJson(doc, body);

  divider('=');
  Serial.println("UPLOADING TO SUPABASE...");
  divider();
  Serial.println(body);

  int code = http.POST(body);
  long id = -1;
  if (code == 201) {
    DynamicJsonDocument r(256);
    deserializeJson(r, http.getString());
    id = r[0]["id"].as<long>();
    Serial.printf("SUCCESS — row id = %ld\n", id);
  } else {
    Serial.printf("FAILED — HTTP %d\n%s\n", code, http.getString().c_str());
  }
  http.end();
  divider('=');
  return id;
}

bool pollResult(long rid,
                char* name, size_t nLen,
                float& bgl, float& ciLo, float& ciHi,
                char* zone, size_t zLen,
                char* cat,  size_t cLen,
                bool& ood) {
  HTTPClient http;
  http.begin(String("https://") + SUPABASE_PROJECT +
             ".supabase.co/rest/v1/readings"
             "?id=eq." + String(rid) +
             "&select=status,patient_name,predicted_bgl_mg_dl,"
             "ci_low_mg_dl,ci_high_mg_dl,clarke_zone,glucose_category,is_ood");
  addHeaders(http);
  http.addHeader("Accept", "application/json");

  int code = http.GET();
  bool done = false;
  if (code == 200) {
    DynamicJsonDocument doc(512);
    if (!deserializeJson(doc, http.getString()) && doc.size() > 0) {
      const char* st = doc[0]["status"] | "pending";
      Serial.printf("[Poll] status = %s\n", st);
      if (strcmp(st, "complete") == 0) {
        strlcpy(name, doc[0]["patient_name"] | "", nLen);
        bgl  = doc[0]["predicted_bgl_mg_dl"] | 0.0f;
        ciLo = doc[0]["ci_low_mg_dl"]        | (bgl - 20.0f);
        ciHi = doc[0]["ci_high_mg_dl"]        | (bgl + 20.0f);
        strlcpy(zone, doc[0]["clarke_zone"]       | "Zone A", zLen);
        strlcpy(cat,  doc[0]["glucose_category"]  | "Normal", cLen);
        ood  = doc[0]["is_ood"] | false;
        done = true;
      }
    }
  } else {
    Serial.printf("[Poll] HTTP %d\n", code);
  }
  http.end();
  return done;
}

// =============================================================================
// ── WEB SERVER (remote trigger from dashboard) ───────────────────────────────
// =============================================================================

void handleStart() {
  if (step != HOME) {
    server.send(409, "application/json", "{\"error\":\"busy\"}");
    return;
  }
  server.send(200, "application/json", "{\"status\":\"started\"}");
  Serial.println("[Web] Remote trigger received");
  step = PH_READY;
  screenPHReady();   // safe — defined above
}

void handleStatus() {
  StaticJsonDocument<128> d;
  d["step"] = (int)step;
  d["row"]  = rowId;
  d["ip"]   = WiFi.localIP().toString();
  String r; serializeJson(d, r);
  server.send(200, "application/json", r);
}

// =============================================================================
// ── SETUP ────────────────────────────────────────────────────────────────────
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  randomSeed(analogRead(0));

  divider('=');
  Serial.println("  ESP32-S3 Glucose Monitor — DEMO MODE");
  divider('=');

  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.println("  Button: GPIO14 ready");

  Serial.printf("  WiFi: Connecting to '%s'...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t++ < 24) {
    delay(500); Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("  WiFi: Connected — %s\n", WiFi.localIP().toString().c_str());
    server.on("/start_reading", HTTP_POST, handleStart);
    server.on("/status",        HTTP_GET,  handleStatus);
    server.begin();
    Serial.println("  Web server: started");
  } else {
    Serial.println("  WiFi: FAILED — upload will not work");
  }

  divider('=');
  Serial.println("  Setup complete.");
  divider('=');
  screenHome();
}

// =============================================================================
// ── LOOP — STATE MACHINE ─────────────────────────────────────────────────────
// =============================================================================
void loop() {
  server.handleClient();
  bool btn = btnPressed();

  switch (step) {

    case HOME:
      if (btn) { step = PH_READY; screenPHReady(); }
      break;

    case PH_READY:
      if (btn) {
        s_ph = randPH();
        step = PH_DONE;
        screenPHDone();
      }
      break;

    case PH_DONE:
      if (btn) { step = TEMP_READY; screenTempReady(); }
      break;

    case TEMP_READY:
      if (btn) {
        s_temp = randTemp();
        step = TEMP_DONE;
        screenTempDone();
      }
      break;

    case TEMP_DONE:
      if (btn) { step = PPG_READY; screenPPGReady(); }
      break;

    case PPG_READY:
      if (btn) {
        s_hr = randHR();
        genPPG(s_hr);
        step = PPG_DONE;
        screenPPGDone();
      }
      break;

    case PPG_DONE:
      if (btn) {
        step = UPLOAD;

        if (WiFi.status() != WL_CONNECTED) {
          Serial.println("ERROR: No WiFi");
          step = HOME; delay(2000); screenHome(); break;
        }

        rowId = uploadPending();
        if (rowId < 0) {
          Serial.println("ERROR: Upload failed");
          step = HOME; delay(2000); screenHome(); break;
        }

        divider('=');
        Serial.println("  WAITING FOR DASHBOARD");
        divider();
        Serial.printf("  Row ID  : %ld\n", rowId);
        Serial.println("  1. Open Streamlit dashboard");
        Serial.println("  2. Select LIVE SENSOR MODE");
        Serial.println("  3. Fill in patient details");
        Serial.println("  4. Click Submit");
        divider();
        Serial.println("  Polling Supabase every 5s...");
        divider('=');

        char name[64]="", zone[32]="Zone A", cat[32]="Normal";
        float bgl=0, ciLo=0, ciHi=0;
        bool ood=false, got=false;
        unsigned long t0 = millis();

        while ((millis() - t0) < POLL_TIMEOUT_MS) {
          delay(POLL_INTERVAL_MS);
          int elapsed = (millis() - t0) / 1000;
          Serial.printf("[Poll] %ds elapsed...\r", elapsed);
          server.handleClient();
          got = pollResult(rowId,
                           name, sizeof(name),
                           bgl, ciLo, ciHi,
                           zone, sizeof(zone),
                           cat,  sizeof(cat),
                           ood);
          if (got) break;
        }

        if (got) {
          screenResults(name, bgl, ciLo, ciHi, zone, cat, ood);
          delay(30000);
        } else {
          Serial.println("\nTIMEOUT: Dashboard not submitted");
          delay(3000);
        }

        // Reset for next demo run
        step = HOME;
        s_ph=0; s_temp=0; s_dc=0; s_ac=0; s_hr=0; s_pi=0; s_pw=0;
        screenHome();
      }
      break;

    case UPLOAD:
      break;
  }

  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  delay(20);
}
