#ifndef TEST_ARDUINO_H
#define TEST_ARDUINO_H

#include <stdint.h>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

static const int A0 = 14;
static const int A1 = 15;
extern uint32_t fakeMillis;
extern int fakeA0;
extern int fakeA1;
extern std::vector<int> analogPinsRead;

inline unsigned long millis() { return fakeMillis; }
inline int analogRead(int pin) {
  analogPinsRead.push_back(pin);
  return pin == A0 ? fakeA0 : fakeA1;
}

class FakeSerial {
 public:
  unsigned long baud = 0;
  std::string output;
  void begin(unsigned long value) { baud = value; }
  void print(float value, int decimals) {
    std::ostringstream stream;
    stream << std::fixed << std::setprecision(decimals) << value;
    output += stream.str();
  }
  void print(char value) { output += value; }
  void print(const char *value) { output += value; }
  void print(uint32_t value) { output += std::to_string(value); }
  void println() { output += '\n'; }
};

extern FakeSerial Serial;

#endif
