#include <WiFi.h>
#include <WebServer.h>
#include <nvs_flash.h>
#include <Preferences.h>
#include <math.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ================= KONFIGURASI DINAMIS (NVS STORAGE) =================
Preferences prefs;
String currentSSID = "M@rx!";
String currentPass = "mametzisdabest!";
String currentHoneyName = "RouterOS-Core-Gateway";
String currentProfile = "ROUTER_OS";

int currentWebPort = 80;
int currentTelnetPort = 23;
int currentSSHPort = 0; // 0 = disabled, 22 = active
bool webEnabled = true;
bool telnetEnabled = true;
bool sshEnabled = false;

String currentStaticIP = "";
String currentGateway = "";
String currentSubnet = "";

// Hotspot Darurat / Setup Mode jika WiFi gagal
const char* AP_SSID = "A-HIDS-SETUP";
const char* AP_PASS = "12345678";

// ================= PIN HARDWARE INTERLINK =================
#define PIN_TX_TO_NANO    17  // TX2 UART2 terhubung ke Pin D2 Nano
#define PIN_PURGE_STRAP   27  // Terhubung ke Pin D8 Nano (Active LOW)
#define PIN_STATUS_LED     2  // Onboard LED ESP32

// Pin I2C untuk OLED 0.96"
#define PIN_OLED_SDA      21
#define PIN_OLED_SCL      22
#define SCREEN_WIDTH     128
#define SCREEN_HEIGHT     64
#define OLED_RESET        -1
#define SCREEN_ADDRESS  0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool oledActive = false;

// ================= STRUKTUR A-HIDS DEFENSE =================
#define MAX_TRACKED_IPS     16
#define THRESHOLD_CRITICAL  50
#define TOKEN_SECRET_NODE   "NODE-AUTH-SEC-2026"

struct IPRecord {
  String ip;
  int score;
  unsigned long lastSeen;
  bool isBanned;
};

IPRecord ipTable[MAX_TRACKED_IPS];
unsigned long seqCounter = 1;
unsigned long lastHeartbeat = 0;

WebServer server(80);
WiFiServer telnetServer(23); // Telnet Canary Trap
WiFiServer sshServer(22);    // SSH Banner Canary Trap

// OLED Telemetry Counters
unsigned long totalAttacksCount = 0;
String lastThreatName = "NONE";
String lastThreatSource = "-";
int lastThreatScoreVal = 0;
unsigned long alertDisplayUntil = 0;
unsigned long lastOledRefresh = 0;

// Helper URL Decode
String urlDecode(String str) {
  String decoded = "";
  char ch;
  int i, j;
  for (i = 0; i < str.length(); i++) {
    if (str[i] == '%') {
      sscanf(str.substring(i + 1, i + 3).c_str(), "%x", &j);
      ch = static_cast<char>(j);
      decoded += ch;
      i += 2;
    } else if (str[i] == '+') {
      decoded += ' ';
    } else {
      decoded += str[i];
    }
  }
  return decoded;
}

// 1. Kalkulator Shannon Entropy
float calculateEntropy(const String &data) {
  int len = data.length();
  if (len < 24) return 3.0; // String pendek diabaikan (cegah false-positive)

  int counts[256] = {0};
  for (int i = 0; i < len; i++) {
    counts[(uint8_t)data[i]]++;
  }

  float entropy = 0.0;
  for (int i = 0; i < 256; i++) {
    if (counts[i] > 0) {
      float p = (float)counts[i] / len;
      entropy -= p * (log(p) / log(2.0));
    }
  }
  return entropy;
}

// 2. Tracking IP dengan Exponential Score Decay (LRU Bounded Table)
IPRecord* getOrUpdateIP(String ip) {
  unsigned long now = millis();
  int oldestIdx = 0;
  unsigned long oldestTime = 0xFFFFFFFF;

  for (int i = 0; i < MAX_TRACKED_IPS; i++) {
    if (ipTable[i].ip == ip) {
      // Score decay: berkurang 5 poin setiap 15 detik tidak aktif
      unsigned long elapsed = now - ipTable[i].lastSeen;
      int decay = (elapsed / 15000) * 5;
      ipTable[i].score = max(0, ipTable[i].score - decay);
      ipTable[i].lastSeen = now;
      return &ipTable[i];
    }
    if (ipTable[i].lastSeen < oldestTime) {
      oldestTime = ipTable[i].lastSeen;
      oldestIdx = i;
    }
  }

  // Jika slot baru (IP baru pertama kali muncul)
  ipTable[oldestIdx].ip = ip;
  ipTable[oldestIdx].score = 0;
  ipTable[oldestIdx].lastSeen = now;
  ipTable[oldestIdx].isBanned = false;

  // Notifikasi deteksi device baru ke Sentinel Nano & Master C2
  if (ip != "0.0.0.0" && ip != "127.0.0.1" && ip.indexOf("192.168.4.1") < 0) {
    Serial2.printf("EV,%lu,NOTICE,%s,NEW_DEVICE_DISCOVERED,0\n", seqCounter++, ip.c_str());
    Serial.printf("[NEW DEVICE] IP baru terdeteksi di subnet: %s (Status: UNDER_WATCH)\n", ip.c_str());
  }

  return &ipTable[oldestIdx];
}

