// =============================================================================
// Landslide Monitoring Node - firmware 4.0.0
// ESP32 + capacitive soil moisture + analog water sensor + MPU6050 + SW-420
//
// Responsibilities of this firmware:
//   * automatic initialisation and calibration at power-on and on `demo`
//   * sampling, filtering, feature extraction and sensor diagnostics
//   * one combined, versioned JSON telemetry record per upload interval
//   * Wi-Fi / HTTP with bounded offline buffering and retry back-off
//   * LEDs and buzzers driven ONLY by validated Random Forest predictions
//     returned by the backend (no on-device risk formula)
//
// Hardware-independent logic lives in ls_logic.h (unit-tested on a PC).
// Wi-Fi credentials, backend address and the device token live in secrets.h
// (git-ignored; copy secrets.example.h).
// =============================================================================

#include <Arduino.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <sys/time.h>
#include <time.h>
#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif
#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 2
#endif

#include "ls_logic.h"
#include "node_types.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID "Realme"
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD "qwertyuiop"
#endif
#ifndef API_HOST
#define API_HOST ""
#endif
#ifndef API_PORT
#define API_PORT 8000
#endif
#ifndef DEVICE_ID
#define DEVICE_ID "esp32-node-1"
#endif
#ifndef DEVICE_TOKEN
#define DEVICE_TOKEN ""
#endif

// ----------------------------------------------------------------------------
// Versions
// ----------------------------------------------------------------------------
static const char* FW_VERSION = "4.0.0";
static const char* TELEMETRY_SCHEMA = "2.0";
static const char* FEATURE_SCHEMA = "fs-1";

// ----------------------------------------------------------------------------
// GPIO (standard ESP32 DevKit). Analog inputs are on ADC1 so they keep working
// while Wi-Fi is active. GPIO 12 and 15 are boot-strapping pins: see
// docs/HARDWARE.md (buzzer 1 must not pull GPIO 12 high during reset).
// ----------------------------------------------------------------------------
static const int PIN_LED_RED = 19;
static const int PIN_LED_BLUE = 21;
static const int PIN_LED_YELLOW = 22;
static const int PIN_LED_GREEN = 23;
static const int PIN_SOIL_AO = 35;   // ADC1_CH7
static const int PIN_SOIL_DO = 32;
static const int PIN_WATER_S = 34;   // ADC1_CH6, analog water sensor signal
static const int PIN_VIB_DO = 15;    // SW-420 digital output (interrupt)
static const int PIN_SDA = 25;
static const int PIN_SCL = 26;
static const int PIN_BUZZER1 = 12;
static const int PIN_BUZZER2 = 13;

// Soil comparator polarity: typical modules pull DO LOW when wetter than the
// potentiometer threshold. Only used as a diagnostic cross-check.
static const int SOIL_DO_WET_LEVEL = LOW;

// MPU6050 mounting: the sensor axis that points up when the model is level.
static const int MOUNT_AXIS = 2;  // 0 = X, 1 = Y, 2 = Z
static const int MOUNT_SIGN = 1;  // +1 or -1

// ----------------------------------------------------------------------------
// Timing
// ----------------------------------------------------------------------------
static const uint32_t MPU_PERIOD_US = 20000;          // 50 Hz
static const uint32_t ADC_PERIOD_MS = 100;           // 10 Hz analog sampling
static const uint32_t SNAPSHOT_PERIOD_MS = 1000;     // processed snapshot
static const uint8_t TELEMETRY_EVERY_N_SNAPSHOTS = 2;  // one reading every 2 s
static const uint32_t PRED_FRESH_MS = 10000;         // prediction valid for alerts
static const uint32_t BACKEND_REACHABLE_MS = 15000;
static const uint32_t WIFI_RETRY_MIN_MS = 2000;
static const uint32_t WIFI_RETRY_MAX_MS = 30000;
static const uint32_t HTTP_CONNECT_TIMEOUT_MS = 3000;
static const uint32_t HTTP_TIMEOUT_MS = 4000;
static const uint32_t POST_BACKOFF_MIN_MS = 1000;
static const uint32_t POST_BACKOFF_MAX_MS = 30000;
static const uint32_t AUTH_BACKOFF_MS = 30000;
static const uint32_t MPU_RETRY_MS = 3000;
static const uint32_t WDT_TIMEOUT_S = 30;

// ----------------------------------------------------------------------------
// Calibration
// ----------------------------------------------------------------------------
static const uint16_t CAL_SAMPLES = 200;
static const uint32_t CAL_SETTLE_MS = 500;
static const uint32_t CAL_COLLECT_TIMEOUT_MS = 8000;
static const uint8_t CAL_MAX_ATTEMPTS = 3;
static const uint32_t CAL_RETRY_DELAY_MS = 2000;
static const float CAL_MAX_ACCEL_STD_G = 0.03f;
static const float CAL_MAX_GYRO_STD_DPS = 1.5f;
static const float CAL_MOVING_GYRO_DPS = 30.0f;
static const float CAL_GRAVITY_MIN_G = 0.90f;
static const float CAL_GRAVITY_MAX_G = 1.10f;
static const float CAL_MAX_GYRO_BIAS_DPS = 20.0f;
static const uint16_t CAL_MAX_INVALID_SAMPLES = 20;
static const uint8_t MAX_AUTO_RECAL = 2;  // automatic retries when the MPU appears later
static const uint32_t VIB_DEAD_TIME_US = 20000;  // SW-420 software debounce
static const float COMP_FILTER_K = 0.98f;

// Feature windows (seconds) and minimum points
static const float SOIL_WIN_S = 30.0f;
static const int SOIL_MIN_PTS = 10;
static const float WATER_WIN_S = 30.0f;
static const int WATER_MIN_PTS = 10;
static const float TILT_RATE_WIN_S = 10.0f;
static const int TILT_RATE_MIN_PTS = 5;
static const int VIB_BINS = 30;

static const int QUEUE_CAP = 120;  // ~4 min of readings at 2 s
static const int8_t RSSI_NONE = 127;

// ----------------------------------------------------------------------------
// Globals (sensor state is owned by the loop task on core 1)
// ----------------------------------------------------------------------------
static Preferences prefs;
static portMUX_TYPE gMux = portMUX_INITIALIZER_UNLOCKED;   // PredState / NetState / session
static portMUX_TYPE vibMux = portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t queueMutex = nullptr;

static char bootId[9] = "00000000";
static uint32_t bootCount = 0;
static uint16_t sessionNo = 0;
static uint32_t seqCounter = 0;
static CalInfo calInfo[2];  // current and previous session
static uint8_t calStatus = CS_NONE;
static uint8_t autoRecalCount = 0;

// Calibration work area
static struct {
  CalPhase phase;
  uint32_t phaseStartMs;
  uint16_t n;
  uint16_t invalid;
  double sa[3], sa2[3], sg[3], sg2[3];
  uint32_t vibEventsStart;
  uint32_t collectStartMs;
} cal;

// Soil
static struct {
  int raw;
  float filt;
  int dout;
  float moist, rate, mean30, std30;
  Health health;
  uint16_t flags;
  ls::RunningStats win;
  uint32_t satSinceMs;
  bool satNoiseless;
  int satLastRaw;
  ls::Series<32> series;
} soil;

// Water
static struct {
  int raw;
  float filt;
  float wet, rate, mean30;
  float wetSeconds;
  bool wetDurationValid;
  Health health;
  uint16_t flags;
  ls::RunningStats win;
  ls::Series<32> series;
} water;

// MPU6050
static struct {
  bool present;
  uint8_t addr;
  uint32_t lastInitTryMs;
  uint8_t consecutiveErrors;
  uint32_t totalErrors;
  uint32_t lastSampleUs;
  Vec3 accel, gyroRaw, gyroCorr;
  float tempC;
  Vec3 gEst;
  bool gEstValid;
  bool baselineValid;
  Vec3 g0;
  Vec3 gyroBias;
  float incl0;
  float incl, tilt, inclChange, roll, pitch, tiltRate;
  ls::RunningStats accMagInterval;
  double gyroSqSum;
  uint32_t gyroN;
  uint16_t flags;
  ls::Series<32> tiltSeries;
} mpu;

// SW-420 (ISR counters are monotonic; the loop computes deltas)
static volatile uint32_t vibTotalTransitions = 0;
static volatile uint32_t vibTotalEvents = 0;
static volatile uint32_t vibLastEventUs = 0;
static struct {
  uint32_t lastTransitions, lastEvents;
  uint16_t bins[VIB_BINS];
  int binHead;
  int binsFilled;
  uint32_t intervalEvents, intervalTransitions;
  uint32_t intervalStartMs;
  float bg;  // background events/s measured during calibration
  bool bgValid;
} vib;

// Snapshot / reading scheduling
static uint32_t lastAdcMs = 0;
static uint32_t lastSnapMs = 0;
static uint8_t snapsSinceReading = 0;
static bool forceReading = false;
static Reading lastReading;
static bool haveLastReading = false;

// Offline queue
static Reading queueItems[QUEUE_CAP];
static int queueHead = 0;  // index of oldest
static int queueCount = 0;
static uint32_t queueDropped = 0;

// Shared with the network task
static PredState pred;
static NetState net;

