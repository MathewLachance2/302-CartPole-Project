// Board: Adafruit ItsyBitsy M0 Express.
// Serial Monitor: 115200 baud, Newline (or Both NL & CR).
// Start with the pendulum hanging still and enough track in the chosen direction.
// 50 = one +50 mm trial, then return to the starting position.
// 50* = four +50 mm trials, EACH followed by return and automatic settling.
// -50* does the same in the negative direction. Distance includes accel/decel.
//
// Measurement: finish stopping -> first zero crossing -> following peak.
// This deliberately excludes any peak before that post-stop zero crossing.
// The encoder is decoded continuously; measurements are scheduled at 200 Hz.
// Samples and timing statistics are retained in memory during each trial.
// Serial prints only the final amplitude per trial, after measurement finishes.
// Stop/return use commanded steps, not physical position feedback or homing.

#include <AccelStepper.h>
#include <RotaryEncoder.h>
#include <math.h>

const unsigned long sampleRateHz = 200;  // Samples per second: change here.
const unsigned long sampleIntervalUs = 1000000UL / sampleRateHz;  // 5000 us.

const int STEP_PIN = 9;
const int DIR_PIN = 7;
const int POLE_ENC_A = 10;
const int POLE_ENC_B = 11;

const float stepsPerMM = 40.0;
const float encoderStepsPerDegree = 1.667;
const float maxSpeed = 7000;          // Steps/s (175 mm/s).
const float maxAcceleration = 100000; // Steps/s^2 (2500 mm/s^2).
const float maxDistanceMM = 300;      // Also check available physical travel.
const unsigned long peakTimeoutUs = 3000000UL;     // From the end of motion.
const unsigned long peakConfirmUs = 20000UL;       // Falling for 20 ms.
const unsigned long settleHoldUs = 2000000UL;      // Still for 2 seconds.
const unsigned long settleTimeoutUs = 30000000UL; // Pause after 30 seconds.
const long settleToleranceCounts = 1;             // About 0.6 degrees.
const unsigned int maxSamples = 1200;             // 6 seconds at 200 Hz.

enum RunState { IDLE, OUTBOUND, WAIT_ZERO, WAIT_PEAK, REPORT, RETURNING,
                SETTLING, PAUSED };
RunState state = IDLE;

struct Sample {
  unsigned long timeUs;
  long counts;
};

Sample samples[maxSamples];
unsigned int storedSamples = 0;
bool bufferFull = false;
unsigned long sampleCount = 0;
unsigned long trialStartUs = 0;
unsigned long stopTimeUs = 0;
unsigned long nextSampleUs = 0;
unsigned long lastSampleUs = 0;
unsigned long maxGapUs = 0;
unsigned long missedSamples = 0;

long distanceSteps = 0;
int totalTrials = 1;
int currentTrial = 1;
float maxAngles[4];
float measuredRates[4];
unsigned long largestGaps[4];
unsigned long missedCounts[4];
const char *trialStatuses[4];

int zeroSide = 0;
int peakSide = 0;
long peakCounts = 0;
unsigned long peakTimeUs = 0;
unsigned long fallingSinceUs = 0;
bool falling = false;
unsigned long settlingSinceUs = 0;
unsigned long stillSinceUs = 0;
long lastSettleCounts = 0;

String serialInput;
bool ignoreInputLine = false;

RotaryEncoder encoder(POLE_ENC_A, POLE_ENC_B,
                      RotaryEncoder::LatchMode::FOUR3);

AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

void printPrompt() {
  Serial.println("Pendulum still: enter signed distance in mm (up to 300).");
  Serial.println("50 = one trial; 50* = four automatic outward/return trials.");
}