// ================= OLED GRAPHICAL ROUTINES =================
void updateOledNormal() {
  if (!oledActive) return;
  display.clearDisplay();

  // Header Bar (Inverted Style)
  display.fillRect(0, 0, 128, 11, SSD1306_WHITE);
  display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(4, 2);
  display.printf("A-HIDS: %s", currentProfile.substring(0, 10).c_str());

  display.setTextColor(SSD1306_WHITE);

  // Baris 1: Wi-Fi SSID
  display.setCursor(0, 14);
  display.print("WIFI: ");
  if (WiFi.status() == WL_CONNECTED) {
    display.print(currentSSID.substring(0, 14));
  } else {
    display.print("[AP] A-HIDS-SETUP");
  }

  // Baris 2: IP Address
  display.setCursor(0, 25);
  display.print("IP  : ");
  if (WiFi.status() == WL_CONNECTED) {
    display.print(WiFi.localIP().toString());
  } else {
    display.print(WiFi.softAPIP().toString());
  }

  // Baris 3: Ports Trap
  display.setCursor(0, 36);
  display.print("PORT: ");
  String pStr = "";
  if (webEnabled && currentWebPort > 0) pStr += "W:" + String(currentWebPort) + " ";
  if (telnetEnabled && currentTelnetPort > 0) pStr += "T:" + String(currentTelnetPort) + " ";
  if (sshEnabled && currentSSHPort > 0) pStr += "S:" + String(currentSSHPort);
  if (pStr == "") pStr = "NONE";
  display.print(pStr);

  // Baris 4: Serangan & Heartbeat
  display.setCursor(0, 48);
  display.printf("HITS: %d | HB:#%d", totalAttacksCount, seqCounter % 1000);

  // Baris 5: Status
  display.setCursor(0, 57);
  display.print("STAT: ");
  if (lastThreatName == "NONE") {
    display.print("ARMED (OK)");
  } else {
    display.print(lastThreatName.substring(0, 13));
  }

  display.display();
}

void triggerOledAlert(String threat, String ip, int score) {
  totalAttacksCount++;
  lastThreatName = threat;
  lastThreatSource = ip;
  lastThreatScoreVal = score;
  alertDisplayUntil = millis() + 4500; // Tampilkan banner selama 4.5 detik

  if (!oledActive) return;
  display.clearDisplay();

  // Alert Header Box (Flashing Header)
  display.fillRect(0, 0, 128, 12, SSD1306_WHITE);
  display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(4, 2);
  display.print("! THREAT DETECTED !");

  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 16);
  display.print("TYPE : ");
  display.print(threat.substring(0, 13));

  display.setCursor(0, 28);
  display.print("SRC  : ");
  display.print(ip);

  display.setCursor(0, 40);
  display.print("SCORE: ");
  display.print(score);
  if (score >= THRESHOLD_CRITICAL) {
    display.print(" [CRITICAL]");
  } else {
    display.print(" [LOGGED]");
  }

  display.setCursor(0, 52);
  display.print(">> DISPATCH SENTINEL");
  display.display();
}

// Kirim Event ke Nano via UART2 (GPIO17)
void sendEventToNano(const char* level, String ip, const char* threatType, int score, String payload) {
  // Format: EV,<seq>,<LEVEL>,<IP>,<TYPE>,<SCORE>
  Serial2.printf("EV,%lu,%s,%s,%s,%d\n", seqCounter, level, ip.c_str(), threatType, score);
  Serial.printf("[INTERLINK TX] EV,%lu,%s,%s,%s,%d\n", seqCounter, level, ip.c_str(), threatType, score);
  
  // Format Payload Preview: PL,<seq>,<CLEAN_PAYLOAD>
  payload.replace(",", ";");
  payload.replace("\n", " ");
  payload.replace("\r", " ");
  if (payload.length() > 64) payload = payload.substring(0, 64);
  Serial2.printf("PL,%lu,%s\n", seqCounter, payload.c_str());

  // Tampilkan alert di layar OLED
  triggerOledAlert(String(threatType), ip, score);

  seqCounter++;
}

