// ls_logic.h - hardware-independent logic for the landslide monitor firmware.
//
// Everything in this header is plain C++ (no Arduino headers) so it can be
// compiled and unit-tested on a PC (tests/firmware_host/). Keeping these types
// in a header also avoids the Arduino IDE's automatic prototype generator
// placing function prototypes before user-defined struct declarations.
#pragma once

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace ls {

// ---------------------------------------------------------------------------
// Fixed sensor calibration endpoints (measured during initial hardware tests).
// These are NOT recalculated at runtime: startup calibration only establishes
// dynamic baselines (MPU6050 orientation, SW-420 background activity).
// ---------------------------------------------------------------------------
static const int SOIL_DRY_ADC = 4090;
static const int SOIL_WET_ADC = 921;
static const int WATER_DRY_ADC = 0;
static const int WATER_WET_ADC = 1725;
static const int ADC_MAX = 4095;

// Soil ADC quality thresholds (12-bit counts)
static const int SOIL_SAT_THRESHOLD = 4080;          // at/above: dry-end saturation
static const int SOIL_LOW_RAIL_THRESHOLD = 300;      // below: electrically implausible
static const float ADC_UNSTABLE_STD = 300.0f;        // 1-s window std => floating input
static const uint32_t SOIL_PERSISTENT_SAT_MS = 600000UL;  // 10 min of noiseless saturation
// Water ADC quality thresholds
static const int WATER_HIGH_RAIL_THRESHOLD = 4080;   // sensor max observed ~1725
static const int WATER_ABOVE_RANGE_THRESHOLD = 2300; // well beyond wet endpoint

static const float WATER_WET_THRESHOLD = 0.10f;      // wetness counted as "wet exposure"

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------
enum Risk : uint8_t { RISK_NONE = 0, RISK_SAFE, RISK_WATCH, RISK_WARNING, RISK_CRITICAL };

inline const char* riskName(Risk r) {
  switch (r) {
    case RISK_SAFE: return "SAFE";
    case RISK_WATCH: return "WATCH";
    case RISK_WARNING: return "WARNING";
    case RISK_CRITICAL: return "CRITICAL";
    default: return "NONE";
  }
}

inline Risk riskFromName(const char* s) {
  if (!s) return RISK_NONE;
  if (strcmp(s, "SAFE") == 0) return RISK_SAFE;
  if (strcmp(s, "WATCH") == 0) return RISK_WATCH;
  if (strcmp(s, "WARNING") == 0) return RISK_WARNING;
  if (strcmp(s, "CRITICAL") == 0) return RISK_CRITICAL;
  return RISK_NONE;
}

enum Health : uint8_t { H_OK = 0, H_SUSPECT, H_FAULT, H_NOT_CALIBRATED };

inline const char* healthName(Health h) {
  switch (h) {
    case H_OK: return "OK";
    case H_SUSPECT: return "SUSPECT";
    case H_FAULT: return "FAULT";
    default: return "NOT_CALIBRATED";
  }
}

// Sensor data-quality flags (bitmask, per sensor)
enum SoilFlag : uint16_t {
  SF_DRY_SATURATION = 1 << 0,        // ADC at dry rail; moisture clamped to 0
  SF_PERSISTENT_SATURATION = 1 << 1, // noiseless saturation for a long time
  SF_DO_DISAGREES = 1 << 2,          // digital comparator says wet while ADC says dry
  SF_ABOVE_DRY_ENDPOINT = 1 << 3,    // raw > dry endpoint (clamped)
  SF_BELOW_WET_ENDPOINT = 1 << 4,    // raw < wet endpoint (clamped)
  SF_LOW_RAIL = 1 << 5,              // fault: near 0 V
  SF_UNSTABLE = 1 << 6,              // fault: very noisy (floating input?)
};

enum WaterFlag : uint16_t {
  WF_AT_DRY_ENDPOINT = 1 << 0,       // raw 0: dry, but connectivity cannot be verified
  WF_ABOVE_WET_ENDPOINT = 1 << 1,    // raw > wet endpoint (clamped to 1)
  WF_ABOVE_RANGE = 1 << 2,           // raw far above observed sensor range
  WF_HIGH_RAIL = 1 << 3,             // fault: at 3.3 V rail
  WF_UNSTABLE = 1 << 4,              // fault: very noisy
};

enum MpuFlag : uint16_t {
  MF_ACCEL_SATURATED = 1 << 0,       // an axis reached the +-2 g limit
  MF_RECONNECTED = 1 << 1,           // sensor re-initialised during this session
  MF_NO_BASELINE = 1 << 2,           // no valid baseline in this session
  MF_READ_ERRORS = 1 << 3,           // recent I2C read errors
};

// Device-level faults (bitmask)
enum Fault : uint32_t {
  F_MPU_NOT_DETECTED = 1UL << 0,
  F_MPU_READ_ERROR = 1UL << 1,
  F_CALIBRATION_FAILED = 1UL << 2,
  F_SOIL_FAULT = 1UL << 3,
  F_WATER_FAULT = 1UL << 4,
  F_WIFI_DISCONNECTED = 1UL << 5,
  F_BACKEND_UNREACHABLE = 1UL << 6,
  F_BACKEND_AUTH_REJECTED = 1UL << 7,
  F_BUFFER_OVERFLOW = 1UL << 8,
  F_NVS_ERROR = 1UL << 9,
};

struct NamedBit {
  uint32_t bit;
  const char* name;
};

static const NamedBit FAULT_NAMES[] = {
    {F_MPU_NOT_DETECTED, "MPU_NOT_DETECTED"},       {F_MPU_READ_ERROR, "MPU_READ_ERROR"},
    {F_CALIBRATION_FAILED, "CALIBRATION_FAILED"},   {F_SOIL_FAULT, "SOIL_SENSOR_FAULT"},
    {F_WATER_FAULT, "WATER_SENSOR_FAULT"},          {F_WIFI_DISCONNECTED, "WIFI_DISCONNECTED"},
    {F_BACKEND_UNREACHABLE, "BACKEND_UNREACHABLE"}, {F_BACKEND_AUTH_REJECTED, "BACKEND_AUTH_REJECTED"},
    {F_BUFFER_OVERFLOW, "BUFFER_OVERFLOW"},         {F_NVS_ERROR, "NVS_ERROR"},
};
static const NamedBit SOIL_FLAG_NAMES[] = {
    {SF_DRY_SATURATION, "ADC_DRY_SATURATION"}, {SF_PERSISTENT_SATURATION, "ADC_PERSISTENT_SATURATION"},
    {SF_DO_DISAGREES, "DO_DISAGREES"},         {SF_ABOVE_DRY_ENDPOINT, "ABOVE_DRY_ENDPOINT"},
    {SF_BELOW_WET_ENDPOINT, "BELOW_WET_ENDPOINT"}, {SF_LOW_RAIL, "ADC_LOW_RAIL"},
    {SF_UNSTABLE, "ADC_UNSTABLE"},
};
static const NamedBit WATER_FLAG_NAMES[] = {
    {WF_AT_DRY_ENDPOINT, "AT_DRY_ENDPOINT"}, {WF_ABOVE_WET_ENDPOINT, "ABOVE_WET_ENDPOINT"},
    {WF_ABOVE_RANGE, "ABOVE_SENSOR_RANGE"},  {WF_HIGH_RAIL, "ADC_HIGH_RAIL"},
    {WF_UNSTABLE, "ADC_UNSTABLE"},
};
static const NamedBit MPU_FLAG_NAMES[] = {
    {MF_ACCEL_SATURATED, "ACCEL_SATURATED"}, {MF_RECONNECTED, "RECONNECTED"},
    {MF_NO_BASELINE, "NO_BASELINE"},         {MF_READ_ERRORS, "READ_ERRORS"},
};

// ---------------------------------------------------------------------------
// Numeric helpers
// ---------------------------------------------------------------------------
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline bool isFiniteF(float v) { return isfinite(v); }

// Soil: 4090 (dry) .. 921 (wet), polarity: reading falls when wetter.
inline float soilMoistureFromAdc(float raw) {
  if (!isFiniteF(raw)) return NAN;
  return clampf((SOIL_DRY_ADC - raw) / (float)(SOIL_DRY_ADC - SOIL_WET_ADC), 0.0f, 1.0f);
}

// Water: 0 (dry) .. 1725 (wet), polarity: reading rises when wetter.
inline float waterWetnessFromAdc(float raw) {
  if (!isFiniteF(raw)) return NAN;
  return clampf((raw - WATER_DRY_ADC) / (float)(WATER_WET_ADC - WATER_DRY_ADC), 0.0f, 1.0f);
}

struct SoilQuality {
  Health health;
  uint16_t flags;
};

// Classify one soil snapshot.
//   raw/filt   : latest averaged and filtered ADC counts
//   windowStd  : std of ADC samples in the last 1-s window (NAN if unknown)
//   satMs      : how long the ADC has been continuously saturated
//   satNoiseless : true when the saturated readings showed no variation
//   doIndicatesWet : digital comparator output interpreted as "wet" (-1 = unknown)
inline SoilQuality classifySoil(int raw, float filt, float windowStd, uint32_t satMs, bool satNoiseless,
                                int doIndicatesWet) {
  SoilQuality q = {H_OK, 0};
  if (raw < SOIL_LOW_RAIL_THRESHOLD) {
    q.health = H_FAULT;
    q.flags |= SF_LOW_RAIL;
    return q;
  }
  if (isFiniteF(windowStd) && windowStd > ADC_UNSTABLE_STD) {
    q.health = H_FAULT;
    q.flags |= SF_UNSTABLE;
    return q;
  }
  if (raw >= SOIL_SAT_THRESHOLD) {
    // Dry-end saturation is the expected state for dry soil with this sensor
    // (dry endpoint 4090 is next to the rail), so it is NOT a fault by itself.
    q.flags |= SF_DRY_SATURATION;
    if (satNoiseless && satMs >= SOIL_PERSISTENT_SAT_MS) q.flags |= SF_PERSISTENT_SATURATION;
    if (doIndicatesWet == 1) {
      // Comparator says wet while the analog channel is pinned dry: one of the
      // two channels is wrong. Keep the value but mark it suspect.
      q.flags |= SF_DO_DISAGREES;
      q.health = H_SUSPECT;
    }
  }
  if (isFiniteF(filt)) {
    if (filt > SOIL_DRY_ADC) q.flags |= SF_ABOVE_DRY_ENDPOINT;
    if (filt < SOIL_WET_ADC) q.flags |= SF_BELOW_WET_ENDPOINT;
  }
  return q;
}

struct WaterQuality {
  Health health;
  uint16_t flags;
};

inline WaterQuality classifyWater(int raw, float filt, float windowStd) {
  WaterQuality q = {H_OK, 0};
  if (raw >= WATER_HIGH_RAIL_THRESHOLD) {
    q.health = H_FAULT;
    q.flags |= WF_HIGH_RAIL;
    return q;
  }
  if (isFiniteF(windowStd) && windowStd > ADC_UNSTABLE_STD) {
    q.health = H_FAULT;
    q.flags |= WF_UNSTABLE;
    return q;
  }
  if (raw <= WATER_DRY_ADC) q.flags |= WF_AT_DRY_ENDPOINT;
  if (isFiniteF(filt) && filt > WATER_WET_ADC) q.flags |= WF_ABOVE_WET_ENDPOINT;
  if (raw > WATER_ABOVE_RANGE_THRESHOLD) {
    q.flags |= WF_ABOVE_RANGE;
    q.health = H_SUSPECT;
  }
  return q;
}

// ---------------------------------------------------------------------------
// 3-D vectors (gravity direction / tilt)
// ---------------------------------------------------------------------------
struct Vec3 {
  float x, y, z;
};

inline float vmag(const Vec3& v) { return sqrtf(v.x * v.x + v.y * v.y + v.z * v.z); }
inline float vdot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 vcross(const Vec3& a, const Vec3& b) {
  Vec3 r = {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
  return r;
}
inline Vec3 vscale(const Vec3& v, float s) {
  Vec3 r = {v.x * s, v.y * s, v.z * s};
  return r;
}
inline Vec3 vadd(const Vec3& a, const Vec3& b) {
  Vec3 r = {a.x + b.x, a.y + b.y, a.z + b.z};
  return r;
}
// Returns false (and leaves out untouched) when v has no usable direction.
inline bool vnormalize(const Vec3& v, Vec3& out) {
  float m = vmag(v);
  if (!isFiniteF(m) || m < 1e-6f) return false;
  out = vscale(v, 1.0f / m);
  return true;
}

static const float RAD2DEG = 57.29577951308232f;
static const float DEG2RAD = 0.017453292519943295f;

// Angle between two directions in degrees, 0..180. Uses atan2(|a x b|, a.b),
// which is accurate for small angles and never wraps like roll/pitch angles do.
inline float vangleDeg(const Vec3& a, const Vec3& b) {
  float c = vmag(vcross(a, b));
  float d = vdot(a, b);
  if (!isFiniteF(c) || !isFiniteF(d) || (c == 0.0f && d == 0.0f)) return NAN;
  return atan2f(c, d) * RAD2DEG;
}

// Unit vector of the sensor axis that points "up" when the model is level.
// axis: 0 = X, 1 = Y, 2 = Z; sign: +1 or -1 (sensor mounting orientation).
inline Vec3 mountAxis(int axis, int sign) {
  Vec3 v = {0, 0, 0};
  float s = sign < 0 ? -1.0f : 1.0f;
  if (axis == 0) v.x = s;
  else if (axis == 1) v.y = s;
  else v.z = s;
  return v;
}

// Absolute inclination: angle between the measured gravity direction and the
// configured mounting reference axis (0 deg = reference axis vertical).
inline float inclinationDeg(const Vec3& gravity, int axis, int sign) {
  return vangleDeg(gravity, mountAxis(axis, sign));
}

// Complementary filter step for the gravity direction (sensor frame).
// g      : previous unit gravity estimate
// gyroDps: bias-corrected angular rate in deg/s
// accel  : accelerometer reading in g
// k      : gyro weight (e.g. 0.98)
// Gravity is fixed in the world frame, so in the sensor frame it rotates by
// -omega: dg/dt = -omega x g.
inline Vec3 complementaryStep(const Vec3& g, const Vec3& gyroDps, const Vec3& accel, float dt, float k) {
  Vec3 w = vscale(gyroDps, DEG2RAD);
  Vec3 pred = vadd(g, vscale(vcross(w, g), -dt));
  Vec3 predN, accN;
  bool okPred = vnormalize(pred, predN);
  bool okAcc = vnormalize(accel, accN);
  if (!okPred && !okAcc) return g;
  if (!okPred) return accN;
  float am = vmag(accel);
  // Only trust the accelerometer when it measures roughly 1 g (no strong shocks).
  if (!okAcc || am < 0.7f || am > 1.3f) return predN;
  Vec3 mix = vadd(vscale(predN, k), vscale(accN, 1.0f - k));
  Vec3 out;
  return vnormalize(mix, out) ? out : predN;
}

inline float rollDeg(const Vec3& a) { return atan2f(a.y, a.z) * RAD2DEG; }
inline float pitchDeg(const Vec3& a) { return atan2f(-a.x, sqrtf(a.y * a.y + a.z * a.z)) * RAD2DEG; }

// ---------------------------------------------------------------------------
// Running statistics (Welford) and time series windows
// ---------------------------------------------------------------------------
struct RunningStats {
  uint32_t n;
  double mean, m2;
  float minV, maxV;
  void reset() {
    n = 0;
    mean = 0;
    m2 = 0;
    minV = INFINITY;
    maxV = -INFINITY;
  }
  void add(float x) {
    if (!isFiniteF(x)) return;
    n++;
    double d = x - mean;
    mean += d / n;
    m2 += d * (x - mean);
    if (x < minV) minV = x;
    if (x > maxV) maxV = x;
  }
  float stddev() const { return n >= 2 ? (float)sqrt(m2 / (n - 1)) : (n == 1 ? 0.0f : NAN); }
  float meanf() const { return n ? (float)mean : NAN; }
};

// Fixed-capacity ring of (time [s], value) points for one sensor.
// Each sensor owns its own series, so a fault in one sensor never empties the
// history of another (a bug in the previous firmware).
template <int CAP>
struct Series {
  float t[CAP];
  float v[CAP];
  int head;  // index of the next write
  int count;
  void clear() {
    head = 0;
    count = 0;
  }
  void add(float ts, float value) {
    if (!isFiniteF(ts) || !isFiniteF(value)) return;
    t[head] = ts;
    v[head] = value;
    head = (head + 1) % CAP;
    if (count < CAP) count++;
  }
  // Iterate points whose time >= since. Returns number of points.
  int collect(float since, float* ts, float* vs) const {
    int n = 0;
    for (int i = 0; i < count; i++) {
      int idx = (head - count + i + CAP) % CAP;
      if (t[idx] >= since) {
        ts[n] = t[idx];
        vs[n] = v[idx];
        n++;
      }
    }
    return n;
  }
  float mean(float now, float window, int minPts) const {
    float ts[CAP], vs[CAP];
    int n = collect(now - window, ts, vs);
    if (n < minPts) return NAN;
    double s = 0;
    for (int i = 0; i < n; i++) s += vs[i];
    return (float)(s / n);
  }
  float stddev(float now, float window, int minPts) const {
    float ts[CAP], vs[CAP];
    int n = collect(now - window, ts, vs);
    if (n < minPts || n < 2) return NAN;
    double s = 0, s2 = 0;
    for (int i = 0; i < n; i++) s += vs[i];
    double m = s / n;
    for (int i = 0; i < n; i++) s2 += (vs[i] - m) * (vs[i] - m);
    return (float)sqrt(s2 / (n - 1));
  }
  // Least-squares slope in value units per MINUTE over the window. Requires at
  // least minPts points spanning at least minSpan seconds; NAN otherwise.
  float slopePerMin(float now, float window, int minPts, float minSpan) const {
    float ts[CAP], vs[CAP];
    int n = collect(now - window, ts, vs);
    if (n < minPts || n < 2) return NAN;
    if (ts[n - 1] - ts[0] < minSpan) return NAN;
    double tm = 0, vm = 0;
    for (int i = 0; i < n; i++) {
      tm += ts[i];
      vm += vs[i];
    }
    tm /= n;
    vm /= n;
    double num = 0, den = 0;
    for (int i = 0; i < n; i++) {
      num += (ts[i] - tm) * (vs[i] - vm);
      den += (ts[i] - tm) * (ts[i] - tm);
    }
    if (den <= 0) return NAN;
    return (float)(num / den * 60.0);
  }
};

// ---------------------------------------------------------------------------
// CRC32 (for NVS calibration records)
// ---------------------------------------------------------------------------
inline uint32_t crc32(const void* data, size_t len) {
  const uint8_t* p = (const uint8_t*)data;
  uint32_t crc = 0xFFFFFFFFUL;
  for (size_t i = 0; i < len; i++) {
    crc ^= p[i];
    for (int b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xEDB88320UL & (0UL - (crc & 1UL)));
  }
  return ~crc;
}

// ---------------------------------------------------------------------------
// Bounded JSON writer. Never emits NaN/Infinity: non-finite numbers -> null.
// ---------------------------------------------------------------------------
class JsonWriter {
 public:
  JsonWriter(char* buf, size_t cap) : buf_(buf), cap_(cap), len_(0), overflow_(false), needComma_(false) {
    if (cap_) buf_[0] = 0;
  }
  bool ok() const { return !overflow_; }
  size_t length() const { return len_; }
  const char* c_str() const { return buf_; }

  void beginObject(const char* key = nullptr) {
    prefix(key);
    put("{");
    needComma_ = false;
  }
  void endObject() {
    put("}");
    needComma_ = true;
  }
  void beginArray(const char* key = nullptr) {
    prefix(key);
    put("[");
    needComma_ = false;
  }
  void endArray() {
    put("]");
    needComma_ = true;
  }
  void str(const char* key, const char* value) {
    prefix(key);
    if (!value) {
      put("null");
    } else {
      put("\"");
      for (const char* c = value; *c; c++) {
        char ch = *c;
        if (ch == '"' || ch == '\\') {
          char esc[3] = {'\\', ch, 0};
          put(esc);
        } else if ((unsigned char)ch < 0x20) {
          char esc[8];
          snprintf(esc, sizeof(esc), "\\u%04x", (unsigned)(unsigned char)ch);
          put(esc);
        } else {
          char one[2] = {ch, 0};
          put(one);
        }
      }
      put("\"");
    }
    needComma_ = true;
  }
  void num(const char* key, float value, int digits = 4) {
    prefix(key);
    if (!isFiniteF(value)) {
      put("null");
    } else {
      char tmp[32];
      snprintf(tmp, sizeof(tmp), "%.*f", digits, (double)value);
      // "-0.0000" -> "0"
      put(strcmp(tmp, "-0") == 0 ? "0" : tmp);
    }
    needComma_ = true;
  }
  void integer(const char* key, long long value) {
    prefix(key);
    char tmp[24];
    snprintf(tmp, sizeof(tmp), "%lld", value);
    put(tmp);
    needComma_ = true;
  }
  void boolean(const char* key, bool value) {
    prefix(key);
    put(value ? "true" : "false");
    needComma_ = true;
  }
  void null(const char* key) {
    prefix(key);
    put("null");
    needComma_ = true;
  }
  // Array of flag names for the bits set in mask.
  void flags(const char* key, uint32_t mask, const NamedBit* names, size_t count) {
    beginArray(key);
    for (size_t i = 0; i < count; i++)
      if (mask & names[i].bit) str(nullptr, names[i].name);
    endArray();
  }

 private:
  void prefix(const char* key) {
    if (needComma_) put(",");
    if (key) {
      put("\"");
      put(key);
      put("\":");
    }
  }
  void put(const char* s) {
    size_t n = strlen(s);
    if (len_ + n + 1 > cap_) {
      overflow_ = true;
      return;
    }
    memcpy(buf_ + len_, s, n);
    len_ += n;
    buf_[len_] = 0;
  }
  char* buf_;
  size_t cap_;
  size_t len_;
  bool overflow_;
  bool needComma_;
};

// ---------------------------------------------------------------------------
// Minimal JSON reader for the backend's flat response object. Only keys at the
// top level (depth 1) are matched, so keys inside nested objects (e.g.
// "probabilities") never shadow top-level fields.
// Returns a pointer to the first character of the value, or nullptr.
// ---------------------------------------------------------------------------
inline const char* jsonFindTopLevel(const char* json, const char* key) {
  if (!json || !key) return nullptr;
  size_t klen = strlen(key);
  int depth = 0;
  bool inStr = false;
  const char* strStart = nullptr;
  for (const char* p = json; *p; p++) {
    char c = *p;
    if (inStr) {
      if (c == '\\' && p[1]) {
        p++;
        continue;
      }
      if (c == '"') {
        inStr = false;
        // Is this string a key at depth 1?
        if (depth == 1 && (size_t)(p - strStart) == klen && strncmp(strStart, key, klen) == 0) {
          const char* q = p + 1;
          while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
          if (*q == ':') {
            q++;
            while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
            return q;
          }
        }
      }
      continue;
    }
    if (c == '"') {
      inStr = true;
      strStart = p + 1;
    } else if (c == '{' || c == '[') {
      depth++;
    } else if (c == '}' || c == ']') {
      depth--;
    }
  }
  return nullptr;
}

// Copies a top-level string value (simple escapes only). false if missing,
// not a string, or too long.
inline bool jsonGetString(const char* json, const char* key, char* out, size_t outLen) {
  const char* v = jsonFindTopLevel(json, key);
  if (!v || *v != '"' || outLen == 0) return false;
  size_t n = 0;
  for (const char* p = v + 1; *p; p++) {
    char c = *p;
    if (c == '"') {
      out[n] = 0;
      return true;
    }
    if (c == '\\') {
      p++;
      if (!*p) return false;
      c = *p;
      if (c == 'n') c = '\n';
      else if (c == 't') c = '\t';
      else if (c == 'u') return false;  // not needed for our identifiers
    }
    if (n + 1 >= outLen) return false;
    out[n++] = c;
  }
  return false;
}

inline bool jsonGetNumber(const char* json, const char* key, double& out) {
  const char* v = jsonFindTopLevel(json, key);
  if (!v) return false;
  if (*v != '-' && (*v < '0' || *v > '9')) return false;
  char* end = nullptr;
  double d = strtod(v, &end);
  if (end == v || !isfinite(d)) return false;
  out = d;
  return true;
}

inline bool jsonGetBool(const char* json, const char* key, bool& out) {
  const char* v = jsonFindTopLevel(json, key);
  if (!v) return false;
  if (strncmp(v, "true", 4) == 0) {
    out = true;
    return true;
  }
  if (strncmp(v, "false", 5) == 0) {
    out = false;
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Backend prediction response validation
// ---------------------------------------------------------------------------
enum PredCheck : uint8_t {
  PC_VALID = 0,           // usable prediction for the current reading
  PC_UNAVAILABLE,         // backend explicitly reported no prediction
  PC_BAD_JSON,
  PC_DEVICE_MISMATCH,
  PC_SESSION_MISMATCH,
  PC_READING_MISMATCH,
  PC_UNKNOWN_CLASS,
  PC_NO_MODEL_VERSION,
  PC_STALE,               // reading too old to drive alerts
  PC_NOT_ACCEPTED,        // backend status is not success/duplicate
};

inline const char* predCheckName(PredCheck c) {
  static const char* names[] = {"VALID",          "UNAVAILABLE",   "BAD_JSON",       "DEVICE_MISMATCH",
                                 "SESSION_MISMATCH", "READING_MISMATCH", "UNKNOWN_CLASS", "NO_MODEL_VERSION",
                                 "STALE",          "NOT_ACCEPTED"};
  return c <= PC_NOT_ACCEPTED ? names[c] : "?";
}

struct ParsedPrediction {
  Risk risk;
  float confidence;
  char modelVersion[40];
  char status[24];        // prediction_status from backend (OK / MODEL_NOT_READY / ...)
  char reason[64];
};

// Validates a backend response for the reading that was just sent.
//   readingAgeMs : now - reading creation time (device uptime clock)
//   maxAgeMs     : freshness limit for alert control
inline PredCheck validatePrediction(const char* json, const char* deviceId, const char* sessionId,
                                    const char* readingId, uint32_t readingAgeMs, uint32_t maxAgeMs,
                                    ParsedPrediction& out) {
  memset(&out, 0, sizeof(out));
  out.risk = RISK_NONE;
  out.confidence = NAN;
  char status[16], dev[48], sess[48], rid[64], risk[16];
  if (!jsonGetString(json, "status", status, sizeof(status))) return PC_BAD_JSON;
  if (strcmp(status, "success") != 0 && strcmp(status, "duplicate") != 0) return PC_NOT_ACCEPTED;
  if (!jsonGetString(json, "device_id", dev, sizeof(dev)) || strcmp(dev, deviceId) != 0) return PC_DEVICE_MISMATCH;
  if (!jsonGetString(json, "session_id", sess, sizeof(sess)) || strcmp(sess, sessionId) != 0)
    return PC_SESSION_MISMATCH;
  if (!jsonGetString(json, "reading_id", rid, sizeof(rid)) || strcmp(rid, readingId) != 0)
    return PC_READING_MISMATCH;
  jsonGetString(json, "prediction_status", out.status, sizeof(out.status));
  jsonGetString(json, "reason", out.reason, sizeof(out.reason));
  bool available = false;
  if (!jsonGetBool(json, "prediction_available", available)) return PC_BAD_JSON;
  if (!available) return PC_UNAVAILABLE;
  if (!jsonGetString(json, "risk_state", risk, sizeof(risk))) return PC_UNKNOWN_CLASS;
  out.risk = riskFromName(risk);
  if (out.risk == RISK_NONE) return PC_UNKNOWN_CLASS;
  if (!jsonGetString(json, "model_version", out.modelVersion, sizeof(out.modelVersion)) || !out.modelVersion[0])
    return PC_NO_MODEL_VERSION;
  double conf;
  if (jsonGetNumber(json, "confidence", conf) && conf >= 0.0 && conf <= 1.0) out.confidence = (float)conf;
  if (readingAgeMs > maxAgeMs) return PC_STALE;
  return PC_VALID;
}

// ---------------------------------------------------------------------------
// LED / buzzer indication (pure function of state and time -> non-blocking)
// ---------------------------------------------------------------------------
enum Indication : uint8_t {
  IND_RISK = 0,        // fresh prediction: steady risk LED (+ buzzer for WARNING/CRITICAL)
  IND_STALE_ALERT,     // last prediction WARNING/CRITICAL but no longer fresh: blinking + buzzer latched
  IND_CALIBRATING,     // LED chase, buzzers off
  IND_SENSOR_FAULT,    // all LEDs short flash each second + short chirp every 10 s
  IND_COMM_LOST,       // blue double flash every 2 s
  IND_NO_PREDICTION,   // blue + yellow short flash every 2 s (model not ready / insufficient data)
  IND_TEST,            // hardware self-test (driven by caller)
};

inline const char* indicationName(Indication i) {
  static const char* n[] = {"RISK", "STALE_ALERT", "CALIBRATING", "SENSOR_FAULT", "COMM_LOST", "NO_PREDICTION", "TEST"};
  return i <= IND_TEST ? n[i] : "?";
}

struct IndicationInput {
  bool calibrating;
  bool localSensorFault;   // calibration failed or a required sensor reports FAULT
  bool backendReachable;   // Wi-Fi up and the last POST got an HTTP response
  bool havePrediction;     // valid prediction for the current session exists
  uint32_t predictionAgeMs;
  uint32_t freshMs;
  Risk predictedRisk;
  bool backendSensorFault; // backend reported SENSOR_FAULT for the latest reading
};

inline Indication selectIndication(const IndicationInput& in) {
  if (in.calibrating) return IND_CALIBRATING;
  if (in.havePrediction && in.predictionAgeMs <= in.freshMs) return IND_RISK;
  // A stale WARNING/CRITICAL is never silently downgraded: it keeps alerting.
  if (in.havePrediction && (in.predictedRisk == RISK_WARNING || in.predictedRisk == RISK_CRITICAL))
    return IND_STALE_ALERT;
  // A stale SAFE/WATCH is NOT shown as SAFE/WATCH (no green without a fresh prediction).
  if (in.localSensorFault || in.backendSensorFault) return IND_SENSOR_FAULT;
  if (!in.backendReachable) return IND_COMM_LOST;
  return IND_NO_PREDICTION;
}

struct Outputs {
  bool green, blue, yellow, red, buzzer1, buzzer2;
};

inline Outputs indicationOutputs(Indication ind, Risk risk, uint32_t t) {
  Outputs o = {false, false, false, false, false, false};
  switch (ind) {
    case IND_RISK:
      if (risk == RISK_SAFE) o.green = true;
      else if (risk == RISK_WATCH) o.blue = true;
      else if (risk == RISK_WARNING) {
        o.yellow = true;
        o.buzzer1 = (t % 1500) < 300;  // intermittent
      } else if (risk == RISK_CRITICAL) {
        o.red = true;
        o.buzzer2 = (t % 200) < 100;   // rapid
      }
      break;
    case IND_STALE_ALERT: {
      bool on = (t % 1000) < 500;
      if (risk == RISK_WARNING) {
        o.yellow = on;
        o.buzzer1 = (t % 1500) < 300;
      } else {
        o.red = on;
        o.buzzer2 = (t % 200) < 100;
      }
      break;
    }
    case IND_CALIBRATING: {
      uint32_t idx = (t / 150) % 4;
      o.green = idx == 0;
      o.blue = idx == 1;
      o.yellow = idx == 2;
      o.red = idx == 3;
      break;
    }
    case IND_SENSOR_FAULT: {
      bool on = (t % 1000) < 100;
      o.green = o.blue = o.yellow = o.red = on;
      o.buzzer1 = (t % 10000) < 60;
      break;
    }
    case IND_COMM_LOST: {
      uint32_t p = t % 2000;
      o.blue = p < 100 || (p >= 200 && p < 300);
      break;
    }
    case IND_NO_PREDICTION: {
      bool on = (t % 2000) < 100;
      o.blue = on;
      o.yellow = on;
      break;
    }
    case IND_TEST:
      break;
  }
  return o;
}

}  // namespace ls
