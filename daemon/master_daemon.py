#!/usr/bin/env python3
"""
A-HIDS Master Orchestrator Daemon (Primary Transmitter & C2 Core)
----------------------------------------------------------------
1. Menerima telemetri ancaman dari Arduino Sentinel via USB Serial (/dev/ttyACM0).
2. Adaptive Defense Posture & Sensitivity Counter Engine (Level 1, Level 2, Level 3).
3. Menganalisis tipe serangan & membuat resep penanggulangan (Tactical Countermeasure).
4. Memancarkan sinyal darurat UDP Broadcast (Port 9999) ke seluruh armada endpoint (Linux/Windows).
5. Mengirimkan notifikasi instan ke HP/Smartphone via ntfy.sh / Telegram / Webhook (Zero-Emoji).
6. Menjalankan isolasi lokal pada firewall laptop master & mencatat audit forensik.
"""

import sys
import os
import time
import json
import socket
import serial
import serial.tools.list_ports
import subprocess
import urllib.request
import urllib.parse
import argparse
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import A-HIDS Native Device Manager & ZT-NAC Engine
from device_manager import device_manager

# ================= KONFIGURASI GLOBAL =================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "ahids_config.json")
LOG_DIR = os.path.expanduser("~/Download/siber/logs")
LOG_FILE = os.path.join(LOG_DIR, "incident_forensic.log")

DEFAULT_BAUD = 115200
DEFAULT_BROADCAST_PORT = 9999
DEFAULT_SECRET_KEY = "SECRET_MASTER_NANO_2026"
DEFAULT_NTFY_TOPIC = "ahids_alert_mametz"

# ANSI Colors
C_RESET = "\033[0m"
C_RED = "\033[91;1m"
C_GREEN = "\033[92;1m"
C_YELLOW = "\033[93;1m"
C_CYAN = "\033[96;1m"
C_MAGENTA = "\033[95;1m"
C_WHITE = "\033[97m"