// Indicators
static uint32_t testStartMs = 0;
static bool testActive = false;
static bool outState[6] = {false, false, false, false, false, false};
static ls::Indication lastIndication = ls::IND_TEST;

// Serial
static char serialLine[40];
static uint8_t serialLen = 0;

// ----------------------------------------------------------------------------
// Small helpers
// ----------------------------------------------------------------------------
void sessionIdFor(uint16_t no, char* out, size_t len) { snprintf(out, len, "%s-%02u", bootId, (unsigned)no); }

void readingIdFor(const Reading& r, char* out, size_t len) {
  snprintf(out, len, "%s-%02u-%06lu", bootId, (unsigned)r.sessionNo, (unsigned long)r.seq);
}

int64_t epochMsNow() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;  // clock not synchronised
  return (int64_t)tv.tv_sec * 1000LL + tv.tv_usec / 1000;
}

void wdtSetup() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg;
  memset(&cfg, 0, sizeof(cfg));
  cfg.timeout_ms = WDT_TIMEOUT_S * 1000;
  cfg.idle_core_mask = 0;
  cfg.trigger_panic = true;
  if (esp_task_wdt_reconfigure(&cfg) != ESP_OK) esp_task_wdt_init(&cfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);  // reconfigures if already initialised
#endif
}

CalInfo* calInfoFor(uint16_t no) {
  for (int i = 0; i < 2; i++)
    if (calInfo[i].sessionNo == no) return &calInfo[i];
  return nullptr;
}

// ----------------------------------------------------------------------------
// SW-420 interrupt
// ----------------------------------------------------------------------------
void IRAM_ATTR vibrationIsr() {
  uint32_t now = micros();
  portENTER_CRITICAL_ISR(&vibMux);
  vibTotalTransitions = vibTotalTransitions + 1;  // (C++20: no ++ on volatile)
  if ((uint32_t)(now - vibLastEventUs) >= VIB_DEAD_TIME_US) {
    vibTotalEvents = vibTotalEvents + 1;
    vibLastEventUs = now;
  }
  portEXIT_CRITICAL_ISR(&vibMux);
}

void vibReadTotals(uint32_t& transitions, uint32_t& events) {
  portENTER_CRITICAL(&vibMux);
  transitions = vibTotalTransitions;
  events = vibTotalEvents;
  portEXIT_CRITICAL(&vibMux);
}

void vibResetHistory() {
  vibReadTotals(vib.lastTransitions, vib.lastEvents);
  memset(vib.bins, 0, sizeof(vib.bins));
  vib.binHead = 0;
  vib.binsFilled = 0;
  vib.intervalEvents = 0;
  vib.intervalTransitions = 0;
  vib.intervalStartMs = millis();
}

float vibWindowSum(int seconds) {
  if (vib.binsFilled < seconds) return NAN;
  uint32_t s = 0;
  for (int i = 1; i <= seconds; i++) s += vib.bins[(vib.binHead - i + VIB_BINS) % VIB_BINS];
  return (float)s;
}

// ----------------------------------------------------------------------------
// MPU6050
// ----------------------------------------------------------------------------
bool mpuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(mpu.addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool mpuInit() {
  mpu.lastInitTryMs = millis();
  uint8_t found = 0;
  for (uint8_t a = 0x68; a <= 0x69; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      found = a;
      break;
    }
  }
  if (!found) return false;
  mpu.addr = found;
  bool ok = mpuWrite(0x6B, 0x80);  // device reset
  delay(100);
  ok = mpuWrite(0x6B, 0x01) && ok;  // wake, PLL with X gyro clock
  delay(10);
  ok = ok && mpuWrite(0x1A, 0x04);  // DLPF ~21 Hz (anti-aliasing for 50 Hz sampling)
  ok = ok && mpuWrite(0x19, 19);    // sample rate 1 kHz / (1 + 19) = 50 Hz
  ok = ok && mpuWrite(0x1B, 0x00);  // gyro +-250 deg/s
  ok = ok && mpuWrite(0x1C, 0x00);  // accel +-2 g
  if (!ok) return false;
  mpu.consecutiveErrors = 0;
  mpu.gEstValid = false;
  Serial.printf("[mpu] MPU6050 ready at 0x%02X\n", found);
  return true;
}

bool mpuReadRaw(Vec3& a, Vec3& g, float& tempC) {
  Wire.beginTransmission(mpu.addr);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint16_t)mpu.addr, (uint8_t)14, true) != 14) return false;
  int16_t v[7];
  for (int i = 0; i < 7; i++) {
    uint8_t hi = Wire.read();
    uint8_t lo = Wire.read();
    v[i] = (int16_t)((hi << 8) | lo);
  }
  a.x = v[0] / 16384.0f;
  a.y = v[1] / 16384.0f;
  a.z = v[2] / 16384.0f;
  tempC = v[3] / 340.0f + 36.53f;
  g.x = v[4] / 131.0f;
  g.y = v[5] / 131.0f;
  g.z = v[6] / 131.0f;
  // An all-zero frame usually means a bus glitch, not a real measurement.
  if (v[0] == 0 && v[1] == 0 && v[2] == 0) return false;
  return true;
}

void calibrationAddSample(const Vec3& a, const Vec3& g);

void mpuSample() {
  uint32_t nowUs = micros();
  if ((uint32_t)(nowUs - mpu.lastSampleUs) < MPU_PERIOD_US) return;
  float dt = (nowUs - mpu.lastSampleUs) / 1e6f;
  // Keep a fixed cadence; resynchronise after long stalls.
  mpu.lastSampleUs = (dt > 0.1f) ? nowUs : mpu.lastSampleUs + MPU_PERIOD_US;
  if (dt > 0.1f) dt = MPU_PERIOD_US / 1e6f;

  if (!mpu.present) {
    if (millis() - mpu.lastInitTryMs >= MPU_RETRY_MS) {
      if (mpuInit()) {
        mpu.present = true;
        if (mpu.baselineValid) mpu.flags |= ls::MF_RECONNECTED;
      }
    }
    return;
  }

  Vec3 a, g;
  float t;
  if (!mpuReadRaw(a, g, t)) {
    mpu.totalErrors++;
    mpu.flags |= ls::MF_READ_ERRORS;
    if (++mpu.consecutiveErrors >= 10) {
      mpu.present = false;
      Serial.println("[mpu] MPU6050 lost (I2C read errors); retrying");
    }
    return;
  }
  mpu.consecutiveErrors = 0;
  mpu.accel = a;
  mpu.gyroRaw = g;
  mpu.tempC = t;
  Vec3 corr = {g.x - mpu.gyroBias.x, g.y - mpu.gyroBias.y, g.z - mpu.gyroBias.z};
  mpu.gyroCorr = corr;
  if (fabsf(a.x) > 1.99f || fabsf(a.y) > 1.99f || fabsf(a.z) > 1.99f) mpu.flags |= ls::MF_ACCEL_SATURATED;

  if (cal.phase == CP_COLLECT) calibrationAddSample(a, g);

  if (!mpu.gEstValid) {
    Vec3 n;
    if (ls::vnormalize(a, n)) {
      mpu.gEst = n;
      mpu.gEstValid = true;
    }
  } else {
    mpu.gEst = ls::complementaryStep(mpu.gEst, corr, a, dt, COMP_FILTER_K);
  }
  mpu.accMagInterval.add(ls::vmag(a));
  float gm2 = corr.x * corr.x + corr.y * corr.y + corr.z * corr.z;
  mpu.gyroSqSum += gm2;
  mpu.gyroN++;
}

// ----------------------------------------------------------------------------
// Analog sampling (10 Hz, 4-sample average per channel)
// ----------------------------------------------------------------------------
int adcAverage(int pin) {
  uint32_t s = 0;
  for (int i = 0; i < 4; i++) s += analogRead(pin);
  return (int)(s / 4);
}

// Median across five 10 Hz channel averages: rejects isolated spikes, not sustained wetting.
static int waterMedianSamples[5];
static uint8_t waterMedianCount = 0, waterMedianPos = 0;
int waterMedian(int value) {
  waterMedianSamples[waterMedianPos] = value;
  waterMedianPos = (waterMedianPos + 1) % 5;
  if (waterMedianCount < 5) ++waterMedianCount;
  int sorted[5];
  for (uint8_t i = 0; i < waterMedianCount; ++i) {
    sorted[i] = waterMedianSamples[i];
    for (int j = i; j > 0 && sorted[j] < sorted[j-1]; --j) {
      int tmp = sorted[j]; sorted[j] = sorted[j-1]; sorted[j-1] = tmp;
    }
  }
  return sorted[waterMedianCount / 2];
}

void adcSample() {
  uint32_t now = millis();
  if (now - lastAdcMs < ADC_PERIOD_MS) return;
  lastAdcMs = now;

  int s = adcAverage(PIN_SOIL_AO);
  soil.raw = s;
  soil.filt = ls::isFiniteF(soil.filt) ? soil.filt + 0.3f * (s - soil.filt) : (float)s;
  soil.win.add((float)s);
  if (s >= ls::SOIL_SAT_THRESHOLD) {
    if (soil.satSinceMs == 0) {
      soil.satSinceMs = now ? now : 1;
      soil.satNoiseless = true;
      soil.satLastRaw = s;
    } else if (s != soil.satLastRaw) {
      soil.satNoiseless = false;
    }
  } else {
    soil.satSinceMs = 0;
  }

  int w = adcAverage(PIN_WATER_S);
  water.raw = w;
  int filteredInput = waterMedian(w);
  water.filt = ls::isFiniteF(water.filt) ? water.filt + 0.3f * (filteredInput - water.filt) : (float)filteredInput;
  water.win.add((float)w);
}

