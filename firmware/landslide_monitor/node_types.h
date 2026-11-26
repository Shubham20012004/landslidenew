// node_types.h - data structures of the landslide monitor firmware.
// Kept in a header so the Arduino IDE's automatic prototype generation never
// places function prototypes before these declarations.
#pragma once

#include <stdint.h>

#include "ls_logic.h"

using ls::Health;
using ls::Risk;
using ls::Vec3;

enum CalStatus : uint8_t { CS_NONE = 0, CS_IN_PROGRESS, CS_OK, CS_FAILED };
enum CalPhase : uint8_t { CP_IDLE = 0, CP_SETTLE, CP_COLLECT, CP_RETRY_WAIT };

inline const char* calStatusName(uint8_t s) {
  switch (s) {
    case CS_IN_PROGRESS: return "IN_PROGRESS";
    case CS_OK: return "OK";
    case CS_FAILED: return "FAILED";
    default: return "NOT_STARTED";
  }
}

inline const char* opStateName(uint8_t s) {
  switch (s) {
    case CS_IN_PROGRESS: return "CALIBRATING";
    case CS_OK: return "MONITORING";
    case CS_FAILED: return "CALIBRATION_FAILED";
    default: return "INITIALIZING";
  }
}

// Calibration summary for one session (reported in telemetry).
struct CalInfo {
  uint16_t sessionNo;
  uint8_t status;
  uint8_t attempts;
  char trigger[12];
  char error[32];
  uint32_t completedUptimeMs;
  Vec3 gravity0;
  Vec3 gyroBias;
  float incl0;
  float accelStd;
  float gyroStd;
  float vibBg;
  int soilRaw0;
  int waterRaw0;
  float prevIncl0;
  float prevShiftDeg;  // angle between the previous saved baseline and the new one
  bool nvsSaved;
};

// Persistent calibration record (NVS). Soil/water endpoints are fixed
// constants and are deliberately not stored.
struct CalRecord {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  float g0[3];
  float gyroBias[3];
  float incl0;
  float vibBg;
  float accelStd;
  float gyroStd;
  uint32_t bootCount;
  uint32_t crc;
};
static const uint32_t CAL_MAGIC = 0x4C534D34;  // "LSM4"

// One telemetry reading (kept in RAM until acknowledged by the backend).
struct Reading {
  uint32_t seq;
  uint16_t sessionNo;
  uint8_t calStatus;
  uint8_t attempts;
  uint32_t uptimeMs;
  int64_t epochMs;
  int8_t rssi;
  uint32_t faults;
  uint16_t queued;
  uint32_t dropped;
  // soil
  int16_t soilRaw;
  float soilFilt;
  int8_t soilDo;
  float soilMoist, soilRate, soilMean, soilStd;
  uint8_t soilHealth;
  uint16_t soilFlags;
  // water
  int16_t waterRaw;
  float waterFilt, waterWet, waterWetS, waterRate, waterMean;
  uint8_t waterHealth;
  uint16_t waterFlags;
  // mpu
  float ax, ay, az, gx, gy, gz;
  float incl0, incl, tilt, inclChange, roll, pitch, tiltRate;
  float accMag, accDyn, gyroMag, tempC;
  uint8_t mpuHealth;
  uint16_t mpuFlags;
  // vibration
  int8_t vibDo;
  uint32_t vibTransitions, vibEvents, vibWindowMs;
  float vibEps, vibBg, vibAbove, vibEv5, vibEv10, vibEv30, vibTrend;
};

struct PredState {
  bool valid;
  uint16_t sessionNo;
  Risk risk;
  float confidence;
  char model[40];
  uint32_t readingUptimeMs;
  uint32_t receivedMs;
  uint32_t readingSeq;
};

struct NetState {
  bool wifiConnected;
  int rssi;
  int lastHttp;
  uint32_t lastResponseMs;
  uint32_t okCount, failCount, rejectCount, dupCount;
  bool authRejected;
  bool backendSensorFault;
  char backendStatus[24];
  char backendReason[64];
  uint16_t backendStatusSession;
  char lastCheck[20];
  char ip[16];
};

