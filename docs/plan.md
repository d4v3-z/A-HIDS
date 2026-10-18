# Master Plan Proyek: A-HIDS (Adaptive Hybrid Intrusion Defense System)
**Honeypot Deception berbasis ESP32 + Out-of-Band Hardware Sentinel (Arduino Nano) + Automated Self-Purge**

> **Revisi v2 (2026-10-03):** Honeypot diganti dari Arduino Uno → **ESP32**. Virtual bridge di laptop **dihapus** (ESP32 punya WiFi & IP sendiri, jadi laptop gak ikut kena serangan). RTC + MicroSD **dipindah ke Nano** supaya log forensik disimpan di perangkat yang gak bisa dijangkau penyerang.

---

## 1. Ringkasan Konsep

| Peran | Perangkat | Terlihat dari jaringan? | Tugas |
|:---|:---|:---:|:---|
| **The Bait** (umpan) | ESP32 DevKit V1 | ✅ Ya (sengaja) | Honeypot WiFi yang kelihatan lemah, A-HIDS engine, Device Fingerprinting, OLED status |
| **The Ghost Sentinel** (pengawas) | Arduino Nano V3 | ❌ Tidak (air-gapped) | Blackbox log forensik (RTC + SD), ultimatum script, tanda tangan HMAC-SHA256, hard reset + purge ESP32 |
| **Command Center** | Laptop Master | Sesuai kebutuhan | Satu-satunya device yang boleh ubah whitelist; meneruskan perintah lockdown ke daemon lain |
| **Trusted Devices** | PC / Server / Laptop / HP | — | Daemon nunggu sinyal bertanda tangan → firewall lockdown + scan keamanan bawaan OS (HP: cuma notifikasi) |

Prinsip desain:
1. **Blast radius terisolasi:** Penyerang cuma bisa menyentuh ESP32. Laptop gak menjalankan bridge apa pun.
2. **Komunikasi satu arah:** ESP32 → Nano cuma lewat 1 kabel TX. Nano gak pernah ngirim data ke ESP32, jadi ESP32 yang sudah dibobol gak bisa menyerang Nano.
3. **Log gak bisa diubah:** Log forensik ditulis oleh Nano ke SD, bukan oleh ESP32. Penyerang yang menguasai ESP32 gak bisa menghapus jejaknya.
4. **Fail-secure:** Kalau ESP32 berhenti ngirim heartbeat (hang / dibajak / sinyal diblok), Nano menganggapnya sebagai pembobolan dan masuk lockdown.

---

## 2. Bill of Materials (BOM)

### A. Daftar belanja (DT Production, Tokopedia, Kota Manado)
| No | Barang | Qty | Harga | Subtotal | Dipasang di |
|:--:|:---|:---:|:---:|:---:|:---|
| 1 | ESP32 / ESP-32 Development Board | 1 | Rp 61.600 | Rp 61.600 | — (honeypot) |
| 2 | LCD OLED 0.96" Putih I2C 128x64 | 1 | Rp 23.875 | Rp 23.875 | ESP32 |
| 3 | Module Micro SD TF Card Reader (SPI) | 1 | Rp 13.000 | Rp 13.000 | Nano |
| 4 | RTC DS1307 I2C + Baterai | 1 | Rp 7.597 | Rp 7.597 | Nano |
| 5 | Breadboard 830 titik | 1 | Rp 12.300 | Rp 12.300 | — |
| 6 | Kabel Jumper Male-to-Female 10pcs | 2 | Rp 5.788 | Rp 11.576 | — |
| 7 | Kabel Jumper Male-to-Male 10pcs | 2 | Rp 5.788 | Rp 11.576 | — |
| | **TOTAL** | | | **Rp 141.524** | |