// ----------------------------------------------------------------------------
// Calibration (non-blocking state machine)
// ----------------------------------------------------------------------------
void resetSessionHistories() {
  soil.series.clear();
  soil.rate = soil.mean30 = soil.std30 = NAN;
  water.series.clear();
  water.rate = water.mean30 = NAN;
  water.wetSeconds = 0;
  water.wetDurationValid = true;
  mpu.tiltSeries.clear();
  mpu.tiltRate = NAN;
  mpu.flags = 0;
  mpu.accMagInterval.reset();
  mpu.gyroSqSum = 0;
  mpu.gyroN = 0;
  vibResetHistory();
}

void startCalibration(const char* trigger) {
  if (strcmp(trigger, "mpu_return") != 0) autoRecalCount = 0;  // demo / boot restore the retry budget
  sessionNo++;
  CalInfo& ci = calInfo[sessionNo % 2];
  memset(&ci, 0, sizeof(ci));
  ci.sessionNo = sessionNo;
  ci.status = CS_IN_PROGRESS;
  strncpy(ci.trigger, trigger, sizeof(ci.trigger) - 1);
  ci.incl0 = ci.accelStd = ci.gyroStd = ci.vibBg = ci.prevIncl0 = ci.prevShiftDeg = NAN;
  ci.soilRaw0 = ci.waterRaw0 = -1;

  calStatus = CS_IN_PROGRESS;
  mpu.baselineValid = false;
  vib.bgValid = false;
  vib.bg = NAN;
  resetSessionHistories();
  cal.phase = CP_SETTLE;
  cal.phaseStartMs = millis();

  // A new session never inherits the previous session's prediction.
  portENTER_CRITICAL(&gMux);
  pred.valid = false;
  net.backendStatus[0] = 0;
  net.backendReason[0] = 0;
  net.backendSensorFault = false;
  portEXIT_CRITICAL(&gMux);

  char sid[16];
  sessionIdFor(sessionNo, sid, sizeof(sid));
  Serial.println();
  Serial.println("==================================================");
  Serial.printf(" CALIBRATION (%s) - new session %s\n", trigger, sid);
  Serial.println(" Keep the slope model completely still (~5 s).");
  Serial.printf(" Soil endpoints: dry=%d wet=%d | Water endpoints: dry=%d wet=%d (fixed)\n", ls::SOIL_DRY_ADC,
                ls::SOIL_WET_ADC, ls::WATER_DRY_ADC, ls::WATER_WET_ADC);
  Serial.println("==================================================");
}

void calibrationBeginCollect() {
  cal.phase = CP_COLLECT;
  cal.phaseStartMs = millis();
  cal.collectStartMs = cal.phaseStartMs;
  cal.n = 0;
  cal.invalid = 0;
  for (int i = 0; i < 3; i++) cal.sa[i] = cal.sa2[i] = cal.sg[i] = cal.sg2[i] = 0;
  uint32_t t;
  vibReadTotals(t, cal.vibEventsStart);
}

void calibrationFail(const char* reason) {
  CalInfo& ci = calInfo[sessionNo % 2];
  ci.attempts++;
  strncpy(ci.error, reason, sizeof(ci.error) - 1);
  ci.error[sizeof(ci.error) - 1] = 0;
  Serial.printf("[cal] attempt %u/%u failed: %s\n", ci.attempts, CAL_MAX_ATTEMPTS, reason);
  if (ci.attempts < CAL_MAX_ATTEMPTS) {
    cal.phase = CP_RETRY_WAIT;
    cal.phaseStartMs = millis();
    return;
  }
  cal.phase = CP_IDLE;
  calStatus = CS_FAILED;
  ci.status = CS_FAILED;
  Serial.println("[cal] CALIBRATION FAILED. Previous saved baseline kept in NVS (not used).");
  Serial.println("[cal] Tilt features are unavailable in this session. Fix the cause and type 'demo'.");
}

bool loadCalRecord(const char* key, CalRecord& rec) {
  if (prefs.getBytesLength(key) != sizeof(CalRecord)) return false;
  if (prefs.getBytes(key, &rec, sizeof(rec)) != sizeof(rec)) return false;
  if (rec.magic != CAL_MAGIC || rec.version != 1 || rec.size != sizeof(CalRecord)) return false;
  return ls::crc32(&rec, offsetof(CalRecord, crc)) == rec.crc;
}

bool saveCalRecords(const CalRecord& current, const CalRecord* previous) {
  if (!prefs.begin("lsm4", false)) return false;
  bool ok = true;
  // Previous first: if power fails between the two writes, "cal" still holds
  // the last good record.
  if (previous) ok = prefs.putBytes("calprev", previous, sizeof(CalRecord)) == sizeof(CalRecord);
  ok = ok && prefs.putBytes("cal", &current, sizeof(CalRecord)) == sizeof(CalRecord);
  prefs.end();
  return ok;
}

void calibrationAddSample(const Vec3& a, const Vec3& g) {
  float am = ls::vmag(a);
  if (am < 0.8f || am > 1.2f) {
    cal.invalid++;
    return;
  }
  if (ls::vmag(g) > CAL_MOVING_GYRO_DPS) {
    cal.invalid = CAL_MAX_INVALID_SAMPLES + 1;  // obvious movement
    return;
  }
  float av[3] = {a.x, a.y, a.z}, gv[3] = {g.x, g.y, g.z};
  for (int i = 0; i < 3; i++) {
    cal.sa[i] += av[i];
    cal.sa2[i] += (double)av[i] * av[i];
    cal.sg[i] += gv[i];
    cal.sg2[i] += (double)gv[i] * gv[i];
  }
  cal.n++;
}

void calibrationEvaluate() {
  CalInfo& ci = calInfo[sessionNo % 2];
  uint32_t durMs = millis() - cal.collectStartMs;
  float n = cal.n;
  Vec3 meanA = {(float)(cal.sa[0] / n), (float)(cal.sa[1] / n), (float)(cal.sa[2] / n)};
  Vec3 meanG = {(float)(cal.sg[0] / n), (float)(cal.sg[1] / n), (float)(cal.sg[2] / n)};
  double varA = 0, varG = 0;
  for (int i = 0; i < 3; i++) {
    double ma = cal.sa[i] / n, mg = cal.sg[i] / n;
    varA += fmax(0.0, cal.sa2[i] / n - ma * ma);
    varG += fmax(0.0, cal.sg2[i] / n - mg * mg);
  }
  float accelStd = sqrtf((float)varA), gyroStd = sqrtf((float)varG);
  float gmag = ls::vmag(meanA);
  Serial.printf("[cal] |g|=%.4f g  accel std=%.4f g  gyro std=%.3f dps  invalid=%u\n", gmag, accelStd, gyroStd,
                cal.invalid);
  if (accelStd > CAL_MAX_ACCEL_STD_G || gyroStd > CAL_MAX_GYRO_STD_DPS) return calibrationFail("UNSTABLE_MOVEMENT");
  if (gmag < CAL_GRAVITY_MIN_G || gmag > CAL_GRAVITY_MAX_G) return calibrationFail("INVALID_GRAVITY_MAGNITUDE");
  if (fabsf(meanG.x) > CAL_MAX_GYRO_BIAS_DPS || fabsf(meanG.y) > CAL_MAX_GYRO_BIAS_DPS ||
      fabsf(meanG.z) > CAL_MAX_GYRO_BIAS_DPS)
    return calibrationFail("GYRO_BIAS_OUT_OF_RANGE");
  Vec3 g0;
  if (!ls::vnormalize(meanA, g0)) return calibrationFail("INVALID_GRAVITY_VECTOR");

  uint32_t vt, ve;
  vibReadTotals(vt, ve);
  float vibBg = durMs > 0 ? (ve - cal.vibEventsStart) * 1000.0f / durMs : NAN;

  // Compare with the previously saved baseline (diagnostic for movement that
  // happened while the device was off or between sessions).
  CalRecord prev;
  bool havePrev = false;
  if (prefs.begin("lsm4", true)) {
    havePrev = loadCalRecord("cal", prev);
    prefs.end();
  }
  CalRecord rec;
  memset(&rec, 0, sizeof(rec));
  rec.magic = CAL_MAGIC;
  rec.version = 1;
  rec.size = sizeof(CalRecord);
  rec.g0[0] = g0.x;
  rec.g0[1] = g0.y;
  rec.g0[2] = g0.z;
  rec.gyroBias[0] = meanG.x;
  rec.gyroBias[1] = meanG.y;
  rec.gyroBias[2] = meanG.z;
  rec.incl0 = ls::inclinationDeg(g0, MOUNT_AXIS, MOUNT_SIGN);
  rec.vibBg = vibBg;
  rec.accelStd = accelStd;
  rec.gyroStd = gyroStd;
  rec.bootCount = bootCount;
  rec.crc = ls::crc32(&rec, offsetof(CalRecord, crc));
  bool saved = saveCalRecords(rec, havePrev ? &prev : nullptr);

  // Lock the new baseline for this session.
  mpu.g0 = g0;
  mpu.gyroBias = meanG;
  mpu.incl0 = rec.incl0;
  mpu.gEst = g0;
  mpu.gEstValid = true;
  mpu.baselineValid = true;
  mpu.tiltSeries.clear();
  vib.bg = vibBg;
  vib.bgValid = ls::isFiniteF(vibBg);
  calStatus = CS_OK;
  cal.phase = CP_IDLE;

  ci.status = CS_OK;
  ci.attempts++;
  ci.error[0] = 0;
  if (!saved) strncpy(ci.error, "NVS_SAVE_FAILED", sizeof(ci.error) - 1);
  ci.completedUptimeMs = millis();
  ci.gravity0 = g0;
  ci.gyroBias = meanG;
  ci.incl0 = rec.incl0;
  ci.accelStd = accelStd;
  ci.gyroStd = gyroStd;
  ci.vibBg = vibBg;
  ci.soilRaw0 = soil.raw;
  ci.waterRaw0 = water.raw;
  ci.nvsSaved = saved;
  if (havePrev) {
    Vec3 pg = {prev.g0[0], prev.g0[1], prev.g0[2]};
    ci.prevIncl0 = prev.incl0;
    ci.prevShiftDeg = ls::vangleDeg(pg, g0);
  }

  char sid[16];
  sessionIdFor(sessionNo, sid, sizeof(sid));
  Serial.println("--------------------------------------------------");
  Serial.printf(" CALIBRATION OK  session %s\n", sid);
  Serial.printf(" Initial inclination: %.2f deg (mount %c%c)\n", rec.incl0, MOUNT_SIGN < 0 ? '-' : '+',
                "XYZ"[MOUNT_AXIS]);
  Serial.printf(" Gyro bias: %.3f %.3f %.3f dps\n", meanG.x, meanG.y, meanG.z);
  Serial.printf(" SW-420 background: %.3f events/s\n", vibBg);
  Serial.printf(" Soil ADC %d (%.1f %%), water ADC %d (%.1f %%)\n", soil.raw,
                ls::soilMoistureFromAdc(soil.filt) * 100.0f, water.raw, ls::waterWetnessFromAdc(water.filt) * 100.0f);
  if (havePrev) Serial.printf(" Shift from previous saved baseline: %.2f deg\n", ci.prevShiftDeg);
  Serial.printf(" NVS save: %s\n", saved ? "OK" : "FAILED (baseline used from RAM)");
  Serial.println(" Monitoring started; predictions follow once feature windows fill (~30 s).");
  Serial.println("--------------------------------------------------");
}

