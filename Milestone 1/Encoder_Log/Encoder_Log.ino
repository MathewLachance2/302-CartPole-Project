// Adafruit ItsyBitsy M0 Express. Serial: 115200 baud.
// Encoder only. Output: Arduino timestamp in microseconds, raw encoder count.

#include <RotaryEncoder.h>

const int POLE_ENC_A = 10;
const int POLE_ENC_B = 11;

// SAMPLING RATE FOR THE ENCODER (microseconds)
const unsigned long sampleIntervalUs = 5000;  // Target: 200 readings/second.

RotaryEncoder encoder(
  POLE_ENC_A,
  POLE_ENC_B,
  RotaryEncoder::LatchMode::FOUR3
);

unsigned long lastSampleUs = 0;

void setup() {
  Serial.begin(115200);
  pinMode(POLE_ENC_A, INPUT_PULLUP);
  pinMode(POLE_ENC_B, INPUT_PULLUP);
  encoder.tick();
  encoder.setPosition(0);
  lastSampleUs = micros();
}

void loop() {
  encoder.tick();

  unsigned long now = micros();
  if (now - lastSampleUs >= sampleIntervalUs) {
    lastSampleUs = now;
    long counts = encoder.getPosition();
    Serial.print(now);
    Serial.print(',');
    Serial.println(counts);
  }
}