void setup() {
  Serial.begin(115200);

  pinMode(STEP_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);

  pinMode(DIR_PIN, OUTPUT);
  digitalWrite(DIR_PIN, HIGH);

  pinMode(POLE_ENC_A, INPUT_PULLUP);
  pinMode(POLE_ENC_B, INPUT_PULLUP);

  motor.setMinPulseWidth(3);
  motor.setMaxSpeed(maxSpeed);
  motor.setAcceleration(maxAcceleration);

  while (!Serial) {
    encoder.tick();
  }

  printPrompt();
}

void storeSample(unsigned long now, long counts) {
  if (sampleCount > 0) {
    unsigned long gap = now - lastSampleUs;
    if (gap > maxGapUs) maxGapUs = gap;
  }

  lastSampleUs = now;
  sampleCount++;

  if (storedSamples < maxSamples) {
    samples[storedSamples].timeUs = now - trialStartUs;
    samples[storedSamples].counts = counts;
    storedSamples++;
  } else {
    bufferFull = true;
  }
}

void startMove() {
  storedSamples = 0;
  sampleCount = 0;
  maxGapUs = 0;
  missedSamples = 0;
  bufferFull = false;
  peakCounts = 0;
  falling = false;

  encoder.tick();
  trialStartUs = micros();
  nextSampleUs = trialStartUs + sampleIntervalUs;

  storeSample(trialStartUs, encoder.getPosition());

  motor.moveTo(distanceSteps);
  state = OUTBOUND;
}

void beginSettling() {
  encoder.tick();

  settlingSinceUs = micros();
  stillSinceUs = settlingSinceUs;
  lastSettleCounts = encoder.getPosition();
  nextSampleUs = settlingSinceUs + sampleIntervalUs;

  state = SETTLING;
}

void handleCommand() {
  serialInput.trim();

  if (state == PAUSED) {
    if (serialInput == "c" || serialInput == "C") {
      beginSettling();
    } else if (serialInput == "x" || serialInput == "X") {
      state = IDLE;
      printPrompt();
    } else {
      Serial.println("Send c to recheck settling, or x to end this batch.");
    }
    return;
  }

  if (serialInput.length() == 0) return;

  bool fourTrials = serialInput.endsWith("*");
  if (fourTrials) serialInput.remove(serialInput.length() - 1);

  float distanceMM = serialInput.toFloat();

  if (!isfinite(distanceMM) || abs(distanceMM) > maxDistanceMM ||
      abs(distanceMM) < 1.0 / stepsPerMM) {
    Serial.println("Enter a nonzero distance up to 300 mm: 50, -50, or 50*.");
    return;
  }

  distanceSteps = (long)(abs(distanceMM) * stepsPerMM + 0.5);
  if (distanceMM < 0) distanceSteps = -distanceSteps;

  totalTrials = fourTrials ? 4 : 1;
  currentTrial = 1;

  // Calibrate once per batch; do not redefine zero while it is swinging.
  encoder.tick();
  encoder.setPosition(0);
  motor.setCurrentPosition(0); // Software origin, not physical homing.

  startMove();
}

void finishMeasurement(const char *status, bool peakFound) {
  int i = currentTrial - 1;

  trialStatuses[i] = bufferFull ? "BUFFER_FULL" : status;
  maxAngles[i] = (peakFound && !bufferFull)
      ? peakCounts / encoderStepsPerDegree : -1;

  unsigned long elapsed = lastSampleUs - trialStartUs;
  measuredRates[i] = elapsed > 0
      ? (sampleCount - 1) * 1000000.0 / elapsed : 0;

  largestGaps[i] = maxGapUs;
  missedCounts[i] = missedSamples;

  state = REPORT;
}

