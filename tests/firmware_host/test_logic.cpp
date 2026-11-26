// Host unit tests for firmware/landslide_monitor/ls_logic.h (no hardware needed).
// Built and run by tests/test_firmware_logic.py with any C++11 compiler.
#include <stdio.h>

#include "../../firmware/landslide_monitor/ls_logic.h"

using namespace ls;

static int failures = 0, checks = 0;
#define CHECK(cond)                                          \
  do {                                                       \
    checks++;                                                \
    if (!(cond)) {                                           \
      failures++;                                            \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    }                                                        \
  } while (0)
#define NEAR(a, b, eps) CHECK(fabs((double)(a) - (double)(b)) <= (eps))

static Vec3 v3(float x, float y, float z) {
  Vec3 v = {x, y, z};
  return v;
}

static void testNormalization() {
  NEAR(soilMoistureFromAdc(4090), 0.0, 1e-6);
  NEAR(soilMoistureFromAdc(921), 1.0, 1e-6);
  NEAR(soilMoistureFromAdc(4095), 0.0, 1e-6);  // dry-end saturation clamps, not NaN
  NEAR(soilMoistureFromAdc(500), 1.0, 1e-6);
  NEAR(soilMoistureFromAdc(2505.5f), 0.5, 1e-4);
  CHECK(isnan(soilMoistureFromAdc(NAN)));
  NEAR(waterWetnessFromAdc(0), 0.0, 1e-6);
  NEAR(waterWetnessFromAdc(1725), 1.0, 1e-6);
  NEAR(waterWetnessFromAdc(862.5f), 0.5, 1e-4);
  NEAR(waterWetnessFromAdc(3000), 1.0, 1e-6);
}

static void testSoilQuality() {
  SoilQuality q = classifySoil(4095, 4095, 0.0f, 1000, true, 0);
  CHECK(q.health == H_OK);  // dry saturation alone is valid
  CHECK(q.flags & SF_DRY_SATURATION);
  CHECK(!(q.flags & SF_PERSISTENT_SATURATION));
  q = classifySoil(4095, 4095, 0.0f, SOIL_PERSISTENT_SAT_MS + 1, true, 0);
  CHECK(q.health == H_OK && (q.flags & SF_PERSISTENT_SATURATION));
  q = classifySoil(4095, 4095, 0.0f, 5000, true, 1);  // comparator says wet
  CHECK(q.health == H_SUSPECT && (q.flags & SF_DO_DISAGREES));
  q = classifySoil(40, 40, 2.0f, 0, false, 1);
  CHECK(q.health == H_FAULT && (q.flags & SF_LOW_RAIL));
  q = classifySoil(2000, 2000, 900.0f, 0, false, 0);
  CHECK(q.health == H_FAULT && (q.flags & SF_UNSTABLE));
  q = classifySoil(2500, 2500, 5.0f, 0, false, -1);
  CHECK(q.health == H_OK && q.flags == 0);
  q = classifySoil(850, 850, 5.0f, 0, false, 1);
  CHECK(q.health == H_OK && (q.flags & SF_BELOW_WET_ENDPOINT));
}

static void testWaterQuality() {
  WaterQuality q = classifyWater(0, 0, 0);
  CHECK(q.health == H_OK && (q.flags & WF_AT_DRY_ENDPOINT));
  q = classifyWater(1800, 1800, 10);
  CHECK(q.health == H_OK && (q.flags & WF_ABOVE_WET_ENDPOINT));
  q = classifyWater(4095, 4095, 0);
  CHECK(q.health == H_FAULT && (q.flags & WF_HIGH_RAIL));
  q = classifyWater(2600, 2600, 0);
  CHECK(q.health == H_SUSPECT && (q.flags & WF_ABOVE_RANGE));
  q = classifyWater(900, 900, 800);
  CHECK(q.health == H_FAULT && (q.flags & WF_UNSTABLE));
}