def load_config():
    """Memuat konfigurasi dari file JSON jika tersedia"""
    cfg = {
        "broadcast_port": DEFAULT_BROADCAST_PORT,
        "secret_key": DEFAULT_SECRET_KEY,
        "ntfy_enabled": True,
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": DEFAULT_NTFY_TOPIC,
        "telegram_enabled": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "discord_enabled": False,
        "discord_webhook": "",
        "auto_block_master": True,
        "default_quarantine_timeout": 300,
        "esp32_ip": "192.168.0.112",
        "sensitivity_level": 2,
        "sensitivity_thresholds": {
            "lvl1": 100,
            "lvl2": 60,
            "lvl3": 1
        }
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                user_cfg = json.load(f)
                cfg.update(user_cfg)
        except Exception as e:
            print(f"{C_YELLOW}[WARN] Gagal membaca {CONFIG_PATH}: {e}. Memakai konfigurasi default.{C_RESET}")
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        print(f"{C_YELLOW}[WARN] Gagal menyimpan konfigurasi: {e}{C_RESET}")
        return False

def init_env():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write(f"# A-HIDS Forensic Audit Log - Created {datetime.now().isoformat()}\n")

def get_master_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def log_incident(raw_line):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {raw_line}\n"
    with open(LOG_FILE, "a") as f:
        f.write(entry)

def find_serial_port():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    for p in ports:
        if "ACM" in p:
            return p
    for p in ports:
        if "USB" in p:
            return p
    return None

IMMUTABLE_WHITELIST_IPS = {
    "127.0.0.1", "0.0.0.0", "UNKNOWN", "N/A",
    "192.168.0.110", "192.168.0.1", "192.168.0.112", "192.168.0.200"
}

def is_valid_ip(ip):
    if not ip or ip in IMMUTABLE_WHITELIST_IPS:
        return False
    try:
        socket.inet_aton(ip)
        return ip.count('.') == 3
    except Exception:
        return False

# ================= ADAPTIVE THREAT ACCUMULATOR & SENSITIVITY POSTURE =================
class ThreatScoreAccumulator:
    """
    Stateful threat score accumulator & defense posture engine.
    Mencatat multi-stage payload, akumulasi skor, hitungan strike, dan evaluasi ambang batas
    sesuai tingkat sensitivitas (Level 1, Level 2, Level 3).
    """
    def __init__(self, decay_seconds=600):
        self.lock = threading.Lock()
        self.records = {}
        self.processed_seqs = {}  # seq -> timestamp
        self.decay_seconds = decay_seconds

    def record_incident(self, ip, threat_type, score_increment, seq=None, config=None, is_sentinel_lockdown=False):
        if not config:
            config = load_config()

        now = time.time()
        with self.lock:
            # Deduplikasi berdasarkan sequence id dari ESP32/Nano (window 30s)
            if seq:
                if seq in self.processed_seqs and (now - self.processed_seqs[seq] < 30):
                    rec = self.records.get(ip, {})
                    return rec.get("is_blocked", False), {
                        "is_duplicate": True,
                        "ip": ip,
                        "threat_type": threat_type,
                        "total_score": rec.get("total_score", score_increment),
                        "strikes": rec.get("strikes", 1),
                        "level": config.get("sensitivity_level", 2)
                    }
                self.processed_seqs[seq] = now
                if len(self.processed_seqs) > 500:
                    cutoff = now - 60
                    self.processed_seqs = {k: v for k, v in self.processed_seqs.items() if v > cutoff}

            if ip not in self.records:
                self.records[ip] = {
                    "total_score": 0,
                    "strikes": 0,
                    "threats": [],
                    "first_seen": now,
                    "last_seen": now,
                    "is_blocked": False
                }

            rec = self.records[ip]

            # Decay check: kurangi skor secara halus jika tidak aktif lebih dari decay_seconds
            if not rec["is_blocked"] and (now - rec["last_seen"] > self.decay_seconds):
                decay = int((now - rec["last_seen"]) / 60) * 5
                rec["total_score"] = max(0, rec["total_score"] - decay)

            rec["total_score"] += score_increment
            rec["strikes"] += 1
            if threat_type not in rec["threats"]:
                rec["threats"].append(threat_type)
            rec["last_seen"] = now

            sens_lvl = config.get("sensitivity_level", 2)
            thresholds = config.get("sensitivity_thresholds", {"lvl1": 100, "lvl2": 60, "lvl3": 1})

            should_block = False
            level_name = "LEVEL 2 [BALANCED]"
            threshold = 60
            reason = ""

            if sens_lvl == 1:
                # Level 1: Research / Study Mode (Tidak langsung blokir, pelajari serangan)
                threshold = thresholds.get("lvl1", 100)
                level_name = "LEVEL 1 [RESEARCH]"
                is_canary = ("CANARY" in threat_type.upper() or "ADMIN_SECRET" in threat_type.upper())
                if rec["total_score"] >= threshold or is_canary:
                    should_block = True
                    reason = f"Critical threshold breached in Research Mode (Score: {rec['total_score']}/{threshold})"
                else:
                    should_block = False
                    reason = f"Observing in Research Mode (Score: {rec['total_score']}/{threshold}, Strikes: {rec['strikes']})"

            elif sens_lvl == 2:
                # Level 2: Medium / Balanced / Adaptive (Akumulasi skor, blokir saat tembus ambang)
                threshold = thresholds.get("lvl2", 60)
                level_name = "LEVEL 2 [BALANCED]"
                if rec["total_score"] >= threshold or rec["strikes"] >= 3:
                    should_block = True
                    reason = f"Adaptive threshold exceeded (Score: {rec['total_score']}/{threshold}, Strikes: {rec['strikes']}/3)"
                else:
                    should_block = False
                    reason = f"Under Watch Dogs (Score: {rec['total_score']}/{threshold}, Strikes: {rec['strikes']}/3)"

            else:
                # Level 3: Paranoid / Zero-Tolerance (Apapun yang sentuh honeypot langsung diblokir)
                threshold = 1
                level_name = "LEVEL 3 [PARANOID]"
                should_block = True
                reason = f"Zero-tolerance trigger ({threat_type})"

            is_new_breach = False
            if should_block:
                if not rec["is_blocked"]:
                    rec["is_blocked"] = True
                    is_new_breach = True

            return should_block, {
                "is_duplicate": False,
                "is_new_breach": is_new_breach,
                "is_already_blocked": rec["is_blocked"] and not is_new_breach,
                "ip": ip,
                "threat_type": threat_type,
                "level": sens_lvl,
                "level_name": level_name,
                "total_score": rec["total_score"],
                "strikes": rec["strikes"],
                "threshold": threshold,
                "reason": reason,
                "threats": list(rec["threats"])
            }

    def reset_ip(self, ip):
        with self.lock:
            if ip in self.records:
                del self.records[ip]
                return True
            return False

    def get_status(self):
        with self.lock:
            active_probes = {k: v for k, v in self.records.items() if not v.get("is_blocked")}
            blocked_probes = {k: v for k, v in self.records.items() if v.get("is_blocked")}
            return len(active_probes), len(blocked_probes), dict(self.records)

threat_accumulator = ThreatScoreAccumulator()

# ================= GENERATOR RESEP PERTAHANAN (COUNTERMEASURE) =================
def build_countermeasure_recipe(threat_type, attacker_ip, level="CRITICAL"):
    """
    Membuat resep pertahanan taktis spesifik berdasarkan klasifikasi ancaman.
    Resep ini mencakup instruksi OS-agnostik dan perintah spesifik Linux/Windows.
    """
    threat_upper = threat_type.upper()

    recipe = {
        "strategy": "IP_ISOLATION_AND_DROP",
        "action_summary": f"Isolasi lalu lintas dari {attacker_ip} pada semua endpoint armada",
        "target_ip": attacker_ip,
        "target_ports": [],
        "kill_active_connections": True,
        "quarantine_level": "STANDARD",
        "recommended_commands": {
            "linux_iptables": f"iptables -I INPUT -s {attacker_ip} -j DROP && iptables -I OUTPUT -d {attacker_ip} -j DROP",
            "linux_nftables": f"nft add element inet filter blacklist {{ {attacker_ip} }}",
            "linux_ufw": f"ufw insert 1 deny from {attacker_ip}",
            "windows_netsh": f'netsh advfirewall firewall add rule name="AHIDS_BLOCK_{attacker_ip}" dir=in action=block remoteip={attacker_ip}',
            "windows_powershell": f"New-NetFirewallRule -DisplayName 'AHIDS_BLOCK_{attacker_ip}' -Direction Inbound -Action Block -RemoteAddress {attacker_ip}"
        }
    }

    if level == "OBSERVE":
        recipe["strategy"] = "OBSERVE_AND_LEARN"
        recipe["action_summary"] = f"Observasi & Studi Serangan: Skor di bawah ambang batas, armada siaga."
        recipe["kill_active_connections"] = False
        recipe["quarantine_level"] = "MONITOR_ONLY"
        return recipe

    if "TELNET" in threat_upper or "MIRAI" in threat_upper or "23" in threat_upper:
        recipe["strategy"] = "BRUTE_FORCE_COUNTERMEASURE"
        recipe["action_summary"] = f"Blokir IP {attacker_ip}, putuskan socket aktif, dan amankan port manajemen lokal (22, 23)"
        recipe["target_ports"] = [22, 23]
        recipe["quarantine_level"] = "ELEVATED"

    elif "SHELLCODE" in threat_upper or "ENTROPY" in threat_upper or "OVERFLOW" in threat_upper:
        recipe["strategy"] = "MEMORY_EXPLOITATION_CONTAINMENT"
        recipe["action_summary"] = f"DARURAT: Isolasi ketat IP {attacker_ip}, putuskan seluruh koneksi jaringan, flush cache ARP"
        recipe["kill_active_connections"] = True
        recipe["quarantine_level"] = "STRICT_LOCKDOWN"

    elif "SQL" in threat_upper or "INJECTION" in threat_upper:
        recipe["strategy"] = "WEB_EXPLOIT_MITIGATION"
        recipe["action_summary"] = f"Blokir IP {attacker_ip} dan perketat filter web service lokal"
        recipe["target_ports"] = [80, 443, 3306, 5432]
        recipe["quarantine_level"] = "STANDARD"

    elif "XSS" in threat_upper or "TRAVERSAL" in threat_upper:
        recipe["strategy"] = "APP_LAYER_FILTERING"
        recipe["action_summary"] = f"Blokir IP {attacker_ip} dari akses endpoint web internal"
        recipe["target_ports"] = [80, 443, 8080]
        recipe["quarantine_level"] = "STANDARD"

    elif "CANARY" in threat_upper:
        recipe["strategy"] = "DECEPTION_TRIPWIRE_LOCKDOWN"
        recipe["action_summary"] = f"Trap canary tersentuh! Blacklist IP {attacker_ip} permanen di seluruh node"
        recipe["quarantine_level"] = "STRICT_LOCKDOWN"

    elif "HEARTBEAT" in threat_upper or "TIMEOUT" in threat_upper:
        recipe["strategy"] = "SENTINEL_WATCHDOG_ALERT"
        recipe["action_summary"] = "Watchdog mendeteksi ESP32 freeze/mati. Armada siaga pertahanan lokal."
        recipe["target_ip"] = "N/A"
        recipe["kill_active_connections"] = False
        recipe["quarantine_level"] = "DEFENSIVE_STANDBY"

    return recipe

# ================= NOTIFIKASI HP / SMARTPHONE (ZERO-EMOJI) =================
def send_mobile_notification(threat_type, attacker_ip, level, recipe, config, extra_meta=None):
    """
    Mengirimkan notifikasi push instan ke smartphone operator via ntfy.sh / Telegram / Webhook.
    Zero-emoji, mematuhi standar taktis ASCII.
    """
    master_ip = get_master_ip()
    extra_meta = extra_meta or {}
    level_name = extra_meta.get("level_name", f"LVL {config.get('sensitivity_level', 2)}")
    total_score = extra_meta.get("total_score", 0)
    thresh = extra_meta.get("threshold", 0)
    strikes = extra_meta.get("strikes", 1)

    message = (
        f"[A-HIDS ALERT] THREAT EVENT: {threat_type} ({level})\n"
        f"Attacker Host : {attacker_ip}\n"
        f"Defense Mode  : {level_name}\n"
        f"Score Meter   : {total_score} / {thresh} pts [Strike #{strikes}]\n"
        f"Action Recipe : {recipe.get('action_summary')}\n"
        f"Master Node   : http://{master_ip}:8000"
    )

    # 1. ntfy.sh Push Notification
    if config.get("ntfy_enabled", True):
        topic = config.get("ntfy_topic", DEFAULT_NTFY_TOPIC)
        server = config.get("ntfy_server", "https://ntfy.sh").rstrip("/")
        url = f"{server}/{topic}"
        try:
            req = urllib.request.Request(
                url,
                data=message.encode('utf-8'),
                headers={
                    "Title": f"A-HIDS ALERT: {threat_type[:30]}",
                    "Priority": "urgent" if level == "CRITICAL" else "high",
                    "Tags": "shield,warning"
                }
            )
            res = urllib.request.urlopen(req, timeout=4)
            if res.status == 200:
                print(f"{C_GREEN}[NOTIFY]{C_RESET} Push dispatched via ntfy ({url})")
        except Exception as e:
            print(f"{C_YELLOW}[NOTIFY WARN]{C_RESET} ntfy delivery failed: {e}")

    # 2. Telegram Bot
    if config.get("telegram_enabled", False):
        bot_token = config.get("telegram_bot_token")
        chat_id = config.get("telegram_chat_id")
        if bot_token and chat_id:
            try:
                tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                reply_markup = None

                if threat_type == "NEW_DEVICE_DISCOVERED":
                    mac_str = extra_meta.get("mac", "UNKNOWN")
                    vendor_str = extra_meta.get("vendor", "Generic LAN Device")
                    tg_msg = (
                        f"<b>[A-HIDS ZT-NAC] NEW DEVICE DISCOVERED IN LAN</b>\n\n"
                        f"Host IP   : <code>{attacker_ip}</code>\n"
                        f"MAC Addr  : <code>{mac_str}</code>\n"
                        f"Vendor    : <i>{vendor_str}</i>\n"
                        f"Status    : <code>PENDING_REVIEW (Score: 0)</code>\n"
                        f"Action    : Layer-2 detection triggered. Review admittance:\n\n"
                        f"[-] Quarantine / Ban : <code>/block {attacker_ip}</code>\n"
                        f"[+] Allow / Whitelist : <code>/allow {attacker_ip}</code>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "[+] Allow (Whitelist)", "callback_data": f"allow:{attacker_ip}"},
                                {"text": "[-] Ban (Quarantine)", "callback_data": f"ban:{attacker_ip}"}
                            ]
                        ]
                    }

                elif level == "OBSERVE":
                    tg_msg = (
                        f"<b>[A-HIDS WATCHDOG: PROBE OBSERVED]</b>\n\n"
                        f"Source IP   : <code>{attacker_ip}</code>\n"
                        f"Threat Type : <b>{threat_type}</b>\n"
                        f"Posture     : <code>{level_name}</code>\n"
                        f"Score Meter : <code>{total_score} / {thresh} pts [Strike #{strikes}]</code>\n"
                        f"State       : <b>UNDER WATCH DOGS (Isolation Deferred)</b>\n"
                        f"Action      : {recipe.get('action_summary')}\n\n"
                        f"[-] Force Ban : <code>/block {attacker_ip}</code>\n"
                        f"[+] Whitelist : <code>/allow {attacker_ip}</code>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "[+] Allow", "callback_data": f"allow:{attacker_ip}"},
                                {"text": "[-] Force Ban", "callback_data": f"ban:{attacker_ip}"}
                            ]
                        ]
                    }

                else:
                    tg_msg = (
                        f"<b>[A-HIDS FLEET LOCKDOWN: THRESHOLD BREACHED]</b>\n\n"
                        f"Attacker IP : <code>{attacker_ip}</code>\n"
                        f"Threat Type : <b>{threat_type}</b>\n"
                        f"Posture     : <code>{level_name}</code>\n"
                        f"Score Meter : <code>{total_score} / {thresh} pts [Strike #{strikes}]</code>\n"
                        f"Enforcement : <b>{recipe.get('action_summary')}</b>\n"
                        f"Execution   : <code>iptables DROP deployed across all fleet nodes!</code>\n\n"
                        f"Restore     : <code>/allow {attacker_ip}</code>"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "[+] Restore / Allow", "callback_data": f"allow:{attacker_ip}"}
                            ]
                        ]
                    }

                payload_data = {
                    "chat_id": chat_id,
                    "text": tg_msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
                if reply_markup:
                    payload_data["reply_markup"] = reply_markup

                payload = json.dumps(payload_data).encode('utf-8')
                req = urllib.request.Request(tg_url, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
                print(f"{C_GREEN}[TELEGRAM]{C_RESET} Alert successfully dispatched to Telegram chat!")
            except Exception as e:
                print(f"{C_YELLOW}[TELEGRAM WARN]{C_RESET} Failed to send to Telegram: {e}")

    # 3. Discord Webhook
    if config.get("discord_enabled", False):
        dc_url = config.get("discord_webhook")
        if dc_url:
            try:
                payload = json.dumps({"content": f"```ini\n{message}\n```"}).encode('utf-8')
                req = urllib.request.Request(dc_url, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=4)
                print(f"{C_GREEN}[DISCORD]{C_RESET} Alert dispatched to Discord channel!")
            except Exception as e:
                print(f"{C_YELLOW}[DISCORD WARN]{C_RESET} Failed to send to Discord: {e}")

# ================= UDP BROADCAST KE ARMADA ENDPOINT =================
def broadcast_defense_signal(threat_type, attacker_ip, level, recipe, config):
    """
    Memancarkan paket taktis UDP Broadcast ke seluruh subnet agar semua client (Windows/Linux)
    dapat mengeksekusi penanggulangan secara terkoordinasi.
    """
    port = config.get("broadcast_port", DEFAULT_BROADCAST_PORT)
    secret_key = config.get("secret_key", DEFAULT_SECRET_KEY)
    timestamp = datetime.now().isoformat()

    payload = {
        "version": "2.0",
        "source": "A-HIDS-PRIMARY-TRANSMITTER",
        "command": "SYSTEM_LOCKDOWN" if level == "CRITICAL" else "THREAT_DEFENSE_TRIGGER",
        "timestamp": timestamp,
        "threat": {
            "type": threat_type,
            "level": level,
            "attacker_ip": attacker_ip
        },
        "countermeasure": recipe,
        "auth_key": secret_key
    }

    raw_bytes = json.dumps(payload).encode('utf-8')
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(raw_bytes, ('<broadcast>', port))
        sock.sendto(raw_bytes, ('255.255.255.255', port))
        sock.close()
        print(f"{C_CYAN}[UDP BROADCAST]{C_RESET} Resep pertahanan disiarkan ke seluruh armada di port {port}!")
    except Exception as e:
        print(f"{C_YELLOW}[BROADCAST WARN]{C_RESET} Gagal memancarkan UDP: {e}")

# ================= ISOLASI LOKAL PADA MASTER =================
def trigger_master_defense(attacker_ip):
    """Mengeksekusi penanggulangan pada laptop master sendiri"""
    if not is_valid_ip(attacker_ip):
        return

    print(f"{C_RED}[MASTER DEFENSE]{C_RESET} Mengeksekusi isolasi lokal terhadap {attacker_ip}...")
    if os.geteuid() == 0:
        try:
            subprocess.run(["iptables", "-I", "INPUT", "-s", attacker_ip, "-j", "DROP"],
                           capture_output=True, text=True, check=True)
            subprocess.run(["iptables", "-I", "OUTPUT", "-d", attacker_ip, "-j", "DROP"],
                           capture_output=True, text=True, check=True)
            print(f"{C_GREEN}[FIREWALL KERNEL]{C_RESET} Aturan DROP aktif di iptables master.")
        except Exception as e:
            print(f"{C_YELLOW}[FIREWALL WARN]{C_RESET} iptables error: {e}")
    else:
        print(f"{C_CYAN}[SIMULATION]{C_RESET} Master berjalan sebagai user biasa. Jalankan dengan 'sudo' untuk live iptables drop.")

# ================= INCIDENT EVALUATION & DISPATCH =================
def process_telemetry_incident(seq, level, attacker_ip, threat_type, score, config, raw_data="", is_sentinel_lockdown=False):
    """
    Memproses event telemetri melalui Adaptive Threat Accumulator & Sensitivity Posture.
    Menentukan apakah serangan harus diobservasi (Level 1/2 bawah) atau diblokir armada (Level 3/tembus batas).
    """
    if not is_valid_ip(attacker_ip):
        return

    should_block, dec = threat_accumulator.record_incident(
        attacker_ip, threat_type, score, seq=seq, config=config, is_sentinel_lockdown=is_sentinel_lockdown
    )

    if dec.get("is_duplicate"):
        return

    if should_block:
        if dec.get("is_new_breach"):
            print(f"\n{C_RED}+--------------------------------------------------------------------+{C_RESET}")
            print(f"{C_RED}| [DEFENSE ENFORCED] THRESHOLD BREACHED: {threat_type:<23} [{dec['level_name']:<12}] |{C_RESET}")
            print(f"{C_RED}+--------------------------------------------------------------------+{C_RESET}")
            print(f"Attacker Host : {C_RED}{attacker_ip}{C_RESET}")
            print(f"Posture Mode  : {C_YELLOW}{dec['level_name']}{C_RESET}")
            print(f"Score Meter   : {C_WHITE}{dec['total_score']} / {dec['threshold']} pts [Strike #{dec['strikes']}]{C_RESET}")
            print(f"Reason        : {C_WHITE}{dec['reason']}{C_RESET}")
            print(f"Timestamp     : {C_WHITE}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{C_RESET}")

            recipe = build_countermeasure_recipe(threat_type, attacker_ip, "CRITICAL")
            broadcast_defense_signal(threat_type, attacker_ip, "CRITICAL", recipe, config)
            send_mobile_notification(threat_type, attacker_ip, "CRITICAL", recipe, config, extra_meta=dec)
            trigger_master_defense(attacker_ip)
            device_manager.block_device(attacker_ip, reason=dec["reason"])
            log_incident(f"ENFORCEMENT|LEVEL={dec['level']}|ACTION=BLOCK|IP={attacker_ip}|THREAT={threat_type}|SCORE={dec['total_score']}|REASON={dec['reason']}")
            print(f"{C_GREEN}[STATUS] Whole-fleet isolation successfully enforced for {attacker_ip}{C_RESET}\n")
        else:
            print(f"{C_YELLOW}[REPEAT PROBE DROPPED]{C_RESET} Host {attacker_ip} re-probed with {threat_type} (Score: {dec['total_score']}) - Dropped by active firewall.")
    else:
        # Threat score accumulated but under threshold
        print(f"\n{C_YELLOW}+--------------------------------------------------------------------+{C_RESET}")
        print(f"{C_YELLOW}| [DEFENSE OBSERVATION] {dec['level_name']:<28} [{dec['reason']:<16}] |{C_RESET}")
        print(f"{C_YELLOW}+--------------------------------------------------------------------+{C_RESET}")
        print(f"Source Host   : {C_WHITE}{attacker_ip}{C_RESET}")
        print(f"Threat Vector : {C_YELLOW}{threat_type}{C_RESET} (+{score} pts)")
        print(f"Score Meter   : {C_WHITE}{dec['total_score']} / {dec['threshold']} pts [Strike #{dec['strikes']}]{C_RESET}")
        print(f"Action Status : {C_CYAN}FLEET ISOLATION DEFERRED (Studying Attack Telemetry){C_RESET}")
        print(f"Timestamp     : {C_WHITE}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{C_RESET}")

        recipe = build_countermeasure_recipe(threat_type, attacker_ip, "OBSERVE")
        recipe["strategy"] = "OBSERVE_AND_LEARN"
        recipe["action_summary"] = f"Observasi aktif ({dec['level_name']}). Skor {dec['total_score']}/{dec['threshold']}. Armada siaga, belum isolasi."

        send_mobile_notification(threat_type, attacker_ip, "OBSERVE", recipe, config, extra_meta=dec)

        # Update threat score in device_manager without dropping
        dev = device_manager.get_device(attacker_ip)
        if dev:
            with device_manager.lock:
                dev_ref = device_manager.devices.get(attacker_ip)
                if dev_ref and dev_ref.get("status") != "BLOCKED":
                    dev_ref["threat_score"] = dec["total_score"]
                    device_manager._save_devices_unlocked()

        log_incident(f"OBSERVATION|LEVEL={dec['level']}|ACTION=STUDY|IP={attacker_ip}|THREAT={threat_type}|SCORE={dec['total_score']}|STRIKES={dec['strikes']}|REASON={dec['reason']}")
        print(f"{C_CYAN}[STATUS] Observation telemetry recorded. Attacker is under watch.{C_RESET}\n")

# Legacy compatibility wrapper
def handle_incident_event(threat_type, attacker_ip, level, config, raw_data=""):
    score = 100 if level == "CRITICAL" else 40
    process_telemetry_incident(None, level, attacker_ip, threat_type, score, config, raw_data, is_sentinel_lockdown=True)

# ================= TELEGRAM INTERACTIVE COMMAND LISTENER =================
def telegram_command_listener(config):
    """
    Mendengarkan perintah interaktif dari Security Officer via Telegram (Zero-Emoji):
    /devices, /list        - Daftar perangkat LAN & statusnya
    /allow <IP|MAC>        - Whitelist & pulihkan akses armada
    /block <IP|MAC>        - Karantina manual / Ban perangkat
    /scan                  - Pindai aktif subnet lokal
    /sensitivity, /level   - Kontrol sensitivitas HIDS (Level 1, 2, 3)
    /reset <IP>            - Reset skor akumulasi IP
    /client                - Download script trusted_node.py & one-liner
    /status                - Status lengkap armada & C2
    """
    bot_token = config.get("telegram_bot_token")
    if not bot_token or not config.get("telegram_enabled"):
        return

    print(f"{C_GREEN}[TELEGRAM C2]{C_RESET} Interactive Command Poller aktif!")
    offset = 0

    def reply_tg(chat_id, text, reply_markup=None):
        try:
            tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            p = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if reply_markup:
                p["reply_markup"] = reply_markup
            payload = json.dumps(p).encode('utf-8')
            req = urllib.request.Request(tg_url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            print(f"{C_YELLOW}[TG REPLY ERR]{C_RESET} {e}")

    def edit_tg(chat_id, msg_id, text, reply_markup=None):
        try:
            edit_url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
            p = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": text,
                "parse_mode": "HTML"
            }
            if reply_markup:
                p["reply_markup"] = reply_markup
            payload = json.dumps(p).encode('utf-8')
            req = urllib.request.Request(edit_url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def ack_cb(cb_id, text=None):
        try:
            ans_url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
            p = {"callback_query_id": cb_id}
            if text:
                p["text"] = text
            req = urllib.request.Request(ans_url, data=json.dumps(p).encode('utf-8'), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=4)
        except Exception:
            pass

    while True:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset}&timeout=10"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if data.get("ok"):
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1

                    # 1. Handle Inline Button Callback Queries
                    cb = upd.get("callback_query")
                    if cb:
                        cb_id = cb.get("id")
                        cb_data = cb.get("data", "")
                        cb_msg = cb.get("message", {})
                        chat_id = cb_msg.get("chat", {}).get("id")
                        msg_id = cb_msg.get("message_id")

                        if cb_data.startswith("allow:"):
                            target_ip = cb_data.split(":", 1)[1]
                            dev = device_manager.allow_device(target_ip)
                            threat_accumulator.reset_ip(target_ip)
                            log_incident(f"MANUAL_OVERRIDE|ACTION=ALLOW|IP={target_ip}|SOURCE=TELEGRAM_BUTTON")
                            ack_cb(cb_id, f"Allowed {target_ip}")
                            edit_tg(chat_id, msg_id, (
                                f"<b>[DECISION: ALLOWED]</b>\n\n"
                                f"Host <code>{target_ip}</code> (<i>{dev.get('vendor', 'Unknown')}</i>) telah di-<b>WHITELIST</b>.\n"
                                f"Sinyal pemulihan akses telah disiarkan ke seluruh armada endpoint."
                            ))
                        elif cb_data.startswith("ban:") or cb_data.startswith("block:"):
                            target_ip = cb_data.split(":", 1)[1]
                            dev = device_manager.block_device(target_ip, reason="TELEGRAM_BUTTON_BLOCK")
                            trigger_master_defense(target_ip)
                            log_incident(f"MANUAL_OVERRIDE|ACTION=BLOCK|IP={target_ip}|SOURCE=TELEGRAM_BUTTON")
                            ack_cb(cb_id, f"Banned {target_ip}")
                            edit_tg(chat_id, msg_id, (
                                f"<b>[DECISION: BANNED / ISOLATED]</b>\n\n"
                                f"Host <code>{target_ip}</code> (<i>{dev.get('vendor', 'Unknown')}</i>) telah di-<b>BLOCKLIST</b>.\n"
                                f"Sinyal isolasi & iptables DROP langsung dieksekusi di seluruh node armada!"
                            ))
                        elif cb_data.startswith("sens:"):
                            new_lvl = int(cb_data.split(":", 1)[1])
                            config["sensitivity_level"] = new_lvl
                            save_config(config)
                            names = {
                                1: "LEVEL 1 [RESEARCH / LEARNING]",
                                2: "LEVEL 2 [BALANCED / ADAPTIVE]",
                                3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"
                            }
                            lvl_name = names.get(new_lvl, f"LEVEL {new_lvl}")
                            ack_cb(cb_id, f"Posture set to Level {new_lvl}")
                            edit_tg(chat_id, msg_id, (
                                f"<b>[SENSITIVITY POSTURE UPDATED]</b>\n\n"
                                f"A-HIDS Defense Posture saat ini: <b>{lvl_name}</b>\n"
                                f"Konfigurasi aktif langsung diterapkan ke deteksi real-time."
                            ))
                        continue

                    # 2. Handle Text Commands
                    msg = upd.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = msg.get("chat", {}).get("id")

                    if not text or not chat_id:
                        continue

                    parts = text.split()
                    cmd = parts[0].lower()
                    target_arg = parts[1] if len(parts) > 1 else None

                    if cmd == "/start" or cmd == "/help":
                        reply_tg(chat_id, (
                            "<b>A-HIDS TACTICAL MOBILE C2 (ZERO-TRUST)</b>\n\n"
                            "<b>Kendali Akses & Perangkat:</b>\n"
                            "• <code>/devices</code> atau <code>/list</code> - Daftar perangkat LAN & statusnya\n"
                            "• <code>/allow &lt;IP/MAC&gt;</code> - Whitelist & pulihkan akses armada\n"
                            "• <code>/block &lt;IP/MAC&gt;</code> - Karantina manual / Ban perangkat\n"
                            "• <code>/ban &lt;IP/MAC&gt;</code> - Alias /block\n"
                            "• <code>/scan</code> - Pindai ulang subnet secara aktif\n\n"
                            "<b>Defense Posture & Sensitivitas:</b>\n"
                            "• <code>/sensitivity</code> atau <code>/level</code> - Cek & ubah level sensitivitas\n"
                            "• <code>/level 1</code> - Mode Research (Observasi penuh, tidak auto-block)\n"
                            "• <code>/level 2</code> - Mode Balanced (Ambang batas toleransi & strike)\n"
                            "• <code>/level 3</code> - Mode Paranoid (Zero tolerance, sentuh langsung drop)\n"
                            "• <code>/reset &lt;IP&gt;</code> - Reset akumulator skor ancaman IP\n\n"
                            "<b>Onboarding Endpoint:</b>\n"
                            "• <code>/client</code> - Unduh script trusted_node.py & one-liner\n\n"
                            "<b>Telemetri:</b>\n"
                            "• <code>/status</code> - Cek status armada & device matrix\n"
                            "• <code>/ping</code> - Tes koneksi C2"
                        ))

                    elif cmd == "/ping":
                        reply_tg(chat_id, "<b>[OK] PONG!</b> Master C2 Orchestrator is ACTIVE.")

                    elif cmd in ["/sensitivity", "/level"]:
                        sens_lvl = config.get("sensitivity_level", 2)
                        thresh_map = config.get("sensitivity_thresholds", {"lvl1": 100, "lvl2": 60, "lvl3": 1})
                        names = {
                            1: "LEVEL 1 [RESEARCH / LEARNING]",
                            2: "LEVEL 2 [BALANCED / ADAPTIVE]",
                            3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"
                        }

                        if target_arg and target_arg in ["1", "2", "3"]:
                            new_lvl = int(target_arg)
                            config["sensitivity_level"] = new_lvl
                            save_config(config)
                            reply_tg(chat_id, (
                                f"<b>[SENSITIVITY UPDATED]</b>\n\n"
                                f"A-HIDS Defense Posture set to: <b>{names[new_lvl]}</b>\n"
                                f"Threshold: <code>{thresh_map.get(f'lvl{new_lvl}', 1)} pts</code>\n"
                                f"Kebijakan ini langsung aktif untuk seluruh probe berikutnya."
                            ))
                        else:
                            reply_tg(chat_id, (
                                f"<b>[A-HIDS DEFENSE POSTURE & SENSITIVITY]</b>\n\n"
                                f"Active Posture : <b>{names.get(sens_lvl, f'LEVEL {sens_lvl}')}</b>\n"
                                f"Threshold Limit: <code>{thresh_map.get(f'lvl{sens_lvl}', 60)} pts</code>\n\n"
                                f"<b>Profil Mode:</b>\n"
                                f"• <b>Level 1 [RESEARCH / LEARNING]:</b>\n"
                                f"  Tidak memblokir probe. Observasi penuh & catat multi-stage payload. Isolasi hanya pada Canary leak (Score &gt;= 100).\n"
                                f"• <b>Level 2 [BALANCED / ADAPTIVE]:</b>\n"
                                f"  Mode toleransi seimbang. Probe di bawah 60 poin berstatus UNDER WATCH DOGS. Blokir armada aktif saat Score &gt;= 60 atau 3 strike.\n"
                                f"• <b>Level 3 [PARANOID / ZERO-TOLERANCE]:</b>\n"
                                f"  Zero tolerance. Setiap sentuhan ke honeypot langsung memicu SYSTEM_LOCKDOWN & drop seluruh armada.\n\n"
                                f"<i>Pilih level di bawah untuk mengganti mode:</i>"
                            ), reply_markup={
                                "inline_keyboard": [
                                    [
                                        {"text": "[1] Lvl 1: Research", "callback_data": "sens:1"},
                                        {"text": "[2] Lvl 2: Balanced", "callback_data": "sens:2"},
                                        {"text": "[3] Lvl 3: Paranoid", "callback_data": "sens:3"}
                                    ]
                                ]
                            })

                    elif cmd in ["/reset", "/clear"] and target_arg:
                        ok = threat_accumulator.reset_ip(target_arg)
                        reply_tg(chat_id, (
                            f"<b>[ACCUMULATOR RESET]</b>\n\n"
                            f"Skor ancaman untuk host <code>{target_arg}</code> telah di-reset ke 0."
                        ))

                    elif cmd in ["/devices", "/list"]:
                        devs = device_manager.get_devices()
                        allowed_cnt = sum(1 for d in devs if d.get("status") == "ALLOWED")
                        blocked_cnt = sum(1 for d in devs if d.get("status") == "BLOCKED")
                        pending_cnt = sum(1 for d in devs if d.get("status") == "PENDING_REVIEW")

                        lines = [
                            "<b>A-HIDS ZERO-TRUST DEVICE ACCESS MATRIX</b>\n",
                            f"Total: <code>{len(devs)} Devices</code> ([+] {allowed_cnt} Allowed | [-] {blocked_cnt} Blocked | [?] {pending_cnt} Pending)\n"
                        ]
                        for d in devs:
                            st = d.get("status", "UNKNOWN")
                            badge = "[ALLOWED]" if st == "ALLOWED" else ("[BLOCKED]" if st == "BLOCKED" else "[PENDING]")
                            ip = d.get("ip")
                            mac = d.get("mac", "N/A")
                            vendor = d.get("vendor", "Generic LAN Device")
                            dtype = d.get("device_type", "UNKNOWN")
                            score = d.get("threat_score", 0)
                            lines.append(f"• <b>{badge}</b> <code>{ip}</code> (Score: {score})\n  MAC: <code>{mac}</code>\n  Vendor: <i>{vendor}</i> ({dtype})\n")

                        lines.append("<i>Gunakan /allow &lt;IP&gt; untuk mengizinkan atau /block &lt;IP&gt; untuk memblokir.</i>")
                        reply_tg(chat_id, "\n".join(lines))

                    elif cmd in ["/block", "/ban"] and target_arg:
                        dev = device_manager.block_device(target_arg, reason="MANUAL_ADMIN_BLOCK")
                        target_ip = dev.get("ip", target_arg)
                        trigger_master_defense(target_ip)
                        log_incident(f"MANUAL_OVERRIDE|ACTION=BLOCK|TARGET={target_arg}|SOURCE=TELEGRAM_OFFICER")
                        reply_tg(chat_id, (
                            f"<b>[ENFORCED / BANNED]</b>\n"
                            f"Perangkat <code>{target_ip}</code> (<i>{dev.get('vendor')}</i>) telah di-<b>BLOCKLIST</b> secara instan di seluruh node armada!"
                        ))

                    elif cmd in ["/unblock", "/allow"] and target_arg:
                        dev = device_manager.allow_device(target_arg)
                        target_ip = dev.get("ip", target_arg)
                        threat_accumulator.reset_ip(target_ip)
                        log_incident(f"MANUAL_OVERRIDE|ACTION=ALLOW|TARGET={target_arg}|SOURCE=TELEGRAM_OFFICER")
                        reply_tg(chat_id, (
                            f"<b>[RESTORED / ALLOWED]</b>\n"
                            f"Perangkat <code>{target_ip}</code> (<i>{dev.get('vendor')}</i>) telah di-<b>ALLOW</b> pada seluruh endpoint armada."
                        ))

                    elif cmd == "/scan":
                        reply_tg(chat_id, "<i>[SCAN] Memulai active discovery sweep pada subnet lokal...</i>")
                        tot, new_devs = device_manager.scan_subnet()
                        new_txt = f"\n⚡ <b>Terdeteksi {len(new_devs)} host baru!</b>" if new_devs else ""
                        reply_tg(chat_id, (
                            f"<b>[SCAN COMPLETE]</b>\n"
                            f"Total perangkat aktif terdaftar: <code>{tot}</code>{new_txt}\n\n"
                            f"Ketik <code>/devices</code> untuk melihat daftar lengkap."
                        ))

                    elif cmd in ["/client", "/getnode", "/download", "/enroll"]:
                        master_ip = get_master_ip()
                        client_path = os.path.join(SCRIPT_DIR, "trusted_node.py")
                        reply_tg(chat_id, (
                            "<b>A-HIDS ENDPOINT PROVISIONING</b>\n\n"
                            "Perintah Onboarding untuk Laptop / Node Baru:\n\n"
                            "Linux / macOS (One-Liner):\n"
                            f"<code>curl -s http://{master_ip}:8000/client | sudo python3 -</code>\n\n"
                            "Windows PowerShell (Run as Admin):\n"
                            f"<code>irm http://{master_ip}:8000/win.ps1 | iex</code>\n\n"
                            "<i>Mengirimkan dokumen trusted_node.py langsung ke chat...</i>"
                        ))
                        try:
                            subprocess.run([
                                "curl", "-s", "-F", f"chat_id={chat_id}",
                                "-F", f"document=@{client_path}",
                                "-F", "caption=A-HIDS Universal Endpoint Defender Client (trusted_node.py)",
                                f"https://api.telegram.org/bot{bot_token}/sendDocument"
                            ], capture_output=True, timeout=10)
                        except Exception:
                            pass

                    elif cmd == "/status":
                        master_ip = get_master_ip()
                        devs = device_manager.get_devices()
                        blocked_list = [d["ip"] for d in devs if d.get("status") == "BLOCKED"]
                        blocked_str = ", ".join(blocked_list) if blocked_list else "None"
                        sens_lvl = config.get("sensitivity_level", 2)
                        names = {1: "LVL 1 [RESEARCH]", 2: "LVL 2 [BALANCED]", 3: "LVL 3 [PARANOID]"}
                        act_probes, blk_probes, _ = threat_accumulator.get_status()

                        reply_tg(chat_id, (
                            f"<b>A-HIDS FLEET STATUS MATRIX</b>\n\n"
                            f"• Master C2 IP      : <code>{master_ip}</code>\n"
                            f"• UDP Transmitter   : <code>PORT 9999 (ACTIVE)</code>\n"
                            f"• Provisioning Port : <code>PORT 8000 (ACTIVE)</code>\n"
                            f"• Sentinel Watchdog : <code>ARMED & SUPERVISING</code>\n"
                            f"• Defense Posture   : <code>{names.get(sens_lvl, f'LVL {sens_lvl}')}</code>\n"
                            f"• Total LAN Hosts   : <code>{len(devs)} Perangkat</code>\n"
                            f"• Active Probes     : <code>{act_probes} Under Watch</code>\n"
                            f"• Isolated Hosts    : <code>{blocked_str}</code>\n"
                            f"• Mode Operasi      : <code>ZERO-TRUST REAL-TIME</code>"
                        ))

            time.sleep(1)
        except Exception:
            time.sleep(3)

# ================= HEADLESS CLIENT PROVISIONING & STATE SYNC SERVER =================
class HeadlessProvisioningHandler(BaseHTTPRequestHandler):
    """
    Micro-HTTP provisioning & fleet state synchronization socket.
    1. Menyediakan script binary trusted_node.py & one-liner (/client, /install, /win.ps1).
    2. Endpoint REST Synchronization (/sync, /state): Menyinkronkan IP terblokir, watchlist, dan posture ke node baru.
    3. Endpoint Registrasi Armada (/register_node): Mendaftarkan endpoint pertahanan baru ke C2 Device Matrix.
    """
    def do_GET(self):
        master_ip = get_master_ip()
        client_path = os.path.join(SCRIPT_DIR, "trusted_node.py")

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ["/sync", "/state", "/policy"]:
            config = load_config()
            sens_lvl = config.get("sensitivity_level", 2)
            names = {1: "LEVEL 1 [RESEARCH / LEARNING]", 2: "LEVEL 2 [BALANCED / ADAPTIVE]", 3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"}
            thresh_map = config.get("sensitivity_thresholds", {"lvl1": 100, "lvl2": 60, "lvl3": 1})

            all_devs = device_manager.get_devices()
            blocked_ips = [d["ip"] for d in all_devs if d.get("status") == "BLOCKED" and is_valid_ip(d.get("ip"))]

            _, _, records = threat_accumulator.get_status()
            watch_ips = []
            for ip, rec in records.items():
                if not rec.get("is_blocked") and rec.get("total_score", 0) > 0 and is_valid_ip(ip):
                    watch_ips.append({
                        "ip": ip,
                        "score": rec.get("total_score", 0),
                        "strikes": rec.get("strikes", 0),
                        "threats": rec.get("threats", [])
                    })

            state_payload = {
                "status": "OK",
                "version": "2.0",
                "master_ip": master_ip,
                "timestamp": datetime.now().isoformat(),
                "posture": {
                    "level": sens_lvl,
                    "name": names.get(sens_lvl, f"LEVEL {sens_lvl}"),
                    "threshold": thresh_map.get(f"lvl{sens_lvl}", 60)
                },
                "blocked_ips": blocked_ips,
                "blocked_details": [
                    {
                        "ip": d["ip"],
                        "threat": d.get("threat_type", "SECURITY_OFFICER_BLOCK"),
                        "reason": d.get("block_reason", "MANUAL_OR_AUTOMATED_QUARANTINE"),
                        "score": d.get("threat_score", 100),
                        "timestamp": d.get("last_seen", "")
                    }
                    for d in all_devs if d.get("status") == "BLOCKED" and is_valid_ip(d.get("ip"))
                ],
                "watch_ips": watch_ips,
                "whitelist_ips": list(IMMUTABLE_WHITELIST_IPS),
                "defense_nodes": [
                    {
                        "ip": d["ip"],
                        "hostname": d.get("hostname", "N/A"),
                        "vendor": d.get("vendor", "N/A"),
                        "last_seen": d.get("last_seen", "")
                    }
                    for d in all_devs if d.get("device_type") == "DEFENSE_NODE"
                ]
            }

            resp_data = json.dumps(state_payload, indent=2).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)
            return

        elif path in ["/register_node", "/enroll_node", "/node_ping"]:
            client_ip = query.get("ip", [self.client_address[0]])[0]
            hostname = query.get("hostname", ["trusted-node"])[0]
            os_name = query.get("os", ["Linux"])[0]

            dev = device_manager.register_defense_node(client_ip, hostname=hostname, os_name=os_name)
            log_incident(f"NODE_REGISTERED|IP={client_ip}|HOST={hostname}|OS={os_name}|SOURCE=HTTP_SYNC")
            print(f"{C_GREEN}[NODE ENROLLED]{C_RESET} Node baru tersinkronisasi: {client_ip} ({hostname} - {os_name})")

            resp_data = json.dumps({
                "status": "REGISTERED",
                "message": f"Node {client_ip} ({hostname}) registered in A-HIDS defense fleet",
                "master_ip": master_ip,
                "timestamp": datetime.now().isoformat()
            }).encode('utf-8')

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)
            return

        elif self.path in ["/client", "/trusted_node.py", "/node"]:
            if os.path.exists(client_path):
                with open(client_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/x-python; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        elif self.path in ["/install", "/install.sh"]:
            bootstrap = f"""#!/bin/bash
echo "[+] A-HIDS Endpoint Provisioning & Fleet Sync Initiated..."
mkdir -p /opt/ahids
curl -s http://{master_ip}:8000/client -o /opt/ahids/trusted_node.py
echo "[+] Binary saved to /opt/ahids/trusted_node.py"
echo "[+] Launching A-HIDS Synchronized Endpoint Defender..."
python3 /opt/ahids/trusted_node.py --master {master_ip}
""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(bootstrap)))
            self.end_headers()
            self.wfile.write(bootstrap)
            return

        elif self.path in ["/win", "/win.ps1"]:
            ps1 = f"""# A-HIDS Windows Fast Client Provisioner & Fleet Sync
Write-Host "[*] Downloading A-HIDS Endpoint Defender from Master C2 ({master_ip})..." -ForegroundColor Cyan
$dest = "$env:TEMP\\ahids_trusted_node.py"
Invoke-WebRequest -Uri "http://{master_ip}:8000/client" -OutFile $dest -UseBasicParsing
Write-Host "[+] Binary ready. Initializing fleet synchronization and watchdog..." -ForegroundColor Green
python $dest --master {master_ip}
""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(ps1)))
            self.end_headers()
            self.wfile.write(ps1)
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def log_message(self, format, *args):
        return

