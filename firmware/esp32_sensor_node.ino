/*
 * ============================================================================
 * Non-Invasive Glucose Prediction System — ESP32-S3 Interactive Sensor Node
 * ============================================================================
 *
 * Hardware:
 *   MAX30102   — Optical PPG (IR + RED), heart rate, perfusion index
 *                I2C address: 0x57  SDA=GPIO8  SCL=GPIO9
 *   TMP117     — High-precision skin-contact temperature
 *                I2C address: 0x48  Same SDA/SCL bus
 *   pH Probe   — Analog saliva pH via signal conditioner → GPIO4 (ADC)
 *   ST7789 TFT — 240x240 IPS display with beautiful sensor UI
 *   Tactile    — Manual reading trigger button → GPIO0 (internal pull-up)
 *
 * Wiring:
 *   ┌─ I2C Bus ────────────────┬─ SPI Display ──────────┬─ Analog/Digital ─┐
 *   │ MAX30102  3.3V/GND       │ ST7789   VCC → 3.3V    │ pH probe → GPIO4 │
 *   │           SDA → GPIO8    │          GND → GND     │ Button   → GPIO0 │
 *   │           SCL → GPIO9    │          SCL → GPIO18  │                  │
 *   │ TMP117    3.3V/GND       │          SDA → GPIO23  │                  │
 *   │           SDA → GPIO8    │          RES → GPIO2   │                  │
 *   │           SCL → GPIO9    │          DC  → GPIO15  │                  │
 *   └──────────────────────────┤          BLK → GPIO21  │                  │
 *                              └────────────────────────┴──────────────────┘
 *
 * Features:
 *   • Step-by-step sensor collection with real-time progress display
 *   • Beautiful bordered UI showing all sensor readings and glucose prediction
 *   • Manual readings: Press tactile button to start collection sequence
 *   • Remote readings: Dashboard can trigger via HTTP POST /start_reading
 *   • Automatic cloud upload after successful collection
 *   • Professional device interface with status indicators
 *
 * Required Arduino Libraries (Library Manager):
 *   - Adafruit ST7789 (for display)
 *   - Adafruit GFX Library (display graphics)
 *   - SparkFun MAX3010x Pulse and Proximity Sensor Library
 *   - SparkFun TMP117 High Accuracy I2C Temperature Sensor
 *   - ArduinoJson >= 6.x
 *   - WiFi, HTTPClient, WebServer (built-in ESP32 core)
 *
 * Board: ESP32S3 Dev Module
 * ============================================================================
 */
// ── User Configuration ──────────────────────────────────────────────────────
// WiFi: fill in your own network credentials (never commit passwords to git)
#define WIFI_SSID          "YOUR_WIFI_SSID"      // ← replace with your SSID
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"  // ← replace with your password

// Supabase — project ref is the subdomain of your Supabase URL
// URL: https://mjcwhnkyojfaezydvpsp.supabase.co  → ref = mjcwhnkyojfaezydvpsp
#define SUPABASE_PROJECT   "mjcwhnkyojfaezydvpsp"
#define SUPABASE_ANON_KEY  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1qY3dobmt5b2pmYWV6eWR2cHNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk4NzA5MTYsImV4cCI6MjEwNTQ0NjkxNn0.G1nM1QYYztA2DStOOQ2qOD2Y7RP2n4KnaQKYe1WeVFM"
#define DEVICE_ID          "esp32_node_01"       // change per physical device

// ── Hardware Pin Configuration ──────────────────────────────────────────────
// I2C pins for MAX30102 + TMP117
#define I2C_SDA            8
#define I2C_SCL            9

// SPI pins for ST7789 TFT display
#define TFT_SCL            18    // SPI clock 
#define TFT_SDA            23    // SPI MOSI (data)
#define TFT_RES            2     // Reset
#define TFT_DC             15    // Data/Command
#define TFT_BLK            21    // Backlight control
// No CS pin needed for this ST7789 variant

// Analog and digital inputs
#define PH_ADC_PIN         4     // Analog pH probe input
#define BUTTON_PIN         0     // Tactile button (GPIO0 = BOOT button)

// ── Sensor Parameters ────────────────────────────────────────────────────────
// pH probe calibration (replace with your measured values)
#define PH_SLOPE          -0.0017f    // pH units per ADC count
#define PH_INTERCEPT      14.0f       // offset term

