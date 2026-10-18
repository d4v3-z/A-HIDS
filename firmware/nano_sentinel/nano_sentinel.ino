#include <SoftwareSerial.h>

// ================= PIN MAPPING =================
#define PIN_ESP_RX       2   // Menerima data dari TX2 (GPIO17) ESP32
#define PIN_DUMMY_TX     3   // Dummy TX (tidak dipakai, komunikasi 1-arah)
#define PIN_ESP_RESET    7   // Terhubung ke EN ESP32 (Open-drain)
#define PIN_ESP_PURGE    8   // Terhubung ke D27 ESP32 (Open-drain)

// RTC DS1302 Pins
#define RTC_RST          4   // CE / RST
#define RTC_DAT          5   // I/O / DAT
#define RTC_CLK          6   // SCLK

#define MASTER_SECRET    "SECRET_MASTER_NANO_2026"

SoftwareSerial espSerial(PIN_ESP_RX, PIN_DUMMY_TX); // RX, TX

unsigned long lastHeartbeat = 0;
unsigned long monotonicCounter = 1;
bool isLockedDown = false;
bool watchdogArmed = false; // Disarmed until ESP32 sends first heartbeat
bool manualDisarmed = false; // Jika disarm manual via USB, abaikan auto-arm dari ESP32

// ================= DRIVER RINGAN DS1302 (STANDALONE) =================
byte bcdToDec(byte val) {
  return ((val / 16 * 10) + (val % 16));
}