void calibrationStep() {
  uint32_t now = millis();
  switch (cal.phase) {
    case CP_IDLE:
      return;
    case CP_SETTLE:
      if (now - cal.phaseStartMs < CAL_SETTLE_MS) return;
      if (!mpu.present) {
        mpu.present = mpuInit();
        if (!mpu.present) return calibrationFail("MPU_NOT_DETECTED");
      }
      calibrationBeginCollect();
      return;
    case CP_COLLECT:
      if (!mpu.present) return calibrationFail("MPU_READ_FAILED");
      if (cal.invalid > CAL_MAX_INVALID_SAMPLES) return calibrationFail("MOVEMENT_OR_INVALID_ACCEL");
      if (cal.n >= CAL_SAMPLES) return calibrationEvaluate();
      if (now - cal.collectStartMs > CAL_COLLECT_TIMEOUT_MS) return calibrationFail("SAMPLE_TIMEOUT");
      return;
    case CP_RETRY_WAIT:
      if (now - cal.phaseStartMs >= CAL_RETRY_DELAY_MS) {
        cal.phase = CP_SETTLE;
        cal.phaseStartMs = now;
      }
      return;
  }
}

// ----------------------------------------------------------------------------
// Snapshot (1 Hz): features, diagnostics, histories
// ----------------------------------------------------------------------------
void enqueueReading(const Reading& r);

void snapshotSoil(float ts) {
  float winStd = soil.win.stddev();
  soil.win.reset();
  soil.dout = digitalRead(PIN_SOIL_DO);
  int doWet = soil.dout == SOIL_DO_WET_LEVEL ? 1 : 0;
  uint32_t satMs = soil.satSinceMs ? millis() - soil.satSinceMs : 0;
  ls::SoilQuality q = ls::classifySoil(soil.raw, soil.filt, winStd, satMs, soil.satNoiseless, doWet);
  soil.health = q.health;
  soil.flags = q.flags;
  if (q.health == ls::H_FAULT) {
    soil.moist = NAN;
    soil.series.clear();  // only the soil history is affected
  } else {
    soil.moist = ls::soilMoistureFromAdc(soil.filt);
    soil.series.add(ts, soil.moist);
  }
  soil.rate = soil.series.slopePerMin(ts, SOIL_WIN_S, SOIL_MIN_PTS, 9.0f);
  soil.mean30 = soil.series.mean(ts, SOIL_WIN_S, SOIL_MIN_PTS);
  soil.std30 = soil.series.stddev(ts, SOIL_WIN_S, SOIL_MIN_PTS);
}

void snapshotWater(float ts, float dtS) {
  float winStd = water.win.stddev();
  water.win.reset();
  ls::WaterQuality q = ls::classifyWater(water.raw, water.filt, winStd);
  water.health = q.health;
  water.flags = q.flags;
  if (q.health == ls::H_FAULT) {
    water.wet = NAN;
    water.series.clear();
    water.wetSeconds = 0;
    water.wetDurationValid = false;  // continuity broken: duration unknown
  } else {
    water.wet = ls::waterWetnessFromAdc(water.filt);
    water.series.add(ts, water.wet);
    if (water.wet >= ls::WATER_WET_THRESHOLD) {
      water.wetSeconds += dtS;
    } else {
      water.wetSeconds = 0;
      water.wetDurationValid = true;
    }
  }
  water.rate = water.series.slopePerMin(ts, WATER_WIN_S, WATER_MIN_PTS, 9.0f);
  water.mean30 = water.series.mean(ts, WATER_WIN_S, WATER_MIN_PTS);
}

void snapshotMpu(float ts) {
  if (!mpu.present || !mpu.gEstValid) {
    mpu.incl = mpu.tilt = mpu.inclChange = mpu.roll = mpu.pitch = mpu.tiltRate = NAN;
    mpu.tiltSeries.clear();
    return;
  }
  mpu.incl = ls::inclinationDeg(mpu.gEst, MOUNT_AXIS, MOUNT_SIGN);
  mpu.roll = ls::rollDeg(mpu.gEst);
  mpu.pitch = ls::pitchDeg(mpu.gEst);
  if (mpu.baselineValid) {
    mpu.tilt = ls::vangleDeg(mpu.g0, mpu.gEst);
    mpu.inclChange = mpu.incl - mpu.incl0;
    mpu.tiltSeries.add(ts, mpu.tilt);
    mpu.tiltRate = mpu.tiltSeries.slopePerMin(ts, TILT_RATE_WIN_S, TILT_RATE_MIN_PTS, 4.0f);
  } else {
    mpu.tilt = mpu.inclChange = mpu.tiltRate = NAN;
  }
}

void snapshotVibration() {
  uint32_t t, e;
  vibReadTotals(t, e);
  uint32_t de = e - vib.lastEvents, dt = t - vib.lastTransitions;
  vib.lastEvents = e;
  vib.lastTransitions = t;
  vib.bins[vib.binHead] = de > 65535 ? 65535 : (uint16_t)de;
  vib.binHead = (vib.binHead + 1) % VIB_BINS;
  if (vib.binsFilled < VIB_BINS) vib.binsFilled++;
  vib.intervalEvents += de;
  vib.intervalTransitions += dt;
}

Health mpuHealth() {
  if (!mpu.present) return ls::H_FAULT;
  if (!mpu.baselineValid) return ls::H_NOT_CALIBRATED;
  return ls::H_OK;
}

uint32_t currentFaults() {
  uint32_t f = 0;
  if (!mpu.present) f |= ls::F_MPU_NOT_DETECTED;
  if (mpu.flags & ls::MF_READ_ERRORS) f |= ls::F_MPU_READ_ERROR;
  if (calStatus == CS_FAILED) f |= ls::F_CALIBRATION_FAILED;
  if (soil.health == ls::H_FAULT) f |= ls::F_SOIL_FAULT;
  if (water.health == ls::H_FAULT) f |= ls::F_WATER_FAULT;
  CalInfo* ci = calInfoFor(sessionNo);
  if (ci && ci->status == CS_OK && !ci->nvsSaved) f |= ls::F_NVS_ERROR;
  portENTER_CRITICAL(&gMux);
  bool wifi = net.wifiConnected;
  bool reachable = net.lastResponseMs && millis() - net.lastResponseMs < BACKEND_REACHABLE_MS;
  bool auth = net.authRejected;
  portEXIT_CRITICAL(&gMux);
  if (strlen(WIFI_SSID) && !wifi) f |= ls::F_WIFI_DISCONNECTED;
  if (wifi && !reachable) f |= ls::F_BACKEND_UNREACHABLE;
  if (auth) f |= ls::F_BACKEND_AUTH_REJECTED;
  if (queueDropped) f |= ls::F_BUFFER_OVERFLOW;
  return f;
}

