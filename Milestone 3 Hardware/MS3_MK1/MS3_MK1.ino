// Adafruit ItsyBitsy M0 Express. Serial: 115200 baud, Newline.
// Manually place the cart near either end and let the pendulum hang still.
// For decay, send a signed distance such as 50 decay and save the CSV report.
// Then send XXX to return using the trial's stored step counts.
// Send stop to ramp down or ! to cancel step pulses immediately.
// The older feedback modes and encoder CSV replay are also available. See status.

#include <AccelStepper.h>
#include <RotaryEncoder.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// Rig pin assignments and default motion and encoder calibration.
const int STEP_PIN = 9, DIR_PIN = 7, ENC_A = 10, ENC_B = 11;
float STEPS_PER_MM = 40.0f, COUNTS_PER_DEGREE = 1.667f;
float ANGLE_SIGN = 1;  // +angle must mean the bob leans toward +motor travel.
float MAX_SPEED = 7000.0f, MAX_ACCEL = 100000.0f;  // steps/s, steps/s^2
float TRACK_MM = 300.0f, END_MARGIN_MM = 5.0f, ANGLE_LIMIT_DEG = 60.0f;
float PERIOD_S = 0.70f, HOLD_S = 0.70f, CONTROL_S = 10.0f;
float BASELINE_AUTO_RETURN = 1;
float MAX_TUNE_HZ = 10.0f;
float RATE_FILTER_S = 0.03f, ENVELOPE_FILTER_S = 0.20f;
const float RAD_PER_DEG = PI / 180.0f;
float SAMPLE_MS = 10.0f;
uint32_t SAMPLE_US = 10000;
uint32_t decaySampleUs = 10000, decayFreeUs = 5000000, decaySettleUs = 1000000;
float decaySpeed = 7000.0f, decayAcceleration = 100000.0f;
bool decayBrakeReturn = false;

AccelStepper motor(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);
RotaryEncoder encoder(ENC_A, ENC_B, RotaryEncoder::LatchMode::FOUR3);

enum State { IDLE, LAUNCH, FREE, HOLD, CONTROL, STOPPING, REPORT, RETURNING };
enum Mode { BASELINE, QUASH, MAINTAIN, TUNE, DECAY };

State state = IDLE;
Mode mode = BASELINE;

// Acceleration feedback [mm/s^2], plus slow cart centering:
// a = gp*theta + gd*rate - cartKp*(x - reference) - cartKd*v.
float gp = 0, gd = 0, fixedGp = 0, fixedGd = 0;
float cartKp = 0, cartKd = 0, cartReference = 0, feedbackDamping = 0;
float CART_W2 = 1.0f, CART_DAMPING = 2.0f, QUASH_DAMPING = 1.4f;

float manualGains = 0, centeringEnabled = 1;
bool modelReady = false;
bool hasTrialOrigin = false, returnInProgress = false;
uint32_t returnTickUs = 0;

struct BrakedReturn {
  long distance = 0;  // A nonzero distance marks an active braking profile.
  float brakeDistance, peakSpeed, accelDistance, deceleration, endTime;
  float stepTime;
  uint32_t stepIntervalUs, lastStepUs;
};
BrakedReturn brakedReturn;

float damping = 0;  // c [1/s] in theta'' + c*theta' + wn^2*theta = -beta*x''.
float amplitudeStrength = 2.0f;  // Set with "amp 0" for constant-gain trials.
float recordedDegreesPerCount = ANGLE_SIGN / COUNTS_PER_DEGREE;
float naturalW2 = 0, targetW = 0, beta = 0;
float theta = 0, previousTheta = 0, rate = 0;
float initialAmplitude = 0, envelope = 0;
float inwardSign = 1, launchSpeed = 0;

uint32_t startUs, launchEndUs, controlStartUs, stopStartUs;
uint32_t nextSampleUs, lastSampleUs;
uint32_t maxGapUs = 0, missedSamples = 0, limitEvents = 0;
uint32_t belowSinceUs = 0, lastEncoderMotionUs = 0;

long previousCount = 0;
const char *stopReason = "complete";

// 18 kB, leaving room on the M0's 32 kB RAM. Never print mid-motion.
struct Sample {
  uint32_t us;
  float damping;
  int16_t steps;
  int16_t counts;
};

const int MAX_SAMPLES = 1500;
Sample samples[MAX_SAMPLES];
int sampleCount = 0;

// Positive peaks from complete cycles; used for frequency and log decrement.
const int MAX_PEAKS = 128;  // Room for 10 seconds of peaks at up to 10 Hz.
float peakTimes[MAX_PEAKS], peakAngles[MAX_PEAKS];
float peakAngle = 0, peakTime = 0;
int peakCount = 0, peakPhase = 0;

char command[384];
int commandLength = 0;
bool commandOverflow = false, commandStartedBusy = false;

