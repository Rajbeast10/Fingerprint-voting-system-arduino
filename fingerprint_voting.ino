/*
 * ============================================================
 * Fingerprint-Based Voting System — Arduino Code
 * Hardware: Arduino Uno/Nano + R307/R503 Fingerprint Sensor
 * Library : Adafruit_Fingerprint
 * Serial  : USB Serial @ 57600 baud (to PC)
 *           SoftwareSerial @ 57600 baud (to sensor on D2/D3)
 * ============================================================
 *
 * COMMAND PROTOCOL (PC → Arduino):
 *   ENROLL:<id>   – Start enrollment for fingerprint ID <id>
 *   VERIFY        – Identify a fingerprint; returns its stored ID
 *   DELETE:<id>   – Delete fingerprint ID <id> from sensor
 *   COUNT         – Return total stored template count
 *
 * RESPONSE PROTOCOL (Arduino → PC):
 *   OK:<id>       – Success with fingerprint ID
 *   ERROR:<msg>   – Failure with reason
 *   READY         – Arduino booted successfully
 * ============================================================
 */

#include <Adafruit_Fingerprint.h>
#include <SoftwareSerial.h>

// ── Pin Definitions ──────────────────────────────────────────
// R307/R503: TX pin → D2 (Arduino RX), RX pin → D3 (Arduino TX)
SoftwareSerial mySerial(2, 3);   // RX=D2, TX=D3

Adafruit_Fingerprint finger = Adafruit_Fingerprint(&mySerial);

// ── State Variables ──────────────────────────────────────────
String inputCommand = "";        // Buffer for incoming serial command
bool   commandReady = false;     // Flag: full command received

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(57600);           // USB serial to PC
  delay(100);

  finger.begin(57600);           // Sensor baud rate

  if (finger.verifyPassword()) {
    Serial.println("READY");     // Sensor found & password OK
  } else {
    Serial.println("ERROR:Sensor not found. Check wiring.");
    while (true) { delay(1); }  // Halt
  }

  finger.getTemplateCount();
}

// ============================================================
// LOOP — Read commands from PC
// ============================================================
void loop() {
  // Read USB serial byte by byte; newline = end of command
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputCommand.length() > 0) {
        commandReady = true;
      }
    } else {
      inputCommand += c;
    }
  }

  if (commandReady) {
    commandReady = false;
    String cmd = inputCommand;
    inputCommand = "";
    processCommand(cmd);
  }
}

// ============================================================
// processCommand — Dispatch incoming command string
// ============================================================
void processCommand(String cmd) {
  cmd.trim();

  if (cmd.startsWith("ENROLL:")) {
    int id = cmd.substring(7).toInt();
    enrollFingerprint(id);

  } else if (cmd == "VERIFY") {
    verifyFingerprint();

  } else if (cmd.startsWith("DELETE:")) {
    int id = cmd.substring(7).toInt();
    deleteFingerprint(id);

  } else if (cmd == "COUNT") {
    finger.getTemplateCount();
    Serial.print("OK:");
    Serial.println(finger.templateCount);

  } else {
    Serial.println("ERROR:Unknown command");
  }
}