void buildReading(Reading& r) {
  memset(&r, 0, sizeof(r));
  uint32_t now = millis();
  r.seq = ++seqCounter;
  r.sessionNo = sessionNo;
  r.calStatus = calStatus;
  r.uptimeMs = now;
  r.epochMs = epochMsNow();
  portENTER_CRITICAL(&gMux);
  r.rssi = net.wifiConnected ? (int8_t)constrain(net.rssi, -127, 0) : RSSI_NONE;
  portEXIT_CRITICAL(&gMux);
  r.faults = currentFaults();
  r.dropped = queueDropped;

  r.soilRaw = soil.raw;
  r.soilFilt = soil.filt;
  r.soilDo = soil.dout;
  r.soilMoist = soil.moist;
  r.soilRate = soil.rate;
  r.soilMean = soil.mean30;
  r.soilStd = soil.std30;
  r.soilHealth = soil.health;
  r.soilFlags = soil.flags;

  r.waterRaw = water.raw;
  r.waterFilt = water.filt;
  r.waterWet = water.wet;
  r.waterWetS = water.wetDurationValid ? water.wetSeconds : NAN;
  r.waterRate = water.rate;
  r.waterMean = water.mean30;
  r.waterHealth = water.health;
  r.waterFlags = water.flags;

  bool m = mpu.present;
  r.ax = m ? mpu.accel.x : NAN;
  r.ay = m ? mpu.accel.y : NAN;
  r.az = m ? mpu.accel.z : NAN;
  r.gx = m ? mpu.gyroCorr.x : NAN;
  r.gy = m ? mpu.gyroCorr.y : NAN;
  r.gz = m ? mpu.gyroCorr.z : NAN;
  r.tempC = m ? mpu.tempC : NAN;
  r.incl0 = mpu.baselineValid ? mpu.incl0 : NAN;
  r.incl = mpu.incl;
  r.tilt = mpu.tilt;
  r.inclChange = mpu.inclChange;
  r.roll = mpu.roll;
  r.pitch = mpu.pitch;
  r.tiltRate = mpu.tiltRate;
  bool statsOk = m && mpu.accMagInterval.n >= 10;
  r.accMag = statsOk ? mpu.accMagInterval.meanf() : NAN;
  r.accDyn = statsOk ? mpu.accMagInterval.stddev() : NAN;
  r.gyroMag = (m && mpu.gyroN >= 10 && mpu.baselineValid) ? sqrtf((float)(mpu.gyroSqSum / mpu.gyroN)) : NAN;
  r.mpuHealth = mpuHealth();
  r.mpuFlags = mpu.flags | (mpu.baselineValid ? 0 : ls::MF_NO_BASELINE);
  mpu.accMagInterval.reset();
  mpu.gyroSqSum = 0;
  mpu.gyroN = 0;
  mpu.flags &= ~(ls::MF_READ_ERRORS | ls::MF_ACCEL_SATURATED);  // per-interval flags

  r.vibDo = digitalRead(PIN_VIB_DO);
  r.vibTransitions = vib.intervalTransitions;
  r.vibEvents = vib.intervalEvents;
  r.vibWindowMs = now - vib.intervalStartMs;
  r.vibEps = r.vibWindowMs ? r.vibEvents * 1000.0f / r.vibWindowMs : NAN;
  r.vibBg = vib.bgValid ? vib.bg : NAN;
  r.vibAbove = (vib.bgValid && ls::isFiniteF(r.vibEps)) ? fmaxf(0.0f, r.vibEps - vib.bg) : NAN;
  r.vibEv5 = vibWindowSum(5);
  r.vibEv10 = vibWindowSum(10);
  r.vibEv30 = vibWindowSum(30);
  r.vibTrend = (ls::isFiniteF(r.vibEv10) && ls::isFiniteF(r.vibEv30))
                   ? r.vibEv10 / 10.0f - (r.vibEv30 - r.vibEv10) / 20.0f
                   : NAN;
  vib.intervalEvents = 0;
  vib.intervalTransitions = 0;
  vib.intervalStartMs = now;
}

void snapshotStep() {
  uint32_t now = millis();
  if (now - lastSnapMs < SNAPSHOT_PERIOD_MS) return;
  float dtS = (now - lastSnapMs) / 1000.0f;
  lastSnapMs = (now - lastSnapMs > 3 * SNAPSHOT_PERIOD_MS) ? now : lastSnapMs + SNAPSHOT_PERIOD_MS;
  if (dtS > 3.0f) dtS = 1.0f;
  float ts = now / 1000.0f;

  snapshotSoil(ts);
  snapshotWater(ts, dtS);
  snapshotMpu(ts);
  snapshotVibration();

  if (++snapsSinceReading >= TELEMETRY_EVERY_N_SNAPSHOTS || forceReading) {
    snapsSinceReading = 0;
    forceReading = false;
    Reading r;
    buildReading(r);
    lastReading = r;
    haveLastReading = true;
    enqueueReading(r);
  }
}

// ----------------------------------------------------------------------------
// Offline queue (shared with the network task)
// ----------------------------------------------------------------------------
void enqueueReading(const Reading& r) {
  xSemaphoreTake(queueMutex, portMAX_DELAY);
  if (queueCount == QUEUE_CAP) {
    queueHead = (queueHead + 1) % QUEUE_CAP;  // drop oldest
    queueCount--;
    queueDropped++;
  }
  Reading& slot = queueItems[(queueHead + queueCount) % QUEUE_CAP];
  slot = r;
  slot.queued = queueCount;
  queueCount++;
  xSemaphoreGive(queueMutex);
}

// Newest never-attempted reading first (live), otherwise the oldest (backlog).
bool queuePickNext(Reading& out, bool& isLive) {
  bool found = false;
  xSemaphoreTake(queueMutex, portMAX_DELAY);
  if (queueCount > 0) {
    Reading& newest = queueItems[(queueHead + queueCount - 1) % QUEUE_CAP];
    if (newest.attempts == 0 && millis() - newest.uptimeMs <= PRED_FRESH_MS) {
      out = newest;
      isLive = true;
    } else {
      out = queueItems[queueHead];
      isLive = false;
    }
    out.queued = queueCount - 1;
    found = true;
  }
  xSemaphoreGive(queueMutex);
  return found;
}

void queueRemoveSeq(uint32_t seq) {
  xSemaphoreTake(queueMutex, portMAX_DELAY);
  for (int i = 0; i < queueCount; i++) {
    int idx = (queueHead + i) % QUEUE_CAP;
    if (queueItems[idx].seq != seq) continue;
    for (int j = i; j < queueCount - 1; j++)
      queueItems[(queueHead + j) % QUEUE_CAP] = queueItems[(queueHead + j + 1) % QUEUE_CAP];
    queueCount--;
    break;
  }
  xSemaphoreGive(queueMutex);
}

void queueMarkAttempt(uint32_t seq) {
  xSemaphoreTake(queueMutex, portMAX_DELAY);
  for (int i = 0; i < queueCount; i++) {
    int idx = (queueHead + i) % QUEUE_CAP;
    if (queueItems[idx].seq == seq) {
      if (queueItems[idx].attempts < 255) queueItems[idx].attempts++;
      break;
    }
  }
  xSemaphoreGive(queueMutex);
}

int queueSize() {
  xSemaphoreTake(queueMutex, portMAX_DELAY);
  int n = queueCount;
  xSemaphoreGive(queueMutex);
  return n;
}

// ----------------------------------------------------------------------------
// Telemetry JSON
// ----------------------------------------------------------------------------
void writeVec(ls::JsonWriter& w, const char* key, const Vec3& v, int digits) {
  w.beginArray(key);
  w.num(nullptr, v.x, digits);
  w.num(nullptr, v.y, digits);
  w.num(nullptr, v.z, digits);
  w.endArray();
}

void writeOptCount(ls::JsonWriter& w, const char* key, float v) {
  if (ls::isFiniteF(v)) w.integer(key, (long long)v);
  else w.null(key);
}