// Keep wire order in sync with cartpole_settings.py. Bounds retain the
// original rig's maximum motor speed, acceleration and travel envelope.
struct Setting {
  float *value;
  float low, high;
};
const Setting settings[] = {
  {&MAX_SPEED, 100, 7000}, {&MAX_ACCEL, 1000, 100000},
  {&TRACK_MM, 20, 300}, {&END_MARGIN_MM, 5, 50},
  {&STEPS_PER_MM, 1, 200}, {&COUNTS_PER_DEGREE, 0.1f, 100},
  {&ANGLE_SIGN, -1, 1}, {&ANGLE_LIMIT_DEG, 5, 60},
  {&PERIOD_S, 0.2f, 3}, {&HOLD_S, 0.1f, 3},
  {&CONTROL_S, 0.1f, 30}, {&SAMPLE_MS, 5, 50},
  {&MAX_TUNE_HZ, 0.1f, 10}, {&BASELINE_AUTO_RETURN, 0, 1},
  {&RATE_FILTER_S, 0, 1}, {&ENVELOPE_FILTER_S, 0, 2},
  {&CART_W2, 0.01f, 10}, {&CART_DAMPING, 0, 10},
  {&centeringEnabled, 0, 1}, {&amplitudeStrength, 0, 5},
  {&QUASH_DAMPING, 0.1f, 3}, {&manualGains, 0, 1},
  {&fixedGp, -10000, 10000}, {&fixedGd, -1000, 1000},
};
enum SettingIndex {
  TRACK_SETTING = 2, MARGIN_SETTING, STEPS_SETTING, COUNTS_SETTING, SIGN_SETTING,
  PERIOD_SETTING = 8, HOLD_SETTING, DURATION_SETTING, SAMPLE_SETTING, TUNE_LIMIT_SETTING,
};

float clampValue(float value, float low, float high) {
  return fmaxf(low, fminf(value, high));
}

void updateModel() {
  float wd = 2.0f * PI / PERIOD_S;
  naturalW2 = wd * wd + damping * damping / 4.0f;
  beta = naturalW2 / 9810.0f;
}

void help() {
  Serial.println("# MS3 DECAY: signed distance in mm, e.g. 50 decay");
  Serial.println("# MS3 DECAY SETTINGS V1");
  Serial.println("# MS3 DECAY RETURN BRAKING");
  Serial.println("# Manually position cart at one end; let pendulum hang still.");
  Serial.println("# distance_mm baseline|quash|maintain|tune [frequency_Hz]");
  Serial.println("# Examples: 49 baseline; 49 quash; -49 tune 2.0");
  Serial.print("# Baseline automatic return: ");
  Serial.println(BASELINE_AUTO_RETURN ? "on" : "off");
  Serial.println("# XXX returns to the last trial's start. Do not reposition by hand first.");
  Serial.println("# csv exports the latest trial: time_s,encoder_counts,angle_deg");
  Serial.println("# amp 0|1; center 0|1; damping c_per_s; gains gp gd; auto; status; stop; !");

  Serial.print("# damping_per_s=");
  Serial.println(damping, 5);

  Serial.print("# model_ready=");
  Serial.println(modelReady);

  Serial.print("# config_v1=");
  bool first = true;
  for (const Setting &item : settings) {
    if (!first) Serial.print(' ');
    Serial.print(*item.value, 6);
    first = false;
  }
  Serial.println();
  Serial.println("# READY");
}

// Match the ideal unfiltered fourth-order model to:
// (s^2 + desiredDamping*s + targetW^2) * (s^2 + CART_DAMPING*s + CART_W2).
// Filtering, sampling and motor limits make the measured result approximate.
void chooseGains(float desiredDamping) {
  float targetW2 = targetW * targetW;

  cartKp = targetW2 * CART_W2 / naturalW2;

  cartKd = (desiredDamping * CART_W2
            + CART_DAMPING * targetW2
            - damping * cartKp) / naturalW2;

  gp = (targetW2 + desiredDamping * CART_DAMPING + CART_W2
        - naturalW2 - damping * cartKd - cartKp) / beta;

  gd = (desiredDamping + CART_DAMPING - damping - cartKd) / beta;

  if (!centeringEnabled) {
    cartKp = cartKd = 0;
    gp = (targetW2 - naturalW2) / beta;
    gd = (desiredDamping - damping) / beta;
  }

  if (manualGains) {
    gp = fixedGp;
    gd = fixedGd;
  }
}

void beginStop(const char *reason, uint32_t now) {
  if (state == IDLE || state == REPORT || state == STOPPING) return;

  stopReason = reason;
  stopStartUs = now;
  state = STOPPING;
}

void emergencyStop() {
  if (state == IDLE || state == REPORT) return;

  stopReason = "emergency: step pulses stopped";
  if (!stopStartUs) stopStartUs = micros();

  hasTrialOrigin = false;  // Abrupt stops can lose physical position.
  motor.setSpeed(0);       // Immediate pulse cancellation, not a physical brake.
  state = REPORT;
}

void readyForCommand() {
  // Never let input accumulated during motion become a new launch.
  while (Serial.available()) Serial.read();

  commandLength = 0;
  commandOverflow = commandStartedBusy = false;
  state = IDLE;
  help();
}

float brakedReturnTime(long remaining) {
  // Invert the motion profile to find when each step position is reached.
  const float travelled = brakedReturn.distance - remaining;
  if (travelled <= brakedReturn.accelDistance) {
    return sqrtf(2 * travelled / decayAcceleration);
  }
  if (remaining >= brakedReturn.brakeDistance) {
    return brakedReturn.peakSpeed / decayAcceleration
           + (travelled - brakedReturn.accelDistance) / brakedReturn.peakSpeed;
  }
  return brakedReturn.endTime - sqrtf(2 * remaining / brakedReturn.deceleration);
}

