/*
 * ============================================================================
 * DEMO: Random Sensor Values — No Hardware Sensors Required
 * ============================================================================
 *
 * PURPOSE:
 *   Demonstrates the complete button-driven flow and Supabase upload
 *   without any sensors connected.  Randomized realistic values are
 *   generated for each sensor step.  Great for showing how the system
 *   works end-to-end before wiring up real hardware.
 *
 * FLOW (identical to real firmware):
 *   Press 1 → "pH Step" ready screen
 *   Press 2 → Simulates pH collection (3 sec) → shows value
 *   Press 3 → Simulates Temperature collection → shows value
 *   Press 4 → Simulates PPG / HR collection (5 sec) → shows values
 *   Press 5 → Packs JSON → uploads to Supabase (status=pending)
 *   → Dashboard receives row → user enters details → Submit
 *   → ESP32 polls → shows prediction result on Serial Monitor
 *
 * LIBRARIES NEEDED (all you already have):
 *   ✅ WiFi          — built into ESP32 Arduino core
 *   ✅ HTTPClient    — built into ESP32 Arduino core
 *   ✅ WebServer     — built into ESP32 Arduino core
 *   ✅ ArduinoJson   — install from Library Manager: "ArduinoJson" by Benoit Blanchon
 *   (NO sensor libraries needed for this demo)
 *
 * WIRING (minimal — only button):
 *   Tactile button → GPIO14  + GND   (internal pull-up used)
 *   Nothing else needed
 *
 * FILL IN your WiFi credentials below before flashing.
 * ============================================================================
 */

// ── Config ───────────────────────────────────────────────────────────────────
#define WIFI_SSID         "YOUR_WIFI_SSID"      // ← fill in
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"  // ← fill in
#define SUPABASE_PROJECT  "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID         "esp32_demo_01"

#define BUTTON_PIN   14    // GPIO14 — safe general input (NOT GPIO0 = BOOT)
#define DEBOUNCE_MS 250
#define POLL_INTERVAL_MS 5000
#define POLL_TIMEOUT_MS  300000   // 5 min

// ── Libraries ────────────────────────────────────────────────────────────────
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ArduinoJson.h>

WebServer server(80);

// ── State ────────────────────────────────────────────────────────────────────
enum Step { HOME=0, PH_READY, PH_DONE, TEMP_READY, TEMP_DONE,
            PPG_READY, PPG_DONE, UPLOAD };

Step          step       = HOME;
unsigned long lastBtn    = 0;
long          rowId      = -1;

// Simulated values
float s_ph, s_temp, s_dc, s_ac, s_hr, s_pi, s_pw;

// ── Serial helpers ───────────────────────────────────────────────────────────
void div(char c = '-') { for(int i=0;i<44;i++) Serial.print(c); Serial.println(); }

// ── Realistic random value generators ────────────────────────────────────────
// Ranges match what a real healthy adult produces on these sensors.

float randPH() {
  // Normal saliva pH 6.2–7.6; slightly wider for demo variety
  return 6.0f + (float)random(0, 200) / 100.0f;   // 6.00 – 8.00
}

float randTemp() {
  // Skin surface temp 35.5 – 37.5 °C
  return 35.5f + (float)random(0, 200) / 100.0f;
}

float randHR() {
  // Resting HR 55–95 BPM
  return 55.0f + (float)random(0, 400) / 10.0f;
}

void generatePPG(float hr) {
  // DC baseline: typical MAX30102 IR values ~140k–200k ADC counts
  s_dc = 140000.0f + (float)random(0, 60000);
  // AC amplitude: ~0.5–2% of DC for good perfusion
  s_pi = 0.5f + (float)random(0, 150) / 100.0f;  // 0.50–2.00 %
  s_ac = s_dc * s_pi / 100.0f;
  s_pw = 60000.0f / hr;
}

// ── Button ───────────────────────────────────────────────────────────────────
bool btnPressed() {
  if (digitalRead(BUTTON_PIN) == LOW && (millis() - lastBtn) > DEBOUNCE_MS) {
    lastBtn = millis();
    while (digitalRead(BUTTON_PIN) == LOW) delay(10);
    delay(50);
    Serial.printf("[BTN] Pressed at step %d\n", (int)step);
    return true;
  }
  return false;
}

// ── Supabase ─────────────────────────────────────────────────────────────────
void sbHeaders(HTTPClient& h) {
  h.addHeader("Content-Type",  "application/json");
  h.addHeader("apikey",         SUPABASE_ANON_KEY);
  h.addHeader("Authorization", String("Bearer ") + SUPABASE_ANON_KEY);
}