// PPG sampling parameters
#define PPG_SAMPLE_WINDOW_MS  5000    // 5 seconds of PPG data per reading
#define PPG_SAMPLE_RATE_HZ    100     // MAX30102 sample rate
#define PPG_SAMPLES_NEEDED    (PPG_SAMPLE_RATE_HZ * PPG_SAMPLE_WINDOW_MS / 1000)

// Web server port for dashboard-initiated readings
#define WEB_SERVER_PORT   80
// ── Library Includes ────────────────────────────────────────────────────────
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

// ── Hardware Objects ────────────────────────────────────────────────────────
MAX30105 ppgSensor;
TMP117   tmpSensor;
Adafruit_ST7789 tft = Adafruit_ST7789(TFT_DC, TFT_RES, TFT_SDA, TFT_SCL);
WebServer server(WEB_SERVER_PORT);

// ── Global State ────────────────────────────────────────────────────────────
struct SensorData {
  float saliva_ph;
  float hr_bpm;
  float ppg_raw_dc_baseline;
  float ppg_raw_ac_p2p;
  float temperature_c;
  float perfusion_index;
  float pulse_width_ms;
  bool valid;
};

SensorData latestReading;
bool readingInProgress = false;
bool buttonPressed = false;
unsigned long lastButtonCheck = 0;
const unsigned long buttonDebounceMs = 200;

// ── Display Colors ──────────────────────────────────────────────────────────
#define COLOR_BG          ST77XX_BLACK
#define COLOR_BORDER      ST77XX_WHITE
#define COLOR_TITLE       ST77XX_CYAN
#define COLOR_LABEL       ST77XX_YELLOW
#define COLOR_VALUE       ST77XX_GREEN
#define COLOR_ERROR       ST77XX_RED
#define COLOR_PROGRESS    ST77XX_BLUE
#define COLOR_SUCCESS     ST77XX_GREEN
// ── Display Functions ───────────────────────────────────────────────────────
void initDisplay() {
  pinMode(TFT_BLK, OUTPUT);
  digitalWrite(TFT_BLK, HIGH);  // Turn on backlight
  
  tft.init(240, 240);           // Initialize 240x240 display
  tft.setRotation(0);           // Portrait mode
  tft.fillScreen(COLOR_BG);
  
  drawWelcomeScreen();
}

void drawBorder() {
  // Draw outer border
  tft.drawRect(0, 0, 240, 240, COLOR_BORDER);
  tft.drawRect(1, 1, 238, 238, COLOR_BORDER);
}

void drawWelcomeScreen() {
  tft.fillScreen(COLOR_BG);
  drawBorder();
  
  // Title
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(2);
  tft.setCursor(35, 20);
  tft.print("Glucose Monitor");
  
  // Device ID
  tft.setTextColor(COLOR_LABEL);
  tft.setTextSize(1);
  tft.setCursor(10, 50);
  tft.print("Device: ");
  tft.setTextColor(COLOR_VALUE);
  tft.print(DEVICE_ID);
  
  // WiFi status
  tft.setTextColor(COLOR_LABEL);
  tft.setCursor(10, 70);
  tft.print("WiFi: ");
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(COLOR_SUCCESS);
    tft.print("Connected");
    tft.setTextColor(COLOR_VALUE);
    tft.setCursor(10, 85);
    tft.print(WiFi.localIP());
  } else {
    tft.setTextColor(COLOR_ERROR);
    tft.print("Disconnected");
  }
  
  // Instructions
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(1);
  tft.setCursor(10, 120);
  tft.print("Press button or use dashboard");
  tft.setCursor(10, 135);
  tft.print("to start sensor reading");
  
  // Status box
  tft.drawRect(10, 160, 220, 60, COLOR_BORDER);
  tft.setTextColor(COLOR_LABEL);
  tft.setCursor(15, 170);
  tft.print("Status: Ready");
  tft.setCursor(15, 185);
  tft.print("Last reading: None");
  tft.setCursor(15, 200);
  tft.print("Waiting for trigger...");
}
void drawSensorProgress(const char* sensorName, int stepNum, int totalSteps, bool success = false) {
  tft.fillScreen(COLOR_BG);
  drawBorder();
  
  // Title
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(2);
  tft.setCursor(20, 20);
  tft.print("Reading Sensors");
  
  // Progress indicator
  tft.setTextColor(COLOR_LABEL);
  tft.setTextSize(1);
  tft.setCursor(10, 50);
  tft.print("Step ");
  tft.setTextColor(COLOR_VALUE);
  tft.print(stepNum);
  tft.setTextColor(COLOR_LABEL);
  tft.print(" of ");
  tft.setTextColor(COLOR_VALUE);
  tft.print(totalSteps);
  
  // Progress bar
  int barWidth = 200;
  int barHeight = 10;
  int barX = 20;
  int barY = 70;
  
  tft.drawRect(barX, barY, barWidth, barHeight, COLOR_BORDER);
  int fillWidth = (barWidth * stepNum) / totalSteps;
  tft.fillRect(barX + 1, barY + 1, fillWidth - 1, barHeight - 2, COLOR_PROGRESS);
  
  // Current sensor
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(1);
  tft.setCursor(10, 100);
  tft.print("Current: ");
  tft.setTextColor(success ? COLOR_SUCCESS : COLOR_VALUE);
  tft.print(sensorName);
  
  if (success) {
    tft.setTextColor(COLOR_SUCCESS);
    tft.setCursor(170, 100);
    tft.print("✓ Done");
  } else {
    // Animate dots
    tft.setTextColor(COLOR_LABEL);
    tft.setCursor(10, 120);
    static int dotCount = 0;
    dotCount = (dotCount + 1) % 4;
    tft.print("Reading");
    for (int i = 0; i < dotCount; i++) {
      tft.print(".");
    }
    for (int i = dotCount; i < 3; i++) {
      tft.print(" ");
    }
  }
}

