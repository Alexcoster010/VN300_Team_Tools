#include <cassert>
#include <iostream>
#include "Arduino.h"

uint32_t fakeMillis = 0;
int fakeA0 = 512;
int fakeA1 = 102;
std::vector<int> analogPinsRead;
FakeSerial Serial;
#include "../../arduino/analog_telemetry/analog_telemetry.ino"

static std::string sampleAt(uint32_t at) {
  fakeMillis = at;
  Serial.output.clear();
  loop();
  return Serial.output;
}
int main() {
  setup();
  assert(Serial.baud == 115200);
  assert(sampleAt(0) == "2.502,0.499\n");
  assert(analogPinsRead == std::vector<int>({A0, A1}));
  assert(sampleAt(19).empty());
  fakeA0 = 0;
  fakeA1 = 1023;
  assert(sampleAt(20) == "0.000,5.000\n");
  assert(sampleAt(200) == "0.000,5.000\n");
  assert(sampleAt(201).empty());
  fakeMillis = 0xFFFFFFF0;
  setup();
  assert(sampleAt(0xFFFFFFF0) == "0.000,5.000\n");
  assert(sampleAt(3).empty());
  assert(sampleAt(4) == "0.000,5.000\n");
  std::cout << "Analog firmware checks passed\n";
}
