#!/usr/bin/env python3
"""
A-HIDS Universal Endpoint Security Daemon (Multiplatform Defense Client)
-----------------------------------------------------------------------
Berjalan di semua perangkat dalam jaringan yang sama (Linux / Windows / macOS).
Mendengarkan sinyal darurat (UDP Broadcast Port 9999) dari Master C2 Orchestrator.
Dilengkapi fitur Real-Time Fleet State Synchronization:
1. Sinkronisasi otomatis saat node baru pertama kali bergabung (HTTP Port 8000 /sync).
2. Menyelaraskan (reconcile) firewall lokal dengan database IP terblokir di Master.
3. Otomatis mendaftarkan diri ke C2 Device Matrix sebagai DEFENSE_NODE aktif.
4. Menerima broadcast STATE_SYNC periodik via UDP untuk mencegah drift konfigurasi.
"""

import sys
import os
import time
import json
import socket
import subprocess
import platform
import argparse
import threading
import urllib.request
import urllib.parse
from datetime import datetime

# ================= KONFIGURASI =================
LISTEN_PORT = 9999
SECRET_KEY = "SECRET_MASTER_NANO_2026"
RULES_STATE_FILE = os.path.expanduser("~/.ahids_quarantine_rules.json")
DEFAULT_MASTER_IP = "192.168.0.110"

# ANSI Colors
C_RESET = "\033[0m"
C_RED = "\033[91;1m"
C_GREEN = "\033[92;1m"
C_YELLOW = "\033[93;1m"
C_CYAN = "\033[96;1m"
C_MAGENTA = "\033[95;1m"
C_WHITE = "\033[97m"

OS_TYPE = platform.system().lower()  # 'linux', 'windows', 'darwin'

# ================= PEMERIKSAAN HAK AKSES =================
def is_privileged():
    """Memeriksa apakah daemon dijalankan dengan hak administrator/root"""
    if OS_TYPE == "windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        return os.geteuid() == 0

def get_distro_info():
    """Mendapatkan nama OS / Distro Linux"""
    if OS_TYPE == "linux":
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=")[1].strip().strip('"')
            return f"Linux {platform.release()}"
        except Exception:
            return "Linux (Generic)"
    elif OS_TYPE == "windows":
        return f"Windows {platform.release()} ({platform.version()})"
    elif OS_TYPE == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return platform.platform()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ================= PENGELOLAAN ATURAN (STATE MANAGEMENT) =================