void scheduleBrakedReturnStep(long position) {
  const float nextTime = brakedReturnTime(labs(position) - 1);
  brakedReturn.stepIntervalUs = (uint32_t)ceilf((nextTime - brakedReturn.stepTime) * 1e6f);
  brakedReturn.stepTime = nextTime;
  motor.setSpeed((position > 0 ? -1e6f : 1e6f) / brakedReturn.stepIntervalUs);
}

void startBrakedReturn(long position) {
  brakedReturn.distance = labs(position);
  brakedReturn.brakeDistance = brakedReturn.distance / 5.0f;
  const float approachDistance = brakedReturn.distance - brakedReturn.brakeDistance;
  const float peakSpeedSquared = fminf(decaySpeed * decaySpeed,
      2 * decayAcceleration * fminf(approachDistance, brakedReturn.brakeDistance));
  brakedReturn.peakSpeed = sqrtf(peakSpeedSquared);
  brakedReturn.accelDistance = peakSpeedSquared / (2 * decayAcceleration);
  brakedReturn.deceleration = fminf(decayAcceleration,
      peakSpeedSquared / (2 * brakedReturn.brakeDistance));
  brakedReturn.endTime = brakedReturn.peakSpeed / decayAcceleration
      + (approachDistance - brakedReturn.accelDistance) / brakedReturn.peakSpeed
      + brakedReturn.peakSpeed / brakedReturn.deceleration;

  motor.setCurrentPosition(position);
  motor.setMaxSpeed(brakedReturn.peakSpeed);
  brakedReturn.stepTime = 0;
  scheduleBrakedReturnStep(position);
  brakedReturn.lastStepUs = micros();
}

void runBrakedReturn() {
  const uint32_t now = micros();
  if (now - brakedReturn.lastStepUs < brakedReturn.stepIntervalUs || !motor.runSpeed()) return;

  const long position = motor.currentPosition();
  if (position == 0) {
    motor.setCurrentPosition(0);
    state = REPORT;
  } else {
    brakedReturn.lastStepUs = now;
    scheduleBrakedReturnStep(position);
  }
}

void returnToStart() {
  // REPORT is allowed so baseline can return without an intermediate READY.
  if (state != IDLE && state != REPORT) return;

  if (!hasTrialOrigin) {
    Serial.println("# No valid recorded start. Manually position the cart and run a trial first.");
    return;
  }

  const long position = motor.currentPosition();
  float inwardMM = inwardSign * position / STEPS_PER_MM;

  if (inwardMM < 0 || inwardMM > TRACK_MM) {
    hasTrialOrigin = false;
    Serial.println("# Position estimate is outside the track; return cancelled.");
    return;
  }

  if (position == 0) {
    Serial.println("# Already at the recorded start.");
    return;
  }

  Serial.println("# Returning to the recorded start. stop or ! cancels.");

  if (mode == DECAY && decayBrakeReturn) {
    startBrakedReturn(position);
  } else {
    motor.setMaxSpeed(MAX_SPEED);
    motor.setAcceleration(MAX_ACCEL);

    // Re-arm AccelStepper after a cancelled return whose old target was also zero.
    motor.moveTo(position);
    motor.moveTo(0);
  }

  // Preserve both position counters: zero remains the last trial's origin.
  returnInProgress = true;
  returnTickUs = micros();
  stopStartUs = 0;
  stopReason = "return complete";
  state = RETURNING;
}

void finishReturn() {
  Serial.print("# Return: ");
  Serial.println(stopReason);

  Serial.print("# Estimated position from start (mm): ");
  Serial.println(motor.currentPosition() / STEPS_PER_MM, 3);

  returnInProgress = false;
  brakedReturn.distance = 0;
  readyForCommand();
}

void recordSample(uint32_t now, bool feedbackApplied) {
  if (sampleCount == MAX_SAMPLES) {
    beginStop("data buffer full", now);
    return;
  }

  // NaN marks a sample with no feedback command.
  samples[sampleCount++] = {
    now - startUs,
    feedbackApplied ? feedbackDamping : NAN,
    (int16_t)motor.currentPosition(),
    (int16_t)encoder.getPosition()
  };
}

// Replay stored encoder samples through serial using the calibration at capture.
void printEncoderCSV() {
  if (state != IDLE) return;

  if (sampleCount == 0) {
    Serial.println("# No recorded samples. Run a trial first.");
    return;
  }

  Serial.println("# BEGIN");
  Serial.println("time_s,encoder_counts,angle_deg");

  for (int i = 0; i < sampleCount; i++) {
    const Sample &s = samples[i];

    Serial.print(s.us * 1e-6f, 5);
    Serial.print(',');
    Serial.print(s.counts);
    Serial.print(',');
    Serial.println(recordedDegreesPerCount * s.counts, 3);

    encoder.tick();
  }

  Serial.println("# END");
  readyForCommand();
}