void measurePeak(unsigned long now, long counts) {
  int side = (counts > 0) - (counts < 0);

  if (state == WAIT_ZERO) {
    // A sign change counts as crossing, even if no sample is exactly zero.
    // If it stopped at encoder zero, count departure as the crossing at stop.
    if (side == 0 || side == zeroSide) return;

    peakSide = side;
    peakCounts = abs(counts);
    peakTimeUs = now - trialStartUs;
    falling = false;
    state = WAIT_PEAK;
    return;
  }

  // Keep the peak on THIS side of zero; do not accidentally take a later swing.
  long excursion = counts * peakSide;

  if (excursion >= peakCounts) {
    if (excursion > peakCounts) peakTimeUs = now - trialStartUs;
    peakCounts = excursion;
    falling = false;
  } else {
    // Confirm at least one count of reversal for 20 ms (handles flat peaks).
    if (!falling) {
      falling = true;
      fallingSinceUs = now;
    }

    if (now - fallingSinceUs >= peakConfirmUs) {
      finishMeasurement("OK", true);
    }
  }
}

void reportAndReturn() {
  int i = currentTrial - 1;

  Serial.print("Trial ");
  Serial.print(currentTrial);
  Serial.print(": ");

  if (maxAngles[i] >= 0) {
    Serial.print(maxAngles[i], 2);
    Serial.println(" deg");
  } else {
    Serial.print("N/A (");
    Serial.print(trialStatuses[i]);
    Serial.println(')');
  }

  motor.moveTo(0);
  state = RETURNING;
}

void checkSettled(unsigned long now, long counts) {
  if (abs(counts) > settleToleranceCounts || counts != lastSettleCounts) {
    stillSinceUs = now;
  }

  lastSettleCounts = counts;

  if (now - stillSinceUs >= settleHoldUs) {
    if (currentTrial < totalTrials) {
      currentTrial++;
      startMove();
    } else {
      state = IDLE;
      printPrompt();
    }
  } else if (now - settlingSinceUs >= settleTimeoutUs) {
    state = PAUSED;
    Serial.println("Not settled after 30 s. Batch paused at starting position.");
    Serial.println("Let it settle, then send c to recheck; x ends the batch.");
  }
}

void loop() {
  encoder.tick();

  // Read without blocking. Discard commands during an active trial.
  if (Serial.available() > 0) {
    char input = Serial.read();

    if (state != IDLE && state != PAUSED) {
      ignoreInputLine = true;
    }

    if (input == '\n') {
      if (!ignoreInputLine) handleCommand();
      serialInput = "";
      ignoreInputLine = false;
    } else if (input != '\r' && !ignoreInputLine) {
      if (serialInput.length() < 20) {
        serialInput += input;
      } else {
        ignoreInputLine = true;
      }
    }
  }

  if (state == OUTBOUND || state == RETURNING) {
    motor.run();
    encoder.tick();

    if (motor.distanceToGo() == 0 && motor.speed() == 0) {
      if (state == OUTBOUND) {
        stopTimeUs = micros();
        long counts = encoder.getPosition();
        zeroSide = (counts > 0) - (counts < 0);
        state = WAIT_ZERO; // Peak detection only starts after stopping.
      } else {
        beginSettling();
      }
    }
  }

  if (state == OUTBOUND || state == WAIT_ZERO ||
      state == WAIT_PEAK || state == SETTLING) {
    unsigned long now = micros();

    if ((long)(now - nextSampleUs) >= 0) {
      // Keep the schedule anchored. Skip missed slots, never fabricate samples.
      unsigned long skipped = (now - nextSampleUs) / sampleIntervalUs;
      nextSampleUs += (skipped + 1) * sampleIntervalUs;

      long counts = encoder.getPosition();

      if (state == SETTLING) {
        checkSettled(now, counts);
      } else {
        missedSamples += skipped;
        storeSample(now, counts);

        if (state == WAIT_ZERO || state == WAIT_PEAK) {
          measurePeak(now, counts);
        }
      }
    }
  }

  if ((state == WAIT_ZERO || state == WAIT_PEAK) &&
      micros() - stopTimeUs >= peakTimeoutUs) {
    finishMeasurement(
      state == WAIT_ZERO ? "NO_ZERO_CROSSING" : "NO_CONFIRMED_PEAK",
      false
    );
  }

  if (state == REPORT) {
    reportAndReturn();
  }
}