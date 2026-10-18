#!/usr/bin/env python3
"""
A-HIDS ESP32 Honeypot Quick Setup Wizard
----------------------------------------
Provisioning Ramah Pengguna untuk ESP32 Deception Honeypot Core:
1. Pemilihan Wi-Fi (Scan otomatis, urut sinyal, deteksi koneksi aktif).
2. Password Wi-Fi (Auto-fill dari NetworkManager / input manual).
3. Identitas & Persona Honeypot (MikroTik WebFig, Cisco Switch, IoT Camera, Debian Server, Custom).
4. Konfigurasi Mode IP (DHCP Otomatis vs Custom Static IP).
5. Konfigurasi Port Jebakan (Web, Telnet, SSH Canary).
6. Handshake UART aman dengan validasi ACK [CONFIG] Provisioned & status verifikasi web live.
"""

import os
import re
import sys
import time
import json
import socket
import argparse
import subprocess
import urllib.request
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ANSI Colors
CLR_RESET   = "\033[0m"
CLR_BOLD    = "\033[1m"
CLR_DIM     = "\033[2m"
CLR_GREEN   = "\033[92m"
CLR_RED     = "\033[91m"
CLR_YELLOW  = "\033[93m"
CLR_CYAN    = "\033[96m"
CLR_WHITE   = "\033[97m"
CLR_MAGENTA = "\033[95m"