void trackPeak(uint32_t now) {
  float noise = 1.2f * RAD_PER_DEG;

  if (peakPhase == 0 && theta < -noise) peakPhase = 1;

  if (peakPhase == 1 && theta > noise) {
    peakPhase = 2;
    peakAngle = 0;
  }

  if (peakPhase != 2) return;

  if (theta > peakAngle) {
    peakAngle = theta;
    peakTime = (now - controlStartUs) * 1e-6f;
  }

  if (theta < -noise) {
    if (peakCount < MAX_PEAKS && peakAngle > 3.0f * RAD_PER_DEG) {
      peakTimes[peakCount] = peakTime;
      peakAngles[peakCount++] = peakAngle;
    }

    peakPhase = 1;
  }
}

// Baseline fits ln(positive peak amplitude) = intercept - (c/2)*time.
bool identifyDamping() {
  if (peakCount < 5 || missedSamples
      || strcmp(stopReason, "complete") != 0) return false;

  float sumT = 0, sumY = 0, sumTT = 0, sumTY = 0;

  for (int i = 0; i < peakCount; i++) {
    float t = peakTimes[i];
    float y = logf(peakAngles[i]);

    sumT += t;
    sumY += y;
    sumTT += t * t;
    sumTY += t * y;
  }

  float denominator = peakCount * sumTT - sumT * sumT;
  if (denominator <= 0) return false;

  float estimate =
      -2.0f * (peakCount * sumTY - sumT * sumY) / denominator;

  if (!isfinite(estimate) || estimate < -0.05f || estimate > 5.0f) {
    return false;
  }

  damping = fmaxf(0.0f, estimate);
  modelReady = true;
  updateModel();
  return true;
}

void setFeedbackSpeed(float desiredMMs, float dt) {
  float desiredSteps =
      clampValue(desiredMMs * STEPS_PER_MM, -MAX_SPEED, MAX_SPEED);

  float currentSpeed = motor.speed();
  float change = MAX_ACCEL * dt;

  float speed =
      clampValue(desiredSteps, currentSpeed - change, currentSpeed + change);

  if (state == CONTROL
      && (fabsf(desiredSteps - desiredMMs * STEPS_PER_MM) > 1
          || fabsf(speed - desiredSteps) > 1)) {
    limitEvents++;
  }

  motor.setSpeed(speed);
}

void controlTick(uint32_t now) {
  bool feedbackApplied = false;
  uint32_t gap = now - lastSampleUs;
  lastSampleUs = now;

  if (gap > maxGapUs) maxGapUs = gap;

  float dt = gap * 1e-6f;

  previousTheta = theta;
  theta =
      ANGLE_SIGN * encoder.getPosition() / COUNTS_PER_DEGREE * RAD_PER_DEG;

  rate += dt / (RATE_FILTER_S + dt)
          * ((theta - previousTheta) / dt - rate);

  uint32_t sampleUs = mode == DECAY ? decaySampleUs : SAMPLE_US;
  if (gap > 2 * sampleUs) {
    missedSamples++;
    beginStop("sampling overrun", now);
  }

  if ((mode != DECAY || state == LAUNCH)
      && fabsf(theta) > ANGLE_LIMIT_DEG * RAD_PER_DEG) {
    beginStop("angle limit", now);
  }

  if (state == FREE && now - launchEndUs >= decayFreeUs) state = REPORT;

  if (state == HOLD) {
    initialAmplitude = fmaxf(initialAmplitude, fabsf(theta));

    if ((now - launchEndUs) * 1e-6f >= HOLD_S) {
      if (initialAmplitude < 3 * RAD_PER_DEG) {
        beginStop("swing too small to measure", now);
      } else {
        controlStartUs = now;
        envelope = initialAmplitude;
        state = CONTROL;
      }
    }
  }

  if (state == CONTROL) {
    trackPeak(now);

    if (fabsf(theta) > 0.1f * initialAmplitude) {
      belowSinceUs = 0;
    } else if (!belowSinceUs) {
      belowSinceUs = now;
    }

    if ((now - controlStartUs) * 1e-6f >= CONTROL_S) {
      beginStop("complete", now);
    }
  }

  if (state == CONTROL && mode != BASELINE) {
    float instantaneousAmplitude = hypotf(theta, rate / targetW);

    envelope += dt / (ENVELOPE_FILTER_S + dt)
                * (instantaneousAmplitude - envelope);

    feedbackDamping =
        mode == QUASH ? QUASH_DAMPING * targetW :
        manualGains ? 0.0f :
        clampValue(
          amplitudeStrength * (envelope / initialAmplitude - 1.0f),
          -2.0f,
          2.0f
        );

    chooseGains(feedbackDamping);
    feedbackApplied = true;

    float positionMM = motor.currentPosition() / STEPS_PER_MM;
    float speedMMs = motor.speed() / STEPS_PER_MM;

    float acceleration =
        gp * theta + gd * rate
        - cartKp * (positionMM - cartReference)
        - cartKd * speedMMs;

    float boundedAcceleration = clampValue(
      acceleration,
      -MAX_ACCEL / STEPS_PER_MM,
      MAX_ACCEL / STEPS_PER_MM
    );

    if (boundedAcceleration != acceleration) limitEvents++;

    setFeedbackSpeed(speedMMs + boundedAcceleration * dt, dt);

    float x = inwardSign * motor.currentPosition() / STEPS_PER_MM;
    float v = inwardSign * motor.speed() / STEPS_PER_MM;

    float braking =
        v * v / (2 * MAX_ACCEL / STEPS_PER_MM)
        + fabsf(v) * 0.02f
        + END_MARGIN_MM;

    if ((v > 0 && x + braking >= TRACK_MM)
        || (v < 0 && x - braking <= 0)) {
      beginStop("travel margin reached", now);
    }
  } else if (state == STOPPING) {
    setFeedbackSpeed(0, dt);

    if (motor.speed() == 0) state = REPORT;
  }

  recordSample(now, feedbackApplied);
}