static void testVectors() {
  Vec3 z = v3(0, 0, 1), x = v3(1, 0, 0);
  NEAR(vangleDeg(z, x), 90.0, 1e-4);
  NEAR(vangleDeg(z, z), 0.0, 1e-6);
  Vec3 tilted = v3(sinf(2 * DEG2RAD), 0, cosf(2 * DEG2RAD));
  NEAR(vangleDeg(z, tilted), 2.0, 1e-3);
  Vec3 small = v3(sinf(0.05f * DEG2RAD), 0, cosf(0.05f * DEG2RAD));
  NEAR(vangleDeg(z, small), 0.05, 1e-3);  // accurate for tiny angles
  // No wrap-around near 180 deg roll, where roll angles jump from +180 to -180.
  Vec3 a = v3(0, sinf(179 * DEG2RAD), cosf(179 * DEG2RAD));
  Vec3 b = v3(0, sinf(-179 * DEG2RAD), cosf(-179 * DEG2RAD));
  NEAR(vangleDeg(a, b), 2.0, 1e-3);
  NEAR(inclinationDeg(tilted, 2, 1), 2.0, 1e-3);
  NEAR(inclinationDeg(v3(0, 0, -1), 2, -1), 0.0, 1e-4);  // sensor mounted upside down
  Vec3 n;
  CHECK(!vnormalize(v3(0, 0, 0), n));
  CHECK(isnan(vangleDeg(v3(0, 0, 0), z)));
}

static void testComplementaryFilter() {
  Vec3 g = v3(0, 0, 1);
  Vec3 noGyro = v3(0, 0, 0);
  Vec3 tiltedAcc = v3(sinf(10 * DEG2RAD), 0, cosf(10 * DEG2RAD));
  for (int i = 0; i < 500; i++) g = complementaryStep(g, noGyro, tiltedAcc, 0.02f, 0.98f);
  NEAR(vangleDeg(g, tiltedAcc), 0.0, 0.05);  // converges to the accelerometer
  Vec3 g2 = complementaryStep(v3(0, 0, 1), noGyro, v3(2.5f, 0, 0), 0.02f, 0.98f);
  NEAR(vangleDeg(g2, v3(0, 0, 1)), 0.0, 1e-4);  // a 2.5 g shock is ignored
  Vec3 g3 = v3(0, 0, 1);
  for (int i = 0; i < 1000; i++) g3 = complementaryStep(g3, v3(0, 90, 0), v3(0, 0, 0), 0.001f, 0.98f);
  NEAR(vangleDeg(g3, v3(0, 0, 1)), 90.0, 1.0);  // 90 deg/s for 1 s
}

static void testSeries() {
  Series<32> s;
  s.clear();
  CHECK(isnan(s.slopePerMin(10, 30, 5, 4)));
  for (int i = 0; i < 20; i++) s.add((float)i, 0.5f + 0.01f * i);  // +0.01 per s
  NEAR(s.slopePerMin(19, 30, 10, 9), 0.6, 1e-4);
  NEAR(s.mean(19, 30, 10), 0.595, 1e-4);
  CHECK(s.stddev(19, 30, 10) > 0);
  CHECK(isnan(s.slopePerMin(19, 3, 10, 9)));  // too few points in window
  for (int i = 20; i < 100; i++) s.add((float)i, 1.0f);  // ring wraps at 32
  CHECK(s.count == 32);
  NEAR(s.slopePerMin(99, 30, 10, 9), 0.0, 1e-6);
  s.add(100, NAN);  // ignored
  CHECK(s.count == 32);
  RunningStats r;
  r.reset();
  r.add(1);
  r.add(2);
  r.add(3);
  NEAR(r.meanf(), 2.0, 1e-9);
  NEAR(r.stddev(), 1.0, 1e-6);
}