def start_provisioning_server(port=8000):
    try:
        server = HTTPServer(('0.0.0.0', port), HeadlessProvisioningHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"{C_GREEN}[PROVISIONER]{C_RESET} Headless provisioning & sync socket aktif di port {port}")
    except Exception as e:
        print(f"{C_YELLOW}[PROVISIONER WARN]{C_RESET} Gagal bind port {port}: {e}")

def periodic_state_sync_broadcaster(config, interval=30):
    """
    Memancarkan sinyal STATE_SYNC UDP secara berkala (setiap 30 detik)
    agar seluruh armada node yang baru aktif/booting langsung sinkron otomatis.
    """
    port = config.get("broadcast_port", DEFAULT_BROADCAST_PORT)
    secret_key = config.get("secret_key", DEFAULT_SECRET_KEY)

    while True:
        try:
            time.sleep(interval)
            all_devs = device_manager.get_devices()
            blocked_ips = [d["ip"] for d in all_devs if d.get("status") == "BLOCKED" and is_valid_ip(d.get("ip"))]
            sens_lvl = config.get("sensitivity_level", 2)

            payload = {
                "version": "2.0",
                "source": "A-HIDS-PRIMARY-TRANSMITTER",
                "command": "STATE_SYNC",
                "timestamp": datetime.now().isoformat(),
                "posture_level": sens_lvl,
                "blocked_ips": blocked_ips,
                "auth_key": secret_key
            }

            raw_bytes = json.dumps(payload).encode('utf-8')
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(raw_bytes, ('<broadcast>', port))
            sock.sendto(raw_bytes, ('255.255.255.255', port))
            sock.close()
        except Exception:
            pass