bool buildTelemetryJson(const Reading& r, bool replay, char* buf, size_t cap) {
  ls::JsonWriter w(buf, cap);
  char sid[16], rid[32];
  sessionIdFor(r.sessionNo, sid, sizeof(sid));
  readingIdFor(r, rid, sizeof(rid));
  w.beginObject();
  w.str("schema", TELEMETRY_SCHEMA);
  w.str("feature_schema", FEATURE_SCHEMA);
  w.str("origin", "device");
  w.str("device_id", DEVICE_ID);
  w.str("fw", FW_VERSION);
  w.str("boot_id", bootId);
  w.str("session_id", sid);
  w.str("reading_id", rid);
  w.integer("seq", r.seq);
  w.integer("uptime_ms", r.uptimeMs);
  if (r.epochMs > 0) w.integer("epoch_ms", r.epochMs);
  else w.null("epoch_ms");
  w.boolean("replay", replay);
  w.str("op_state", opStateName(r.calStatus));
  if (r.rssi == RSSI_NONE) w.null("rssi");
  else w.integer("rssi", r.rssi);
  w.flags("faults", r.faults, ls::FAULT_NAMES, sizeof(ls::FAULT_NAMES) / sizeof(ls::FAULT_NAMES[0]));
  w.beginObject("buffer");
  w.integer("queued", r.queued);
  w.integer("dropped", r.dropped);
  w.endObject();

  CalInfo* ci = calInfoFor(r.sessionNo);
  if (ci) {
    w.beginObject("cal");
    w.str("status", calStatusName(ci->status));
    w.str("trigger", ci->trigger);
    if (ci->error[0]) w.str("error", ci->error);
    else w.null("error");
    w.integer("attempts", ci->attempts);
    if (ci->status == CS_OK) {
      w.integer("completed_uptime_ms", ci->completedUptimeMs);
      writeVec(w, "gravity0", ci->gravity0, 5);
      writeVec(w, "gyro_bias_dps", ci->gyroBias, 3);
      w.num("incl0_deg", ci->incl0, 3);
      w.num("accel_std_g", ci->accelStd, 5);
      w.num("gyro_std_dps", ci->gyroStd, 4);
      w.num("vib_bg_eps", ci->vibBg, 4);
      w.integer("soil_raw0", ci->soilRaw0);
      w.integer("water_raw0", ci->waterRaw0);
      w.num("prev_incl0_deg", ci->prevIncl0, 3);
      w.num("prev_shift_deg", ci->prevShiftDeg, 3);
      w.boolean("nvs_saved", ci->nvsSaved);
    }
    char mount[3] = {MOUNT_SIGN < 0 ? '-' : '+', "XYZ"[MOUNT_AXIS], 0};
    w.str("mount_axis", mount);
    w.integer("samples", CAL_SAMPLES);
    w.endObject();
  } else {
    w.null("cal");
  }

  w.beginObject("soil");
  w.integer("raw", r.soilRaw);
  w.num("filt", r.soilFilt, 1);
  w.integer("do", r.soilDo);
  w.num("moisture", r.soilMoist, 4);
  w.num("pct", ls::isFiniteF(r.soilMoist) ? r.soilMoist * 100.0f : NAN, 2);
  w.num("rate_per_min", r.soilRate, 5);
  w.num("mean_30s", r.soilMean, 4);
  w.num("std_30s", r.soilStd, 5);
  w.str("health", ls::healthName((Health)r.soilHealth));
  w.flags("flags", r.soilFlags, ls::SOIL_FLAG_NAMES, sizeof(ls::SOIL_FLAG_NAMES) / sizeof(ls::SOIL_FLAG_NAMES[0]));
  w.endObject();

  w.beginObject("water");
  w.integer("raw", r.waterRaw);
  w.num("filt", r.waterFilt, 1);
  w.num("wetness", r.waterWet, 4);
  w.num("pct", ls::isFiniteF(r.waterWet) ? r.waterWet * 100.0f : NAN, 2);
  w.num("wet_s", r.waterWetS, 1);
  w.num("rate_per_min", r.waterRate, 5);
  w.num("mean_30s", r.waterMean, 4);
  w.str("health", ls::healthName((Health)r.waterHealth));
  w.flags("flags", r.waterFlags, ls::WATER_FLAG_NAMES, sizeof(ls::WATER_FLAG_NAMES) / sizeof(ls::WATER_FLAG_NAMES[0]));
  w.endObject();

  w.beginObject("mpu");
  w.num("ax", r.ax, 4);
  w.num("ay", r.ay, 4);
  w.num("az", r.az, 4);
  w.num("gx", r.gx, 3);
  w.num("gy", r.gy, 3);
  w.num("gz", r.gz, 3);
  w.num("incl0_deg", r.incl0, 3);
  w.num("incl_deg", r.incl, 3);
  w.num("tilt_deg", r.tilt, 3);
  w.num("incl_change_deg", r.inclChange, 3);
  w.num("roll_deg", r.roll, 2);
  w.num("pitch_deg", r.pitch, 2);
  w.num("tilt_rate_dpm", r.tiltRate, 3);
  w.num("acc_mag_g", r.accMag, 4);
  w.num("acc_dyn_g", r.accDyn, 5);
  w.num("gyro_mag_dps", r.gyroMag, 3);
  w.num("temp_c", r.tempC, 1);
  w.str("health", ls::healthName((Health)r.mpuHealth));
  w.flags("flags", r.mpuFlags, ls::MPU_FLAG_NAMES, sizeof(ls::MPU_FLAG_NAMES) / sizeof(ls::MPU_FLAG_NAMES[0]));
  w.endObject();

  w.beginObject("vib");
  w.integer("do", r.vibDo);
  w.integer("transitions", r.vibTransitions);
  w.integer("events", r.vibEvents);
  w.integer("window_ms", r.vibWindowMs);
  w.num("eps", r.vibEps, 3);
  w.num("bg_eps", r.vibBg, 4);
  w.num("above_bg_eps", r.vibAbove, 3);
  writeOptCount(w, "ev_5s", r.vibEv5);
  writeOptCount(w, "ev_10s", r.vibEv10);
  writeOptCount(w, "ev_30s", r.vibEv30);
  w.num("trend_eps", r.vibTrend, 3);
  w.str("health", "OK");  // a digital output alone cannot prove connectivity
  w.endObject();

  w.endObject();
  return w.ok();
}

// ----------------------------------------------------------------------------
// Network task (core 0): Wi-Fi, HTTP, response validation
// ----------------------------------------------------------------------------
static char netJson[2304];
static char netResp[1024];