// 3. WAF & Anomaly Analysis Pipeline
void analyzeRequest(bool isCanary = false, const char* canaryName = "") {
  String clientIP = server.client().remoteIP().toString();
  IPRecord* client = getOrUpdateIP(clientIP);
  unsigned long now = millis();

  // Kumpulkan Payload (Query Args + Body POST)
  String rawPayload = "";
  for (int i = 0; i < server.args(); i++) {
    rawPayload += server.argName(i) + "=" + server.arg(i) + " ";
  }
  String normalizedPayload = urlDecode(rawPayload);
  normalizedPayload.toLowerCase();

  int threatScore = 0;
  String detectedThreat = "";

  // A. PILAR 1: Canary Tripwire (Pelanggaran Tertinggi)
  if (isCanary) {
    threatScore += 100;
    detectedThreat = canaryName;
  }

  // B. PILAR 2: WAF Signature Heuristics
  // 1. SQL Injection
  if (normalizedPayload.indexOf("union select") >= 0 ||
      normalizedPayload.indexOf("or 1=1") >= 0 ||
      normalizedPayload.indexOf("' or '1'='1") >= 0 ||
      normalizedPayload.indexOf("--") >= 0 ||
      normalizedPayload.indexOf("sleep(") >= 0) {
    threatScore += 60;
    detectedThreat = "SQL_INJECTION";
  }
  // 2. Command Injection & Modern RCE (including Log4j/JNDI)
  else if (normalizedPayload.indexOf(";cat") >= 0 ||
           normalizedPayload.indexOf("|/bin/") >= 0 ||
           normalizedPayload.indexOf("$(whoami)") >= 0 ||
           normalizedPayload.indexOf("`id`") >= 0 ||
           normalizedPayload.indexOf("&&") >= 0 ||
           normalizedPayload.indexOf("${jndi:") >= 0 ||
           normalizedPayload.indexOf("ldap://") >= 0 ||
           normalizedPayload.indexOf("/bin/sh") >= 0 ||
           normalizedPayload.indexOf("/bin/bash") >= 0) {
    threatScore += 70;
    detectedThreat = "CMD_INJECTION";
  }
  // 3. Directory Traversal / LFI / Arbitrary File Read (CVE-2021-41773 & Winbox CVE-2018-14847)
  else if (normalizedPayload.indexOf("../") >= 0 ||
           normalizedPayload.indexOf("..\\") >= 0 ||
           normalizedPayload.indexOf("%2e%2e") >= 0 ||
           normalizedPayload.indexOf("etc/passwd") >= 0 ||
           normalizedPayload.indexOf("etc/shadow") >= 0 ||
           normalizedPayload.indexOf("boot.ini") >= 0 ||
           normalizedPayload.indexOf("winbox") >= 0 ||
           normalizedPayload.indexOf("user.dat") >= 0) {
    threatScore += 60;
    detectedThreat = "DIR_TRAVERSAL_LFI";
  }
  // 4. Server-Side Request Forgery (SSRF) & Metadata Service Theft
  else if (normalizedPayload.indexOf("169.254.169.254") >= 0 ||
           normalizedPayload.indexOf("metadata.google.internal") >= 0 ||
           normalizedPayload.indexOf("127.0.0.1:") >= 0 ||
           normalizedPayload.indexOf("localhost:") >= 0) {
    threatScore += 65;
    detectedThreat = "SSRF_METADATA_PROBE";
  }
  // 5. Cross-Site Scripting (XSS)
  else if (normalizedPayload.indexOf("<script>") >= 0 ||
           normalizedPayload.indexOf("javascript:") >= 0 ||
           normalizedPayload.indexOf("onerror=") >= 0 ||
           normalizedPayload.indexOf("onload=") >= 0) {
    threatScore += 35;
    detectedThreat = "XSS_REFLECTED";
  }
  // 6. Automated Scanner Signatures (User-Agent Heuristics)
  String ua = server.header("User-Agent");
  ua.toLowerCase();
  if (ua.indexOf("sqlmap") >= 0 || ua.indexOf("nikto") >= 0 || ua.indexOf("nmap") >= 0 || ua.indexOf("masscan") >= 0 || ua.indexOf("acunetix") >= 0) {
    threatScore += 55;
    if (detectedThreat == "") detectedThreat = "AUTOMATED_SCANNER";
  }

  // C. PILAR 3: Shannon Entropy (Shellcode & NOP Sled Detection)
  float entropy = calculateEntropy(rawPayload);
  if (rawPayload.length() >= 24) {
    if (entropy > 4.5) {
      threatScore += 55;
      detectedThreat = "HIGH_ENTROPY_SHELLCODE";
    } else if (entropy < 1.8 && rawPayload.length() >= 32) {
      threatScore += 40;
      detectedThreat = "LOW_ENTROPY_NOP_SLED";
    }
  }

  // Akumulasikan ke IP Record
  client->score += threatScore;

  // Keputusan Respon & Telemetri Interlink
  if (threatScore > 0) {
    const char* level = (client->score >= THRESHOLD_CRITICAL) ? "CRITICAL" : "WARN";
    sendEventToNano(level, clientIP, detectedThreat.c_str(), client->score, rawPayload);

    // Karantina lokal di bait jika tembus ambang batas
    if (client->score >= THRESHOLD_CRITICAL) {
      client->isBanned = true;
      Serial.printf("[LOCAL BAN] IP %s diblokir lokal (Skor: %d)\n", clientIP.c_str(), client->score);
    }
  }
}