static void testJsonWriter() {
  char buf[256];
  JsonWriter w(buf, sizeof(buf));
  w.beginObject();
  w.str("id", "a\"b");
  w.num("x", NAN);
  w.num("y", INFINITY);
  w.num("z", 1.25f, 2);
  w.integer("n", -5);
  w.boolean("ok", true);
  w.flags("f", SF_DRY_SATURATION | SF_UNSTABLE, SOIL_FLAG_NAMES, sizeof(SOIL_FLAG_NAMES) / sizeof(SOIL_FLAG_NAMES[0]));
  w.endObject();
  CHECK(w.ok());
  CHECK(strcmp(buf,
               "{\"id\":\"a\\\"b\",\"x\":null,\"y\":null,\"z\":1.25,\"n\":-5,\"ok\":true,"
               "\"f\":[\"ADC_DRY_SATURATION\",\"ADC_UNSTABLE\"]}") == 0);
  CHECK(strstr(buf, "nan") == nullptr && strstr(buf, "inf") == nullptr);
  char tiny[8];
  JsonWriter t(tiny, sizeof(tiny));
  t.beginObject();
  t.str("long_key", "long value");
  CHECK(!t.ok());
}

static const char* RESP =
    "{\"status\":\"success\",\"device_id\":\"esp32-node-1\",\"session_id\":\"abcd1234-01\","
    "\"reading_id\":\"abcd1234-01-000007\",\"prediction_available\":true,\"prediction_status\":\"OK\","
    "\"risk_state\":\"WARNING\",\"confidence\":0.87,\"probabilities\":{\"SAFE\":0.03,\"WATCH\":0.06,"
    "\"WARNING\":0.87,\"CRITICAL\":0.04,\"risk_state\":\"SAFE\"},\"model_version\":\"rf-1\",\"reason\":null}";

static void testResponseParsing() {
  char s[32];
  CHECK(jsonGetString(RESP, "risk_state", s, sizeof(s)) && strcmp(s, "WARNING") == 0);  // nested key ignored
  double d;
  CHECK(jsonGetNumber(RESP, "confidence", d) && fabs(d - 0.87) < 1e-9);
  bool b;
  CHECK(jsonGetBool(RESP, "prediction_available", b) && b);
  CHECK(!jsonGetString(RESP, "reason", s, sizeof(s)));  // null is not a string
  CHECK(!jsonGetString(RESP, "missing", s, sizeof(s)));
  CHECK(!jsonGetString("{\"a\":\"x", "a", s, sizeof(s)));  // truncated

  ParsedPrediction p;
  const char* dev = "esp32-node-1";
  const char* sess = "abcd1234-01";
  const char* rid = "abcd1234-01-000007";
  CHECK(validatePrediction(RESP, dev, sess, rid, 1000, 10000, p) == PC_VALID);
  CHECK(p.risk == RISK_WARNING && fabs(p.confidence - 0.87f) < 1e-6 && strcmp(p.modelVersion, "rf-1") == 0);
  CHECK(validatePrediction(RESP, "other", sess, rid, 1000, 10000, p) == PC_DEVICE_MISMATCH);
  // An old-session prediction can never drive the new session.
  CHECK(validatePrediction(RESP, dev, "abcd1234-02", rid, 1000, 10000, p) == PC_SESSION_MISMATCH);
  CHECK(validatePrediction(RESP, dev, sess, "abcd1234-01-000008", 1000, 10000, p) == PC_READING_MISMATCH);
  CHECK(validatePrediction(RESP, dev, sess, rid, 20000, 10000, p) == PC_STALE);
  const char* unknown =
      "{\"status\":\"success\",\"device_id\":\"d\",\"session_id\":\"s\",\"reading_id\":\"r\","
      "\"prediction_available\":true,\"risk_state\":\"DANGER\",\"model_version\":\"m\"}";
  CHECK(validatePrediction(unknown, "d", "s", "r", 0, 10000, p) == PC_UNKNOWN_CLASS);
  const char* nomodel =
      "{\"status\":\"success\",\"device_id\":\"d\",\"session_id\":\"s\",\"reading_id\":\"r\","
      "\"prediction_available\":true,\"risk_state\":\"SAFE\",\"model_version\":\"\"}";
  CHECK(validatePrediction(nomodel, "d", "s", "r", 0, 10000, p) == PC_NO_MODEL_VERSION);
  const char* unavailable =
      "{\"status\":\"success\",\"device_id\":\"d\",\"session_id\":\"s\",\"reading_id\":\"r\","
      "\"prediction_available\":false,\"prediction_status\":\"MODEL_NOT_READY\",\"risk_state\":null,"
      "\"reason\":\"no active model\"}";
  CHECK(validatePrediction(unavailable, "d", "s", "r", 0, 10000, p) == PC_UNAVAILABLE);
  CHECK(strcmp(p.status, "MODEL_NOT_READY") == 0 && p.risk == RISK_NONE);
  CHECK(validatePrediction("{\"status\":\"error\"}", "d", "s", "r", 0, 10000, p) == PC_NOT_ACCEPTED);
  CHECK(validatePrediction("garbage", "d", "s", "r", 0, 10000, p) == PC_BAD_JSON);
}