int postJson(const char* json, size_t len, char* resp, size_t respCap) {
  char url[112];
  snprintf(url, sizeof(url), "http://%s:%u/api/readings", API_HOST, (unsigned)API_PORT);
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(HTTP_CONNECT_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setReuse(false);
  resp[0] = 0;
  if (!http.begin(client, url)) return -100;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Id", DEVICE_ID);
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  int code = http.POST((uint8_t*)json, len);
  if (code > 0) {
    String body = http.getString();
    strncpy(resp, body.c_str(), respCap - 1);
    resp[respCap - 1] = 0;
  }
  http.end();
  return code;
}

void handleResponse(const Reading& r, bool isLive, const char* body) {
  char sid[16], rid[32];
  sessionIdFor(r.sessionNo, sid, sizeof(sid));
  readingIdFor(r, rid, sizeof(rid));
  uint32_t age = millis() - r.uptimeMs;
  ls::ParsedPrediction p;
  ls::PredCheck c = ls::validatePrediction(body, DEVICE_ID, sid, rid, age, PRED_FRESH_MS, p);

  portENTER_CRITICAL(&gMux);
  strncpy(net.lastCheck, ls::predCheckName(c), sizeof(net.lastCheck) - 1);
  uint16_t currentSession = sessionNo;
  bool forCurrent = isLive && r.sessionNo == currentSession;
  if (forCurrent && (c == ls::PC_VALID || c == ls::PC_UNAVAILABLE || c == ls::PC_STALE)) {
    strncpy(net.backendStatus, p.status, sizeof(net.backendStatus) - 1);
    strncpy(net.backendReason, p.reason, sizeof(net.backendReason) - 1);
    net.backendStatusSession = r.sessionNo;
    net.backendSensorFault = strcmp(p.status, "SENSOR_FAULT") == 0;
  }
  bool changed = false;
  Risk prevRisk = pred.risk;
  if (forCurrent && c == ls::PC_VALID) {
    changed = !pred.valid || prevRisk != p.risk;
    pred.valid = true;
    pred.sessionNo = r.sessionNo;
    pred.risk = p.risk;
    pred.confidence = p.confidence;
    strncpy(pred.model, p.modelVersion, sizeof(pred.model) - 1);
    pred.model[sizeof(pred.model) - 1] = 0;
    pred.readingUptimeMs = r.uptimeMs;
    pred.receivedMs = millis();
    pred.readingSeq = r.seq;
  }
  portEXIT_CRITICAL(&gMux);

  if (changed) Serial.printf("[ml] %s (confidence %.2f, model %s)\n", ls::riskName(p.risk), p.confidence, p.modelVersion);
  if (isLive && c != ls::PC_VALID && c != ls::PC_UNAVAILABLE)
    Serial.printf("[net] prediction response rejected: %s (reading age %lu ms)\n", ls::predCheckName(c), (unsigned long)age);
}

void netTask(void*) {
  esp_task_wdt_add(nullptr);
  bool wifiEnabled = strlen(WIFI_SSID) > 0 && strlen(API_HOST) > 0;
  uint32_t lastWifiAttempt = 0, wifiRetry = WIFI_RETRY_MIN_MS;
  uint32_t backoffUntil = 0, backoff = POST_BACKOFF_MIN_MS;
  bool wasConnected = false, wasReachable = true;
  if (wifiEnabled) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWifiAttempt = millis();
  }
  for (;;) {
    esp_task_wdt_reset();
    if (!wifiEnabled) {
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    bool connected = WiFi.status() == WL_CONNECTED;
    if (connected && !wasConnected) {
      Serial.printf("[wifi] connected, IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      configTime(0, 0, "pool.ntp.org", "time.google.com");
      wifiRetry = WIFI_RETRY_MIN_MS;
    } else if (!connected && wasConnected) {
      Serial.println("[wifi] disconnected; readings are buffered");
    }
    wasConnected = connected;
    portENTER_CRITICAL(&gMux);
    net.wifiConnected = connected;
    portEXIT_CRITICAL(&gMux);
    if (connected) {
      int rssi = WiFi.RSSI();
      String ip = WiFi.localIP().toString();
      portENTER_CRITICAL(&gMux);
      net.rssi = rssi;
      strncpy(net.ip, ip.c_str(), sizeof(net.ip) - 1);
      portEXIT_CRITICAL(&gMux);
    }

    if (!connected) {
      if (millis() - lastWifiAttempt >= wifiRetry) {
        WiFi.disconnect();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        lastWifiAttempt = millis();
        wifiRetry = min(wifiRetry * 2, WIFI_RETRY_MAX_MS);
      }
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }

    Reading r;
    bool isLive = false;
    if ((int32_t)(millis() - backoffUntil) < 0 || !queuePickNext(r, isLive)) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    // Never present backlog as a fresh reading or let it drive alerts.
    if (millis() - r.uptimeMs > PRED_FRESH_MS) isLive = false;
    bool replay = !isLive || r.attempts > 0;
    if (!buildTelemetryJson(r, replay, netJson, sizeof(netJson))) {
      Serial.println("[net] JSON buffer overflow; reading dropped");
      queueRemoveSeq(r.seq);
      continue;
    }
    int code = postJson(netJson, strlen(netJson), netResp, sizeof(netResp));
    portENTER_CRITICAL(&gMux);
    net.lastHttp = code;
    if (code > 0) net.lastResponseMs = millis();
    portEXIT_CRITICAL(&gMux);

    if (code >= 200 && code < 300) {
      queueRemoveSeq(r.seq);
      backoff = POST_BACKOFF_MIN_MS;
      portENTER_CRITICAL(&gMux);
      net.okCount++;
      net.authRejected = false;
      portEXIT_CRITICAL(&gMux);
      handleResponse(r, isLive, netResp);
      if (!wasReachable) Serial.println("[net] backend reachable again");
      wasReachable = true;
    } else if (code == 401 || code == 403) {
      // Credentials problem: keep the data, retry slowly.
      queueMarkAttempt(r.seq);
      portENTER_CRITICAL(&gMux);
      net.authRejected = true;
      net.failCount++;
      portEXIT_CRITICAL(&gMux);
      Serial.printf("[net] HTTP %d: device token rejected. Check DEVICE_TOKEN in secrets.h\n", code);
      backoffUntil = millis() + AUTH_BACKOFF_MS;
    } else if (code == 400 || code == 409 || code == 413 || code == 422) {
      // The backend can never accept this record: drop it so it cannot block the queue.
      queueRemoveSeq(r.seq);
      portENTER_CRITICAL(&gMux);
      net.rejectCount++;
      portEXIT_CRITICAL(&gMux);
      Serial.printf("[net] HTTP %d: reading %lu rejected by backend: %.200s\n", code, (unsigned long)r.seq, netResp);
    } else {
      queueMarkAttempt(r.seq);
      portENTER_CRITICAL(&gMux);
      net.failCount++;
      portEXIT_CRITICAL(&gMux);
      if (wasReachable) {
        if (code < 0) Serial.printf("[net] backend unreachable (%s); buffering\n", HTTPClient::errorToString(code).c_str());
        else Serial.printf("[net] backend HTTP %d; buffering\n", code);
      }
      wasReachable = false;
      backoffUntil = millis() + backoff;
      backoff = min(backoff * 2, POST_BACKOFF_MAX_MS);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// ----------------------------------------------------------------------------
// LEDs and buzzers (non-blocking)
// ----------------------------------------------------------------------------
void setOutput(int idx, int pin, bool on) {
  if (outState[idx] == on) return;
  outState[idx] = on;
  digitalWrite(pin, on ? HIGH : LOW);
}

void applyOutputs(const ls::Outputs& o) {
  setOutput(0, PIN_LED_GREEN, o.green);
  setOutput(1, PIN_LED_BLUE, o.blue);
  setOutput(2, PIN_LED_YELLOW, o.yellow);
  setOutput(3, PIN_LED_RED, o.red);
  setOutput(4, PIN_BUZZER1, o.buzzer1);
  setOutput(5, PIN_BUZZER2, o.buzzer2);
}

ls::IndicationInput indicationInput() {
  ls::IndicationInput in;
  memset(&in, 0, sizeof(in));
  in.calibrating = calStatus == CS_IN_PROGRESS;
  in.localSensorFault = calStatus == CS_FAILED || soil.health == ls::H_FAULT || water.health == ls::H_FAULT ||
                        (calStatus == CS_OK && !mpu.present);
  in.freshMs = PRED_FRESH_MS;
  portENTER_CRITICAL(&gMux);
  in.backendReachable = net.wifiConnected && net.lastResponseMs && millis() - net.lastResponseMs < BACKEND_REACHABLE_MS &&
                        !net.authRejected;
  in.backendSensorFault = net.backendSensorFault && net.backendStatusSession == sessionNo;
  in.havePrediction = pred.valid && pred.sessionNo == sessionNo;
  in.predictionAgeMs = millis() - pred.readingUptimeMs;
  in.predictedRisk = pred.risk;
  portEXIT_CRITICAL(&gMux);
  return in;
}

void updateIndicators() {
  uint32_t now = millis();
  if (testActive) {
    uint32_t t = now - testStartMs;
    ls::Outputs o = {false, false, false, false, false, false};
    if (t < 500) o.green = true;
    else if (t < 1000) o.blue = true;
    else if (t < 1500) o.yellow = true;
    else if (t < 2000) o.red = true;
    else if (t < 2400) o.buzzer1 = true;
    else if (t >= 2600 && t < 3000) o.buzzer2 = true;
    else if (t >= 3200) {
      testActive = false;
      Serial.println("[test] LED/buzzer test finished");
    }
    applyOutputs(o);
    return;
  }
  ls::IndicationInput in = indicationInput();
  ls::Indication ind = ls::selectIndication(in);
  if (ind != lastIndication) {
    Serial.printf("[alert] indication: %s%s%s\n", ls::indicationName(ind),
                  (ind == ls::IND_RISK || ind == ls::IND_STALE_ALERT) ? " " : "",
                  (ind == ls::IND_RISK || ind == ls::IND_STALE_ALERT) ? ls::riskName(in.predictedRisk) : "");
    lastIndication = ind;
  }
  applyOutputs(ls::indicationOutputs(ind, in.predictedRisk, now));
}

// ----------------------------------------------------------------------------
// Serial commands
// ----------------------------------------------------------------------------
void printHelp() {
  Serial.println("Commands:");
  Serial.println("  demo   - recalibrate all sensors and start a new session");
  Serial.println("  status - sensor readings, calibration, health and latest ML result");
  Serial.println("  raw    - raw sensor measurements");
  Serial.println("  test   - test I2C, LEDs and buzzers");
  Serial.println("  wifi   - network diagnostics");
  Serial.println("  json   - latest combined telemetry payload");
  Serial.println("  send   - transmit a snapshot immediately");
  Serial.println("  help   - this list");
}

void printStatus() {
  char sid[16];
  sessionIdFor(sessionNo, sid, sizeof(sid));
  CalInfo* ci = calInfoFor(sessionNo);
  Serial.println("------------------------- STATUS -------------------------");
  Serial.printf("Firmware %s | device %s | boot %s (#%lu) | session %s | uptime %lus\n", FW_VERSION, DEVICE_ID, bootId,
                (unsigned long)bootCount, sid, (unsigned long)(millis() / 1000));
  Serial.printf("State %s | calibration %s", opStateName(calStatus), ci ? calStatusName(ci->status) : "-");
  if (ci && ci->error[0]) Serial.printf(" (%s)", ci->error);
  Serial.println();
  Serial.printf("Soil  : raw %d filt %.0f DO %d | moisture %.1f %% | rate %.4f /min | %s\n", soil.raw, soil.filt, soil.dout,
                soil.moist * 100.0f, soil.rate, ls::healthName(soil.health));
  Serial.printf("Water : raw %d filt %.0f | wetness %.1f %% | wet for %.0f s | %s\n", water.raw, water.filt,
                water.wet * 100.0f, water.wetSeconds, ls::healthName(water.health));
  Serial.printf("MPU   : %s | incl0 %.2f incl %.2f | tilt %.3f deg | rate %.3f deg/min | %.1f C\n",
                ls::healthName(mpuHealth()), mpu.incl0, mpu.incl, mpu.tilt, mpu.tiltRate, mpu.tempC);
  Serial.printf("SW-420: 5s %.0f 10s %.0f 30s %.0f events | background %.3f ev/s\n", vibWindowSum(5),
                vibWindowSum(10), vibWindowSum(30), vib.bg);
  ls::IndicationInput in = indicationInput();
  portENTER_CRITICAL(&gMux);
  PredState p = pred;
  char bs[24], br[64];
  strncpy(bs, net.backendStatus, sizeof(bs));
  strncpy(br, net.backendReason, sizeof(br));
  portEXIT_CRITICAL(&gMux);
  if (in.havePrediction)
    Serial.printf("ML    : %s confidence %.2f model %s | age %lus%s\n", ls::riskName(p.risk), p.confidence, p.model,
                  (unsigned long)(in.predictionAgeMs / 1000), in.predictionAgeMs > PRED_FRESH_MS ? " (STALE)" : "");
  else
    Serial.printf("ML    : no prediction for this session (%s %s)\n", bs[0] ? bs : "waiting", br);
  Serial.printf("Alert : %s | queue %d | dropped %lu\n", ls::indicationName(ls::selectIndication(in)), queueSize(),
                (unsigned long)queueDropped);
  Serial.println("-----------------------------------------------------------");
}

void printRaw() {
  Vec3 a = mpu.accel, g = mpu.gyroRaw;
  Serial.printf("soil_ao=%d soil_do=%d water_s=%d vib_do=%d\n", analogRead(PIN_SOIL_AO), digitalRead(PIN_SOIL_DO),
                analogRead(PIN_WATER_S), digitalRead(PIN_VIB_DO));
  if (mpu.present)
    Serial.printf("accel g: %.4f %.4f %.4f |a|=%.4f  gyro dps: %.3f %.3f %.3f  temp %.1f C\n", a.x, a.y, a.z,
                  ls::vmag(a), g.x, g.y, g.z, mpu.tempC);
  else
    Serial.println("MPU6050 not detected");
  uint32_t t, e;
  vibReadTotals(t, e);
  Serial.printf("sw420 totals since boot: transitions=%lu events=%lu\n", (unsigned long)t, (unsigned long)e);
}

void printWifi() {
  portENTER_CRITICAL(&gMux);
  NetState n = net;
  portEXIT_CRITICAL(&gMux);
  Serial.printf("SSID '%s' | %s | IP %s | RSSI %d dBm\n", WIFI_SSID, n.wifiConnected ? "connected" : "NOT connected",
                n.wifiConnected ? n.ip : "-", n.wifiConnected ? n.rssi : 0);
  Serial.printf("Backend http://%s:%u/api/readings | token %s\n", API_HOST, (unsigned)API_PORT,
                strlen(DEVICE_TOKEN) ? "set" : "NOT SET");
  Serial.printf("Last HTTP %d (%s) | ok %lu fail %lu rejected %lu | last response %lus ago | queue %d\n", n.lastHttp,
                n.lastHttp < 0 ? HTTPClient::errorToString(n.lastHttp).c_str() : "-", (unsigned long)n.okCount,
                (unsigned long)n.failCount, (unsigned long)n.rejectCount,
                n.lastResponseMs ? (unsigned long)((millis() - n.lastResponseMs) / 1000) : 0UL, queueSize());
  Serial.printf("Last prediction check: %s | backend status %s %s\n", n.lastCheck[0] ? n.lastCheck : "-",
                n.backendStatus[0] ? n.backendStatus : "-", n.backendReason);
  if (!strlen(WIFI_SSID)) Serial.println("Wi-Fi disabled: create secrets.h from secrets.example.h");
}

void hardwareTest() {
  Serial.println("[test] I2C scan on SDA 25 / SCL 26:");
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  device at 0x%02X%s\n", a, (a == 0x68 || a == 0x69) ? " (MPU6050)" : "");
      found++;
    }
  }
  if (!found) Serial.println("  no I2C devices found");
  printRaw();
  Serial.println("[test] LEDs green, blue, yellow, red; then buzzer 1, buzzer 2");
  testActive = true;
  testStartMs = millis();
}

void handleCommand(const char* cmd) {
  if (!strcmp(cmd, "demo")) startCalibration("demo");
  else if (!strcmp(cmd, "status")) printStatus();
  else if (!strcmp(cmd, "raw")) printRaw();
  else if (!strcmp(cmd, "test")) hardwareTest();
  else if (!strcmp(cmd, "wifi")) printWifi();
  else if (!strcmp(cmd, "json")) {
    static char buf[2304];
    if (!haveLastReading) Serial.println("No reading yet.");
    else if (buildTelemetryJson(lastReading, false, buf, sizeof(buf))) Serial.println(buf);
    else Serial.println("JSON buffer overflow");
  } else if (!strcmp(cmd, "send")) {
    forceReading = true;
    Serial.println("A reading will be queued at the next snapshot (<1 s).");
  } else if (!strcmp(cmd, "help") || !strcmp(cmd, "?")) printHelp();
  else if (cmd[0]) Serial.printf("Unknown command '%s'. Type 'help'.\n", cmd);
}

void serialStep() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r' || c == '\n') {
      serialLine[serialLen] = 0;
      // trim + lowercase
      char* s = serialLine;
      while (*s == ' ') s++;
      for (char* p = s; *p; p++) *p = (char)tolower(*p);
      size_t n = strlen(s);
      while (n && s[n - 1] == ' ') s[--n] = 0;
      if (n) handleCommand(s);
      serialLen = 0;
    } else if (serialLen < sizeof(serialLine) - 1) {
      serialLine[serialLen++] = c;
    }
  }
}