// ================= HANDLERS =================
void handleRoot() {
  String clientIP = server.client().remoteIP().toString();
  IPRecord* client = getOrUpdateIP(clientIP);

  if (client->isBanned) {
    server.send(403, "text/plain", "ACCESS DENIED: IP Quarantined by A-HIDS.");
    return;
  }

  // 1. Cisco Enterprise Managed Switch Persona
  if (currentProfile == "ENTERPRISE_SWITCH") {
    String html = "<!DOCTYPE html><html><head><title>Cisco Systems - Catalyst Switch Management</title>"
                  "<style>body{font-family:Arial,sans-serif;background:#ece9d8;color:#000;margin:0;padding:25px;}"
                  ".box{background:#fff;border:1px solid #7f9db9;padding:25px;width:380px;box-shadow:0 4px 8px rgba(0,0,0,0.1);}"
                  ".header{background:#003366;color:#fff;padding:12px;font-weight:bold;font-size:15px;margin:-25px -25px 20px -25px;}"
                  "input{width:100%;padding:8px;margin:6px 0;border:1px solid #7f9db9;border-radius:2px;box-sizing:border-box;}"
                  "button{background:#d4d0c8;border:2px outset #fff;padding:8px 16px;cursor:pointer;font-weight:bold;margin-top:10px;}"
                  "</style></head><body><div class='box'>"
                  "<div class='header'>CISCO SYSTEMS - Catalyst 2960 Management Console</div>"
                  "<b style='color:#c00;'>Level 15 Administrative Access Required</b><br><br>"
                  "<form method='POST' action='/login'>"
                  "Username:<br><input type='text' name='user' placeholder='cisco' required><br>"
                  "Password:<br><input type='password' name='pass' required><br>"
                  "<button type='submit'>Login to Switch</button></form>"
                  "<p style='color:#666;font-size:11px;margin-top:20px;'>Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE4</p>"
                  "</div></body></html>";
    server.send(200, "text/html", html);
    return;
  }

  // 2. MikroTik RouterOS Gateway Persona
  if (currentProfile == "ROUTER_OS") {
    String html = "<!DOCTYPE html><html><head><title>MikroTik RouterOS - WebFig</title>"
                  "<style>body{font-family:Tahoma,sans-serif;background:#242424;color:#fff;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}"
                  ".box{background:#181818;padding:32px;border:1px solid #333;width:320px;border-radius:6px;box-shadow:0 4px 15px rgba(0,0,0,0.8);}"
                  ".logo{color:#3f9;font-weight:bold;font-size:22px;letter-spacing:1px;margin-bottom:4px;}"
                  "input{width:100%;padding:9px;margin:8px 0;background:#2e2e2e;border:1px solid #444;color:#fff;border-radius:4px;box-sizing:border-box;}"
                  "button{width:100%;padding:10px;background:#3f9;border:none;color:#000;font-weight:bold;cursor:pointer;border-radius:4px;margin-top:8px;}"
                  "</style></head><body><div class='box'><div class='logo'>MIKROTIK</div>"
                  "<p style='color:#888;font-size:12px;margin-top:0;'>RouterOS v6.49.8 (stable)</p>"
                  "<form method='POST' action='/login'>"
                  "<input type='text' name='user' placeholder='Login' required>"
                  "<input type='password' name='pass' placeholder='Password'>"
                  "<button type='submit'>Log In</button></form>"
                  "<p style='color:#555;font-size:11px;margin-top:15px;text-align:center;'>WinBox v3.37 available</p></div></body></html>";
    server.send(200, "text/html", html);
    return;
  }

  // 3. IoT Edge / IP Camera Gateway Persona
  if (currentProfile == "IOT_GATEWAY") {
    String html = "<!DOCTYPE html><html><head><title>Network Video Recorder & Camera Portal</title>"
                  "<style>body{font-family:Arial,sans-serif;background:#1a1a1a;color:#ccc;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}"
                  ".box{background:#262626;border:1px solid #404040;padding:28px;width:340px;border-radius:6px;}"
                  ".title{color:#e67e22;font-size:17px;font-weight:bold;margin-bottom:4px;}"
                  "input{width:100%;padding:9px;margin:8px 0;background:#333;border:1px solid #555;color:#fff;border-radius:4px;box-sizing:border-box;}"
                  "button{width:100%;padding:10px;background:#e67e22;border:none;color:#fff;font-weight:bold;cursor:pointer;border-radius:4px;margin-top:8px;}"
                  "</style></head><body><div class='box'><div class='title'>LIVE SURVEILLANCE GATEWAY</div>"
                  "<p style='font-size:12px;color:#888;margin-top:0;'>Embedded Linux H.264 Video Stream v2.4</p>"
                  "<form method='POST' action='/login'>"
                  "<input type='text' name='user' placeholder='Admin Account' required>"
                  "<input type='password' name='pass' placeholder='Password' required>"
                  "<button type='submit'>View Camera Stream</button></form></div></body></html>";
    server.send(200, "text/html", html);
    return;
  }

  // 4. Debian 12 GNU/Linux Server Persona
  if (currentProfile == "DEBIAN_SERVER") {
    String html = "<!DOCTYPE html><html><head><title>Welcome to Nginx on Debian GNU/Linux</title>"
                  "<style>body{font-family:sans-serif;background:#f8fafc;color:#1e293b;margin:60px auto;max-width:650px;padding:30px;border:1px solid #cbd5e1;background:#fff;border-radius:8px;box-shadow:0 4px 6px rgba(0,0,0,0.05);}"
                  "h1{color:#d70a53;margin-top:0;}input{padding:8px;margin:6px 0;border:1px solid #cbd5e1;border-radius:4px;width:240px;}"
                  "button{background:#d70a53;color:#fff;border:none;padding:8px 16px;font-weight:bold;border-radius:4px;cursor:pointer;}"
                  "</style></head><body><h1>Welcome to Debian 12 (Bookworm)!</h1>"
                  "<p>If you see this page, the Nginx web server is successfully installed and working.</p>"
                  "<hr style='border:0;border-top:1px solid #e2e8f0;'><p style='font-size:12px;color:#64748b;'>Host: Homelab-Debian-Srv01 (Debian 12 / Nginx 1.22.1)</p>"
                  "<form method='POST' action='/login' style='margin-top:20px;padding:15px;background:#f1f5f9;border-radius:6px;'>"
                  "<b>Server Administration Portal</b><br>"
                  "<input type='text' name='user' placeholder='Username' required><br>"
                  "<input type='password' name='pass' placeholder='Password' required><br>"
                  "<button type='submit'>Authenticate</button></form></body></html>";
    server.send(200, "text/html", html);
    return;
  }

  // Fallback Generic
  String html = "<!DOCTYPE html><html><head><title>" + currentHoneyName + "</title>"
                "<style>body{font-family:sans-serif;background:#1e293b;color:#f8fafc;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}"
                ".box{background:#0f172a;padding:30px;border-radius:8px;width:320px;}"
                "input{width:100%;padding:10px;margin:10px 0;border:1px solid #334155;background:#1e293b;color:#fff;border-radius:4px;box-sizing:border-box;}"
                "button{width:100%;padding:10px;background:#0284c7;border:none;color:#fff;font-weight:bold;border-radius:4px;cursor:pointer;}</style></head>"
                "<body><div class='box'><h2>" + currentHoneyName + "</h2>"
                "<form method='POST' action='/login'>"
                "<input type='text' name='user' placeholder='Username' required>"
                "<input type='password' name='pass' placeholder='Password' required>"
                "<button type='submit'>Sign In</button></form></div></body></html>";
  server.send(200, "text/html", html);
}