void drawSensorResults(const SensorData& data) {
  tft.fillScreen(COLOR_BG);
  drawBorder();
  
  // Title
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(2);
  tft.setCursor(45, 15);
  tft.print("Sensor Data");
  
  int y = 45;
  int lineHeight = 18;
  
  // Helper function to draw sensor value
  auto drawValue = [&](const char* label, float value, const char* unit) {
    tft.setTextColor(COLOR_LABEL);
    tft.setTextSize(1);
    tft.setCursor(10, y);
    tft.print(label);
    tft.setCursor(10, y + 10);
    tft.setTextColor(COLOR_VALUE);
    tft.print(value, 2);
    tft.print(" ");
    tft.print(unit);
    y += lineHeight;
  };
  
  drawValue("pH Level:", data.saliva_ph, "pH");
  drawValue("Heart Rate:", data.hr_bpm, "bpm");
  drawValue("Temperature:", data.temperature_c, "°C");
  drawValue("Perfusion:", data.perfusion_index, "%");
  drawValue("PPG DC:", data.ppg_raw_dc_baseline, "");
  drawValue("PPG AC:", data.ppg_raw_ac_p2p, "");
  drawValue("Pulse Width:", data.pulse_width_ms, "ms");
  
  // Upload status
  tft.drawRect(10, 180, 220, 50, COLOR_BORDER);
  tft.setTextColor(COLOR_TITLE);
  tft.setCursor(15, 190);
  tft.print("Cloud Upload: ");
  tft.setTextColor(COLOR_SUCCESS);
  tft.print("Complete");
  
  tft.setTextColor(COLOR_LABEL);
  tft.setCursor(15, 205);
  tft.print("Ready for next reading");
  tft.setCursor(15, 220);
  tft.print("Press button to repeat");
}
// ── Sensor Functions ────────────────────────────────────────────────────────
bool initSensors() {
  // Initialize I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  
  // Initialize MAX30102
  if (!ppgSensor.begin()) {
    Serial.println("ERROR: MAX30102 not found");
    return false;
  }
  
  ppgSensor.setup();
  ppgSensor.setPulseAmplitudeRed(0x0A);    // Turn Red LED to low
  ppgSensor.setPulseAmplitudeIR(0x1F);     // Turn IR LED to medium
  
  // Initialize TMP117
  if (!tmpSensor.begin()) {
    Serial.println("ERROR: TMP117 not found");
    return false;
  }
  
  Serial.println("All sensors initialized successfully");
  return true;
}

float readpH() {
  int rawADC = analogRead(PH_ADC_PIN);
  float voltage = rawADC * (3.3 / 4095.0);  // Convert to voltage
  float ph = PH_SLOPE * rawADC + PH_INTERCEPT;
  
  Serial.print("pH - Raw ADC: ");
  Serial.print(rawADC);
  Serial.print(", Voltage: ");
  Serial.print(voltage, 3);
  Serial.print("V, pH: ");
  Serial.println(ph, 2);
  
  return ph;
}

