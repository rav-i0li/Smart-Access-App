/*
  hardware/firmware/switch_button.ino

  SmartAccess — Switch Access firmware (Day 5)

  Wiring: one leg of the tactile button to pin 2, the other leg to GND.
  INPUT_PULLUP means the pin reads HIGH when unpressed and LOW when
  pressed — no external resistor needed.

  Sends a newline-delimited "PRESS" over serial on a debounced press,
  and "RELEASE" on release, at 9600 baud (must match
  hardware/arduino_serial_manager.py's baud_rate).
*/

const int BUTTON_PIN = 2;
const unsigned long DEBOUNCE_MS = 50;

int lastStableState = HIGH;
int lastReading = HIGH;
unsigned long lastChangeTime = 0;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(9600);
}

void loop() {
  int reading = digitalRead(BUTTON_PIN);

  if (reading != lastReading) {
    lastChangeTime = millis();
  }

  if ((millis() - lastChangeTime) > DEBOUNCE_MS) {
    if (reading != lastStableState) {
      lastStableState = reading;
      if (lastStableState == LOW) {
        Serial.println("PRESS");
      } else {
        Serial.println("RELEASE");
      }
    }
  }

  lastReading = reading;
}