// ================= QUICK SETUP WEB PORTAL =================
void handleSetupGet() {
  String html = "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>A-HIDS Quick Setup</title>"
                "<style>body{font-family:sans-serif;background:#0b0f19;color:#f8fafc;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:20px;box-sizing:border-box;}"
                ".card{background:#141924;padding:28px;border-radius:12px;border:1px solid #232d42;width:100%;max-width:400px;box-shadow:0 12px 24px rgba(0,0,0,0.6);}"
                "h2{margin-top:0;color:#00ff66;font-size:18px;letter-spacing:1px;font-family:monospace;}"
                "label{display:block;font-size:11px;color:#94a3b8;margin-top:14px;margin-bottom:4px;text-transform:uppercase;font-weight:bold;}"
                "input,select{width:100%;padding:11px;background:#080a0f;border:1px solid #2d374d;color:#fff;border-radius:6px;box-sizing:border-box;font-size:13px;}"
                "button{width:100%;margin-top:24px;padding:12px;background:#00e5ff;border:none;color:#000;font-weight:bold;border-radius:6px;cursor:pointer;font-size:14px;letter-spacing:0.5px;}"
                "button:hover{background:#33ebff;}"
                ".desc{font-size:12px;color:#64748b;margin-bottom:16px;line-height:1.4;}"
                "</style></head><body>"
                "<div class='card'>"
                "<h2>⚡ A-HIDS QUICK SETUP</h2>"
                "<p class='desc'>Konfigurasi dinamis target Wi-Fi (HomeLab / Router Rumah) & identitas umpan Honeypot.</p>"
                "<form method='POST' action='/setup'>"
                "<label>Wi-Fi SSID (Nama Router / Hotspot):</label>"
                "<input type='text' name='ssid' value='" + currentSSID + "' required>"
                "<label>Wi-Fi Password:</label>"
                "<input type='password' name='pass' value='" + currentPass + "'>"
                "<label>Nama Identitas Honeypot (Umpan):</label>"
                "<input type='text' name='name' value='" + currentHoneyName + "' required>"
                "<label>Profil Umpan Telnet / Banner:</label>"
                "<select name='profile'>"
                "<option value='IOT_GATEWAY' " + String(currentProfile == "IOT_GATEWAY" ? "selected" : "") + ">IoT Edge Gateway (BusyBox Linux)</option>"
                "<option value='ROUTER_OS' " + String(currentProfile == "ROUTER_OS" ? "selected" : "") + ">RouterOS / MikroTik Core Decoy</option>"
                "<option value='ENTERPRISE_SWITCH' " + String(currentProfile == "ENTERPRISE_SWITCH" ? "selected" : "") + ">Enterprise Managed Switch v3</option>"
                "<option value='DEBIAN_SERVER' " + String(currentProfile == "DEBIAN_SERVER" ? "selected" : "") + ">Debian 12 GNU/Linux Server</option>"
                "</select>"
                "<button type='submit'>💾 SIMPAN & REBOOT CHIP</button>"
                "</form></div></body></html>";
  server.send(200, "text/html", html);
}

void handleSetupPost() {
  if (server.hasArg("ssid")) currentSSID = server.arg("ssid");
  if (server.hasArg("pass")) currentPass = server.arg("pass");
  if (server.hasArg("name")) currentHoneyName = server.arg("name");
  if (server.hasArg("profile")) currentProfile = server.arg("profile");

  // Simpan permanen ke NVS Flash ESP32
  prefs.begin("ahids", false);
  prefs.putString("ssid", currentSSID);
  prefs.putString("pass", currentPass);
  prefs.putString("name", currentHoneyName);
  prefs.putString("prof", currentProfile);
  prefs.end();

  String html = "<!DOCTYPE html><html><head><meta http-equiv='refresh' content='10;url=/'><style>body{background:#0b0f19;color:#00ff66;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;text-align:center;}</style></head><body>"
                "<div><h2>✔ KONFIGURASI TERSIMPAN!</h2><p style='color:#fff;'>ESP32 sedang reboot untuk menyambung ke: <b>" + currentSSID + "</b>...</p>"
                "<p style='color:#94a3b8;font-size:12px;'>Silakan pantau IP baru pada layar OLED atau Dashboard C2.</p></div></body></html>";
  server.send(200, "text/html", html);
  delay(1200);
  ESP.restart();
}

void handleLogin() {
  analyzeRequest();
  String clientIP = server.client().remoteIP().toString();
  IPRecord* client = getOrUpdateIP(clientIP);

  if (client->isBanned) {
    server.send(403, "text/plain", "ACCESS DENIED: Security Violation.");
    return;
  }

  delay(200); // Simulasi delay verifikasi
  server.send(401, "text/plain", "Invalid authentication credentials.");
}

// Canary Trap Endpoints
void handleCanarySecret() {
  analyzeRequest(true, "CANARY_SECRET_LEAK");
  server.send(404, "text/plain", "Not Found.");
}

void handleCanaryEnv() {
  analyzeRequest(true, "ENV_FILE_PROBE");
  server.send(404, "text/plain", "Not Found.");
}

void handleCanaryWp() {
  analyzeRequest(true, "WORDPRESS_PROBE");
  server.send(200, "text/html", "<h2>WordPress Login (Fake)</h2>");
}

