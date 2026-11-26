| Signal | ESP32 pin / address | Remarks (from .ino) |
|---|---|---|
| PIN_SOIL_AO | 34 | ADC1_CH6, input-only. ADC1 works while Wi-Fi is on. |
| PIN_RAIN_AO | 35 | ADC1_CH7, input-only. ADC1 works while Wi-Fi is on. |
| PIN_RAIN_DO | 27 | optional digital output of rain module; -1 = not wired |
| PIN_I2C_SDA | 25 | MPU6050 SDA (do not reuse while I2C is active) |
| PIN_I2C_SCL | 26 | MPU6050 SCL (do not reuse while I2C is active) |
| PIN_LED_SAFE | 16 | green, 220-330 ohm series resistor |
| PIN_LED_WATCH | 17 | yellow, 220-330 ohm series resistor |
| PIN_LED_WARNING | 18 | orange, 220-330 ohm series resistor |
| PIN_LED_CRITICAL | 19 | red, 220-330 ohm series resistor |
| PIN_BUZZER | 23 | active buzzer, via NPN transistor if >~12 mA |
| MPU_ADDR_PRIMARY | 0x68 | AD0 LOW |
| MPU_ADDR_ALT | 0x69 | AD0 HIGH |