long uploadPending() {
  HTTPClient http;
  http.begin(String("https://") + SUPABASE_PROJECT +
             ".supabase.co/rest/v1/readings?select=id");
  sbHeaders(http);
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

  String body; serializeJson(doc, body);

  div('=');
  Serial.println("UPLOADING TO SUPABASE");
  div();
  Serial.println("Payload JSON:");
  Serial.println(body);

  int code = http.POST(body);
  long id = -1;
  if (code == 201) {
    DynamicJsonDocument r(256);
    deserializeJson(r, http.getString());
    id = r[0]["id"].as<long>();
    Serial.printf("Upload SUCCESS — row id = %ld\n", id);
  } else {
    Serial.printf("Upload FAILED — HTTP %d\n%s\n", code, http.getString().c_str());
  }
  http.end();
  div('=');
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
  sbHeaders(http);
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
        strlcpy(zone, doc[0]["clarke_zone"]      | "Zone A", zLen);
        strlcpy(cat,  doc[0]["glucose_category"] | "Normal", cLen);
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

// ── Web server (remote trigger) ───────────────────────────────────────────────
void onStart() {
  if (step != HOME) {
    server.send(409,"application/json","{\"error\":\"busy\"}");
    return;
  }
  server.send(200,"application/json","{\"status\":\"started\"}");
  Serial.println("[Web] Remote trigger received");
  step = PH_READY;
  printPHReady();
}
void onStatus() {
  StaticJsonDocument<128> d;
  d["step"] = (int)step; d["row"] = rowId;
  d["ip"] = WiFi.localIP().toString();
  String r; serializeJson(d,r);
  server.send(200,"application/json",r);
}

// ── Forward declarations (needed because onStart() calls printPHReady()) ──────
void printHome();
void printPHReady();

// ── Screen print functions (Serial Monitor) ───────────────────────────────────
void printHome() {
  div('=');
  Serial.println("  DEMO — Glucose Monitor");
  div();
  Serial.printf("  Device : %s\n", DEVICE_ID);
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("  WiFi   : %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("  WiFi   : not connected");
  div();
  Serial.println("  [DEMO MODE — No sensors required]");
  Serial.println("  Values are randomly generated in realistic ranges.");
  div();
  Serial.println("  >>> PRESS BUTTON to begin <<<");
  div('=');
}

void printPHReady() {
  div('=');
  Serial.println("  STEP 1 — SALIVA pH");
  div();
  Serial.println("  [DEMO] Pretend to place pH probe in saliva");
  Serial.println("  >>> PRESS BUTTON to simulate pH collection <<<");
  div('=');
}

void printPHDone() {
  Serial.printf("\n  Simulating 32 ADC samples");
  for (int i = 0; i < 8; i++) { delay(200); Serial.print("."); }
  Serial.println();
  div();
  Serial.printf("  [pH] Simulated result: %.3f pH\n", s_ph);
  if      (s_ph < 6.2f) Serial.println("  Interpretation: Acidic");
  else if (s_ph < 7.4f) Serial.println("  Interpretation: Normal (6.2–7.4)");
  else                   Serial.println("  Interpretation: Slightly Alkaline");
  div();
  Serial.println("  >>> PRESS BUTTON for Temperature <<<");
}

void printTempReady() {
  div('=');
  Serial.println("  STEP 2 — TEMPERATURE");
  div();
  Serial.println("  [DEMO] Pretend to place TMP117 on skin");
  Serial.println("  >>> PRESS BUTTON to simulate temp collection <<<");
  div('=');
}

void printTempDone() {
  Serial.printf("\n  Simulating 10 temperature readings");
  for (int i = 0; i < 10; i++) {
    float v = s_temp + ((float)random(-10,10)/100.0f);
    delay(150);
    Serial.printf("\n    Reading %d: %.3f C", i+1, v);
  }
  Serial.println();
  div();
  Serial.printf("  [Temp] Trimmed mean result: %.3f C\n", s_temp);
  Serial.println("  Status: Normal range (35.5–37.5 C)");
  div();
  Serial.println("  >>> PRESS BUTTON for PPG / Heart Rate <<<");
}

void printPPGReady() {
  div('=');
  Serial.println("  STEP 3 — PPG / HEART RATE");
  div();
  Serial.println("  [DEMO] Pretend to place finger on MAX30102");
  Serial.println("  >>> PRESS BUTTON to simulate 5-sec PPG collection <<<");
  div('=');
}

void printPPGDone() {
  Serial.println("\n  Simulating 5-second PPG collection...");
  for (int p = 0; p <= 100; p += 10) {
    Serial.printf("  Progress: %3d%%\r", p);
    delay(500);
  }
  Serial.println();
  div();
  Serial.printf("  [PPG] Heart Rate    : %.1f BPM\n",  s_hr);
  Serial.printf("  [PPG] Perfusion Idx : %.3f %%\n",   s_pi);
  Serial.printf("  [PPG] PPG DC        : %.0f ADC\n",  s_dc);
  Serial.printf("  [PPG] PPG AC        : %.1f ADC\n",  s_ac);
  Serial.printf("  [PPG] Pulse Width   : %.1f ms\n",   s_pw);
  div();
  Serial.println("  All 3 sensors simulated!");
  Serial.println("  >>> PRESS BUTTON to PACK JSON and UPLOAD <<<");
}

void printResults(const char* name, float bgl, float ciLo, float ciHi,
                  const char* zone, const char* cat, bool ood) {
  div('=');
  Serial.println("  *** PREDICTION RESULT ***");
  div('=');
  Serial.printf("  Patient       : %s\n", name[0] ? name : "—");
  Serial.printf("  Blood Glucose : %.1f mg/dL\n", bgl);
  Serial.printf("  90%% CI        : %.1f – %.1f mg/dL\n", ciLo, ciHi);
  Serial.printf("  Clarke Zone   : %s\n", zone);
  Serial.printf("  Category      : %s\n", cat);
  if (ood) Serial.println("  !! Out-of-distribution input flag set");
  div('=');
  Serial.println("  Demo complete. Restarting in 30 seconds...");
}

// =============================================================================
// ── SETUP ────────────────────────────────────────────────────────────────────
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  randomSeed(analogRead(0));   // seed from floating ADC pin

  div('=');
  Serial.println("  ESP32-S3 Glucose Monitor — DEMO MODE");
  div('=');

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
    server.on("/start_reading", HTTP_POST, onStart);
    server.on("/status",        HTTP_GET,  onStatus);
    server.begin();
    Serial.println("  Web server: started");
  } else {
    Serial.println("  WiFi: FAILED — Supabase upload will not work");
  }

  div('=');
  Serial.println("  Setup complete.");
  div('=');
  printHome();
}

