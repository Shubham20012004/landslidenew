#pragma once
// Copy this file to secrets.h (same folder) and fill in your values.
// secrets.h is git-ignored. Without it the firmware runs without Wi-Fi
// (sensors, calibration and diagnostics still work; no predictions).

#define WIFI_SSID     "your-2.4GHz-ssid"
#define WIFI_PASSWORD "your-wifi-password"

// LAN IPv4 address of the laptop running the backend (printed by start.bat / start.sh).
// Not localhost / 127.0.0.1: that would point at the ESP32 itself.
#define API_HOST      "192.168.1.100"
#define API_PORT      8000

#define DEVICE_ID     "esp32-node-1"
// Must match the entry for DEVICE_ID in LANDSLIDE_DEVICE_TOKENS in the backend .env
// (setup.bat / setup.sh generates both and prints the value).
#define DEVICE_TOKEN  "paste-device-token-here"