// ================= SETUP & LOOP =================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=========================================");
  Serial.println("  A-HIDS ESP32 THE BAIT (HONEYPOT CORE)  ");
  Serial.println("=========================================");

  // Load Preferences dari NVS
  prefs.begin("ahids", false);
  currentSSID = prefs.getString("ssid", currentSSID);
  currentPass = prefs.getString("pass", currentPass);
  currentHoneyName = prefs.getString("name", currentHoneyName);
  currentProfile = prefs.getString("prof", currentProfile);
  currentWebPort = prefs.getInt("wport", 80);
  currentTelnetPort = prefs.getInt("tport", 23);
  currentSSHPort = prefs.getInt("sport", 0);
  webEnabled = (currentWebPort > 0);
  telnetEnabled = (currentTelnetPort > 0);
  sshEnabled = (currentSSHPort > 0);
  currentStaticIP = prefs.getString("static_ip", "");
  currentGateway = prefs.getString("gw", "");
  currentSubnet = prefs.getString("mask", "");
  prefs.end();

  // Inisialisasi I2C dan OLED Display
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  Wire.setTimeOut(100);
  if (display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    oledActive = true;
    display.clearDisplay();
    display.fillRect(0, 0, 128, 14, SSD1306_WHITE);
    display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(6, 3);
    display.print("A-HIDS BAIT CORE");

    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 24);
    display.println("Connecting WiFi...");
    display.print("SSID: ");
    display.println(currentSSID);
    display.display();
    Serial.println("[OLED] Display OLED 0.96\" I2C siap pada GPIO21/22!");
  } else {
    Serial.println("[OLED] OLED tidak terdeteksi pada 0x3C, berjalan normal.");
  }

  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, LOW);

  // Inisialisasi Pin Purge Strap dari Nano (Pin D27)
  pinMode(PIN_PURGE_STRAP, INPUT_PULLUP);
  if (digitalRead(PIN_PURGE_STRAP) == LOW) {
    if (oledActive) {
      display.clearDisplay();
      display.fillRect(0, 0, 128, 14, SSD1306_WHITE);
      display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
      display.setCursor(4, 3);
      display.print("! SELF-PURGE EVENT !");
      display.setTextColor(SSD1306_WHITE);
      display.setCursor(0, 24);
      display.println("PURGE PIN ACTIVE");
      display.println("ZEROIZING NVS FLASH");
      display.println("RESTORING STERILE STATE");
      display.display();
    }
    Serial.println("\n⚡ [PURGE DETECTED] Sinyal Purge dari Sentinel Nano!");
    Serial.println("⚡ Menghapus seluruh NVS Flash & Memory...");
    nvs_flash_erase();
    nvs_flash_init();
    Serial.println("⚡ Factory Reset Selesai. State Steril.");
    for(int i=0; i<5; i++) {
      digitalWrite(PIN_STATUS_LED, HIGH); delay(100);
      digitalWrite(PIN_STATUS_LED, LOW); delay(100);
    }
  }

  // Inisialisasi UART2 ke Nano (TX2 = GPIO17, 9600 bps)
  Serial2.begin(9600, SERIAL_8N1, -1, PIN_TX_TO_NANO);
  Serial.printf("[INIT] Interlink UART2 aktif pada GPIO%d -> Nano D2\n", PIN_TX_TO_NANO);
  Serial2.println("HB,0"); // Segera beri tanda hidup ke Sentinel Uno!

  // Hubungkan ke WiFi
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(100);
  WiFi.setSleep(false);

  // Jika custom static IP diatur di NVS, aplikasikan konfigurasi manual
  if (currentStaticIP.length() > 6) {
    IPAddress ip, gw, mask;
    if (ip.fromString(currentStaticIP) && gw.fromString(currentGateway) && mask.fromString(currentSubnet)) {
      WiFi.config(ip, gw, mask);
      Serial.printf("[IP CONFIG] Static IP diaktifkan: %s (GW: %s, MASK: %s)\n",
                    currentStaticIP.c_str(), currentGateway.c_str(), currentSubnet.c_str());
    }
  }

  WiFi.begin(currentSSID.c_str(), currentPass.c_str());
  Serial.printf("[WIFI] Menghubungkan ke %s", currentSSID.c_str());

  int timeout = 0;
  while (WiFi.status() != WL_CONNECTED && timeout < 40) {
    delay(500);
    Serial.print(".");
    if (timeout % 2 == 0) Serial2.println("HB,0"); // Rutin kirim tanda boot ke Sentinel
    timeout++;
  }

  // Jika WiFi gagal, aktifkan SoftAP darurat untuk Quick Setup Portal
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WIFI CONNECTED] IP Address: %s\n", WiFi.localIP().toString().c_str());
    Serial2.printf("EV,0,INFO,%s,WIFI_ONLINE,0\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WIFI FAILED] Mengaktifkan Standalone Quick Setup SoftAP...");
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    Serial.printf("[AP ACTIVE] SSID: %s | IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
    Serial2.printf("EV,0,INFO,%s,AP_ONLINE,0\n", WiFi.softAPIP().toString().c_str());
  }

  // Update tampilan awal normal
  updateOledNormal();

  // Setup Web Server Endpoints jika port aktif
  if (webEnabled && currentWebPort > 0) {
    server.on("/", HTTP_GET, handleRoot);
    server.on("/login", HTTP_POST, handleLogin);
    server.on("/admin_secret", handleCanarySecret);
    server.on("/.env", handleCanaryEnv);
    server.on("/wp-login.php", handleCanaryWp);
    
    // Quick Setup Portal Endpoints (Dapat diakses di /setup atau /config)
    server.on("/setup", HTTP_GET, handleSetupGet);
    server.on("/setup", HTTP_POST, handleSetupPost);
    server.on("/config", HTTP_GET, handleSetupGet);
    server.on("/config", HTTP_POST, handleSetupPost);

    server.onNotFound([]() {
      analyzeRequest();
      server.send(404, "text/plain", "Resource Not Found on " + currentHoneyName);
    });

    server.begin(currentWebPort);
    Serial.printf("[SERVER] Honeypot Web Server (%s) siap di port %d!\n", currentHoneyName.c_str(), currentWebPort);
    Serial.printf("[SETUP PORTAL] Buka http://<ip_esp32>:%d/setup untuk konfigurasi visual.\n", currentWebPort);
  } else {
    Serial.println("[SERVER] Web Server Trap DINONAKTIFKAN sesuai konfigurasi.");
  }

  // Setup Telnet Honeypot Trap
  if (telnetEnabled && currentTelnetPort > 0) {
    telnetServer.begin(currentTelnetPort);
    telnetServer.setNoDelay(true);
    Serial.printf("[TELNET] Decoy Banner (%s) siap di port %d!\n", currentProfile.c_str(), currentTelnetPort);
  } else {
    Serial.println("[TELNET] Telnet Trap DINONAKTIFKAN sesuai konfigurasi.");
  }

  // Setup SSH Banner Canary Trap jika diaktifkan
  if (sshEnabled && currentSSHPort > 0) {
    sshServer.begin(currentSSHPort);
    sshServer.setNoDelay(true);
    Serial.printf("[SSH] SSH Banner Canary Trap siap di port %d!\n", currentSSHPort);
  }
}