const char *phaseName(uint32_t elapsed) {
  if (stopStartUs && elapsed >= stopStartUs - startUs) return "stop";
  if (controlStartUs && elapsed >= controlStartUs - startUs) return "control";
  if (launchEndUs && elapsed >= launchEndUs - startUs) {
    return mode == DECAY ? "free" : "hold";
  }
  return "launch";
}

void printDecayReport() {
  Serial.println("# BEGIN");
  Serial.print("# stop_reason=");
  Serial.println(stopReason);
  Serial.print("# missed_samples=");
  Serial.println(missedSamples);
  Serial.println("time_s,phase,cart_mm_est,angle_deg,encoder_counts");

  for (int i = 0; i < sampleCount; i++) {
    const Sample &sample = samples[i];
    Serial.print(sample.us * 1e-6f, 6);
    Serial.print(',');
    Serial.print(phaseName(sample.us));
    Serial.print(',');
    Serial.print(sample.steps / STEPS_PER_MM, 3);
    Serial.print(',');
    Serial.print(recordedDegreesPerCount * sample.counts, 3);
    Serial.print(',');
    Serial.println(sample.counts);
    encoder.tick();
  }

  Serial.println("# END");
  readyForCommand();
}

void printReport() {
  bool fitted = mode == BASELINE && identifyDamping();

  float launchSeconds =
      launchEndUs ? (launchEndUs - startUs) * 1e-6f : 0;

  bool standardLaunch =
      launchEndUs
      && fabsf(launchSeconds - PERIOD_S / 2) <= 0.015f
      && launchSpeed >= 6000
      && initialAmplitude >= 15 * RAD_PER_DEG;

  Serial.println("# BEGIN");

  Serial.print("# stop_reason=");
  Serial.println(stopReason);

  Serial.print("# mode=");
  Serial.println((int)mode);  // 0 baseline, 1 quash, 2 maintain, 3 tune

  Serial.print("# initial_amplitude_deg=");
  Serial.println(initialAmplitude / RAD_PER_DEG, 3);

  Serial.print("# launch_duration_s=");
  Serial.println(launchSeconds, 5);

  Serial.print("# standard_launch=");
  Serial.println(standardLaunch);

  Serial.print("# model_damping_per_s=");
  Serial.println(damping, 6);

  Serial.print("# baseline_fit_updated=");
  Serial.println(fitted);

  Serial.print("# requested_frequency_Hz=");
  Serial.println(targetW / (2 * PI), 5);

  Serial.print("# adaptive_amplitude=");
  Serial.println(
    !manualGains && amplitudeStrength > 0 && mode >= MAINTAIN
  );

  Serial.print("# cart_centering=");
  Serial.println(centeringEnabled);

  Serial.print("# max_sample_gap_us=");
  Serial.println(maxGapUs);

  Serial.print("# missed_samples=");
  Serial.println(missedSamples);

  Serial.print("# limit_events=");
  Serial.println(limitEvents);

  Serial.print("# model_omega_n_squared=");
  Serial.println(naturalW2, 6);

  Serial.print("# model_beta_per_mm=");
  Serial.println(beta, 8);

  Serial.print("# rate_filter_s=");
  Serial.println(RATE_FILTER_S, 3);

  if (peakCount >= 2) {
    Serial.print("# measured_frequency_Hz=");
    Serial.println(
      (peakCount - 1) / (peakTimes[peakCount - 1] - peakTimes[0]),
      5
    );

    float sum = 0;

    for (int i = 0; i < peakCount; i++) {
      sum += peakAngles[i];
    }

    Serial.print("# mean_peak_amplitude_deg=");
    Serial.println(sum / peakCount / RAD_PER_DEG, 3);
  }

  if (mode == QUASH
      && !strcmp(stopReason, "complete")
      && belowSinceUs
      && stopStartUs - belowSinceUs >= (uint32_t)(PERIOD_S * 1e6f)) {
    Serial.print("# settling_time_s=");
    Serial.println((belowSinceUs - controlStartUs) * 1e-6f, 5);
  }

  Serial.print("# cart_reference_mm_est=");
  Serial.println(cartReference, 3);

  Serial.println(
    "time_s,phase,cart_mm_est,angle_deg,"
    "gp_mm_s2_rad,gd_mm_s_rad,cart_kp_per_s2,cart_kd_per_s,encoder_counts"
  );

  for (int i = 0; i < sampleCount; i++) {
    Sample &s = samples[i];
    const char *phase = phaseName(s.us);

    Serial.print(s.us * 1e-6f, 5);
    Serial.print(',');
    Serial.print(phase);
    Serial.print(',');
    Serial.print(s.steps / STEPS_PER_MM, 3);
    Serial.print(',');
    Serial.print(ANGLE_SIGN * s.counts / COUNTS_PER_DEGREE, 3);
    Serial.print(',');

    if (isfinite(s.damping)) {
      chooseGains(s.damping);
    } else {
      gp = gd = cartKp = cartKd = 0;
    }

    Serial.print(gp, 5);
    Serial.print(',');
    Serial.print(gd, 5);
    Serial.print(',');
    Serial.print(cartKp, 5);
    Serial.print(',');
    Serial.print(cartKd, 5);
    Serial.print(',');
    Serial.println(s.counts);

    encoder.tick();
  }

  Serial.println("# END");
  if (BASELINE_AUTO_RETURN && mode == BASELINE && !strcmp(stopReason, "complete")) {
    returnToStart();
    if (returnInProgress) return;
  }
  readyForCommand();
}