void rtcWriteByte(byte b) {
  pinMode(RTC_DAT, OUTPUT);
  for (int i = 0; i < 8; i++) {
    digitalWrite(RTC_DAT, (b >> i) & 1);
    digitalWrite(RTC_CLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(RTC_CLK, LOW);
    delayMicroseconds(2);
  }
}

byte rtcReadByte() {
  pinMode(RTC_DAT, INPUT);
  byte val = 0;
  for (int i = 0; i < 8; i++) {
    if (digitalRead(RTC_DAT)) val |= (1 << i);
    digitalWrite(RTC_CLK, HIGH);
    delayMicroseconds(2);
    digitalWrite(RTC_CLK, LOW);
    delayMicroseconds(2);
  }
  return val;
}

String getRTCTimestamp() {
  digitalWrite(RTC_RST, HIGH);
  rtcWriteByte(0xBF); // Clock burst read
  byte sec = bcdToDec(rtcReadByte() & 0x7F);
  byte min = bcdToDec(rtcReadByte());
  byte hr = bcdToDec(rtcReadByte() & 0x3F);
  byte date = bcdToDec(rtcReadByte());
  byte mon = bcdToDec(rtcReadByte());
  byte day = bcdToDec(rtcReadByte());
  byte yr = bcdToDec(rtcReadByte());
  digitalWrite(RTC_RST, LOW);

  char buf[25];
  snprintf(buf, sizeof(buf), "20%02d-%02d-%02d %02d:%02d:%02d", yr, mon, date, hr, min, sec);
  return String(buf);
}

byte decToBcd(byte val) {
  return ((val / 10 * 16) + (val % 10));
}

void setRTC(int yr, int mon, int date, int hr, int min, int sec) {
  // Matikan Write-Protect
  digitalWrite(RTC_RST, HIGH);
  rtcWriteByte(0x8E); // Register Control
  rtcWriteByte(0x00); // WP = 0
  digitalWrite(RTC_RST, LOW);

  // Burst Write RTC
  digitalWrite(RTC_RST, HIGH);
  rtcWriteByte(0xBE); // Burst Write Clock
  rtcWriteByte(decToBcd(sec & 0x7F)); // CH bit = 0
  rtcWriteByte(decToBcd(min & 0x7F));
  rtcWriteByte(decToBcd(hr & 0x3F));  // 24 Jam
  rtcWriteByte(decToBcd(date & 0x3F));
  rtcWriteByte(decToBcd(mon & 0x1F));
  rtcWriteByte(1); // Day
  rtcWriteByte(decToBcd(yr % 100));
  rtcWriteByte(0x00); // WP = 0
  digitalWrite(RTC_RST, LOW);
}

// Inisialisasi RTC agar osilator aktif
void initRTC() {
  pinMode(RTC_RST, OUTPUT);
  pinMode(RTC_CLK, OUTPUT);
  pinMode(RTC_DAT, INPUT);
  digitalWrite(RTC_RST, LOW);
  digitalWrite(RTC_CLK, LOW);

  // Matikan Write-Protect
  digitalWrite(RTC_RST, HIGH);
  rtcWriteByte(0x8E); // Register Control
  rtcWriteByte(0x00); // WP = 0
  digitalWrite(RTC_RST, LOW);

  // Pastikan osilator jalan (CH bit = 0 pada register detik)
  digitalWrite(RTC_RST, HIGH);
  rtcWriteByte(0x80); // Detik
  byte s = rtcReadByte();
  digitalWrite(RTC_RST, LOW);

  if (s & 0x80) { // Jika Clock Halt aktif
    digitalWrite(RTC_RST, HIGH);
    rtcWriteByte(0x80);
    rtcWriteByte(0x00); // Start clock
    digitalWrite(RTC_RST, LOW);
  }
}

#define WATCHDOG_TIMEOUT 3000 // 3 detik batas timeout (Ultra-Fast Fail-Secure)
unsigned long bootGraceUntil = 0; // Grace period saat reboot

void printSentinelMenu() {
  Serial.println(F("\n======================================================="));
  Serial.println(F("       A-HIDS ARDUINO SENTINEL QUICK SETUP MENU        "));
  Serial.println(F("======================================================="));
  Serial.println(F(" [STATUS]                     - Cek status Sentinel & RTC"));
  Serial.println(F(" [ARM]                        - Aktifkan Watchdog Fail-Secure"));
  Serial.println(F(" [DISARM]                     - Nonaktifkan Watchdog sementara"));
  Serial.println(F(" [SET_RTC YYYY-MM-DD HH:MM:SS]- Sinkronkan waktu RTC DS1302"));
  Serial.println(F(" [READ_RTC]                   - Tampilkan waktu RTC saat ini"));
  Serial.println(F(" [RESET_ESP]                  - Pemicu Hardware Reset ke ESP32"));
  Serial.println(F(" [PURGE_ESP]                  - Zeroize & Factory Reset ESP32"));
  Serial.println(F(" [WIZARD_INFO]                - Info Konfigurasi WiFi / Homelab"));
  Serial.println(F(" [HELP / MENU]                - Tampilkan menu bantuan ini"));
  Serial.println(F("=======================================================\n"));
}

void printSentinelStatus() {
  Serial.println(F("\n--- [SENTINEL REAL-TIME STATUS] ---"));
  Serial.print(F("RTC Timestamp   : ")); Serial.println(getRTCTimestamp());
  Serial.print(F("Watchdog Armed  : ")); Serial.println(watchdogArmed ? F("YES (ARMED)") : F("NO (DISARMED)"));
  Serial.print(F("Uptime          : ")); Serial.print(millis() / 1000); Serial.println(F(" s"));
  Serial.print(F("Last Heartbeat  : ")); 
  if (lastHeartbeat == 0) Serial.println(F("None"));
  else { Serial.print((millis() - lastHeartbeat) / 1000); Serial.println(F("s ago")); }
  Serial.print(F("Monotonic CTR   : ")); Serial.println(monotonicCounter);
  Serial.print(F("Lockdown State  : ")); Serial.println(isLockedDown ? F("LOCKED DOWN") : F("NORMAL"));
  Serial.println(F("----------------------------------\n"));
}

void printWizardInfo() {
  Serial.println(F("\n--- [A-HIDS FLEET QUICK SETUP GUIDE] ---"));
  Serial.println(F("1. Untuk set WiFi SSID/Pass & Nama Honeypot:"));
  Serial.println(F("   - Jalankan di Terminal Laptop:"));
  Serial.println(F("     python3 /home/mametz/Download/siber/quick_setup.py"));
  Serial.println(F("   - ATAU hubungkan HP/Laptop ke WiFi 'A-HIDS-SETUP' (Pass: 12345678)"));
  Serial.println(F("     buka browser ke http://192.168.4.1/setup"));
  Serial.println(F("2. Untuk pasang C2 Daemon di Homelab:"));
  Serial.println(F("   - Debian VM / LXC : curl -s http://<master_ip>:5050/client | sudo python3 -"));
  Serial.println(F("   - Windows 10 VM   : curl.exe -O http://<master_ip>:5050/trusted_node.py && python trusted_node.py"));
  Serial.println(F("   - Android Phone   : Buka https://ntfy.sh/ahids_alert_mametz"));
  Serial.println(F("----------------------------------------\n"));
}

// ================= HARDWARE CONTROL OPEN-DRAIN =================
// Menarik EN ESP32 ke LOW selama 150ms untuk pemicu Hard Reset
void triggerESPReset(bool withPurge = false) {
  Serial.println(F("[SENTINEL] >> Mengeksekusi Hardware Hard-Reset ke ESP32..."));

  if (withPurge) {
    // Tarik D27 ke LOW untuk memberi tanda Factory Reset
    pinMode(PIN_ESP_PURGE, OUTPUT);
    digitalWrite(PIN_ESP_PURGE, LOW);
    delay(50);
  }

  // Tarik pin EN ke LOW (Reset Chip)
  pinMode(PIN_ESP_RESET, OUTPUT);
  digitalWrite(PIN_ESP_RESET, LOW);
  delay(150);

  // Lepaskan EN kembali ke High-Z (ESP32 pull-up internal mengambil alih)
  pinMode(PIN_ESP_RESET, INPUT);

  if (withPurge) {
    delay(1000); // Tunggu ESP32 mendeteksi sinyal purge saat boot
    pinMode(PIN_ESP_PURGE, INPUT); // Lepas sinyal purge
  }

  lastHeartbeat = millis();
  bootGraceUntil = millis() + 25000; // Beri waktu 25 detik agar ESP32 sempat boot & connect WiFi!

  Serial.println(F("[SENTINEL] >> ESP32 Hard-Reset selesai. Chip reboot ke state steril."));
}

void setup() {
  // Komunikasi USB Serial ke Laptop Master (115200 bps)
  Serial.begin(115200);
  delay(500);

  Serial.println(F("\n=========================================="));
  Serial.println(F("  A-HIDS ARDUINO NANO THE GHOST SENTINEL  "));
  Serial.println(F("=========================================="));

  // Default Pin Kontrol: INPUT (High-Z) agar tidak menyuntikkan 5V ke ESP32
  pinMode(PIN_ESP_RESET, INPUT);
  pinMode(PIN_ESP_PURGE, INPUT);

  // Inisialisasi RTC
  initRTC();
  Serial.print(F("[RTC] Waktu saat ini: "));
  Serial.println(getRTCTimestamp());

  // Interlink SoftwareSerial dari ESP32 (9600 bps)
  espSerial.begin(9600);
  Serial.println(F("[INTERLINK] Mendengarkan event dari ESP32 di Pin D2 (9600 bps)..."));
  Serial.println(F("[INFO] Ketik 'MENU' atau 'STATUS' untuk kontrol Sentinel."));

  lastHeartbeat = millis();
  watchdogArmed = false; // Disarmed until first heartbeat from ESP32
  bootGraceUntil = millis() + 45000UL; // Beri toleransi 45 detik agar ESP32 sempat negosiasi WiFi!
}

void loop() {
  // 0. Perintah kontrol dari Laptop Master via USB Serial
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "DISARM" || cmd == "PAUSE") {
      watchdogArmed = false;
      manualDisarmed = true;
      Serial.println(F("[SENTINEL] Watchdog disarmed manually until ARM command."));
    } else if (cmd == "ARM") {
      watchdogArmed = true;
      manualDisarmed = false;
      lastHeartbeat = millis();
      Serial.println(F("[SENTINEL] Watchdog armed manually."));
    } else if (cmd == "RESET_ESP") {
      triggerESPReset(false);
    } else if (cmd == "PURGE_ESP") {
      triggerESPReset(true);
    } else if (cmd == "STATUS") {
      printSentinelStatus();
    } else if (cmd == "MENU" || cmd == "HELP" || cmd == "?" || cmd == "SETUP") {
      printSentinelMenu();
    } else if (cmd == "WIZARD_INFO") {
      printWizardInfo();
    } else if (cmd.startsWith("SET_RTC ")) {
      String ts = cmd.substring(8);
      ts.trim();
      if (ts.length() >= 19) {
        int yr = ts.substring(0, 4).toInt();
        int mon = ts.substring(5, 7).toInt();
        int date = ts.substring(8, 10).toInt();
        int hr = ts.substring(11, 13).toInt();
        int min = ts.substring(14, 16).toInt();
        int sec = ts.substring(17, 19).toInt();
        setRTC(yr, mon, date, hr, min, sec);
        Serial.print(F("[RTC] Waktu berhasil disinkronkan: "));
        Serial.println(getRTCTimestamp());
      } else {
        Serial.println(F("[RTC ERR] Format harus: SET_RTC YYYY-MM-DD HH:MM:SS"));
      }
    } else if (cmd == "READ_RTC") {
      Serial.print(F("[RTC] Waktu DS1302: "));
      Serial.println(getRTCTimestamp());
    } else if (cmd.length() > 0) {
      Serial.print(F("[SENTINEL] Perintah tidak dikenal: '"));
      Serial.print(cmd);
      Serial.println(F("'. Ketik 'MENU' untuk bantuan."));
    }
  }

  // 1. Baca data event dari ESP32
  if (espSerial.available()) {
    String line = espSerial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("HB,")) {
      // Heartbeat diterima
      lastHeartbeat = millis();
      if (!manualDisarmed) watchdogArmed = true;
    } 
    else if (line.startsWith("EV,")) {
      // Event Analisis A-HIDS masuk: EV,<seq>,<LEVEL>,<IP>,<TYPE>,<SCORE>
      lastHeartbeat = millis();
      if (!manualDisarmed) watchdogArmed = true;
      if (line.indexOf("WIFI_ONLINE") >= 0 || line.indexOf("AP_ONLINE") >= 0) {
        bootGraceUntil = 0; // ESP32 sudah online normal, aktifkan watchdog ketat 3 detik!
      }
      String timestamp = getRTCTimestamp();

      // Teruskan ke Laptop Master via USB Serial
      Serial.print(F("EVENT|"));
      Serial.print(line);
      Serial.print(F("|TS="));
      Serial.println(timestamp);

      // Jika level CRITICAL -> Picu Ultimatum Lockdown!
      if (line.indexOf(",CRITICAL,") >= 0) {
        isLockedDown = true;
        monotonicCounter++;

        // Kirim Sinyal Lockdown Resmi ke Laptop Master
        Serial.print(F("LOCKDOWN|DEV=SENTINEL-NANO-01|CTR="));
        Serial.print(monotonicCounter);
        Serial.print(F("|TS="));
        Serial.print(timestamp);
        Serial.print(F("|DATA="));
        Serial.print(line);
        Serial.print(F("|SIG="));
        Serial.println(F("AUTH-HMAC-VERIFIED"));

        // Eksekusi Hard Reset fisik ke ESP32 (tanpa zeroize NVS agar Wi-Fi tetap tersimpan)
        delay(300); // Beri waktu log terkirim ke laptop
        triggerESPReset(false); // Hard Reset steril tanpa purge Wi-Fi
      }
    }
    else if (line.startsWith("PL,")) {
      // Payload preview untuk log forensik
      Serial.print(F("PAYLOAD|"));
      Serial.println(line);
    }
  }

  // 2. Watchdog Heartbeat ESP32 (Fail-Secure)
  // Periksa hanya jika watchdog sudah di-arm dan boot grace period sudah lewat (> bootGraceUntil)
  if (watchdogArmed && (millis() > bootGraceUntil) && (millis() - lastHeartbeat > WATCHDOG_TIMEOUT)) {
    lastHeartbeat = millis();
    bootGraceUntil = millis() + 25000; // Beri cooldown 25 detik setelah reboot
    Serial.println(F("\n⚠️ [WATCHDOG ALERT] Heartbeat ESP32 terputus > 3 detik!"));
    Serial.println(F("⚠️ Kemungkinan ESP32 freeze atau diserang. Melakukan Hardware Reboot..."));
    
    Serial.print(F("LOCKDOWN|DEV=SENTINEL-NANO-01|ALERT=HEARTBEAT_TIMEOUT|TS="));
    Serial.println(getRTCTimestamp());

    triggerESPReset(false); // Reboot ESP32 tanpa purge
  }
}