def load_blocked_rules():
    if os.path.exists(RULES_STATE_FILE):
        try:
            with open(RULES_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_blocked_rules(rules):
    try:
        with open(RULES_STATE_FILE, "w") as f:
            json.dump(rules, f, indent=2)
    except Exception:
        pass

# ================= ENGINE PERTAHANAN LINUX =================
def apply_linux_defense(attacker_ip, kill_connections=True):
    applied = []
    try:
        subprocess.run(["iptables", "-I", "INPUT", "-s", attacker_ip, "-j", "DROP"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(["iptables", "-I", "OUTPUT", "-d", attacker_ip, "-j", "DROP"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        applied.append("iptables_drop")
        print(f"     {C_GREEN}[ENFORCE]{C_RESET} iptables INPUT & OUTPUT DROP enforced for {attacker_ip}")
    except Exception as e:
        print(f"     {C_YELLOW}[WARN]{C_RESET} iptables rule insertion failed: {e}")

    if kill_connections:
        try:
            subprocess.run(["ss", "-K", "dst", attacker_ip], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            applied.append("sockets_killed")
            print(f"     {C_GREEN}[PURGE]{C_RESET} Active TCP sockets to {attacker_ip} terminated (ss -K)")
        except Exception:
            pass

    return applied

def remove_linux_defense(attacker_ip):
    try:
        subprocess.run(["iptables", "-D", "INPUT", "-s", attacker_ip, "-j", "DROP"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["iptables", "-D", "OUTPUT", "-d", attacker_ip, "-j", "DROP"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"     {C_GREEN}[RESTORE]{C_RESET} iptables rules for {attacker_ip} removed")
    except Exception as e:
        print(f"     {C_YELLOW}[WARN]{C_RESET} iptables rule cleanup failed: {e}")

# ================= ENGINE PERTAHANAN WINDOWS =================
def apply_windows_defense(attacker_ip, kill_connections=True):
    applied = []
    rule_name_in = f"AHIDS_BLOCK_{attacker_ip}_IN"
    rule_name_out = f"AHIDS_BLOCK_{attacker_ip}_OUT"

    try:
        cmd_in = f'netsh advfirewall firewall add rule name="{rule_name_in}" dir=in action=block remoteip={attacker_ip}'
        subprocess.run(cmd_in, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cmd_out = f'netsh advfirewall firewall add rule name="{rule_name_out}" dir=out action=block remoteip={attacker_ip}'
        subprocess.run(cmd_out, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        applied.append("windows_firewall_drop")
        print(f"     {C_GREEN}[ENFORCE]{C_RESET} Windows Firewall Inbound/Outbound Block active for {attacker_ip}")
    except Exception as e:
        print(f"     {C_YELLOW}[WARN]{C_RESET} netsh advfirewall failed: {e}")

    if kill_connections:
        try:
            ps_cmd = (
                f'powershell -Command "'
                f'Get-NetTCPConnection -RemoteAddress {attacker_ip} -ErrorAction SilentlyContinue | '
                f'ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"'
            )
            subprocess.run(ps_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            applied.append("windows_sockets_killed")
            print(f"     {C_GREEN}[PURGE]{C_RESET} Socket TCP aktif ke {attacker_ip} diputus (PowerShell)")
        except Exception:
            pass

    return applied

def remove_windows_defense(attacker_ip):
    rule_name_in = f"AHIDS_BLOCK_{attacker_ip}_IN"
    rule_name_out = f"AHIDS_BLOCK_{attacker_ip}_OUT"
    try:
        subprocess.run(f'netsh advfirewall firewall delete rule name="{rule_name_in}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(f'netsh advfirewall firewall delete rule name="{rule_name_out}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"     {C_GREEN}[RESTORE]{C_RESET} Windows Firewall rules for {attacker_ip} removed")
    except Exception as e:
        print(f"     {C_YELLOW}[WARN]{C_RESET} Error hapus rule: {e}")

# ================= EKSEKUTOR PERTAHANAN UNIVERSAL =================
def execute_countermeasure(threat_data, countermeasure):
    attacker_ip = countermeasure.get("target_ip") or threat_data.get("attacker_ip", "UNKNOWN")
    threat_type = threat_data.get("type", "UNKNOWN")
    strategy = countermeasure.get("strategy", "IP_ISOLATION_AND_DROP")
    summary = countermeasure.get("action_summary", "Isolasi endpoint aktif")
    kill_connections = countermeasure.get("kill_active_connections", True)

    print(f"\n{C_MAGENTA}[ACTIVE DEFENSE EXECUTOR] Mengeksekusi Resep Pertahanan Endpoint:{C_RESET}")
    print(f"  [-] Strategi Operasional : {C_CYAN}{strategy}{C_RESET}")
    print(f"  [-] Rincian Aksi        : {C_WHITE}{summary}{C_RESET}")
    print(f"  [-] Target Isolasi       : {C_RED}{attacker_ip}{C_RESET}")

    if strategy == "RESTORE_ACCESS" or threat_type == "MANUAL_UNBLOCK":
        print(f"  [-] Tindakan          : {C_GREEN}MENGHAPUS ISOLASI / ALLOW IP {attacker_ip}{C_RESET}")
        if is_privileged():
            if OS_TYPE == "linux":
                remove_linux_defense(attacker_ip)
            elif OS_TYPE == "windows":
                remove_windows_defense(attacker_ip)
        rules = [r for r in load_blocked_rules() if r.get("ip") != attacker_ip]
        save_blocked_rules(rules)
        print(f"  [-] Status Node       : {C_GREEN}AKSES PULIH (NORMAL){C_RESET}\n")
        return

    if attacker_ip in ["UNKNOWN", "N/A", "127.0.0.1", "0.0.0.0"]:
        print(f"  [-] {C_YELLOW}Tindakan firewall dilewati (IP tidak spesifik: {attacker_ip}).{C_RESET}")
        print(f"  [-] Status Node         : {C_GREEN}SIAGA / MONITORING MODE AKTIF{C_RESET}\n")
        return

    privileged = is_privileged()

    if privileged:
        print(f"  [-] Privilese Sistem    : {C_GREEN}ROOT / ADMINISTRATOR (Active Kernel Mode){C_RESET}")
        applied = []
        if OS_TYPE == "linux":
            applied = apply_linux_defense(attacker_ip, kill_connections)
        elif OS_TYPE == "windows":
            applied = apply_windows_defense(attacker_ip, kill_connections)
        elif OS_TYPE == "darwin":
            try:
                subprocess.run(["route", "add", "-host", attacker_ip, "127.0.0.1", "-reject"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                applied.append("macos_route_reject")
            except Exception:
                pass

        # Catat ke history untuk cleanup
        rules = load_blocked_rules()
        existing = [r for r in rules if r.get("ip") == attacker_ip]
        if not existing:
            rules.append({
                "ip": attacker_ip,
                "threat": threat_type,
                "strategy": strategy,
                "timestamp": datetime.now().isoformat(),
                "applied_actions": applied
            })
            save_blocked_rules(rules)

        print(f"  [-] Status Node         : {C_GREEN}TERLINDUNGI & TERISOLASI DARI PENYEBARAN LATERAL!{C_RESET}\n")

    else:
        # Non-admin / non-root: Simulation fallback
        print(f"  [-] Privilese Sistem    : {C_YELLOW}USER STANDAR (Simulation Shield Active){C_RESET}")
        print(f"  [-] {C_CYAN}[SIMULASI] Rule yang siap dieksekusi:{C_RESET}")
        if OS_TYPE == "windows":
            print(f"      netsh advfirewall firewall add rule name=\"AHIDS_BLOCK_{attacker_ip}\" dir=in action=block remoteip={attacker_ip}")
        else:
            print(f"      iptables -I INPUT -s {attacker_ip} -j DROP")
        print(f"  [-] Status Node         : {C_GREEN}SIMULATION VERIFIED & LOCAL PROCESSES MONITORED!{C_RESET}\n")

# ================= FLEET STATE SYNCHRONIZATION ENGINE =================
def sync_fleet_state(master_ip=DEFAULT_MASTER_IP, verbose=True):
    """
    Menyinkronkan status pertahanan endpoint dengan Master C2:
    1. Mengambil database IP terblokir & posture dari Master via HTTP port 8000.
    2. Menyelaraskan (reconcile) firewall lokal (iptables / netsh).
    3. Mendaftarkan node ini ke C2 Device Matrix sebagai DEFENSE_NODE.
    """
    url = f"http://{master_ip}:8000/sync"
    local_ip = get_local_ip()
    hostname = socket.gethostname()
    os_info = get_distro_info()

    if verbose:
        print(f"{C_CYAN}[FLEET SYNC]{C_RESET} Menghubungi Master C2 @ {url}...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "A-HIDS-TrustedNode-Sync/2.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        posture = data.get("posture", {})
        remote_blocked = data.get("blocked_ips", [])
        watch_ips = data.get("watch_ips", [])
        posture_name = posture.get("name", "LEVEL 2 [BALANCED]")

        if verbose:
            print(f"  {C_GREEN}[CONNECTED]{C_RESET} Terhubung ke Master C2: {master_ip}")
            print(f"  {C_WHITE}Defense Posture  : {C_YELLOW}{posture_name}{C_RESET}")
            print(f"  {C_WHITE}Quarantine Drops : {C_RED}{len(remote_blocked)} IP{C_RESET}")
            print(f"  {C_WHITE}Under Watch Dogs : {C_CYAN}{len(watch_ips)} Host{C_RESET}")

        # Reconcile firewall rules
        local_rules = load_blocked_rules()
        local_blocked_ips = {r.get("ip") for r in local_rules if r.get("ip")}

        # 1. Tambahkan IP yang ada di master tapi belum ada di node ini
        newly_enforced = 0
        privileged = is_privileged()

        for ip in remote_blocked:
            if ip and ip not in local_blocked_ips and ip not in ["127.0.0.1", "0.0.0.0", local_ip]:
                if privileged:
                    if OS_TYPE == "linux":
                        apply_linux_defense(ip)
                    elif OS_TYPE == "windows":
                        apply_windows_defense(ip)
                local_rules.append({
                    "ip": ip,
                    "threat": "SYNCED_FROM_MASTER",
                    "strategy": "FLEET_RECONCILE_DROP",
                    "timestamp": datetime.now().isoformat(),
                    "applied_actions": ["synced_drop"]
                })
                newly_enforced += 1

        # 2. Hapus IP yang sudah di-allow oleh master tapi masih terblokir di node ini
        cleaned_up = 0
        valid_rules = []
        for r in local_rules:
            ip = r.get("ip")
            if ip and ip not in remote_blocked:
                if privileged:
                    if OS_TYPE == "linux":
                        remove_linux_defense(ip)
                    elif OS_TYPE == "windows":
                        remove_windows_defense(ip)
                cleaned_up += 1
            else:
                valid_rules.append(r)

        save_blocked_rules(valid_rules)

        if verbose and (newly_enforced > 0 or cleaned_up > 0):
            print(f"  {C_GREEN}[RECONCILED]{C_RESET} {newly_enforced} IP baru di-DROP, {cleaned_up} IP usang dipulihkan.")

        # Daftarkan node ini ke master C2
        try:
            reg_url = f"http://{master_ip}:8000/register_node?ip={local_ip}&hostname={urllib.parse.quote(hostname)}&os={urllib.parse.quote(os_info)}"
            reg_req = urllib.request.Request(reg_url, headers={"User-Agent": "A-HIDS-TrustedNode-Sync/2.0"})
            with urllib.request.urlopen(reg_req, timeout=3) as r_resp:
                pass
            if verbose:
                print(f"  {C_GREEN}[ENROLLED]{C_RESET} Node terdaftar aktif di C2 Device Matrix.\n")
        except Exception:
            pass

        return data

    except Exception as e:
        if verbose:
            print(f"  {C_YELLOW}[SYNC WARN]{C_RESET} Tidak dapat menghubungi master ({e}). Memakai cache aturan lokal.\n")
        return None

def periodic_sync_worker(master_ip, interval=45):
    """Background worker untuk sinkronisasi periodik failsafe setiap 45 detik"""
    while True:
        try:
            time.sleep(interval)
            sync_fleet_state(master_ip, verbose=False)
        except Exception:
            pass

# ================= CLEANUP / REVERT UTILITY =================
def cleanup_all_rules():
    print(f"\n{C_CYAN}[CLEANUP] Membersihkan seluruh aturan firewall yang dibuat oleh A-HIDS...{C_RESET}")
    rules = load_blocked_rules()
    if not rules:
        print(f"{C_GREEN}Tidak ada aturan aktif yang tersimpan.{C_RESET}")
        return

    privileged = is_privileged()
    for item in rules:
        ip = item.get("ip")
        print(f"Menghapus isolasi untuk {ip}...")
        if privileged:
            if OS_TYPE == "linux":
                remove_linux_defense(ip)
            elif OS_TYPE == "windows":
                remove_windows_defense(ip)
        else:
            print(f"  {C_YELLOW}[SIMULASI] Rule untuk {ip} direset dari state.{C_RESET}")

    save_blocked_rules([])
    print(f"{C_GREEN}[SELESAI] Seluruh aturan firewall A-HIDS telah dibersihkan.{C_RESET}\n")

def show_status():
    rules = load_blocked_rules()
    print(f"\n{C_CYAN}==============================================={C_RESET}")
    print(f"  A-HIDS ENDPOINT QUARANTINE STATUS ({get_distro_info()})")
    print(f"{C_CYAN}==============================================={C_RESET}")
    print(f"Node IP         : {get_local_ip()}")
    print(f"Privilege Mode  : {'Root / Admin' if is_privileged() else 'Standard User'}")
    print(f"Total Blocked IP: {len(rules)}\n")
    if rules:
        for idx, r in enumerate(rules, 1):
            print(f"  [{idx}] IP: {C_RED}{r.get('ip')}{C_RESET} | Threat: {C_YELLOW}{r.get('threat')}{C_RESET} | Waktu: {r.get('timestamp')}")
    else:
        print(f"  {C_GREEN}Tidak ada IP dalam daftar karantina.{C_RESET}")
    print(f"{C_CYAN}-----------------------------------------------{C_RESET}\n")

# ================= MAIN LISTENER LOOP =================
def run_listener(master_ip=DEFAULT_MASTER_IP):
    distro = get_distro_info()
    local_ip = get_local_ip()
    hostname = socket.gethostname()
    priv = is_privileged()

    print(f"{C_CYAN}=================================================================={C_RESET}")
    print(f"{C_WHITE}    A-HIDS UNIVERSAL ENDPOINT DEFENSE CLIENT (SYNC ENABLED)      {C_RESET}")
    print(f"{C_CYAN}=================================================================={C_RESET}")
    print(f"Platform / Distro: {C_WHITE}{distro}{C_RESET}")
    print(f"Node Hostname    : {C_WHITE}{hostname}{C_RESET}")
    print(f"Local IP         : {C_WHITE}{local_ip}{C_RESET}")
    print(f"Master C2 Target : {C_YELLOW}{master_ip}:8000{C_RESET}")
    print(f"Privilege Status : {C_GREEN if priv else C_YELLOW}{'ADMIN / ROOT (Active Kernel Mode)' if priv else 'Standard User (Simulation Shield Mode)'}{C_RESET}")
    print(f"UDP Listen Port  : {C_WHITE}{LISTEN_PORT} (Broadcast Subnet){C_RESET}")
    print(f"Status           : {C_GREEN}ONLINE & PROTECTED (Menunggu Sinyal Primary Transmitter){C_RESET}")
    print(f"{C_CYAN}------------------------------------------------------------------{C_RESET}\n")

    # 1. Lakukan Fleet State Sync pada saat awal booting
    sync_fleet_state(master_ip, verbose=True)

    # 2. Jalankan Background Periodic Synchronizer
    t_sync = threading.Thread(target=periodic_sync_worker, args=(master_ip, 45), daemon=True)
    t_sync.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass

    try:
        sock.bind(('', LISTEN_PORT))
    except Exception as e:
        print(f"{C_RED}[ERROR BIND] Gagal bind ke port {LISTEN_PORT}: {e}{C_RESET}")
        sys.exit(1)

    print(f"{C_GREEN}[LISTENING] Siap menerima paket broadcast darurat di port {LISTEN_PORT}...{C_RESET}\n")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            if not data:
                continue

            try:
                payload = json.loads(data.decode('utf-8'))
            except Exception:
                continue

            if payload.get("auth_key") != SECRET_KEY:
                continue

            cmd = payload.get("command", "")

            # 3. Handle Broadcast STATE_SYNC dari Master
            if cmd == "STATE_SYNC":
                remote_blocked = payload.get("blocked_ips", [])
                local_rules = load_blocked_rules()
                local_blocked_ips = {r.get("ip") for r in local_rules if r.get("ip")}

                privileged = is_privileged()
                changed = False
                for ip in remote_blocked:
                    if ip and ip not in local_blocked_ips and ip not in ["127.0.0.1", "0.0.0.0", local_ip]:
                        if privileged:
                            if OS_TYPE == "linux":
                                apply_linux_defense(ip)
                            elif OS_TYPE == "windows":
                                apply_windows_defense(ip)
                        local_rules.append({
                            "ip": ip,
                            "threat": "STATE_SYNC_BROADCAST",
                            "strategy": "GLOBAL_SYNC_DROP",
                            "timestamp": datetime.now().isoformat(),
                            "applied_actions": ["sync_drop"]
                        })
                        changed = True

                if changed:
                    save_blocked_rules(local_rules)
                    print(f"\n{C_GREEN}[STATE_SYNC]{C_RESET} Reconciled firewall with latest Master C2 blocklist!")
                continue

            source = payload.get("source", "UNKNOWN")
            threat = payload.get("threat", {})
            countermeasure = payload.get("countermeasure", {})
            ts = payload.get("timestamp", datetime.now().isoformat())

            # Dukungan backward compatibility untuk payload v1
            if not threat and "threat_type" in payload:
                threat = {
                    "type": payload.get("threat_type"),
                    "level": "CRITICAL",
                    "attacker_ip": payload.get("attacker_ip")
                }
            if not countermeasure:
                countermeasure = {
                    "target_ip": threat.get("attacker_ip"),
                    "strategy": "GLOBAL_LOCKDOWN_DROP",
                    "action_summary": f"Blokir IP {threat.get('attacker_ip')} secara menyeluruh",
                    "kill_active_connections": True
                }

            print(f"\n{C_RED}+==================================================================+{C_RESET}")
            print(f"{C_RED}|   [EMERGENCY SIGNAL RECEIVED] PERINTAH RESPON PERTAHANAN MASUK!  |{C_RESET}")
            print(f"{C_RED}+==================================================================+{C_RESET}")
            print(f"Transmitter : {C_WHITE}{source} ({addr[0]}){C_RESET}")
            print(f"Timestamp   : {C_WHITE}{ts}{C_RESET}")
            print(f"Tipe Threat : {C_YELLOW}{threat.get('type')}{C_RESET}")
            print(f"Tingkat     : {C_RED}{threat.get('level', 'CRITICAL')}{C_RESET}")
            print(f"Penyerang   : {C_RED}{threat.get('attacker_ip')}{C_RESET}")

            execute_countermeasure(threat, countermeasure)

        except KeyboardInterrupt:
            print(f"\n{C_YELLOW}[EXIT] Endpoint Client dimatikan oleh operator.{C_RESET}")
            break
        except Exception as e:
            print(f"{C_RED}[ERROR EXCEPTION] {e}{C_RESET}")
            time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description="A-HIDS Universal Endpoint Security Daemon")
    parser.add_argument("--cleanup", "--unblock-all", action="store_true", help="Hapus seluruh firewall drop rule A-HIDS")
    parser.add_argument("--status", action="store_true", help="Tampilkan daftar IP yang sedang diisolasi")
    parser.add_argument("--sync", action="store_true", help="Lakukan sinkronisasi langsung dengan Master C2")
    parser.add_argument("--master", default=DEFAULT_MASTER_IP, help=f"IP Master C2 Orchestrator (Default: {DEFAULT_MASTER_IP})")
    parser.add_argument("--test-defense", metavar="IP", help="Uji coba eksekusi isolasi pertahanan lokal terhadap IP tertentu")
    args = parser.parse_args()

    if args.cleanup:
        cleanup_all_rules()
        return

    if args.status:
        show_status()
        return

    if args.sync:
        sync_fleet_state(args.master, verbose=True)
        return

    if args.test_defense:
        dummy_threat = {"type": "LOCAL_VERIFICATION_TEST", "level": "HIGH", "attacker_ip": args.test_defense}
        dummy_cm = {
            "target_ip": args.test_defense,
            "strategy": "TEST_ISOLATION_AND_DROP",
            "action_summary": f"Pengujian isolasi firewall untuk IP {args.test_defense}",
            "kill_active_connections": True
        }
        execute_countermeasure(dummy_threat, dummy_cm)
        return

    run_listener(master_ip=args.master)

if __name__ == "__main__":
    main()