// =============================================================================
// ── LOOP ─────────────────────────────────────────────────────────────────────
// =============================================================================
void loop() {
  server.handleClient();
  bool btn = btnPressed();

  switch (step) {

    case HOME:
      if (btn) {
        step = PH_READY;
        printPHReady();
      }
      break;

    case PH_READY:
      if (btn) {
        // Generate random pH value
        s_ph = randPH();
        step = PH_DONE;
        printPHDone();
      }
      break;

    case PH_DONE:
      if (btn) {
        step = TEMP_READY;
        printTempReady();
      }
      break;

    case TEMP_READY:
      if (btn) {
        // Generate random temperature
        s_temp = randTemp();
        step = TEMP_DONE;
        printTempDone();
      }
      break;

    case TEMP_DONE:
      if (btn) {
        step = PPG_READY;
        printPPGReady();
      }
      break;

    case PPG_READY:
      if (btn) {
        // Generate correlated PPG values
        s_hr = randHR();
        generatePPG(s_hr);
        step = PPG_DONE;
        printPPGDone();
      }
      break;

    case PPG_DONE:
      if (btn) {
        step = UPLOAD;

        // ── Upload to Supabase ──────────────────────────────────────────────
        if (WiFi.status() != WL_CONNECTED) {
          Serial.println("ERROR: No WiFi — cannot upload");
          Serial.println("Restarting demo...");
          step = HOME;
          delay(2000);
          printHome();
          break;
        }

        rowId = uploadPending();
        if (rowId < 0) {
          Serial.println("ERROR: Upload failed");
          step = HOME;
          delay(2000);
          printHome();
          break;
        }

        // ── Waiting for dashboard ───────────────────────────────────────────
        div('=');
        Serial.println("  WAITING FOR DASHBOARD");
        div();
        Serial.printf("  Row ID  : %ld\n", rowId);
        Serial.println("  Action  : Open Streamlit dashboard");
        Serial.println("            Select LIVE SENSOR MODE");
        Serial.println("            Fill in patient details");
        Serial.println("            Click Submit");
        div();
        Serial.println("  Polling Supabase every 5s...");
        div('=');

        char name[64]="", zone[32]="Zone A", cat[32]="Normal";
        float bgl=0, ciLo=0, ciHi=0;
        bool ood=false, got=false;
        unsigned long t0 = millis();

        while ((millis()-t0) < POLL_TIMEOUT_MS) {
          delay(POLL_INTERVAL_MS);
          int elapsed = (millis()-t0) / 1000;
          Serial.printf("[Poll] %ds elapsed...\r", elapsed);
          server.handleClient();
          got = pollResult(rowId, name, sizeof(name),
                           bgl, ciLo, ciHi,
                           zone, sizeof(zone), cat, sizeof(cat), ood);
          if (got) break;
        }

        if (got) {
          printResults(name, bgl, ciLo, ciHi, zone, cat, ood);
          delay(30000);
        } else {
          Serial.println("\nTIMEOUT: Dashboard not submitted within 5 minutes");
          delay(3000);
        }

        // Reset for next demo
        step = HOME;
        s_ph=0; s_temp=0; s_dc=0; s_ac=0; s_hr=0; s_pi=0; s_pw=0;
        printHome();
      }
      break;

    // UPLOAD step falls through to HOME after polling completes
    case UPLOAD:
      break;
  }

  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  delay(20);
}