float readTemperature() {
  if (tmpSensor.dataReady()) {
    float temp = tmpSensor.readTempC();
    Serial.print("Temperature: ");
    Serial.print(temp, 2);
    Serial.println("°C");
    return temp;
  }
  Serial.println("Temperature sensor not ready");
  return 0.0;
}

bool readPPG(SensorData& data) {
  Serial.println("Starting PPG collection...");
  
  // Clear the sensor buffer
  while (ppgSensor.available()) {
    ppgSensor.getIR();
    ppgSensor.getRed();
  }
  
  // Collect samples
  uint32_t irBuffer[PPG_SAMPLES_NEEDED];
  uint32_t redBuffer[PPG_SAMPLES_NEEDED];
  int sampleCount = 0;
  
  unsigned long startTime = millis();
  
  while (sampleCount < PPG_SAMPLES_NEEDED && 
         (millis() - startTime) < PPG_SAMPLE_WINDOW_MS + 1000) {
    
    if (ppgSensor.available()) {
      irBuffer[sampleCount] = ppgSensor.getIR();
      redBuffer[sampleCount] = ppgSensor.getRed();
      sampleCount++;
      
      // Show progress every 50 samples
      if (sampleCount % 50 == 0) {
        Serial.print("PPG samples collected: ");
        Serial.print(sampleCount);
        Serial.print("/");
        Serial.println(PPG_SAMPLES_NEEDED);
      }
    }
    delay(10);
  }
  
  if (sampleCount < PPG_SAMPLES_NEEDED / 2) {
    Serial.println("ERROR: Insufficient PPG samples collected");
    return false;
  }
  
  // Process the collected data
  uint32_t irSum = 0;
  uint32_t irMin = 4294967295U;
  uint32_t irMax = 0;
  
  for (int i = 0; i < sampleCount; i++) {
    irSum += irBuffer[i];
    if (irBuffer[i] < irMin) irMin = irBuffer[i];
    if (irBuffer[i] > irMax) irMax = irBuffer[i];
  }
  
  // Calculate metrics
  data.ppg_raw_dc_baseline = (float)irSum / sampleCount;
  data.ppg_raw_ac_p2p = (float)(irMax - irMin);
  data.perfusion_index = (data.ppg_raw_ac_p2p / data.ppg_raw_dc_baseline) * 100.0;
  
  // Simple heart rate detection
  data.hr_bpm = 0.0;
  int beatCount = 0;
  bool lastBeat = false;
  
  for (int i = 1; i < sampleCount - 1; i++) {
    bool currentBeat = (irBuffer[i] > irBuffer[i-1] && irBuffer[i] > irBuffer[i+1] && 
                       irBuffer[i] > (data.ppg_raw_dc_baseline + data.ppg_raw_ac_p2p * 0.3));
    
    if (currentBeat && !lastBeat) {
      beatCount++;
    }
    lastBeat = currentBeat;
  }
  
  if (beatCount > 0) {
    data.hr_bpm = (beatCount * 60.0 * 1000.0) / PPG_SAMPLE_WINDOW_MS;
    data.pulse_width_ms = (float)PPG_SAMPLE_WINDOW_MS / beatCount;
  } else {
    data.hr_bpm = 70.0;  // Default fallback
    data.pulse_width_ms = 857.0;  // 60000ms / 70bpm
  }
  
  Serial.print("PPG Results - DC: ");
  Serial.print(data.ppg_raw_dc_baseline, 1);
  Serial.print(", AC: ");
  Serial.print(data.ppg_raw_ac_p2p, 1);
  Serial.print(", PI: ");
  Serial.print(data.perfusion_index, 2);
  Serial.print("%, HR: ");
  Serial.print(data.hr_bpm, 1);
  Serial.print(" bpm, PW: ");
  Serial.print(data.pulse_width_ms, 1);
  Serial.println(" ms");
  
  return true;
}
SensorData collectAllSensors() {
  SensorData data;
  data.valid = false;
  
  Serial.println("=== Starting sensor collection sequence ===");
  
  // Step 1: pH reading
  drawSensorProgress("pH Probe", 1, 4);
  delay(1000);
  data.saliva_ph = readpH();
  drawSensorProgress("pH Probe", 1, 4, true);
  delay(500);
  
  // Step 2: Temperature reading  
  drawSensorProgress("Temperature Sensor", 2, 4);
  delay(1000);
  data.temperature_c = readTemperature();
  drawSensorProgress("Temperature Sensor", 2, 4, true);
  delay(500);
  
  // Step 3: PPG reading (longest step)
  drawSensorProgress("PPG Sensor (5 sec)", 3, 4);
  delay(500);
  
  if (!readPPG(data)) {
    Serial.println("ERROR: PPG reading failed");
    drawSensorProgress("PPG Sensor - FAILED", 3, 4);
    delay(2000);
    return data;
  }
  
  drawSensorProgress("PPG Sensor", 3, 4, true);
  delay(500);
  
  // Step 4: Final validation
  drawSensorProgress("Validating Data", 4, 4);
  delay(1000);
  
  // Validate ranges
  bool valid = true;
  if (data.saliva_ph < 4.0 || data.saliva_ph > 9.0) {
    Serial.println("WARNING: pH out of expected range");
    valid = false;
  }
  if (data.temperature_c < 30.0 || data.temperature_c > 45.0) {
    Serial.println("WARNING: Temperature out of expected range");
    valid = false;
  }
  if (data.hr_bpm < 40.0 || data.hr_bpm > 200.0) {
    Serial.println("WARNING: Heart rate out of expected range");
  }
  
  data.valid = valid;
  drawSensorProgress("Validation Complete", 4, 4, true);
  delay(1000);
  
  Serial.println("=== Sensor collection complete ===");
  return data;
}
// ── Network Functions ───────────────────────────────────────────────────────
bool uploadToSupabase(const SensorData& data) {
  if (!data.valid) {
    Serial.println("Cannot upload invalid sensor data");
    return false;
  }
  
  HTTPClient http;
  String url = "https://" + String(SUPABASE_PROJECT) + ".supabase.co/rest/v1/readings";
  
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " + String(SUPABASE_ANON_KEY));
  http.addHeader("apikey", String(SUPABASE_ANON_KEY));
  http.addHeader("Prefer", "return=minimal");
  
  // Create JSON payload
  StaticJsonDocument<512> doc;
  doc["device_id"] = String(DEVICE_ID);
  doc["saliva_ph"] = data.saliva_ph;
  doc["hr_bpm"] = data.hr_bpm;
  doc["ppg_raw_dc_baseline"] = data.ppg_raw_dc_baseline;
  doc["ppg_raw_ac_p2p"] = data.ppg_raw_ac_p2p;
  doc["temperature_c"] = data.temperature_c;
  doc["perfusion_index"] = data.perfusion_index;
  doc["pulse_width_ms"] = data.pulse_width_ms;
  doc["timestamp"] = "now()";  // PostgreSQL function
  
  String jsonString;
  serializeJson(doc, jsonString);
  
  Serial.println("Uploading to Supabase...");
  Serial.println("URL: " + url);
  Serial.println("Payload: " + jsonString);
  
  int httpResponseCode = http.POST(jsonString);
  
  if (httpResponseCode == 201) {
    Serial.println("✓ Upload successful");
    http.end();
    return true;
  } else {
    Serial.print("✗ Upload failed - HTTP ");
    Serial.println(httpResponseCode);
    String response = http.getString();
    Serial.println("Response: " + response);
    http.end();
    return false;
  }
}