> [!NOTE]
> Hapus **ESP8266 ESP-01** dari keranjang, sudah gak dipakai.
> Jumlah jumper diturunkan dari 3 → 2 pack masing-masing (40 kabel), masih cukup untuk wiring di bawah (~22 kabel).
> Total lewat dari 100rb karena harga ESP32, tapi tetap jauh lebih murah dibanding pakai Ethernet Shield (Rp 218.650), dan gak perlu modul jaringan tambahan lagi.

### B. Sudah dimiliki
- Arduino Nano V3 (sentinel)
- Arduino Uno R3 (cadangan / bisa jadi "trusted IoT node" tambahan pas demo)
- Kartu MicroSD 4–16 GB (format **FAT32**)
- Kabel USB data (Mini-USB buat Nano, Micro-USB/Type-C buat ESP32)
- Charger HP / power bank 5V (buat daya ESP32, lihat §4.3)

### C. Kenapa DS1307 sekarang aman
DS1307 butuh VCC **5V** dan resistor pull-up di modulnya ditarik ke 5V. Kalau dipasang ke ESP32 (3.3V), pin I2C ESP32 bisa rusak. Karena RTC sekarang dipasang di **Nano (5V)**, DS1307 jalan normal tanpa modifikasi. DS3231 juga gak tersedia di DT Production.

---

## 3. Arsitektur Sistem

```
                    [ ATTACKER di WiFi yang sama ]
                                 │  HTTP :80 / Telnet :23 / port scan
                                 ▼
┌───────────────────────────────────────────────────────────────┐
│ ESP32  "THE BAIT"            IP sendiri, misal 192.168.1.88    │
│  • Fake web panel / telnet banner (kelihatan lemah)           │
│  • Device Fingerprinting (token terdaftar vs ROGUE)           │
│  • A-HIDS engine: Canary + WAF + Entropy + Sliding Window     │
│  • OLED: status live                                          │
│  • Daya: charger/power bank (TIDAK dicolok ke laptop)         │
└──────────────┬────────────────────────────────────────────────┘
               │ GPIO17 (TX) ──► Nano D2      event + heartbeat (1 arah)
               │ EN pin      ◄── Nano D7      hard reset (open-drain)
               │ GPIO27      ◄── Nano D8      purge strap (open-drain)
               │ GND ─────────── GND
               ▼
┌───────────────────────────────────────────────────────────────┐
│ ARDUINO NANO  "THE GHOST SENTINEL"   (gak punya IP, gak ada RF) │
│  • Terima event dari ESP32, kasih timestamp RTC DS1307        │
│  • Tulis blackbox log ke MicroSD (append-only)                │
│  • Watchdog heartbeat (5 detik gak ada heartbeat = breach)    │
│  • Bikin paket HMAC-SHA256 + counter monotonic                │
│  • Purge + hard reset ESP32                                   │
│  • Verifikasi attestation hash firmware ESP32 sesudah reboot  │
└──────────────┬────────────────────────────────────────────────┘
               │ USB Serial (satu-satunya jalur keluar Nano)
               ▼
┌───────────────────────────────────────────────────────────────┐
│ LAPTOP MASTER  — master_daemon.py                              │
│  • Verifikasi HMAC + anti-replay                              │
│  • Firewall lokal: DROP semua kecuali whitelist               │
│  • Teruskan perintah bertanda tangan (per device) ke daemon   │
│  • Push notif ke HP (ntfy / Telegram)                          │
│  • Satu-satunya yang bisa ubah whitelist                      │
└──────┬───────────────────────┬────────────────────────┬────────┘
       ▼                       ▼                        ▼
  PC Windows daemon      Server/Linux daemon        HP (notif saja)
  Firewall + Defender    iptables + ClamAV/rkhunter "Ganti network,
  scan terarah           scan terarah               cek manual"
```

---

## 4. Wiring / Pinout

