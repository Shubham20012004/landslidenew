# Hardware wiring and electrical notes

| Component | ESP32 GPIO | Direction | Notes |
|---|---:|---|---|
| Red LED (CRITICAL) | 19 | out | 220–330 Ω in series, cathode to GND |
| Blue LED (WATCH) | 21 | out | ″ |
| Yellow LED (WARNING) | 22 | out | ″ |
| Green LED (SAFE) | 23 | out | ″ |
| Soil moisture AO | 35 | analog in (ADC1_CH7) | input-only pin, no pull-up |
| Soil moisture DO | 32 | digital in | comparator output (diagnostic cross-check) |
| Water sensor **S** | 34 | analog in (ADC1_CH6) | single analog signal, observed 0 … ~1725 counts |
| SW-420 DO | 15 | digital in, interrupt | strapping pin (see below) |
| MPU6050 SDA / SCL | 25 / 26 | I²C 400 kHz | AD0 low = 0x68, high = 0x69 (both detected) |
| Buzzer 1 (WARNING) | 12 | out | strapping pin — transistor driver required |
| Buzzer 2 (CRITICAL) | 13 | out | transistor driver |

All grounds common. Analog channels are on ADC1 because ADC2 cannot be read while Wi-Fi is active.

## Voltage safety
* ESP32 inputs tolerate **3.3 V maximum**. Power the soil sensor, water sensor, SW-420 and MPU6050 from 3V3
  where possible. If a module must be powered from 5 V, measure its output first; if it can exceed 3.3 V,
  add a divider (e.g. 10 kΩ / 20 kΩ → ×0.67) on AO/S, or a level shifter on digital outputs.
* The water sensor reaching ~1725 counts (~1.4 V at 11 dB attenuation) is consistent with a 3.3 V-powered
  resistive sensor; keep it that way.
* Resistive water sensors corrode under constant DC; this is a known limitation for long experiments.

## Strapping pins
* **GPIO 12 (MTDI)** selects the flash voltage at reset. If it is HIGH during boot, a module with 3.3 V
  flash (ESP32-WROOM-32) fails to boot (`flash read err`). Drive buzzer 1 through an NPN transistor
  (2N2222/BC547: 1 kΩ base resistor, **10 kΩ base-to-GND pull-down**, buzzer between +V and collector,
  emitter to GND, flyback diode across magnetic buzzers). Never connect a buzzer module whose input has a
  pull-up to GPIO 12. The firmware drives GPIO 12 LOW as its first action. (Alternative: burn the
  flash-voltage eFuse with `espefuse.py set_flash_voltage 3.3V` — permanent, only if you know your module.)
* **GPIO 15 (MTDO)** controls boot-log output and SDIO timing. The SW-420 output level at reset only
  changes whether the ROM boot log is printed; it does not prevent booting. If uploads fail with the
  SW-420 connected, disconnect its DO while flashing.

## Buzzers
Active buzzers (built-in oscillator) are assumed: the firmware switches them on/off with `digitalWrite`.
A typical active buzzer draws 20–35 mA, more than a GPIO should supply, hence the transistor driver for both.

## Mounting
The MPU6050 axis that points up when the model is level is configured in the firmware
(`MOUNT_AXIS = 2` (Z), `MOUNT_SIGN = +1`). Relative tilt is the angle between the current and the
session-baseline gravity vectors, so it is independent of mounting and never wraps; absolute inclination
is measured from the configured reference axis. Mount the sensor rigidly to the slope body: orientation
change is not ground displacement.

## Power
Use a stable USB supply or a 5 V supply into the DevKit's 5V/VIN pin. Brown-outs during Wi-Fi transmission
reset the ESP32 (reset reason is printed at boot and every reset starts a new calibrated session).