void loop() {
  // 0. Serial CLI Interactive Configuration Handler
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("SET_CONFIG ")) {
      // SET_CONFIG <ssid> <pass> <name> <profile> [web_port] [telnet_port] [ssh_port] [static_ip] [gw] [mask]
      String remainder = cmd.substring(11);
      remainder.trim();
      std::vector<String> tokens;
      int startIdx = 0;
      for (int i = 0; i <= remainder.length(); i++) {
        if (i == remainder.length() || remainder[i] == ' ') {
          if (i > startIdx) {
            tokens.push_back(remainder.substring(startIdx, i));
          }
          startIdx = i + 1;
        }
      }

      if (tokens.size() >= 1) currentSSID = tokens[0];
      if (tokens.size() >= 2) {
        currentPass = (tokens[1] == "-" || tokens[1] == "NONE") ? "" : tokens[1];
      }
      if (tokens.size() >= 3) currentHoneyName = tokens[2];
      if (tokens.size() >= 4) currentProfile = tokens[3];
      if (tokens.size() >= 5) currentWebPort = tokens[4].toInt();
      if (tokens.size() >= 6) currentTelnetPort = tokens[5].toInt();
      if (tokens.size() >= 7) currentSSHPort = tokens[6].toInt();
      if (tokens.size() >= 8) {
        currentStaticIP = (tokens[7] == "DHCP" || tokens[7] == "-") ? "" : tokens[7];
      }
      if (tokens.size() >= 9) currentGateway = tokens[8];
      if (tokens.size() >= 10) currentSubnet = tokens[9];

      prefs.begin("ahids", false);
      prefs.putString("ssid", currentSSID);
      prefs.putString("pass", currentPass);
      prefs.putString("name", currentHoneyName);
      prefs.putString("prof", currentProfile);
      prefs.putInt("wport", currentWebPort);
      prefs.putInt("tport", currentTelnetPort);
      prefs.putInt("sport", currentSSHPort);
      prefs.putString("static_ip", currentStaticIP);
      prefs.putString("gw", currentGateway);
      prefs.putString("mask", currentSubnet);
      prefs.end();

      Serial.printf("[CONFIG] Provisioned SSID='%s', Persona='%s' [%s] Ports: W=%d T=%d S=%d IP=%s. Rebooting in 500ms...\n",
                    currentSSID.c_str(), currentHoneyName.c_str(), currentProfile.c_str(),
                    currentWebPort, currentTelnetPort, currentSSHPort,
                    (currentStaticIP == "") ? "DHCP" : currentStaticIP.c_str());
      delay(500);
      ESP.restart();
    } else if (cmd.startsWith("SET_PORTS ")) {
      String remainder = cmd.substring(10);
      remainder.trim();
      int s1 = remainder.indexOf(' ');
      if (s1 > 0) {
        currentWebPort = remainder.substring(0, s1).toInt();
        String rem2 = remainder.substring(s1 + 1);
        rem2.trim();
        int s2 = rem2.indexOf(' ');
        if (s2 > 0) {
          currentTelnetPort = rem2.substring(0, s2).toInt();
          currentSSHPort = rem2.substring(s2 + 1).toInt();
        } else {
          currentTelnetPort = rem2.toInt();
        }
      } else {
        currentWebPort = remainder.toInt();
      }
      prefs.begin("ahids", false);
      prefs.putInt("wport", currentWebPort);
      prefs.putInt("tport", currentTelnetPort);
      prefs.putInt("sport", currentSSHPort);
      prefs.end();
      Serial.printf("[CONFIG] Ports updated: Web=%d, Telnet=%d, SSH=%d. Rebooting...\n", currentWebPort, currentTelnetPort, currentSSHPort);
      delay(500);
      ESP.restart();
    } else if (cmd.startsWith("SET_STATIC_IP ")) {
      String remainder = cmd.substring(14);
      remainder.trim();
      int s1 = remainder.indexOf(' ');
      if (s1 > 0) {
        currentStaticIP = remainder.substring(0, s1);
        String rem2 = remainder.substring(s1 + 1);
        rem2.trim();
        int s2 = rem2.indexOf(' ');
        if (s2 > 0) {
          currentGateway = rem2.substring(0, s2);
          currentSubnet = rem2.substring(s2 + 1);
          currentSubnet.trim();
        }
      }
      prefs.begin("ahids", false);
      prefs.putString("static_ip", currentStaticIP);
      prefs.putString("gw", currentGateway);
      prefs.putString("mask", currentSubnet);
      prefs.end();
      Serial.printf("[CONFIG] Static IP set to: %s (GW: %s). Rebooting...\n", currentStaticIP.c_str(), currentGateway.c_str());
      delay(500);
      ESP.restart();
    } else if (cmd == "SET_DHCP") {
      currentStaticIP = "";
      prefs.begin("ahids", false);
      prefs.putString("static_ip", "");
      prefs.end();
      Serial.println(F("[CONFIG] Static IP cleared. Reverting to DHCP..."));
      delay(500);
      ESP.restart();
    } else if (cmd.startsWith("SET_WIFI ")) {
      int spaceIdx = cmd.indexOf(' ', 9);
      if (spaceIdx > 0) {
        currentSSID = cmd.substring(9, spaceIdx);
        currentPass = cmd.substring(spaceIdx + 1);
      } else {
        currentSSID = cmd.substring(9);
        currentPass = "";
      }
      prefs.begin("ahids", false);
      prefs.putString("ssid", currentSSID);
      prefs.putString("pass", currentPass);
      prefs.end();
      Serial.printf("[CONFIG] WiFi SSID set to: %s. Rebooting...\n", currentSSID.c_str());
      delay(500);
      ESP.restart();
    } else if (cmd.startsWith("SET_NAME ")) {
      currentHoneyName = cmd.substring(9);
      prefs.begin("ahids", false);
      prefs.putString("name", currentHoneyName);
      prefs.end();
      Serial.printf("[CONFIG] Honeypot identity set to: %s. Rebooting...\n", currentHoneyName.c_str());
      delay(500);
      ESP.restart();
    } else if (cmd.startsWith("SET_PROFILE ")) {
      currentProfile = cmd.substring(12);
      currentProfile.trim();
      prefs.begin("ahids", false);
      prefs.putString("prof", currentProfile);
      prefs.end();
      Serial.printf("[CONFIG] Deception profile set to: %s. Rebooting...\n", currentProfile.c_str());
      delay(500);
      ESP.restart();
    } else if (cmd == "CONFIG_DUMP" || cmd == "STATUS" || cmd == "IP") {
      String ipStr = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : "0.0.0.0";
      Serial.printf("{\"ssid\":\"%s\",\"name\":\"%s\",\"profile\":\"%s\",\"wport\":%d,\"tport\":%d,\"sport\":%d,\"ip\":\"%s\",\"static_ip\":\"%s\",\"status\":\"%s\"}\n",
                    currentSSID.c_str(), currentHoneyName.c_str(), currentProfile.c_str(),
                    currentWebPort, currentTelnetPort, currentSSHPort,
                    ipStr.c_str(), currentStaticIP.c_str(),
                    (WiFi.status() == WL_CONNECTED) ? "CONNECTED" : "DISCONNECTED");
      if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WIFI CONNECTED] IP Address: %s\n", ipStr.c_str());
      }
    } else if (cmd == "RESET_CONFIG") {
      prefs.begin("ahids", false);
      prefs.clear();
      prefs.end();
      Serial.println(F("[CONFIG] NVS cleared. Restoring factory defaults..."));
      delay(500);
      ESP.restart();
    } else if (cmd == "HELP" || cmd == "MENU") {
      Serial.println(F("\n+-------------------------------------------------------------+"));
      Serial.println(F("| A-HIDS ESP32 DECEPTION CORE - SERIAL PROVISIONING INTERFACE |"));
      Serial.println(F("+-------------------------------------------------------------+"));
      Serial.println(F("  SET_CONFIG <ssid> <pass> <name> <prof> [wport] [tport] [sport] [ip] [gw] [mask]"));
      Serial.println(F("  SET_PORTS <wport> <tport> [sport]      - Configure active trap ports"));
      Serial.println(F("  SET_STATIC_IP <ip> <gw> <mask>        - Set static IP (or SET_DHCP)"));
      Serial.println(F("  CONFIG_DUMP                            - Print current NVS JSON"));
      Serial.println(F("  RESET_CONFIG                           - Reset NVS to defaults"));
      Serial.println(F("+-------------------------------------------------------------+\n"));
    }
  }

  // 1. Web Honeypot (jika diaktifkan)
  if (webEnabled && currentWebPort > 0) {
    server.handleClient();
  }

  // 2. Telnet Honeypot Trap (jika diaktifkan)
  if (telnetEnabled && currentTelnetPort > 0) {
    WiFiClient telnetClient = telnetServer.available();
    if (telnetClient) {
      String clientIP = telnetClient.remoteIP().toString();
      Serial.printf("\n⚡ [TELNET TRAP] Deteksi koneksi ke port %d dari: %s\n", currentTelnetPort, clientIP.c_str());

      triggerOledAlert("TELNET_TRAP", clientIP, 100);

      // Kirim Fake Banner sesuai Profil
      telnetClient.println("\r\n========================================");
      if (currentProfile == "ROUTER_OS") {
        telnetClient.println("   MikroTik RouterOS v6.49.8 (stable)   ");
        telnetClient.println("   RouterBoard 750Gr3 Decoy Console     ");
      } else if (currentProfile == "ENTERPRISE_SWITCH") {
        telnetClient.println("   Cisco IOS Software, C2960 Software   ");
        telnetClient.println("   Managed Switch Console Subsystem     ");
      } else if (currentProfile == "DEBIAN_SERVER") {
        telnetClient.println("   Debian GNU/Linux 12 (bookworm)       ");
        telnetClient.println("   Linux homelab-srv01 6.1.0-amd64      ");
      } else {
        telnetClient.println("   IoT Gateway Management Console v2.1   ");
        telnetClient.println("   BusyBox v1.20.2 (2021-08-14) ash     ");
      }
      telnetClient.println("========================================");
      telnetClient.print("login: ");
      telnetClient.flush();

      Serial2.printf("EV,%lu,CRITICAL,%s,IOT_BOTNET_TELNET,100\n", seqCounter++, clientIP.c_str());
      Serial2.printf("PL,%lu,PORT_%d_PROBE\n", seqCounter - 1, currentTelnetPort);

      delay(150);
      telnetClient.stop();
    }
  }

  // 2b. SSH Banner Canary Trap (jika diaktifkan)
  if (sshEnabled && currentSSHPort > 0) {
    WiFiClient sshClient = sshServer.available();
    if (sshClient) {
      String clientIP = sshClient.remoteIP().toString();
      Serial.printf("\n⚡ [SSH TRAP] Deteksi koneksi ke port %d dari: %s\n", currentSSHPort, clientIP.c_str());
      triggerOledAlert("SSH_BANNER_TRAP", clientIP, 100);
      if (currentProfile == "DEBIAN_SERVER") {
        sshClient.println("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u2\r\n");
      } else if (currentProfile == "ENTERPRISE_SWITCH") {
        sshClient.println("SSH-2.0-Cisco-1.25\r\n");
      } else if (currentProfile == "ROUTER_OS") {
        sshClient.println("SSH-2.0-ROSSSH\r\n");
      } else {
        sshClient.println("SSH-2.0-Dropbear_2020.81\r\n");
      }
      sshClient.flush();
      Serial2.printf("EV,%lu,CRITICAL,%s,SSH_BANNER_PROBE,100\n", seqCounter++, clientIP.c_str());
      Serial2.printf("PL,%lu,PORT_%d_SSH_PROBE\n", seqCounter - 1, currentSSHPort);
      delay(150);
      sshClient.stop();
    }
  }

  // 3. Kirim Heartbeat ke Nano setiap 1 detik
  unsigned long now = millis();
  if (now - lastHeartbeat >= 1000) {
    lastHeartbeat = now;
    // Format Heartbeat: HB,<seq>
    Serial2.printf("HB,%lu\n", seqCounter++);
    // Blink LED singkat sebagai tanda hidup
    digitalWrite(PIN_STATUS_LED, HIGH);
    delay(15);
    digitalWrite(PIN_STATUS_LED, LOW);
  }

  // 4. Refresh tampilan OLED secara berkala
  if (millis() > alertDisplayUntil && millis() - lastOledRefresh >= 2000) {
    lastOledRefresh = millis();
    updateOledNormal();
  }
}
