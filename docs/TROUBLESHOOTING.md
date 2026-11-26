# Troubleshooting

## ESP32 cannot reach the backend (`HTTP -1`, "connection refused", `wifi` shows last HTTP -1)
1. Start the backend (`start.bat`). It prints `ESP32 endpoint http://<LAN-IP>:8000/api/readings`.
2. Put exactly that IP in `firmware/landslide_monitor/secrets.h` (`API_HOST`), re-flash. The hotspot can give
   the laptop a new IP after reconnecting (previously `10.45.196.162`); check it each session.
3. Laptop and ESP32 must be on the same 2.4 GHz network; disable "AP/client isolation" on the hotspot.
4. Test from a phone on the same Wi-Fi: `http://<LAN-IP>:8000/api/health` must return JSON.
5. Windows Firewall: allow Python on **Private** networks when prompted, or (admin PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "Landslide backend 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```
   Make sure the hotspot network is set to *Private* in Windows network settings.
6. Ubuntu: `sudo ufw allow 8000/tcp` if ufw is active.
Readings are buffered (~4 min) and replayed when the connection returns.

## HTTP 401 / "device token rejected"
`DEVICE_TOKEN` in `secrets.h` must equal the `esp32-node-1:` entry of `LANDSLIDE_DEVICE_TOKENS` in `.env`.
Run `setup.bat` (it inserts/compares the token), re-flash, restart the backend. The firmware keeps the
data and retries every 30 s.

## HTTP 422 (telemetry rejected)
The serial log prints the backend's first error location (e.g. `mpu.tilt_rate_dpm`); the dashboard's
Device & system page lists `TELEMETRY_REJECTED` events. Firmware 4.x and backend 2.x share schema 2.0 —
an old firmware (3.x) is not compatible: flash the current sketch.

## Dashboard shows "Model not ready"
Normal until a real model is trained. For a demonstration use `start.bat demo`; for real data label
experiments and run `train.bat`. The reason is shown on Model & dataset.

## "Insufficient data" for ~30 s after calibration
Expected: rates and 30-s windows must fill before all 13 features exist.

## MPU6050 not detected / calibration failed
Check 3V3/GND, SDA→25, SCL→26, soldered headers; `test` runs an I²C scan (expect 0x68 or 0x69). For
`UNSTABLE_MOVEMENT` keep the model still and type `demo`.

## Soil reads 4095
Usually dry soil (valid dry-end saturation). If the soil is wet, check the AO wire and sensor supply;
the `DO_DISAGREES` flag appears when the comparator reports wet at the same time.

## Water sensor always 0
0 is the dry value. Wet the sensor and check `raw`; if it stays 0, check S→34 and the sensor supply.

## Board does not boot after wiring the buzzers
GPIO 12 is pulled high at reset — see docs/HARDWARE.md (transistor with pull-down).

## Upload fails
Hold BOOT while "Connecting…", use a data cable, close the Serial Monitor, disconnect the SW-420 DO (GPIO 15)
if needed, install the CP210x/CH340 driver.

## Logs
Backend: `logs/backend.log` (rotating). Database backups: `backend/data/backups/`.