### 4.1 ESP32 (honeypot)
| Modul / Sinyal | Pin modul | Pin ESP32 | Catatan |
|:---|:---|:---|:---|
| OLED SSD1306 | VCC | 3V3 | OLED aman di 3.3V |
| | GND | GND | |
| | SDA | GPIO21 | I2C default |
| | SCL | GPIO22 | I2C default |
| Link ke Nano | TX | GPIO17 (UART2 TX) | → Nano D2. **Jangan** sambungkan TX Nano balik ke ESP32 |
| Reset dari Nano | — | EN | ← Nano D7 (open-drain, lihat §4.4) |
| Purge strap | — | GPIO27 | ← Nano D8, `INPUT_PULLUP` di ESP32 |
| Ground | — | GND | Wajib disambung ke GND Nano |

> [!IMPORTANT]
> Hindari strapping pin (GPIO 0, 2, 5, 12, 15) buat sinyal kontrol dari Nano, karena bisa bikin ESP32 boot ke mode yang salah pas di-reset.

### 4.2 Arduino Nano (sentinel)
| Modul | Pin modul | Pin Nano | Catatan |
|:---|:---|:---|:---|
| RTC DS1307 | VCC / GND | 5V / GND | Native 5V |
| | SDA / SCL | A4 / A5 | I2C hardware |
| MicroSD | VCC / GND | 5V / GND | Modul punya regulator + level shifter, kasih 5V |
| | CS | D10 | |
| | MOSI / MISO / SCK | D11 / D12 / D13 | SPI hardware |
| Link dari ESP32 | RX | D2 | SoftwareSerial RX, 9600 bps |
| Reset ESP32 | — | D7 | Ke pin EN ESP32 |
| Purge strap | — | D8 | Ke GPIO27 ESP32 |
| LED status (opsional) | + via 220Ω | D5 (hijau) / D6 (merah) | |
| USB | — | Mini-USB | Ke laptop master |

### 4.3 Daya & isolasi
- **ESP32 dapat daya dari charger/power bank**, bukan dari USB laptop. Kalau dicolok ke laptop, kabel USB itu jadi jalur data dari device yang sudah dibobol ke laptop master, padahal itu yang mau dihindari. Kalau ada, pakai kabel *charge-only*.
- **Nano dapat daya dari USB laptop**, sekaligus jalur serial ke `master_daemon.py`.
- Sambungkan GND ESP32 ↔ GND Nano (common ground buat sinyal).

### 4.4 Beda level tegangan (5V Nano vs 3.3V ESP32)
| Jalur | Arah | Aman? | Cara |
|:---|:---|:---:|:---|
| ESP32 GPIO17 → Nano D2 | 3.3V → 5V | ✅ | Input HIGH ATmega328P @5V minimal ~3.0V; 3.3V lolos. Cukup kabel langsung |
| Nano D7 → ESP32 EN | 5V → 3.3V | ⚠️ | **Open-drain:** D7 normalnya `INPUT` (high-Z, EN ditarik ke 3.3V oleh pull-up board). Buat reset: `pinMode(OUTPUT)` + `LOW`. **Jangan pernah `digitalWrite(HIGH)`** |
| Nano D8 → ESP32 GPIO27 | 5V → 3.3V | ⚠️ | Open-drain juga. ESP32 pakai `INPUT_PULLUP` |

Dengan teknik open-drain, Nano gak pernah ngasih 5V ke pin ESP32, jadi **level shifter gak perlu**.

### 4.5 Rekap jumlah kabel
- ESP32: OLED 4 + interlink 4 = 8
- Nano: RTC 4 + SD 6 + LED 2–4 = 12–14
- Total ±22 kabel (stok 40)

> [!TIP]
> ESP32 DevKit lebar, kalau ditancap di 1 breadboard biasanya cuma 1 sisi pin yang bisa diakses. Di DevKit V1 30-pin, GPIO17/21/22 + 3V3 ada di satu sisi, sedangkan EN + GPIO27 ada di sisi seberangnya. Solusinya: sisi yang ketutup disambung langsung pakai jumper **M-F** dari header pin ESP32, atau ESP32 ditaruh di antara 2 breadboard.

---

## 5. A-HIDS Multi-Layer Engine (jalan di ESP32)