// ============================================================
// enrollFingerprint — Two-scan enrollment for given ID
// ============================================================
void enrollFingerprint(int id) {
  if (id < 1 || id > 127) {
    Serial.println("ERROR:Invalid ID (1-127)");
    return;
  }

  int p = -1;

  // ── First Scan ───────────────────────────────────────────
  Serial.println("STATUS:Place finger on sensor");

  p = -1;
  while (p != FINGERPRINT_OK) {
    p = finger.getImage();
    switch (p) {
      case FINGERPRINT_OK:          break;
      case FINGERPRINT_NOFINGER:    break;  // waiting
      case FINGERPRINT_PACKETRECIEVEERR:
        Serial.println("ERROR:Communication error"); return;
      case FINGERPRINT_IMAGEFAIL:
        Serial.println("ERROR:Imaging error"); return;
    }
    delay(50);
  }

  p = finger.image2Tz(1);          // Convert image → slot 1
  if (p != FINGERPRINT_OK) {
    Serial.println("ERROR:Could not convert first image");
    return;
  }

  Serial.println("STATUS:Remove finger");
  delay(2000);

  // Wait for finger to be removed
  p = 0;
  while (p != FINGERPRINT_NOFINGER) {
    p = finger.getImage();
    delay(50);
  }

  // ── Second Scan ──────────────────────────────────────────
  Serial.println("STATUS:Place same finger again");

  p = -1;
  while (p != FINGERPRINT_OK) {
    p = finger.getImage();
    switch (p) {
      case FINGERPRINT_OK:          break;
      case FINGERPRINT_NOFINGER:    break;  // waiting
      case FINGERPRINT_PACKETRECIEVEERR:
        Serial.println("ERROR:Communication error"); return;
      case FINGERPRINT_IMAGEFAIL:
        Serial.println("ERROR:Imaging error"); return;
    }
    delay(50);
  }

  p = finger.image2Tz(2);          // Convert image → slot 2
  if (p != FINGERPRINT_OK) {
    Serial.println("ERROR:Could not convert second image");
    return;
  }

  // ── Create Model ─────────────────────────────────────────
  p = finger.createModel();
  if (p == FINGERPRINT_ENROLLMISMATCH) {
    Serial.println("ERROR:Fingerprints did not match");
    return;
  } else if (p != FINGERPRINT_OK) {
    Serial.println("ERROR:Could not create model");
    return;
  }

  // ── Store Model ──────────────────────────────────────────
  p = finger.storeModel(id);
  if (p == FINGERPRINT_OK) {
    Serial.print("OK:");
    Serial.println(id);            // Report success + ID
  } else if (p == FINGERPRINT_BADLOCATION) {
    Serial.println("ERROR:Invalid storage location");
  } else if (p == FINGERPRINT_FLASHERR) {
    Serial.println("ERROR:Flash storage error");
  } else {
    Serial.println("ERROR:Unknown enrollment error");
  }
}

// ============================================================
// verifyFingerprint — Search sensor for matching template
// ============================================================
void verifyFingerprint() {
  int p = -1;

  Serial.println("STATUS:Place finger on sensor");

  while (p != FINGERPRINT_OK) {
    p = finger.getImage();
    switch (p) {
      case FINGERPRINT_OK:       break;
      case FINGERPRINT_NOFINGER: break;  // keep waiting
      case FINGERPRINT_PACKETRECIEVEERR:
        Serial.println("ERROR:Communication error"); return;
      case FINGERPRINT_IMAGEFAIL:
        Serial.println("ERROR:Imaging error"); return;
    }
    delay(50);
  }

  p = finger.image2Tz();
  if (p != FINGERPRINT_OK) {
    Serial.println("ERROR:Could not process image");
    return;
  }

  p = finger.fingerSearch();
  if (p == FINGERPRINT_OK) {
    Serial.print("OK:");
    Serial.println(finger.fingerID);    // Matched ID
  } else if (p == FINGERPRINT_NOTFOUND) {
    Serial.println("ERROR:No match found");
  } else {
    Serial.println("ERROR:Search failed");
  }
}

// ============================================================
// deleteFingerprint — Remove template from sensor flash
// ============================================================
void deleteFingerprint(int id) {
  int p = finger.deleteModel(id);
  if (p == FINGERPRINT_OK) {
    Serial.print("OK:");
    Serial.println(id);
  } else if (p == FINGERPRINT_PACKETRECIEVEERR) {
    Serial.println("ERROR:Communication error");
  } else if (p == FINGERPRINT_BADLOCATION) {
    Serial.println("ERROR:Invalid ID");
  } else if (p == FINGERPRINT_FLASHERR) {
    Serial.println("ERROR:Flash error");
  } else {
    Serial.println("ERROR:Delete failed");
  }
}