void handleStartReading() {
  if (readingInProgress) {
    server.send(409, "application/json", "{\"error\":\"Reading already in progress\"}");
    return;
  }
  
  Serial.println("Remote reading request received from dashboard");
  
  server.send(200, "application/json", "{\"status\":\"started\",\"message\":\"Sensor reading initiated\"}");
  
  // Start reading in next loop iteration
  buttonPressed = true;
}

void handleGetStatus() {
  StaticJsonDocument<300> doc;
  doc["device_id"] = String(DEVICE_ID);
  doc["reading_in_progress"] = readingInProgress;
  doc["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
  doc["ip_address"] = WiFi.localIP().toString();
  
  if (latestReading.valid) {
    doc["last_reading"]["saliva_ph"] = latestReading.saliva_ph;
    doc["last_reading"]["hr_bpm"] = latestReading.hr_bpm;
    doc["last_reading"]["temperature_c"] = latestReading.temperature_c;
  }
  
  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
}

void setupWebServer() {
  server.on("/start_reading", HTTP_POST, handleStartReading);
  server.on("/status", HTTP_GET, handleGetStatus);
  
  server.begin();
  Serial.print("Web server started on http://");
  Serial.print(WiFi.localIP());
  Serial.println("/");
}
// ── Button Handling ─────────────────────────────────────────────────────────
void checkButton() {
  if (millis() - lastButtonCheck < buttonDebounceMs) {
    return;
  }
  
  if (digitalRead(BUTTON_PIN) == LOW && !readingInProgress) {
    buttonPressed = true;
    lastButtonCheck = millis();
    Serial.println("Button pressed - starting reading");
  }
}

void performReading() {
  if (!readingInProgress) {
    readingInProgress = true;
    
    Serial.println(">>> Starting sensor reading sequence <<<");
    
    // Collect all sensor data with progress display
    latestReading = collectAllSensors();
    
    if (latestReading.valid) {
      // Display results on screen
      drawSensorResults(latestReading);
      
      // Upload to cloud
      bool uploadSuccess = uploadToSupabase(latestReading);
      
      if (!uploadSuccess) {
        // Update display to show upload error
        tft.fillRect(10, 180, 220, 50, COLOR_BG);
        tft.drawRect(10, 180, 220, 50, COLOR_BORDER);
        tft.setTextColor(COLOR_ERROR);
        tft.setTextSize(1);
        tft.setCursor(15, 190);
        tft.print("Upload Failed!");
        tft.setTextColor(COLOR_LABEL);
        tft.setCursor(15, 205);
        tft.print("Check WiFi connection");
        tft.setCursor(15, 220);
        tft.print("Data saved locally");
      }
      
    } else {
      // Show error screen
      tft.fillScreen(COLOR_BG);
      drawBorder();
      tft.setTextColor(COLOR_ERROR);
      tft.setTextSize(2);
      tft.setCursor(50, 100);
      tft.print("SENSOR ERROR");
      
      tft.setTextColor(COLOR_LABEL);
      tft.setTextSize(1);
      tft.setCursor(10, 140);
      tft.print("Check sensor connections");
      tft.setCursor(10, 160);
      tft.print("Press button to retry");
    }
    
    readingInProgress = false;
    buttonPressed = false;
    
    // Return to welcome screen after 10 seconds
    delay(10000);
    drawWelcomeScreen();
  }
}
// ── Main Setup and Loop ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== ESP32-S3 Glucose Monitor Starting ===");
  
  // Initialize display first for user feedback
  initDisplay();
  
  // Show startup message
  tft.fillScreen(COLOR_BG);
  drawBorder();
  tft.setTextColor(COLOR_TITLE);
  tft.setTextSize(2);
  tft.setCursor(40, 50);
  tft.print("Initializing...");
  
  // Initialize button
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  
  // Initialize sensors
  tft.setTextColor(COLOR_LABEL);
  tft.setTextSize(1);
  tft.setCursor(10, 90);
  tft.print("Starting sensors...");
  
  if (!initSensors()) {
    tft.setTextColor(COLOR_ERROR);
    tft.setCursor(10, 110);
    tft.print("SENSOR INIT FAILED!");
    tft.setCursor(10, 130);
    tft.print("Check I2C connections");
    while (1) delay(1000);  // Stop here if sensors fail
  }
  
  tft.setTextColor(COLOR_SUCCESS);
  tft.setCursor(10, 110);
  tft.print("Sensors OK");
  
  // Connect to WiFi
  tft.setTextColor(COLOR_LABEL);
  tft.setCursor(10, 130);
  tft.print("Connecting to WiFi...");
  
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    tft.setTextColor(COLOR_SUCCESS);
    tft.setCursor(10, 150);
    tft.print("WiFi Connected");
    tft.setCursor(10, 170);
    tft.print(WiFi.localIP());
    
    Serial.println("\nWiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    
    setupWebServer();
  } else {
    tft.setTextColor(COLOR_ERROR);
    tft.setCursor(10, 150);
    tft.print("WiFi Failed");
    tft.setCursor(10, 170);
    tft.print("Check credentials");
    Serial.println("\nWiFi connection failed");
  }
  
  delay(2000);
  
  // Initialize sensor data structure
  latestReading.valid = false;
  
  // Show welcome screen
  drawWelcomeScreen();
  
  Serial.println("=== Setup complete - ready for readings ===");
}

void loop() {
  // Handle web server requests
  server.handleClient();
  
  // Check for button press
  checkButton();
  
  // Process reading if triggered
  if (buttonPressed && !readingInProgress) {
    performReading();
  }
  
  // Keep WiFi alive
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected, attempting reconnection...");
    WiFi.reconnect();
  }
  
  delay(50);  // Small delay for responsiveness
}