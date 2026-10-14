#include <Arduino.h>

// Match these scales to the Pi's Arduino device settings.
static const float REFERENCE_VOLTAGE = 5.0f;
static const float ADC_MAX_COUNT = 1023.0f;
static const uint32_t SAMPLE_PERIOD_MS = 20;  // 50 serial samples/second.
static uint32_t lastSampleMs = 0;

void setup() {
  Serial.begin(115200);
  lastSampleMs = static_cast<uint32_t>(millis()) - SAMPLE_PERIOD_MS;
}

void loop() {
  uint32_t now = static_cast<uint32_t>(millis());
  if (now - lastSampleMs < SAMPLE_PERIOD_MS) return;
  lastSampleMs = now;
  float steeringVoltage = analogRead(A0) * REFERENCE_VOLTAGE / ADC_MAX_COUNT;
  float airspeedVoltage = analogRead(A1) * REFERENCE_VOLTAGE / ADC_MAX_COUNT;
  // Pi contract: A0_voltage,A1_voltage. Mass flow connects to the Pi I2C bus.
  Serial.print(steeringVoltage, 3);
  Serial.print(',');
  Serial.print(airspeedVoltage, 3);
  Serial.println();
}