Skor per IP:
$$S_{total} = S_{canary} + S_{WAF} + S_{entropy} + S_{freq}$$

### Pilar 1: Canary / Decoy Trap
- Endpoint jebakan: `/admin_secret`, `/wp-login.php`, `/.env`, `/backup.zip`, login Telnet port 23.
- Kena endpoint ini = **+100 (instan CRITICAL)**. False positive praktis 0 karena user resmi gak pernah ke sana.

### Pilar 2: WAF Heuristic
| Pola | Contoh | Skor |
|:---|:---|:---:|
| SQL Injection | `' OR '1'='1`, `UNION SELECT` | +30 |
| RCE / Path traversal | `../`, `; cat`, `; ls`, `\| nc` | +35 |
| XSS | `<script>`, `javascript:` | +20 |
| User-Agent scanner | `sqlmap`, `nikto`, `nmap`, `curl` | +25 |

Payload di-normalisasi (URL-decode, lowercase) **sebelum** dicocokkan, supaya `%27%20OR%20` atau `UnIoN SeLeCt` gak lolos.

### Pilar 3: Shannon Entropy
$$H(X) = -\sum_{i} P(x_i)\log_2 P(x_i)$$
- Cuma dihitung kalau panjang payload $L \ge 24$ byte (string pendek bikin estimasi entropi gak valid dan bikin false positive di password kuat).
- $H > 5.2$ → payload ter-encode/obfuscated: **+25**
- $H < 1.8$ dan $L \ge 32$ → NOP sled / padding buffer overflow: **+35**

### Pilar 4: Behavioral Sliding Window + Decay
- Tabel IP statis 32 slot (ESP32 punya RAM lega, tapi tetap dibatasi supaya kebal IP-spoofing flood), eviction LRU.
- Tiap hit ke honeypot: +10.
- Decay eksponensial: $S(t) = S_0 \cdot 2^{-\Delta t / T_{1/2}}$, dengan $T_{1/2} = 60$ detik.

### Level eskalasi
| Skor | Level | Aksi |
|:---|:---|:---|
| 0–19 | NORMAL | Log ringan |
| 20–49 | SUSPICIOUS | OLED warning, tarpit (respons sengaja dilambatin) |
| ≥ 50 / canary | CRITICAL | Kirim event `BREACH` ke Nano → ultimatum |

### Device Fingerprinting
Device resmi ngirim header `X-Node-Token` = HMAC(device_key, device_id ‖ timestamp). Yang gak punya token valid ditandai **ROGUE**. Fingerprint pasif tambahan (User-Agent, urutan header, TTL) dicatat buat profil penyerang.

---

## 6. Protokol ESP32 → Nano (UART satu arah)

Format baris teks, 9600 bps:
```
HB,<seq>                                  # heartbeat tiap 1 detik
EV,<seq>,<level>,<ip>,<type>,<score>      # event deteksi
PL,<seq>,<base64 payload, max 128B>       # potongan payload buat forensik
AT,<sha256 firmware>                      # attestation sesudah boot
```
- Nano **gak pernah ngirim balik** ke ESP32. Kontrol cuma lewat pin EN dan GPIO27.
- Kalau `seq` loncat atau 5 detik gak ada `HB` → Nano anggap ESP32 sudah dibajak/hang → **CRITICAL**.
- Kalau dalam 1 menit ada lebih dari N event (flood dari ESP32 yang sudah dibajak) → Nano tetap lockdown (fail-secure), tapi log di-rate-limit supaya SD gak penuh.

---

## 7. Kriptografi Nano → Laptop (HMAC-SHA256)