bool number(char *token, float &value) {
  if (!token) return false;

  char *end;
  value = strtof(token, &end);

  return end != token && *end == '\0' && isfinite(value);
}

bool applyDecaySettings() {
  // Wire order matches RunSettings.board_command().
  float speed, acceleration, sampleMs, freeSeconds, settleSeconds;
  if (!number(strtok(nullptr, " \t"), speed)
      || !number(strtok(nullptr, " \t"), acceleration)
      || !number(strtok(nullptr, " \t"), sampleMs)
      || !number(strtok(nullptr, " \t"), freeSeconds)
      || !number(strtok(nullptr, " \t"), settleSeconds)) return false;

  const char *brakeReturn = strtok(nullptr, " \t");
  if (brakeReturn && (strcmp(brakeReturn, "0") && strcmp(brakeReturn, "1"))) return false;
  if (strtok(nullptr, " \t")) return false;

  if (speed < 100 || speed > 7000 || acceleration < 1000 || acceleration > 100000
      || sampleMs < 5 || sampleMs > 50 || freeSeconds < 0.1f || freeSeconds > 30
      || settleSeconds < 0.1f || settleSeconds > 10) return false;

  uint32_t sampleUs = lroundf(sampleMs * 1000);
  uint32_t freeUs = lroundf(freeSeconds * 1000000);
  if (freeUs >= (MAX_SAMPLES - 5) * sampleUs) return false;

  decaySpeed = speed;
  decayAcceleration = acceleration;
  decaySampleUs = sampleUs;
  decayFreeUs = freeUs;
  decaySettleUs = lroundf(settleSeconds * 1000000);
  decayBrakeReturn = brakeReturn && brakeReturn[0] == '1';
  return true;
}

bool applySettings() {
  float values[sizeof(settings) / sizeof(settings[0])];
  size_t index = 0;
  for (const Setting &item : settings) {
    float value;
    if (!number(strtok(nullptr, " \t"), value) || value < item.low || value > item.high) {
      return false;
    }
    if (item.value == &ANGLE_SIGN && value != -1 && value != 1) return false;
    if ((item.value == &BASELINE_AUTO_RETURN || item.value == &centeringEnabled
         || item.value == &manualGains) && value != 0 && value != 1) return false;
    values[index++] = value;
  }
  if (strtok(nullptr, " \t")) return false;

  const float track = values[TRACK_SETTING], margin = values[MARGIN_SETTING];
  const float steps = values[STEPS_SETTING], period = values[PERIOD_SETTING];
  const float hold = values[HOLD_SETTING], duration = values[DURATION_SETTING];
  const uint32_t sampleUs = lroundf(values[SAMPLE_SETTING] * 1000);
  const float sampleMs = sampleUs / 1000.0f, tuneHz = values[TUNE_LIMIT_SETTING];
  if (2 * margin >= track || track * steps > 12000.001f
      || tuneHz <= 1 / period || tuneHz * sampleMs > 100.00001f
      || (hold + duration) * 1000 >= (MAX_SAMPLES - 2) * sampleMs) return false;

  if (track != TRACK_MM || steps != STEPS_PER_MM || margin != END_MARGIN_MM) {
    hasTrialOrigin = false;
  }
  if (period != PERIOD_S || values[COUNTS_SETTING] != COUNTS_PER_DEGREE
      || values[SIGN_SETTING] != ANGLE_SIGN) {
    modelReady = false;
    damping = 0;
  }
  index = 0;
  for (const Setting &item : settings) *item.value = values[index++];
  SAMPLE_US = sampleUs;
  SAMPLE_MS = sampleMs;
  motor.setMaxSpeed(MAX_SPEED);
  motor.setAcceleration(MAX_ACCEL);
  updateModel();
  return true;
}