// ----------------------------------------------------------------------------
// Setup / loop
// ----------------------------------------------------------------------------
void setup() {
  // GPIO 12 is a strapping pin: drive it low as early as possible.
  pinMode(PIN_BUZZER1, OUTPUT);
  digitalWrite(PIN_BUZZER1, LOW);
  pinMode(PIN_BUZZER2, OUTPUT);
  digitalWrite(PIN_BUZZER2, LOW);
  const int leds[] = {PIN_LED_GREEN, PIN_LED_BLUE, PIN_LED_YELLOW, PIN_LED_RED};
  for (int p : leds) {
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }

  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.printf("Landslide monitor firmware %s (Arduino-ESP32 core %d.x)\n", FW_VERSION, ESP_ARDUINO_VERSION_MAJOR);

  pinMode(PIN_SOIL_DO, INPUT);
  pinMode(PIN_VIB_DO, INPUT);
  pinMode(PIN_SOIL_AO, INPUT);
  pinMode(PIN_WATER_S, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_SOIL_AO, ADC_11db);
  analogSetPinAttenuation(PIN_WATER_S, ADC_11db);

  queueMutex = xSemaphoreCreateMutex();
  memset(&pred, 0, sizeof(pred));
  memset(&net, 0, sizeof(net));
  memset(calInfo, 0, sizeof(calInfo));
  memset(&mpu, 0, sizeof(mpu));
  memset(&cal, 0, sizeof(cal));
  soil.filt = NAN;
  water.filt = NAN;
  soil.win.reset();
  water.win.reset();
  mpu.accMagInterval.reset();
  mpu.incl0 = mpu.incl = mpu.tilt = mpu.inclChange = mpu.roll = mpu.pitch = mpu.tiltRate = mpu.tempC = NAN;
  vib.bg = NAN;

  snprintf(bootId, sizeof(bootId), "%08lx", (unsigned long)esp_random());
  if (prefs.begin("lsm4", false)) {
    bootCount = prefs.getUInt("boots", 0) + 1;
    prefs.putUInt("boots", bootCount);
    CalRecord rec;
    if (loadCalRecord("cal", rec)) {
      // Only the gyro bias is reused before calibration finishes (for the
      // orientation filter). The saved baseline is NOT used for tilt.
      mpu.gyroBias = {rec.gyroBias[0], rec.gyroBias[1], rec.gyroBias[2]};
      Serial.printf("Saved baseline from boot #%lu: inclination %.2f deg (diagnostic only)\n",
                    (unsigned long)rec.bootCount, rec.incl0);
    }
    prefs.end();
  }
  Serial.printf("Boot id %s (#%lu), reset reason %d\n", bootId, (unsigned long)bootCount, (int)esp_reset_reason());

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  Wire.setTimeOut(20);
  mpu.present = mpuInit();
  if (!mpu.present) Serial.println("[mpu] MPU6050 not detected at 0x68/0x69");
  mpu.lastSampleUs = micros();

  vibReadTotals(vib.lastTransitions, vib.lastEvents);
  attachInterrupt(digitalPinToInterrupt(PIN_VIB_DO), vibrationIsr, CHANGE);

  wdtSetup();
  esp_task_wdt_add(nullptr);
  xTaskCreatePinnedToCore(netTask, "net", 8192, nullptr, 1, nullptr, 0);

  if (!strlen(WIFI_SSID)) Serial.println("Wi-Fi disabled (no secrets.h). Sensors and diagnostics still run.");
  lastSnapMs = millis();
  lastAdcMs = millis() - ADC_PERIOD_MS;
  startCalibration("boot");
  printHelp();
}

void loop() {
  esp_task_wdt_reset();
  serialStep();
  mpuSample();
  adcSample();
  calibrationStep();
  snapshotStep();
  updateIndicators();

  // The MPU6050 appeared afterA a calibration that failed because it was
  // missing: retry automatically (bounded). Other failures wait for `demo`.
  CalInfo* ci = calInfoFor(sessionNo);
  bool failedForMissingMpu =
      ci && (!strcmp(ci->error, "MPU_NOT_DETECTED") || !strcmp(ci->error, "MPU_READ_FAILED"));
  if (calStatus == CS_FAILED && failedForMissingMpu && mpu.present && autoRecalCount < MAX_AUTO_RECAL) {
    autoRecalCount++;
    Serial.println("[cal] MPU6050 available again; retrying calibration automatically");
    startCalibration("mpu_return");
  }
  delay(1);
}