- Library: `Crypto` (rweather) di Nano; HMAC-SHA256 makan ±2–4 ms di 16 MHz.
- Key: `MASTER_KEY` 32 byte, di-hardcode di firmware Nano dan disimpan di laptop master (file permission 600).
- Paket:
```json
{"dev":"SENTINEL-01","ctr":1042,"ts":1790943525,
 "lvl":"CRITICAL","type":"SQL_INJECTION","src":"192.168.1.105",
 "act":"GLOBAL_LOCKDOWN","sig":"<hex HMAC-SHA256>"}
```
- `sig = HMAC-SHA256(MASTER_KEY, dev|ctr|ts|lvl|type|src|act)`
- **Anti-replay:** `ctr` disimpan di EEPROM Nano dan selalu naik; daemon menolak `ctr` ≤ nilai terakhir. `ts` harus dalam ±120 detik dari jam laptop.
- **Per-device key (anti-duplikat):** laptop menurunkan key per device: `K_dev = HMAC-SHA256(MASTER_KEY, device_id)`, lalu menandatangani ulang perintah buat tiap daemon pakai `K_dev`-nya. Kalau 1 device bocor, key device lain tetap aman.
- Transport ke daemon: TCP + TLS (self-signed, cert di-pin) atau minimal payload dienkripsi AES-GCM pakai `K_dev`.

> [!NOTE]
> Nano yang verifikasi HMAC & tanda tangan, bukan ESP32. ESP32 dianggap "sekali pakai" dan gak pernah dipercaya pegang key.

---

## 8. Ultimatum Script (alur lockdown)

1. **Deteksi:** ESP32 kirim `EV,...,CRITICAL,...` (atau heartbeat hilang).
2. **Blackbox:** Nano tulis ke `/LOGS/BREACH.LOG` di SD dengan timestamp RTC + payload.
3. **Sinyal:** Nano kirim paket HMAC ke laptop via USB.
4. **Lockdown laptop:** `master_daemon.py` verifikasi, lalu:
   - Firewall: DROP semua inbound kecuali whitelist.
   - Kirim perintah bertanda tangan per device ke semua daemon whitelist, termasuk `type` serangan.
   - Push notif ke HP.
5. **Purge ESP32:**
   - Nano tarik D8 (GPIO27) LOW → tunggu 50 ms → pulse D7 (EN) LOW 150 ms → lepas EN.
   - ESP32 boot, lihat GPIO27 LOW → `nvs_flash_erase()` + format LittleFS → tulis ulang config default dari firmware → reboot.
   - Nano lepas D8.
6. **Verifikasi aman (attestation):**
   - ESP32 kirim `AT,<sha256 partisi app>` (`esp_partition_get_sha256`).
   - Nano bandingkan dengan **golden hash** yang di-hardcode. Cocok → status `CLEAN`. Gak cocok → tetap lockdown + alert "firmware tampered, reflash manual".
7. **Keluar lockdown:** cuma bisa lewat perintah dari laptop master (konfirmasi manual), sesudah attestation `CLEAN` dan semua daemon lapor scan selesai.

### Hardening firmware ESP32 (biar purge beneran efektif)
- **OTA dimatikan** (partition scheme tanpa OTA). Penyerang dari jaringan gak bisa nulis ulang firmware, jadi reset + wipe NVS sudah membersihkan state.
- Gak ada endpoint yang bisa nulis ke flash selain config di NVS (yang dihapus pas purge).
- Opsional lanjutan: Secure Boot v2 + Flash Encryption. **Hati-hati:** ini membakar eFuse dan gak bisa dibatalkan. Uji dulu di board cadangan.

> [!WARNING]
> Batasan attestation: kalau firmware *sudah* berhasil diganti, firmware jahat bisa bohong soal hash-nya. Tanpa Secure Boot, attestation cuma mendeteksi korupsi/tampering yang gak disengaja. Jelaskan batasan ini terang-terangan ke juri; ini nunjukin kamu paham threat model-nya.

---

## 9. Security Daemon di Trusted Devices

Daemon (`security_daemon.py`) jalan sebagai service (systemd / Windows Service), listen di port khusus, cuma terima koneksi dari IP laptop master + tanda tangan `K_dev` yang valid.