static void testIndication() {
  IndicationInput in;
  memset(&in, 0, sizeof(in));
  in.freshMs = 10000;
  in.backendReachable = true;
  in.calibrating = true;
  CHECK(selectIndication(in) == IND_CALIBRATING);
  in.calibrating = false;
  CHECK(selectIndication(in) == IND_NO_PREDICTION);
  in.backendReachable = false;
  CHECK(selectIndication(in) == IND_COMM_LOST);
  in.localSensorFault = true;
  CHECK(selectIndication(in) == IND_SENSOR_FAULT);
  in.localSensorFault = false;
  in.backendReachable = true;

  const Risk risks[] = {RISK_SAFE, RISK_WATCH, RISK_WARNING, RISK_CRITICAL};
  for (int k = 0; k < 4; k++) {
    Risk r = risks[k];
    in.havePrediction = true;
    in.predictedRisk = r;
    in.predictionAgeMs = 2000;
    CHECK(selectIndication(in) == IND_RISK);
    Outputs o = indicationOutputs(IND_RISK, r, 50);
    CHECK(o.green == (r == RISK_SAFE));
    CHECK(o.blue == (r == RISK_WATCH));
    CHECK(o.yellow == (r == RISK_WARNING));
    CHECK(o.red == (r == RISK_CRITICAL));
    CHECK(o.buzzer1 == (r == RISK_WARNING));
    CHECK(o.buzzer2 == (r == RISK_CRITICAL));
    in.predictionAgeMs = 60000;  // stale
    Indication s = selectIndication(in);
    if (r == RISK_WARNING || r == RISK_CRITICAL)
      CHECK(s == IND_STALE_ALERT);
    else
      CHECK(s == IND_NO_PREDICTION);  // a stale SAFE/WATCH is never shown as SAFE/WATCH
  }
  // A stale CRITICAL during communication loss keeps alerting (never SAFE).
  in.predictedRisk = RISK_CRITICAL;
  in.backendReachable = false;
  CHECK(selectIndication(in) == IND_STALE_ALERT);
  bool anyRed = false, anyGreen = false, anyBuzz = false;
  for (uint32_t t = 0; t < 2000; t += 10) {
    Outputs o = indicationOutputs(IND_STALE_ALERT, RISK_CRITICAL, t);
    anyRed |= o.red;
    anyGreen |= o.green;
    anyBuzz |= o.buzzer2;
  }
  CHECK(anyRed && anyBuzz && !anyGreen);
  // No diagnostic pattern sounds the CRITICAL buzzer; only calibration/fault patterns light green.
  const Indication inds[] = {IND_CALIBRATING, IND_SENSOR_FAULT, IND_COMM_LOST, IND_NO_PREDICTION};
  for (int k = 0; k < 4; k++) {
    for (uint32_t t = 0; t < 20000; t += 7) {
      Outputs o = indicationOutputs(inds[k], RISK_NONE, t);
      CHECK(!o.buzzer2);
      if (inds[k] == IND_COMM_LOST || inds[k] == IND_NO_PREDICTION) CHECK(!o.green);
    }
  }
}

static void testCrc() { CHECK(crc32("123456789", 9) == 0xCBF43926UL); }

int main() {
  testNormalization();
  testSoilQuality();
  testWaterQuality();
  testVectors();
  testComplementaryFilter();
  testSeries();
  testJsonWriter();
  testResponseParsing();
  testIndication();
  testCrc();
  printf("%d checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