void beginTrial(long steps, float speed, float acceleration, uint32_t sampleUs) {
  Serial.println("# RUNNING: ! cancels immediately; data follow after stopping.");

  encoder.setPosition(0);
  recordedDegreesPerCount = ANGLE_SIGN / COUNTS_PER_DEGREE;
  motor.setCurrentPosition(0);  // Commanded position only, not physical homing.
  hasTrialOrigin = true;

  launchSpeed = speed;
  motor.setAcceleration(acceleration);
  motor.setMaxSpeed(speed);

  inwardSign = steps > 0 ? 1 : -1;
  cartReference = steps / STEPS_PER_MM;

  theta = previousTheta = rate = initialAmplitude = envelope = gp = gd = 0;
  sampleCount = peakCount = peakPhase = 0;
  maxGapUs = missedSamples = limitEvents = belowSinceUs = 0;
  launchEndUs = controlStartUs = stopStartUs = 0;

  stopReason = "complete";
  startUs = lastSampleUs = micros();
  nextSampleUs = startUs + sampleUs;

  state = LAUNCH;
  recordSample(startUs, false);
  motor.moveTo(steps);
}

void startTrial(float distance, float frequency) {
  if (fabsf(distance) <= END_MARGIN_MM
      || fabsf(distance) >= TRACK_MM - END_MARGIN_MM) {
    Serial.print("# Distance must satisfy ");
    Serial.print(END_MARGIN_MM, 3);
    Serial.print(" < abs(distance) < ");
    Serial.print(TRACK_MM - END_MARGIN_MM, 3);
    Serial.println(" mm.");
    return;
  }

  if (mode != BASELINE && mode != DECAY && !modelReady) {
    Serial.println(
      "# Run a baseline first, or enter known normalized damping with: damping c_per_s"
    );
    return;
  }

  uint32_t settleUs = mode == DECAY ? decaySettleUs : 500000;
  if (micros() - lastEncoderMotionUs < settleUs) {
    Serial.print("# Let the pendulum hang still for at least ");
    Serial.print(settleUs * 1e-6f, 3);
    Serial.println(" seconds.");
    return;
  }

  long steps = lroundf(distance * STEPS_PER_MM);
  if (mode == DECAY) {
    if (labs(steps) <= decaySpeed * decaySpeed / decayAcceleration) {
      Serial.println("# Distance must permit the full decay launch speed.");
      return;
    }
    float launchUs = 1e6f * (labs(steps) / decaySpeed + decaySpeed / decayAcceleration);
    if (launchUs + decayFreeUs >= (MAX_SAMPLES - 5) * decaySampleUs) {
      Serial.println("# Decay launch and free oscillation exceed the sample buffer.");
      return;
    }
    beginTrial(steps, decaySpeed, decayAcceleration, decaySampleUs);
    return;
  }

  updateModel();
  targetW = mode == TUNE ? 2 * PI * frequency : sqrtf(naturalW2);

  float halfPeriod = PERIOD_S / 2;

  float distanceAtMaxSpeed =
      MAX_SPEED * halfPeriod - MAX_SPEED * MAX_SPEED / MAX_ACCEL;

  launchSpeed = MAX_SPEED;

  if (labs(steps) <= distanceAtMaxSpeed) {
    launchSpeed = 0.5f * MAX_ACCEL
        * (halfPeriod
           - sqrtf(
             halfPeriod * halfPeriod
             - 4.0f * labs(steps) / MAX_ACCEL
           ));
  }
  float rampTime = launchSpeed / MAX_ACCEL;
  float travel = labs(steps);
  float launchTime = travel <= launchSpeed * rampTime
      ? 2 * sqrtf(travel / MAX_ACCEL) : travel / launchSpeed + rampTime;
  if (launchTime + HOLD_S + CONTROL_S + MAX_SPEED / MAX_ACCEL
      >= (MAX_SAMPLES - 5) * SAMPLE_US * 1e-6f) {
    Serial.println("# Distance must fit the sample buffer at the selected speed, acceleration and durations.");
    return;
  }

  beginTrial(steps, launchSpeed, MAX_ACCEL, SAMPLE_US);
}