### Peta tipe serangan → scan terarah
| `type` dari A-HIDS | Windows | Linux |
|:---|:---|:---|
| `SQL_INJECTION` | Audit log web server/DB, cek akun DB baru | Grep log nginx/apache/mysql buat pola SQLi, cek user DB baru |
| `RCE` / `CMD_INJECTION` | `Start-MpScan -ScanType FullScan`, cek proses & scheduled task baru | `rkhunter --check`, `chkrootkit`, audit cron & systemd unit baru |
| `PATH_TRAVERSAL` | Cek file sensitif yang diakses (Event Log) | Cek akses `/etc/passwd`, `.ssh`, `.env` di access log |
| `BRUTE_FORCE` | Cek Event ID 4625/4624, lock akun | `lastb`, `journalctl -u sshd`, fail2ban ban IP |
| `SHELLCODE` / `MALWARE` | Defender Full Scan + `Get-MpThreatDetection` | `clamscan -r --infected /home /tmp /var/www` |
| `CANARY` / `ROGUE_DEVICE` | Cek koneksi aktif (`Get-NetTCPConnection`) | `ss -tunap`, cek listener baru |

Selalu jalan juga: isolasi firewall (whitelist-only) + kirim laporan hasil scan ke laptop master (ditandatangani `K_dev`).

### HP
Push notif (ntfy / Telegram bot):
> 🚨 A-HIDS: Serangan `SQL_INJECTION` dari 192.168.1.105. Putus dari WiFi ini, pindah ke data seluler, dan cek manual perangkatmu.

---

## 10. Skenario Demo

| # | Aksi | Yang terlihat |
|:--:|:---|:---|
| 1 | Normal: node resmi (Uno/laptop) kirim request ber-token | OLED: `DEV: VERIFIED` / `SECURE`, LED hijau Nano |
| 2 | Attacker: `nmap 192.168.1.0/24` | Cuma ESP32 yang kelihatan; Nano gak ada di jaringan |
| 3 | Attacker: `curl "http://192.168.1.88/login?user=admin' OR '1'='1"` | OLED: `THREAT: SQLi`, skor naik |
| 4 | Skor ≥ 50 | Nano LED merah, laptop: popup lockdown + firewall aktif |
| 5 | Daemon PC/server | Scan terarah jalan sesuai tipe serangan |
| 6 | HP | Notif "ganti network" |
| 7 | Purge | ESP32 reboot, NVS terhapus, attestation `CLEAN` |
| 8 | Forensik | Cabut SD dari Nano → tunjukkan `BREACH.LOG` (timestamp RTC, IP, payload, aksi) |
| 9 | Bonus: cabut kabel TX ESP32 → Nano | Heartbeat hilang → Nano otomatis lockdown (bukti fail-secure) |

---

## 11. Roadmap Implementasi

1. **Wiring & smoke test:** ESP32 + OLED; Nano + RTC + SD (I2C scanner, tes tulis SD).
2. **Link UART satu arah:** heartbeat ESP32 → Nano, watchdog timeout.
3. **Honeypot ESP32:** fake web panel + telnet banner, canary endpoint.
4. **A-HIDS engine:** WAF, entropy, sliding window + decay, fingerprinting.
5. **Nano sentinel:** blackbox log, HMAC + counter EEPROM, purge/reset sequence.
6. **Firmware purge path di ESP32:** cek GPIO27 pas boot, wipe NVS/LittleFS, attestation.
7. **`master_daemon.py`:** verifikasi HMAC, firewall, fan-out per-device, ntfy.
8. **`security_daemon.py`:** versi Windows & Linux, peta scan terarah.
9. **Latihan demo end-to-end** + simpan bukti log.

### Struktur folder rencana
```
siber/
├── plan.md
├── firmware/
│   ├── esp32_bait/        # PlatformIO / Arduino
│   └── nano_sentinel/
├── laptop/
│   └── master_daemon.py
└── daemon/
    ├── security_daemon.py
    └── scan_profiles.yaml
```