def get_config_file_path():
    """Menentukan lokasi ahids_config.json yang valid."""
    candidates = [
        os.path.join(SCRIPT_DIR, "daemon", "ahids_config.json"),
        os.path.join(SCRIPT_DIR, "ahids_config.json"),
        os.path.expanduser("~/Download/siber/daemon/ahids_config.json"),
        os.path.expanduser("~/Download/siber/laptop/ahids_config.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Default fallback
    target = os.path.join(SCRIPT_DIR, "daemon", "ahids_config.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target

def get_known_devices_path():
    """Menentukan lokasi known_devices.json yang valid."""
    candidates = [
        os.path.join(SCRIPT_DIR, "daemon", "known_devices.json"),
        os.path.join(SCRIPT_DIR, "known_devices.json"),
        os.path.expanduser("~/Download/siber/daemon/known_devices.json"),
        os.path.expanduser("~/Download/siber/laptop/known_devices.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    target = os.path.join(SCRIPT_DIR, "daemon", "known_devices.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target

def get_master_ip():
    """Mengambil IP lokal aktif laptop master."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def find_serial_ports():
    """Mendeteksi port serial ESP32 (/dev/ttyUSB*) dan Arduino Sentinel (/dev/ttyACM*)."""
    esp_port = None
    esp_desc = ""
    uno_port = None
    uno_desc = ""

    if serial:
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            dev = p.device
            desc = p.description or ""
            # ESP32: CP2102, CH340, FTDI, atau port ttyUSB
            if ("cp210" in desc.lower() or "ch340" in desc.lower() or "uart" in desc.lower() or "ttyusb" in dev.lower()) and not esp_port:
                esp_port = dev
                esp_desc = desc
            # Arduino Uno / Nano: CDC ACM di ttyACM
            elif ("arduino" in desc.lower() or "ttyacm" in dev.lower()) and not uno_port:
                uno_port = dev
                uno_desc = desc

    # Fallback ke path standar jika ada di /dev
    if not esp_port and os.path.exists("/dev/ttyUSB0"):
        esp_port = "/dev/ttyUSB0"
        esp_desc = "USB Serial Device"
    if not uno_port and os.path.exists("/dev/ttyACM0"):
        uno_port = "/dev/ttyACM0"
        uno_desc = "Arduino USB Sentinel"

    return esp_port, esp_desc, uno_port, uno_desc

def load_config():
    """Membaca konfigurasi A-HIDS."""
    cfg_path = get_config_file_path()
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "broadcast_port": 9999,
        "secret_key": "SECRET_MASTER_NANO_2026",
        "ntfy_enabled": True,
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "ahids_alert_mametz",
        "esp32_ip": "192.168.0.112"
    }

def save_config(cfg):
    """Menyimpan konfigurasi A-HIDS."""
    cfg_path = get_config_file_path()
    try:
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"{CLR_RED}[!] Gagal menyimpan {cfg_path}: {e}{CLR_RESET}")

def scan_available_wifi():
    """Memindai daftar Wi-Fi di sekitar menggunakan NetworkManager (nmcli)."""
    networks = []
    seen_ssids = set()
    current_active_ssid = ""

    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,BARS", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=5
        )
        for line in res.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 4:
                in_use = parts[0].strip() == "*"
                ssid = parts[1].strip()
                signal_str = parts[2].strip()
                security = parts[3].strip() or "Open"
                bars = parts[4].strip() if len(parts) > 4 else ""

                if not ssid:
                    continue

                if in_use:
                    current_active_ssid = ssid

                if ssid not in seen_ssids:
                    seen_ssids.add(ssid)
                    signal_val = int(signal_str) if signal_str.isdigit() else 0
                    networks.append({
                        "ssid": ssid,
                        "signal": signal_val,
                        "security": security,
                        "in_use": in_use,
                        "bars": bars
                    })
    except Exception:
        pass

    # Urutkan berdasarkan koneksi aktif dan sinyal terkuat
    networks.sort(key=lambda x: (1 if x["in_use"] else 0, x["signal"]), reverse=True)
    return networks, current_active_ssid

def get_saved_wifi_password(ssid):
    """Mengambil password tersimpan di NetworkManager jika ada."""
    if not ssid:
        return ""
    try:
        res = subprocess.run(
            ["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", ssid],
            capture_output=True, text=True, timeout=3
        )
        psk = res.stdout.strip()
        if psk and not psk.startswith("Error:"):
            return psk
    except Exception:
        pass
    return ""

def set_sentinel_watchdog(uno_port, arm=True):
    """Mengatur watchdog Sentinel Nano (DISARM saat konfigurasi/reboot, ARM setelah selesai)."""
    if not uno_port or not os.path.exists(uno_port) or not serial:
        return
    try:
        s = serial.Serial(uno_port, 115200, timeout=0.5)
        time.sleep(0.3)
        cmd = b"ARM\n" if arm else b"DISARM\n"
        s.write(cmd)
        time.sleep(0.1)
        s.close()
    except Exception:
        pass

# ================= STEP WIZARD FUNCTIONS =================

def prompt_wifi_selection(networks, current_host_ssid):
    """Langkah 1: Pemilihan Jaringan Wi-Fi."""
    print(f"\n{CLR_CYAN}{CLR_BOLD}[LANGKAH 1/5] Pemilihan Jaringan Wi-Fi untuk ESP32{CLR_RESET}")
    print(f"{CLR_DIM}Pilih Wi-Fi yang akan digunakan oleh ESP32 Honeypot (harus 2.4 GHz):{CLR_RESET}\n")

    default_idx = None

    if networks:
        print(f" {CLR_BOLD}{'No':<4} {'SSID / Nama Jaringan':<28} {'Sinyal':<10} {'Keamanan':<12} {'Keterangan'}{CLR_RESET}")
        print(" " + "-" * 74)
        for i, net in enumerate(networks, start=1):
            tag = ""
            if net["in_use"]:
                tag = f"{CLR_GREEN}[Host Terhubung - Rekomendasi]{CLR_RESET}"
                default_idx = i
            elif "5g" in net["ssid"].lower() or "5ghz" in net["ssid"].lower():
                tag = f"{CLR_YELLOW}(5GHz - disarankan 2.4GHz){CLR_RESET}"
            elif net["security"] == "Open":
                tag = f"{CLR_CYAN}[Tanpa Password]{CLR_RESET}"

            sec_label = net["security"][:11] if len(net["security"]) > 11 else net["security"]
            print(f" [{CLR_BOLD}{i}{CLR_RESET}] {net['ssid']:<28} {net['signal']:>2}% {net['bars']:<4} {sec_label:<12} {tag}")

        print(" " + "-" * 74)
        print(f" [{CLR_BOLD}M{CLR_RESET}] Masukkan SSID Manual (Hidden / Jaringan Lain)")
        print(f" [{CLR_BOLD}R{CLR_RESET}] Scan Ulang Jaringan Wi-Fi")
        print(" " + "-" * 74)
    else:
        print(f"{CLR_YELLOW}[!] Tidak ada Wi-Fi terdeteksi via nmcli.{CLR_RESET}")
        print(f" [{CLR_BOLD}M{CLR_RESET}] Masukkan SSID Manual")
        print(f" [{CLR_BOLD}R{CLR_RESET}] Coba Scan Ulang")

    default_prompt = f" [default {default_idx}]" if default_idx else ""
    while True:
        choice = input(f"\n{CLR_BOLD}Pilih nomor Wi-Fi{default_prompt}: {CLR_RESET}").strip()

        if not choice and default_idx:
            chosen = networks[default_idx - 1]
            return chosen["ssid"], chosen["security"]

        if choice.lower() == "r":
            print(f"{CLR_CYAN}[*] Memindai ulang jaringan Wi-Fi...{CLR_RESET}")
            time.sleep(1)
            new_nets, new_cur = scan_available_wifi()
            return prompt_wifi_selection(new_nets, new_cur)

        if choice.lower() == "m":
            while True:
                manual_ssid = input(f"{CLR_BOLD}Masukkan Nama SSID Wi-Fi: {CLR_RESET}").strip()
                if manual_ssid:
                    return manual_ssid, "WPA2"
                print(f"{CLR_RED}[!] SSID tidak boleh kosong.{CLR_RESET}")

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(networks):
                chosen = networks[idx - 1]
                return chosen["ssid"], chosen["security"]

        print(f"{CLR_RED}[!] Pilihan tidak valid. Masukkan nomor 1-{len(networks)}, M, atau R.{CLR_RESET}")

def prompt_wifi_password(ssid, security):
    """Langkah 2: Password Wi-Fi dengan auto-fill dari NetworkManager."""
    print(f"\n{CLR_CYAN}{CLR_BOLD}[LANGKAH 2/5] Password Wi-Fi untuk '{ssid}'{CLR_RESET}")

    if security == "Open":
        print(f"{CLR_GREEN}[+] Jaringan terbuka (Open) tanpa keamanan. Password dilewati.{CLR_RESET}")
        return ""

    saved_pass = get_saved_wifi_password(ssid)

    if saved_pass:
        print(f"{CLR_GREEN}[+] Password tersimpan terdeteksi di laptop: {CLR_BOLD}{saved_pass}{CLR_RESET}")
        pwd_input = input(f"{CLR_BOLD}Gunakan password ini? Tekan Enter untuk pakai, atau ketik password baru: {CLR_RESET}").strip()
        if not pwd_input:
            return saved_pass
        return pwd_input
    else:
        while True:
            pwd_input = input(f"{CLR_BOLD}Masukkan Password Wi-Fi: {CLR_RESET}").strip()
            if pwd_input:
                return pwd_input
            conf = input(f"Lanjutkan tanpa password? [y/N]: ").strip().lower()
            if conf == "y":
                return ""

def prompt_honeypot_identity():
    """Langkah 3: Identitas Decoy & Profil Honeypot."""
    print(f"\n{CLR_CYAN}{CLR_BOLD}[LANGKAH 3/5] Identitas & Persona Honeypot ESP32{CLR_RESET}")
    print(f"{CLR_DIM}Pilih identitas umpan (decoy) yang ditampilkan kepada penyerang di LAN:{CLR_RESET}\n")

    personas = [
        {
            "id": "1",
            "title": "MikroTik RouterOS v6.49 (Rekomendasi)",
            "name": "RouterOS-Core-Gateway",
            "profile": "ROUTER_OS",
            "desc": "Tampilan login WebFig otentik (Port 80) & RouterBoard Telnet trap (Port 23)."
        },
        {
            "id": "2",
            "title": "Cisco Enterprise Managed Switch",
            "name": "Cisco-Catalyst-Switch",
            "profile": "ENTERPRISE_SWITCH",
            "desc": "Konsol Cisco Catalyst Level 15 (Port 80/23) & SSH Canary banner (Port 22)."
        },
        {
            "id": "3",
            "title": "IoT Edge / IP Camera Hub",
            "name": "IoT-Camera-Gateway",
            "profile": "IOT_GATEWAY",
            "desc": "Portal kamera IP / RTSP video streaming BusyBox Linux (Port 80/23)."
        },
        {
            "id": "4",
            "title": "Homelab Linux Server (Debian 12 Bookworm)",
            "name": "Homelab-Debian-Srv01",
            "profile": "DEBIAN_SERVER",
            "desc": "Web default Nginx Debian 12 (Port 80) & Linux login trap (Port 22/23)."
        },
        {
            "id": "5",
            "title": "Nama Decoy Kustom (Ketik Sendiri)",
            "name": "Custom",
            "profile": "ROUTER_OS",
            "desc": "Tentukan nama honeypot kustom sesuai skenario penyerangan."
        }
    ]

    for p in personas:
        default_tag = f" {CLR_GREEN}[Default]{CLR_RESET}" if p["id"] == "1" else ""
        print(f" [{CLR_BOLD}{p['id']}{CLR_RESET}] {CLR_BOLD}{p['title']}{CLR_RESET}{default_tag}")
        print(f"     Nama Decoy: {CLR_CYAN}{p['name']}{CLR_RESET} | Profil: {p['profile']}")
        print(f"     {CLR_DIM}{p['desc']}{CLR_RESET}\n")

    while True:
        choice = input(f"{CLR_BOLD}Pilih nomor persona [1-5, default 1]: {CLR_RESET}").strip()
        if not choice:
            choice = "1"

        if choice in ["1", "2", "3", "4"]:
            selected = next(p for p in personas if p["id"] == choice)
            return selected["name"], selected["profile"]
        elif choice == "5":
            while True:
                custom_name = input(f"{CLR_BOLD}Masukkan nama honeypot kustom [tanpa spasi, misal: Core-Switch-01]: {CLR_RESET}").strip()
                if custom_name:
                    custom_name = custom_name.replace(" ", "-")
                    # Tanyakan profil basis
                    print("  Pilih profil antarmuka:")
                    print("  [1] MikroTik WebFig  [2] Cisco Catalyst  [3] IoT Camera  [4] Debian Nginx")
                    p_sel = input("  Pilih [1-4, default 1]: ").strip()
                    p_map = {"1": "ROUTER_OS", "2": "ENTERPRISE_SWITCH", "3": "IOT_GATEWAY", "4": "DEBIAN_SERVER"}
                    return custom_name, p_map.get(p_sel, "ROUTER_OS")
                print(f"{CLR_RED}[!] Nama tidak boleh kosong.{CLR_RESET}")

        print(f"{CLR_RED}[!] Pilihan tidak valid. Masukkan angka 1 sampai 5.{CLR_RESET}")

def prompt_ip_configuration(master_ip):
    """Langkah 4: Konfigurasi Mode IP (DHCP Otomatis vs Custom Static IP)."""
    print(f"\n{CLR_CYAN}{CLR_BOLD}[LANGKAH 4/5] Konfigurasi Alamat IP Honeypot{CLR_RESET}")
    print(f"{CLR_DIM}Pilih bagaimana ESP32 mendapatkan alamat IP di jaringan LAN:{CLR_RESET}\n")

    # Hitung default gateway dari master IP jika format x.x.x.x
    gw_default = "192.168.0.1"
    parts = master_ip.split(".")
    if len(parts) == 4 and parts[0] != "127":
        gw_default = f"{parts[0]}.{parts[1]}.{parts[2]}.1"

    print(f" [{CLR_BOLD}1{CLR_RESET}] {CLR_BOLD}DHCP Otomatis (Rekomendasi){CLR_RESET} {CLR_GREEN}[Default]{CLR_RESET}")
    print(f"     ESP32 meminta alokasi IP otomatis dari Router / AP.")
    print(f" [{CLR_BOLD}2{CLR_RESET}] {CLR_BOLD}Custom Static IP (IP Manual Tetap){CLR_RESET}")
    print(f"     Tentukan alamat IP statis khusus (misal: honeypot menyamar di IP tertentu).\n")

    choice = input(f"{CLR_BOLD}Pilih mode IP [1 / 2, default 1]: {CLR_RESET}").strip()
    if choice != "2":
        return "", "", ""  # Kosong menandakan DHCP

    # Custom Static IP
    print(f"\n{CLR_CYAN}[*] Masukkan Detail IP Statis Honeypot:{CLR_RESET}")
    while True:
        static_ip = input(f"{CLR_BOLD}  • Alamat IP Honeypot [misal {gw_default[:-1]}222]: {CLR_RESET}").strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', static_ip):
            break
        print(f"{CLR_RED}  [!] Format IP tidak valid. Contoh: 192.168.0.222{CLR_RESET}")

    gw_input = input(f"{CLR_BOLD}  • Gateway Router [default {gw_default}]: {CLR_RESET}").strip()
    gateway = gw_input if gw_input else gw_default

    mask_input = input(f"{CLR_BOLD}  • Subnet Mask [default 255.255.255.0]: {CLR_RESET}").strip()
    subnet = mask_input if mask_input else "255.255.255.0"

    return static_ip, gateway, subnet

def prompt_trap_ports(profile):
    """Langkah 5: Konfigurasi Port Jebakan (Web, Telnet, SSH Canary)."""
    print(f"\n{CLR_CYAN}{CLR_BOLD}[LANGKAH 5/5] Port Layanan Umpan (Trap Ports){CLR_RESET}")
    print(f"{CLR_DIM}Tentukan port jebakan yang akan dibuka oleh ESP32 untuk memancing penyerang:{CLR_RESET}\n")

    # Default ports per profile
    def_web = 80
    def_tel = 23
    def_ssh = 22 if profile in ["ENTERPRISE_SWITCH", "DEBIAN_SERVER"] else 0

    ssh_info = f", SSH Canary Port {def_ssh}" if def_ssh > 0 else " (SSH nonaktif)"
    print(f" [{CLR_BOLD}1{CLR_RESET}] {CLR_BOLD}Gunakan Port Standar Persona{CLR_RESET} {CLR_GREEN}[Default]{CLR_RESET}")
    print(f"     Port Web: {CLR_CYAN}{def_web}{CLR_RESET}, Telnet: {CLR_CYAN}{def_tel}{CLR_RESET}{ssh_info}")
    print(f" [{CLR_BOLD}2{CLR_RESET}] {CLR_BOLD}Kustomisasi Port (Pilih manual atau ubah port){CLR_RESET}\n")

    choice = input(f"{CLR_BOLD}Pilih opsi port [1 / 2, default 1]: {CLR_RESET}").strip()
    if choice != "2":
        return def_web, def_tel, def_ssh

    # Kustomisasi manual
    print(f"\n{CLR_CYAN}[*] Masukkan nomor port (isi 0 untuk menonaktifkan jebakan tersebut):{CLR_RESET}")
    w_in = input(f"{CLR_BOLD}  • Port Web Honeypot [default {def_web}]: {CLR_RESET}").strip()
    web_port = int(w_in) if w_in.isdigit() else def_web

    t_in = input(f"{CLR_BOLD}  • Port Telnet Honeypot [default {def_tel}]: {CLR_RESET}").strip()
    telnet_port = int(t_in) if t_in.isdigit() else def_tel

    s_in = input(f"{CLR_BOLD}  • Port SSH Canary Banner [default {def_ssh}]: {CLR_RESET}").strip()
    ssh_port = int(s_in) if s_in.isdigit() else def_ssh

    return web_port, telnet_port, ssh_port

# ================= EXECUTION & HANDSHAKE CORE =================

def execute_provisioning(esp_port, uno_port, ssid, password, honey_name, profile,
                         web_port=80, telnet_port=23, ssh_port=0,
                         static_ip="", gateway="", subnet=""):
    """
    Mengirim konfigurasi ke ESP32 melalui Serial UART dengan handshake ACK
    dan validasi verifikasi live tanpa spaming output.
    """
    ip_mode_str = f"STATIC ({static_ip})" if static_ip else "DHCP (Otomatis)"
    ports_str = f"Web:{web_port} | Telnet:{telnet_port}" + (f" | SSH:{ssh_port}" if ssh_port > 0 else "")

    print(f"\n{CLR_CYAN}{CLR_BOLD}+========================================================================+")
    print(f"|                  MEMULAI PROVISIONING KE ESP32                         |")
    print(f"+========================================================================+{CLR_RESET}")
    print(f" • Port Serial    : {CLR_BOLD}{esp_port}{CLR_RESET}")
    print(f" • Wi-Fi Target   : {CLR_BOLD}{ssid}{CLR_RESET}")
    print(f" • Nama Honeypot  : {CLR_BOLD}{honey_name}{CLR_RESET} [{profile}]")
    print(f" • Alokasi IP     : {CLR_BOLD}{ip_mode_str}{CLR_RESET}")
    print(f" • Port Jebakan   : {CLR_BOLD}{ports_str}{CLR_RESET}")
    print(f"--------------------------------------------------------------------------")

    if not esp_port or not os.path.exists(esp_port):
        print(f"{CLR_RED}[-] Port serial ESP32 ({esp_port}) tidak ditemukan! Pastikan kabel USB terpasang.{CLR_RESET}")
        return False, None

    if not serial:
        print(f"{CLR_RED}[-] Modul pyserial belum terpasang. Jalankan: pip install pyserial{CLR_RESET}")
        return False, None

    # Step A: Disarm Nano Sentinel Watchdog sementara agar ESP32 tidak di-reset saat reboot
    if uno_port and os.path.exists(uno_port):
        set_sentinel_watchdog(uno_port, arm=False)

    try:
        s_esp = serial.Serial()
        s_esp.port = esp_port
        s_esp.baudrate = 115200
        s_esp.timeout = 0.1
        s_esp.dtr = False
        s_esp.rts = False
        s_esp.open()

        # Step B: Tunggu ESP32 siap (karena open port CP2102 dapat memicu reset transien)
        print(f"{CLR_CYAN}[1/4] Menjalin sinkronisasi UART dengan ESP32...{CLR_RESET}")
        time.sleep(1.2)
        s_esp.reset_input_buffer()
        s_esp.reset_output_buffer()

        # Susun perintah SET_CONFIG lengkap:
        # SET_CONFIG <ssid> <pass> <name> <profile> [wport] [tport] [sport] [static_ip] [gw] [mask]
        pass_arg = password if password else "OPEN_NO_PASS"
        if static_ip:
            cmd = f"SET_CONFIG {ssid} {pass_arg} {honey_name} {profile} {web_port} {telnet_port} {ssh_port} {static_ip} {gateway} {subnet}\n"
        else:
            cmd = f"SET_CONFIG {ssid} {pass_arg} {honey_name} {profile} {web_port} {telnet_port} {ssh_port}\n"

        # Step C: Handshake Loop - Kirim dan tunggu konfirmasi ACK "[CONFIG] Provisioned"
        print(f"{CLR_CYAN}[2/4] Mengirim paket konfigurasi NVS via Serial...{CLR_RESET}")
        ack_received = False
        retries = 4

        for attempt in range(1, retries + 1):
            s_esp.write(b"\n")
            time.sleep(0.1)
            s_esp.write(cmd.encode("utf-8"))
            s_esp.flush()

            # Tunggu respon ACK maksimal 2 detik per percobaan
            t_ack_start = time.time()
            buf = ""
            while time.time() - t_ack_start < 2.0:
                if s_esp.in_waiting > 0:
                    chunk = s_esp.read(s_esp.in_waiting).decode("utf-8", errors="ignore")
                    buf += chunk
                    if "[CONFIG] Provisioned" in buf or "Provisioned SSID" in buf:
                        ack_received = True
                        break
                time.sleep(0.05)

            if ack_received:
                break
            else:
                sys.stdout.write(f"\r  {CLR_YELLOW}[*] Menunggu ACK ESP32 (Percobaan {attempt}/{retries})...{CLR_RESET}")
                sys.stdout.flush()

        if ack_received:
            print(f"\r{CLR_GREEN}[+] Konfigurasi berhasil diterima & disimpan ke flash NVS ESP32!{CLR_RESET}")
        else:
            print(f"\r{CLR_YELLOW}[*] Perintah SET_CONFIG telah dikirim (lanjut ke pemantauan koneksi)...{CLR_RESET}")

        # Step D: Monitor Reboot & Wi-Fi Connection
        print(f"\n{CLR_YELLOW}[3/4] ESP32 merestart dan menghubungkan ke Wi-Fi '{ssid}'...{CLR_RESET}")
        print(f"{CLR_DIM}Menunggu negosiasi DHCP / IP dari jaringan (maksimal 45 detik)...{CLR_RESET}\n")

        assigned_ip = None
        start_t = time.time()
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_idx = 0
        rx_buffer = ""
        last_probe_t = time.time()
        softap_detected = False

        while time.time() - start_t < 45:
            elapsed = int(time.time() - start_t)
            spin_char = spinner[spin_idx % len(spinner)]
            spin_idx += 1

            if s_esp.in_waiting > 0:
                raw_chunk = s_esp.read(s_esp.in_waiting).decode("utf-8", errors="ignore")
                rx_buffer += raw_chunk

                # Tampilkan pesan status secara rapi (hindari spam satu baris)
                for line in raw_chunk.splitlines():
                    ls = line.strip()
                    if ls:
                        if "[WIFI]" in ls and "Menghubungkan" in ls:
                            sys.stdout.write(f"\r{CLR_DIM}  [ESP32] {ls}{CLR_RESET}\n")
                        elif "[WIFI CONNECTED]" in ls or "IP Address:" in ls:
                            sys.stdout.write(f"\r{CLR_GREEN}  [ESP32] {ls}{CLR_RESET}\n")
                        elif "[AP ACTIVE]" in ls:
                            sys.stdout.write(f"\r{CLR_YELLOW}  [ESP32] {ls}{CLR_RESET}\n")
                sys.stdout.flush()

            # Deteksi IP dari buffer
            # Format: [WIFI CONNECTED] IP Address: 192.168.0.x
            # Atau JSON: "ip":"192.168.0.x"
            m_ip = re.search(r'(?:\[WIFI CONNECTED\][^\d]*|IP Address:[^\d]*|"ip":\s*")(\b(?:\d{1,3}\.){3}\d{1,3}\b)', rx_buffer)
            if m_ip:
                cand = m_ip.group(1)
                if cand not in ["0.0.0.0", "127.0.0.1", "192.168.4.1"] and not cand.startswith("255."):
                    assigned_ip = cand
                    break

            # Deteksi jika ESP32 beralih ke SoftAP darurat karena gagal konek Wi-Fi
            if "[AP ACTIVE]" in rx_buffer or "AP_ONLINE" in rx_buffer:
                softap_detected = True
                break

            # Indikator live countdown
            sys.stdout.write(f"\r  {CLR_CYAN}[{spin_char}] Menghubungkan ke '{ssid}' ({elapsed}s / 45s)...{CLR_RESET}")
            sys.stdout.flush()

            # Probe berkala jika sudah 6 detik
            if elapsed >= 6 and (time.time() - last_probe_t) > 3.0:
                try:
                    s_esp.write(b"\nCONFIG_DUMP\n")
                    s_esp.flush()
                except Exception:
                    pass
                last_probe_t = time.time()

            time.sleep(0.08)

        sys.stdout.write("\r" + " " * 70 + "\r")

        # Failsafe: Jika waktu hampir habis dan belum tertangkap, query sekali lagi
        if not assigned_ip and not softap_detected:
            try:
                s_esp.write(b"\nCONFIG_DUMP\n")
                s_esp.flush()
                time.sleep(0.8)
                if s_esp.in_waiting > 0:
                    raw_chunk = s_esp.read(s_esp.in_waiting).decode("utf-8", errors="ignore")
                    rx_buffer += raw_chunk
                    m_ip = re.search(r'(?:\[WIFI CONNECTED\][^\d]*|IP Address:[^\d]*|"ip":\s*")(\b(?:\d{1,3}\.){3}\d{1,3}\b)', rx_buffer)
                    if m_ip:
                        cand = m_ip.group(1)
                        if cand not in ["0.0.0.0", "127.0.0.1", "192.168.4.1"]:
                            assigned_ip = cand
            except Exception:
                pass

        s_esp.close()

    except Exception as e:
        print(f"\n{CLR_RED}[-] Terjadi kesalahan saat komunikasi serial ke ESP32: {e}{CLR_RESET}")
        if uno_port and os.path.exists(uno_port):
            set_sentinel_watchdog(uno_port, arm=True)
        return False, None

    # Step E: Re-arm Nano Sentinel Watchdog
    if uno_port and os.path.exists(uno_port):
        set_sentinel_watchdog(uno_port, arm=True)

    # Step F: Evaluasi Hasil Koneksi
    if softap_detected:
        print(f"\n{CLR_RED}{CLR_BOLD}[!] KONEKSI WI-FI GAGAL!{CLR_RESET}")
        print(f"{CLR_YELLOW}ESP32 tidak dapat terhubung ke SSID '{ssid}'.{CLR_RESET}")
        print(f"{CLR_YELLOW}ESP32 beralih ke Hotspot Darurat: SSID '{CLR_BOLD}A-HIDS-SETUP{CLR_YELLOW}' (IP: 192.168.4.1).{CLR_RESET}")
        print(f"{CLR_DIM}Penyebab umum: Password salah atau SSID berfrekuensi 5 GHz (ESP32 hanya mendukung 2.4 GHz).{CLR_RESET}")
        print(f"{CLR_DIM}Catatan: IP 192.168.4.1 tidak disimpan ke konfigurasi produksi.{CLR_RESET}\n")
        return False, None

    if assigned_ip:
        print(f"{CLR_GREEN}{CLR_BOLD}[4/4] BERHASIL! ESP32 terhubung ke Wi-Fi '{ssid}'!{CLR_RESET}")

        # Verifikasi HTTP Trap Live
        web_status = "Tidak diaktifkan"
        if web_port > 0:
            try:
                req = urllib.request.Request(f"http://{assigned_ip}:{web_port}/", headers={"User-Agent": "A-HIDS-Verifier"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        web_status = f"{CLR_GREEN}AKTIF & MERESPONS (HTTP 200 OK){CLR_RESET}"
            except Exception:
                web_status = f"{CLR_YELLOW}LISTENING (PORT {web_port}){CLR_RESET}"

        # Perbarui konfigurasi lokal
        cfg = load_config()
        cfg["esp32_ip"] = assigned_ip
        save_config(cfg)

        # Perbarui known_devices.json
        kdev_path = get_known_devices_path()
        try:
            kdevs = {}
            if os.path.exists(kdev_path):
                with open(kdev_path, "r") as f:
                    kdevs = json.load(f)
            kdevs[assigned_ip] = {
                "ip": assigned_ip,
                "mac": "8c:94:df:7c:c4:f8",
                "vendor": "Espressif Inc.",
                "hostname": honey_name,
                "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "HONEYPOT_BAIT"
            }
            with open(kdev_path, "w") as f:
                json.dump(kdevs, f, indent=2)
        except Exception:
            pass

        print(f"\n{CLR_GREEN}{CLR_BOLD}+========================================================================+")
        print(f"|               ESP32 DECEPTION HONEYPOT AKTIF & SIAP                    |")
        print(f"+========================================================================+{CLR_RESET}")
        print(f"  {CLR_BOLD}Alamat IP Honeypot  : {CLR_GREEN}{assigned_ip}{CLR_RESET} ({'Static' if static_ip else 'DHCP'})")
        print(f"  {CLR_BOLD}Identitas Decoy     : {CLR_WHITE}{honey_name} [{profile}]{CLR_RESET}")
        print(f"  {CLR_BOLD}Jaringan Wi-Fi      : {CLR_WHITE}{ssid}{CLR_RESET}")
        print(f"  ------------------------------------------------------------------------")
        if web_port > 0:
            print(f"  {CLR_BOLD}Web Trap Port {web_port:<4}  : {CLR_CYAN}http://{assigned_ip}:{web_port}{CLR_RESET} ({web_status})")
        if telnet_port > 0:
            print(f"  {CLR_BOLD}Telnet Trap Port {telnet_port:<2} : {CLR_CYAN}{assigned_ip}:{telnet_port}{CLR_RESET}")
        if ssh_port > 0:
            print(f"  {CLR_BOLD}SSH Canary Port {ssh_port:<3} : {CLR_CYAN}{assigned_ip}:{ssh_port}{CLR_RESET}")
        print(f"  {CLR_BOLD}Web Setup Portal    : {CLR_CYAN}http://{assigned_ip}:{web_port}/setup{CLR_RESET}")
        print(f"+========================================================================+{CLR_RESET}\n")
        return True, assigned_ip
    else:
        print(f"\n{CLR_YELLOW}[!] Waktu habis (45s). ESP32 belum mengirimkan konfirmasi IP.{CLR_RESET}")
        print(f"{CLR_DIM}Silakan periksa apakah Wi-Fi '{ssid}' aktif dan router memberikan DHCP lease.{CLR_RESET}\n")
        return False, None

# ================= RUN WIZARD =================

def run_esp32_setup_wizard():
    """Alur wizard lengkap 5 langkah untuk ESP32."""
    master_ip = get_master_ip()
    esp_port, esp_desc, uno_port, uno_desc = find_serial_ports()

    if not esp_port:
        print(f"\n{CLR_RED}[!] ESP32 tidak terdeteksi pada port USB.{CLR_RESET}")
        print(f"Pastikan kabel USB data terhubung dari laptop ke board ESP32.")
        custom_port = input(f"Ketik port serial manual [misal: /dev/ttyUSB1] atau tekan Enter untuk batal: ").strip()
        if custom_port and os.path.exists(custom_port):
            esp_port = custom_port
        else:
            return

    # Langkah 1: Wi-Fi Scanner & Selector
    print(f"\n{CLR_CYAN}[*] Memindai jaringan Wi-Fi di sekitar...{CLR_RESET}")
    networks, current_host = scan_available_wifi()
    ssid, security = prompt_wifi_selection(networks, current_host)

    # Langkah 2: Password Wi-Fi
    password = prompt_wifi_password(ssid, security)

    # Langkah 3: Identitas & Profil Honeypot
    honey_name, profile = prompt_honeypot_identity()

    # Langkah 4: Mode IP (DHCP vs Custom Static IP)
    static_ip, gateway, subnet = prompt_ip_configuration(master_ip)

    # Langkah 5: Port Jebakan
    web_port, telnet_port, ssh_port = prompt_trap_ports(profile)

    # Ringkasan Konfirmasi
    masked_pass = "*" * len(password) if password else "(Tanpa Password)"
    ip_summary = f"Custom Static ({static_ip})" if static_ip else "DHCP Otomatis"

    print(f"\n{CLR_WHITE}{CLR_BOLD}+========================================================================+")
    print(f"|                 RINGKASAN KONFIGURASI ESP32 HONEYPOT                   |")
    print(f"+========================================================================+{CLR_RESET}")
    print(f"  • Port Serial Target : {CLR_GREEN}{esp_port}{CLR_RESET}")
    print(f"  • Wi-Fi Target       : {CLR_CYAN}{ssid}{CLR_RESET}")
    print(f"  • Password Wi-Fi     : {CLR_WHITE}{masked_pass}{CLR_RESET}")
    print(f"  • Identitas Decoy    : {CLR_BOLD}{honey_name}{CLR_RESET} [{profile}]")
    print(f"  • Konfigurasi IP     : {CLR_BOLD}{ip_summary}{CLR_RESET}")
    print(f"  • Port Web Trap      : {CLR_BOLD}{web_port}{CLR_RESET}")
    print(f"  • Port Telnet Trap   : {CLR_BOLD}{telnet_port}{CLR_RESET}")
    print(f"  • Port SSH Canary    : {CLR_BOLD}{ssh_port or 'Nonaktif'}{CLR_RESET}")
    print(f"+========================================================================+{CLR_RESET}")

    confirm = input(f"\n{CLR_BOLD}Terapkan konfigurasi ke ESP32 sekarang? [Y/n, default Y]: {CLR_RESET}").strip().lower()
    if confirm in ["", "y", "yes"]:
        execute_provisioning(esp_port, uno_port, ssid, password, honey_name, profile,
                             web_port, telnet_port, ssh_port, static_ip, gateway, subnet)
    else:
        print(f"{CLR_YELLOW}[*] Konfigurasi dibatalkan.{CLR_RESET}")

# ================= MAIN MENU =================

def main():
    parser = argparse.ArgumentParser(description="A-HIDS ESP32 Honeypot Quick Setup Wizard")
    parser.add_argument("--set-wifi", nargs=2, metavar=("SSID", "PASS"), help="Konfigurasi Wi-Fi ESP32 langsung via CLI")
    parser.add_argument("--set-name", default="RouterOS-Core-Gateway", help="Nama identitas decoy honeypot")
    parser.add_argument("--profile", default="ROUTER_OS", choices=["ROUTER_OS", "IOT_GATEWAY", "ENTERPRISE_SWITCH", "DEBIAN_SERVER"], help="Profil persona honeypot")
    parser.add_argument("--static-ip", default="", help="Custom static IP (opsional)")
    parser.add_argument("--port", default=None, help="Port serial ESP32 custom")
    args = parser.parse_args()

    # CLI non-interaktif jika dipanggil dengan parameter
    if args.set_wifi:
        esp_port = args.port
        uno_port = None
        if not esp_port:
            esp_port, _, uno_port, _ = find_serial_ports()
        execute_provisioning(esp_port, uno_port, args.set_wifi[0], args.set_wifi[1],
                             args.set_name, args.profile, static_ip=args.static_ip)
        return

    # Tampilan Utama Menu Interaktif (Hanya 1 Opsi Sesuai Permintaan)
    while True:
        master_ip = get_master_ip()
        cfg = load_config()
        current_esp_ip = cfg.get("esp32_ip", "Tidak Diketahui")
        esp_port, esp_desc, uno_port, uno_desc = find_serial_ports()
        _, current_host_wifi = scan_available_wifi()

        print(f"\n{CLR_CYAN}{CLR_BOLD}+========================================================================+")
        print(f"|            A-HIDS HONEYPOT DECEPTION — ESP32 QUICK SETUP               |")
        print(f"+========================================================================+{CLR_RESET}")

        esp_status = f"{CLR_GREEN}TERHUBUNG ({esp_port}){CLR_RESET}" if esp_port else f"{CLR_RED}TIDAK TERDETEKSI{CLR_RESET}"
        uno_status = f"{CLR_GREEN}TERHUBUNG ({uno_port}){CLR_RESET}" if uno_port else f"{CLR_YELLOW}STANDBY{CLR_RESET}"

        # Periksa apakah IP saat ini adalah IP darurat SoftAP
        ip_display = current_esp_ip
        if current_esp_ip == "192.168.4.1":
            ip_display = f"{CLR_YELLOW}192.168.4.1 [Hotspot Darurat]{CLR_RESET}"
        else:
            ip_display = f"{CLR_GREEN}{current_esp_ip}{CLR_RESET}"

        print(f"  • Decoy Honeypot (ESP32)  : {esp_status}")
        print(f"  • Hardware Sentinel (Nano): {uno_status}")
        print(f"  • Master C2 Controller    : {CLR_BOLD}{master_ip}{CLR_RESET} (Parrot C2)")
        print(f"  • Honeypot IP Saat Ini    : {ip_display}")
        print(f"  • Wi-Fi Host Saat Ini     : {CLR_BOLD}{current_host_wifi or 'Tidak Terdeteksi'}{CLR_RESET}")
        print(f"--------------------------------------------------------------------------")
        print(f"  {CLR_BOLD}[1]{CLR_RESET} {CLR_BOLD}Pengaturan ESP32 (Pilih Wi-Fi, Password, Decoy Persona, & IP){CLR_RESET}")
        print(f"  {CLR_BOLD}[Q]{CLR_RESET} Keluar")
        print(f"==========================================================================")

        choice = input(f"{CLR_BOLD}Pilih opsi [1 / Q, default 1]: {CLR_RESET}").strip().lower()

        if choice in ["", "1"]:
            run_esp32_setup_wizard()
        elif choice in ["q", "exit", "quit"]:
            print(f"\n{CLR_CYAN}[*] Selesai. Salam dari A-HIDS Deception Core!{CLR_RESET}\n")
            break
        else:
            print(f"{CLR_RED}[!] Pilihan tidak valid. Ketik 1 atau Q.{CLR_RESET}")

if __name__ == "__main__":
    main()