void handleCommand() {
  char *first = strtok(command, " \t");
  if (!first) return;
  if (!strcmp(first, "decay_config")) {
    if (state == IDLE) {
      if (applyDecaySettings()) Serial.println("# Decay settings applied");
      else Serial.println("# Decay settings rejected: check limits and sample buffer.");
      Serial.println("# READY");
    }
    return;
  }
  if (!strcmp(first, "config")) {
    if (state == IDLE) {
      if (applySettings()) Serial.println("# Configuration applied.");
      else Serial.println("# Config rejected: check ranges and combined limits.");
      help();
    }
    return;
  }
  char *second = strtok(nullptr, " \t");
  char *third = strtok(nullptr, " \t");
  char *extra = strtok(nullptr, " \t");

  if (!strcmp(first, "stop") && !second) {
    beginStop("user stop", micros());
    return;
  }

  if (state != IDLE) return;

  float a, b;

  if ((!strcmp(first, "XXX") || !strcmp(first, "xxx")) && !second) {
    returnToStart();
    return;
  }

  if (!strcmp(first, "csv") && !second) {
    printEncoderCSV();
    return;
  }

  if (!strcmp(first, "status") && !second) {
    help();
    return;
  }

  if (!strcmp(first, "auto") && !second) {
    manualGains = false;
    help();
    return;
  }

  if (!strcmp(first, "amp")
      && number(second, a)
      && !third
      && (a == 0 || a == 1)) {
    amplitudeStrength = 2 * a;
    help();
    return;
  }

  if (!strcmp(first, "center")
      && number(second, a)
      && !third
      && (a == 0 || a == 1)) {
    centeringEnabled = a != 0;
    help();
    return;
  }

  if (!strcmp(first, "damping")
      && number(second, a)
      && !third
      && a >= 0
      && a <= 5) {
    damping = a;
    modelReady = true;
    updateModel();
    help();
    return;
  }

  if (!strcmp(first, "gains")
      && number(second, a)
      && number(third, b)
      && !extra
      && fabsf(a) <= 10000
      && fabsf(b) <= 1000) {
    fixedGp = a;
    fixedGd = b;
    manualGains = true;
    help();
    return;
  }

  if (!number(first, a) || !second || extra) {
    help();
    return;
  }

  if (!strcmp(second, "decay")) {
    if (third || a != truncf(a)) {
      Serial.println("# Distance must be a signed integer in mm.");
    } else {
      mode = DECAY;
      startTrial(a, 0);
    }
    if (state == IDLE) Serial.println("# READY");
    return;
  }

  if (!strcmp(second, "baseline")) {
    mode = BASELINE;
  } else if (!strcmp(second, "quash")) {
    mode = QUASH;
  } else if (!strcmp(second, "maintain")) {
    mode = MAINTAIN;
  } else if (!strcmp(second, "tune")) {
    mode = TUNE;
  } else {
    help();
    return;
  }

  if (mode == TUNE) {
    if (!number(third, b) || b <= 1 / PERIOD_S || b > MAX_TUNE_HZ) {
      Serial.print("# Tune frequency must be above ");
      Serial.print(1 / PERIOD_S, 3);
      Serial.print(" Hz and no more than ");
      Serial.print(MAX_TUNE_HZ, 1);
      Serial.println(" Hz.");
      return;
    }
  } else {
    if (third) {
      help();
      return;
    }

    b = 1 / PERIOD_S;
  }

  startTrial(a, b);
}

void readCommand() {
  if (!Serial.available()) return;

  char c = Serial.read();

  if (c == '!') {
    emergencyStop();
    commandLength = 0;
    commandOverflow = true;
    return;
  }

  if (c == '\r') return;

  if (c == '\n') {
    command[commandLength] = '\0';

    if (!commandOverflow && (!commandStartedBusy || state != IDLE)) {
      handleCommand();
    } else if (state == IDLE) {
      Serial.println("# Command discarded; enter a fresh command.");
    }

    commandLength = 0;
    commandOverflow = commandStartedBusy = false;
  } else {
    if (commandLength == 0) commandStartedBusy = state != IDLE;

    if (commandLength < (int)sizeof(command) - 1) {
      command[commandLength++] = c;
    } else {
      commandOverflow = true;
    }
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(ENC_A, INPUT_PULLUP);
  pinMode(ENC_B, INPUT_PULLUP);

  motor.setMinPulseWidth(3);
  motor.setMaxSpeed(MAX_SPEED);
  motor.setAcceleration(MAX_ACCEL);

  updateModel();

  while (!Serial) encoder.tick();

  lastEncoderMotionUs = micros();
  help();
}

void loop() {
  encoder.tick();

  long count = encoder.getPosition();

  if (count != previousCount) {
    previousCount = count;
    lastEncoderMotionUs = micros();
  }

  if (state == RETURNING && brakedReturn.distance != 0) {
    runBrakedReturn();
  } else if (state == LAUNCH || state == RETURNING) {
    motor.run();

    if (!motor.isRunning()) {
      if (returnInProgress) {
        state = REPORT;
      } else {
        launchEndUs = micros();
        state = mode == DECAY ? FREE : HOLD;
        if (mode != DECAY) motor.setMaxSpeed(MAX_SPEED);
      }
    }
  } else if (state == CONTROL || state == STOPPING) {
    motor.runSpeed();
  }

  readCommand();

  if (state == REPORT) {
    if (returnInProgress) {
      finishReturn();
    } else if (Serial) {
      if (mode == DECAY) printDecayReport();
      else printReport();
    }
    return;
  }

  if (state == IDLE) return;

  // SAMD's bool(Serial) delays 10 ms. Never use it while servicing motion.
  if (!Serial.dtr() || !USBDevice.configured()) {
    beginStop("USB disconnected", micros());
  }

  float inwardMM =
      inwardSign * motor.currentPosition() / STEPS_PER_MM;

  if (inwardMM < 0 || inwardMM > TRACK_MM) {
    emergencyStop();
    return;
  }

  uint32_t now = micros();

  if (returnInProgress) {
    // Returns are separate moves: never reuse the previous trial's sampling clock.
    if (state == STOPPING && now - returnTickUs >= SAMPLE_US) {
      setFeedbackSpeed(0, (now - returnTickUs) * 1e-6f);
      returnTickUs = now;

      if (motor.speed() == 0) state = REPORT;
    } else if (state == RETURNING) {
      returnTickUs = now;
    }
    return;
  }

  if ((int32_t)(now - nextSampleUs) < 0) return;

  uint32_t sampleUs = mode == DECAY ? decaySampleUs : SAMPLE_US;
  nextSampleUs += sampleUs;

  if ((int32_t)(now - nextSampleUs) >= 0) {
    nextSampleUs = now + sampleUs;
    missedSamples++;
  }

  controlTick(now);
}