# ================= LOOP UTAMA SERIAL LISTENER =================
def run_serial_listener(config):
    init_env()
    master_ip = get_master_ip()
    ntfy_topic = config.get("ntfy_topic", DEFAULT_NTFY_TOPIC)

    # Jalankan headless micro provisioning & sync server
    start_provisioning_server(8000)

    # Jalankan background periodic UDP state synchronizer
    threading.Thread(target=periodic_state_sync_broadcaster, args=(config,), daemon=True).start()

    # Jalankan background poller untuk perintah Telegram
    if config.get("telegram_enabled"):
        t_tg = threading.Thread(target=telegram_command_listener, args=(config,), daemon=True)
        t_tg.start()

    # Jalankan native ZT-NAC Layer-2 discovery engine
    def on_new_device_found(dev):
        dev_ip = dev.get("ip")
        dev_mac = dev.get("mac")
        dev_vendor = dev.get("vendor", "Generic LAN Device")
        if not is_valid_ip(dev_ip) or dev_ip in IMMUTABLE_WHITELIST_IPS:
            return
        if dev.get("device_type") in ["MASTER_C2", "HONEYPOT_BAIT", "GATEWAY", "PROXMOX_HOST", "DEFENSE_NODE"]:
            return

        print(f"\n{C_YELLOW}[ZT-NAC DISCOVERY] Device baru terdeteksi di LAN: {dev_ip} [{dev_mac} - {dev_vendor}]{C_RESET}")
        log_incident(f"EVENT|EV,0,NOTICE,{dev_ip},NEW_DEVICE_DISCOVERED,0|MAC={dev_mac}|VENDOR={dev_vendor}")
        recipe = {
            "strategy": "NEW_DEVICE_REVIEW",
            "action_summary": f"Perangkat baru {dev_ip} [{dev_vendor}] terdeteksi di LAN. Menunggu keputusan allow/block dari Security Officer.",
            "target_ip": dev_ip,
            "target_ports": [],
            "kill_active_connections": False
        }
        send_mobile_notification("NEW_DEVICE_DISCOVERED", dev_ip, "NOTICE", recipe, config, extra_meta={"mac": dev_mac, "vendor": dev_vendor})

    device_manager.start_passive_monitor(on_new_device_callback=on_new_device_found)
    threading.Thread(target=device_manager.scan_subnet, daemon=True).start()

    sens_lvl = config.get("sensitivity_level", 2)
    names = {1: "LEVEL 1 [RESEARCH / LEARNING]", 2: "LEVEL 2 [BALANCED / ADAPTIVE]", 3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"}

    print(f"{C_CYAN}+--------------------------------------------------------------------+{C_RESET}")
    print(f"{C_CYAN}|     A-HIDS PRIMARY TRANSMITTER & C2 ORCHESTRATOR MATRIX            |{C_RESET}")
    print(f"{C_CYAN}+--------------------------------------------------------------------+{C_RESET}")
    print(f"  Master IP       : {C_WHITE}{master_ip}{C_RESET}")
    print(f"  Defense Posture : {C_GREEN}{names.get(sens_lvl, f'LEVEL {sens_lvl}')}{C_RESET}")
    print(f"  Broadcast Port  : {C_WHITE}{config.get('broadcast_port')}{C_RESET}")
    print(f"  Mobile Endpoint : {C_YELLOW}https://ntfy.sh/{ntfy_topic}{C_RESET}")
    print(f"  Audit Log       : {C_WHITE}{LOG_FILE}{C_RESET}")
    print(f"{C_CYAN}+--------------------------------------------------------------------+{C_RESET}")

    while True:
        port = find_serial_port()
        if not port:
            print(f"{C_YELLOW}[MENUNGGU HARDWARE]{C_RESET} Sentinel (Uno/Nano) belum terdeteksi. Mencoba menghubungkan kembali dalam 3 detik...")
            time.sleep(3)
            continue

        print(f"{C_GREEN}[TERDETEKSI] Menemukan hardware Sentinel pada {port}. Membuka serial...{C_RESET}")
        try:
            ser = serial.Serial(port, DEFAULT_BAUD, timeout=1)
            time.sleep(2)
            print(f"{C_GREEN}[CONNECTED] Terhubung ke Sentinel @ {port}! Standby mendengarkan interlink...{C_RESET}\n")
        except Exception as e:
            print(f"{C_RED}[ERROR SERIAL] Gagal membuka {port}: {e}. Mencoba lagi...{C_RESET}")
            time.sleep(3)
            continue

        while True:
            try:
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                else:
                    time.sleep(0.02)
                    continue

                log_incident(line)

                # 1. Telemetri Normal (EVENT|)
                if line.startswith("EVENT|"):
                    print(f"{C_WHITE}[SENTINEL LOG]{C_RESET} {line}")
                    try:
                        raw_parts = line.split("|")
                        ev_str = ""
                        for part in raw_parts:
                            if part.startswith("EV,"):
                                ev_str = part
                                break
                        if ev_str:
                            tokens = ev_str.split(",")
                            if len(tokens) >= 6:
                                seq = tokens[1]
                                ev_level = tokens[2]
                                attacker_ip = tokens[3]
                                threat_type = tokens[4]
                                score = int(tokens[5]) if tokens[5].isdigit() else 0

                                if threat_type == "NEW_DEVICE_DISCOVERED":
                                    if not is_valid_ip(attacker_ip) or attacker_ip in IMMUTABLE_WHITELIST_IPS:
                                        continue
                                    recipe = {
                                        "strategy": "NEW_DEVICE_REVIEW",
                                        "action_summary": f"Device baru {attacker_ip} menyentuh honeypot. Menunggu keputusan allow/block.",
                                        "target_ip": attacker_ip,
                                        "target_ports": [],
                                        "kill_active_connections": False
                                    }
                                    send_mobile_notification("NEW_DEVICE_DISCOVERED", attacker_ip, "NOTICE", recipe, config)
                                else:
                                    process_telemetry_incident(seq, ev_level, attacker_ip, threat_type, score, config, line)
                    except Exception as e:
                        print(f"{C_YELLOW}[EVENT PARSE WARN]{C_RESET} {e}")

                # 2. Sinyal LOCKDOWN dari Sentinel (Ultimatum Breach atau Watchdog Timeout)
                elif line.startswith("LOCKDOWN|"):
                    if "ALERT=HEARTBEAT_TIMEOUT" in line:
                        threat_type = "WATCHDOG_HEARTBEAT_TIMEOUT"
                        level = "CRITICAL"
                        attacker_ip = "N/A"
                        print(f"\n{C_RED}[WATCHDOG FAIL-SECURE]{C_RESET} ESP32 Heartbeat hilang > 3 detik! Memancarkan sinyal darurat lokal.")
                        recipe = build_countermeasure_recipe(threat_type, attacker_ip, level)
                        broadcast_defense_signal(threat_type, attacker_ip, level, recipe, config)
                        send_mobile_notification(threat_type, attacker_ip, level, recipe, config)
                    else:
                        seq = None
                        attacker_ip = "UNKNOWN"
                        threat_type = "UNKNOWN"
                        score = 100
                        level = "CRITICAL"
                        for token in line.split("|"):
                            if token.startswith("DATA="):
                                sub = token.replace("DATA=", "").split(",")
                                if len(sub) >= 6:
                                    seq = sub[1]
                                    level = sub[2]
                                    attacker_ip = sub[3]
                                    threat_type = sub[4]
                                    score = int(sub[5]) if sub[5].isdigit() else 100

                        process_telemetry_incident(seq, level, attacker_ip, threat_type, score, config, line, is_sentinel_lockdown=True)

                # 3. Payload Forensik
                elif line.startswith("PAYLOAD|"):
                    print(f"{C_YELLOW}[FORENSIC PAYLOAD]{C_RESET} {line}")

                else:
                    print(f"{C_WHITE}> {line}{C_RESET}")

            except (serial.SerialException, OSError) as e:
                print(f"\n{C_RED}[TERPUTUS] Koneksi serial terputus: {e}. Mengalihkan ke mode auto-reconnect...{C_RESET}")
                try:
                    ser.close()
                except Exception:
                    pass
                break
            except KeyboardInterrupt:
                print(f"\n{C_YELLOW}[EXIT] Daemon dimatikan oleh operator.{C_RESET}")
                return

# ================= SIMULASI TEST VIA CLI =================
def run_cli():
    parser = argparse.ArgumentParser(description="A-HIDS Primary Transmitter & C2 Master")
    parser.add_argument("--simulate", nargs=2, metavar=("THREAT_TYPE", "ATTACKER_IP"),
                        help="Kirimkan sinyal serangan simulasi ke armada & HP (Contoh: --simulate IOT_BOTNET_TELNET 10.211.137.99)")
    parser.add_argument("--test-mobile", action="store_true", help="Uji coba pengiriman notifikasi push ke HP via ntfy.sh")
    parser.add_argument("--test-telegram", action="store_true", help="Uji coba pengiriman notifikasi ke Telegram")
    parser.add_argument("--set-level", type=int, choices=[1, 2, 3], help="Ubah tingkat sensitivitas (1=Research, 2=Balanced, 3=Paranoid)")
    args = parser.parse_args()

    config = load_config()

    if args.set_level:
        config["sensitivity_level"] = args.set_level
        save_config(config)
        names = {1: "LEVEL 1 [RESEARCH / LEARNING]", 2: "LEVEL 2 [BALANCED / ADAPTIVE]", 3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"}
        print(f"{C_GREEN}[SENSITIVITY UPDATED]{C_RESET} Mode berhasil diubah ke: {names[args.set_level]}")
        return

    if args.test_telegram:
        print(f"{C_CYAN}[TEST TELEGRAM] Mengirimkan tes ping notifikasi ke Telegram Bot...{C_RESET}")
        recipe = build_countermeasure_recipe("TELEGRAM_CONNECTIVITY_TEST", "127.0.0.1", "INFO")
        send_mobile_notification("C2_TELEGRAM_HEALTH_CHECK", "127.0.0.1", "INFO", recipe, config)
        return

    if args.test_mobile:
        print(f"{C_CYAN}[TEST MOBILE] Mengirimkan tes ping notifikasi ke smartphone...{C_RESET}")
        recipe = build_countermeasure_recipe("CLI_CONNECTIVITY_TEST", "127.0.0.1", "INFO")
        send_mobile_notification("C2_SYSTEM_HEALTH_CHECK", "127.0.0.1", "INFO", recipe, config)
        print(f"{C_GREEN}Silakan periksa notifikasi di HP Anda pada link:{C_RESET} https://ntfy.sh/{config.get('ntfy_topic')}")
        return

    if args.simulate:
        threat_type, attacker_ip = args.simulate
        print(f"{C_CYAN}[SIMULASI TRANSMISI] Memancarkan insiden: {threat_type} dari {attacker_ip}{C_RESET}")
        process_telemetry_incident(999, "CRITICAL", attacker_ip, threat_type, 60, config, "SIMULATED_CLI")
        return

    # Default: Jalankan serial listener
    run_serial_listener(config)

if __name__ == "__main__":
    run_cli()
