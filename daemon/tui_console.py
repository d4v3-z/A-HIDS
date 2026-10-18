#!/usr/bin/env python3
"""
A-HIDS Tactical C2 TUI Console (Terminal User Interface)
--------------------------------------------------------
Professional, zero-emoji, real-time command center for A-HIDS.
Built with Python curses for low-overhead, air-gapped terminal operations.
Supports live telemetry, interactive incident monitoring, Defense Posture control,
and Zero-Trust Device Control with Fleet Synchronization.

5-Color Tactical Matrix Palette (Zero Background Fills):
1. CYAN   : Headers, Box Titles, Borders, Separators
2. GREEN  : Safe Status, Armed Bait, Whitelisted, Online
3. RED    : Critical Lockdowns, Breaches, Blocked Hosts, Alarms
4. YELLOW : Warnings, Posture Level 2, Pending Reviews, Hotkeys
5. WHITE  : Data Labels, Values, Timestamps, Telemetry Logs
"""

import sys
import os
import time
import json
import socket
import curses
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

# Path references
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "incident_forensic.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "ahids_config.json")

# Import Native Device Manager
from device_manager import device_manager

IMMUTABLE_WHITELIST_IPS = {
    "127.0.0.1", "0.0.0.0", "UNKNOWN", "N/A",
    "192.168.0.110", "192.168.0.1", "192.168.0.112", "192.168.0.200"
}

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"sensitivity_level": 2, "esp32_ip": "192.168.0.112"}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False

def get_master_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def ensure_master_daemon():
    """Memastikan master_daemon.py berjalan di background untuk membaca serial hardware."""
    try:
        res = subprocess.run(["pgrep", "-f", "master_daemon.py"], capture_output=True, text=True)
        if not res.stdout.strip():
            daemon_script = os.path.join(SCRIPT_DIR, "master_daemon.py")
            if os.path.exists(daemon_script):
                subprocess.Popen([sys.executable, "-u", daemon_script],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                time.sleep(0.5)
    except Exception:
        pass

# Shared state between worker thread and TUI renderer
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.events_count = 0
        self.lockdowns_count = 0
        self.canary_count = 0
        self.entropy_count = 0
        self.last_threat = "NONE (MONITORING)"
        self.last_attacker = "NONE"
        self.last_score = 0
        self.last_ts = "0000-00-00 00:00:00"
        self.sentinel_status = "ONLINE (BAIT ACTIVE)"
        self.feed_lines = []
        self.blocked_ips = []
        self.pending_new_device = None
        self.status_msg = "A-HIDS Tactical Console online. All nodes synchronized."
        self.status_ts = datetime.now().strftime("%H:%M:%S")
        self.running = True
        self.view_mode = "DASHBOARD"  # "DASHBOARD" or "DEVICES"
        self.selected_device_idx = 0
        self.feed_paused = False

state = State()

def set_status(msg):
    with state.lock:
        state.status_msg = msg
        state.status_ts = datetime.now().strftime("%H:%M:%S")

def format_feed_line(line):
    """
    Parses serial protocol lines into clean, formatted tactical entries
    mapped strictly to the 5-color palette (1:Cyan, 2:Green, 3:Red, 4:Yellow, 5:White).
    Filters out boot banners and noise so the live stream stays clear.
    """
    if not line:
        return None, 5, False

    ts_prefix = ""
    content = line
    if line.startswith("[") and "]" in line[:22]:
        ts_part, rest = line.split("]", 1)
        try:
            full_ts = ts_part.strip("[").strip()
            time_part = full_ts.split()[1] if " " in full_ts else full_ts
            ts_prefix = f"[{time_part}] "
        except Exception:
            ts_prefix = ts_part + "] "
        content = rest.strip()

    # Filter banner noise from Arduino / ESP32 boot
    if "===" in content or "THE GHOST SENTINEL" in content or "[RTC]" in content or "Ketik 'MENU'" in content or "[INTERLINK]" in content:
        return None, 5, False

    # 1. LOCKDOWN breach (Red - 3)
    if content.startswith("LOCKDOWN|") or "CRITICAL_BREACH" in content:
        attk = "UNKNOWN"
        threat = "CRITICAL_BREACH"
        score = "100"
        if "ALERT=HEARTBEAT_TIMEOUT" in content:
            return f"{ts_prefix}[WATCHDOG FAIL-SECURE] Sentinel timeout! ESP32 hardware reboot initiated", 3, True
        for tok in content.split("|"):
            if tok.startswith("DATA="):
                sub = tok.replace("DATA=", "").split(",")
                if len(sub) >= 6:
                    attk = sub[3]
                    threat = sub[4]
                    score = sub[5]
        return f"{ts_prefix}[CRITICAL LOCKDOWN] {attk} -> {threat} (Score: {score}) | FLEET DROP ENFORCED", 3, True

    # 2. EVENT telemetry
    if content.startswith("EVENT|"):
        try:
            for tok in content.split("|"):
                if tok.startswith("EV,"):
                    sub = tok.split(",")
                    if len(sub) >= 6:
                        lvl = sub[2]
                        ip = sub[3]
                        th = sub[4]
                        sc = sub[5]
                        if th in ["WIFI_ONLINE", "AP_ONLINE"]:
                            return f"{ts_prefix}[HONEYPOT ARMED] Bait online @ {ip} (Port 80/23 Active)", 2, False
                        if th == "NEW_DEVICE_DISCOVERED":
                            return f"{ts_prefix}[ZT-NAC DISCOVERY] New LAN device detected: {ip} [PENDING REVIEW]", 4, True
                        if lvl == "CRITICAL":
                            return f"{ts_prefix}[CRITICAL PROBE] {ip} -> {th} (Score: {sc}) | LOCKDOWN", 3, True
                        return f"{ts_prefix}[THREAT PROBE] {ip} -> {th} (Score: {sc}) | UNDER WATCH", 4, False
        except Exception:
            pass

    # 3. Forensic Payload (White - 5)
    if content.startswith("PAYLOAD|"):
        pl_clean = content.replace("PAYLOAD|", "").replace("PL,", "").strip()
        # Hilangkan trailing koma atau ID urut
        if pl_clean and pl_clean[0].isdigit() and "," in pl_clean[:6]:
            pl_clean = pl_clean.split(",", 1)[1]
        if not pl_clean:
            return None, 5, False
        if len(pl_clean) > 75:
            pl_clean = pl_clean[:72] + "..."
        return f"{ts_prefix}[PAYLOAD RECON] {pl_clean}", 5, False

    # 4. Sentinel Hardware
    if "[SENTINEL]" in content or "Hard-Reset" in content:
        if "Hard-Reset selesai" in content or "finished" in content:
            return f"{ts_prefix}[SENTINEL] ESP32 Hard-Reset finished. Hardware restored to steril state.", 2, False
        if "Hardware Hard-Reset" in content or "triggered" in content:
            return f"{ts_prefix}[SENTINEL] Hard-Reset triggered via open-drain EN pin.", 4, False
        clean_s = content.replace("[SENTINEL]", "").replace(">>", "").strip()
        return f"{ts_prefix}[SENTINEL] {clean_s}", 5, False

    # 5. Overrides & Sync
    if content.startswith("MANUAL_OVERRIDE|"):
        return f"{ts_prefix}[C2 OVERRIDE] {content.replace('MANUAL_OVERRIDE|', '')}", 1, True

    if content.startswith("NODE_REGISTERED|"):
        return f"{ts_prefix}[FLEET ENROLLED] {content.replace('NODE_REGISTERED|', '')}", 2, False

    if content.startswith("SENSITIVITY_BLOCK|"):
        return f"{ts_prefix}[DEFENSE POSTURE] {content.replace('SENSITIVITY_BLOCK|', '')}", 3, True

    if content.startswith("SENSITIVITY_OBSERVE|"):
        return f"{ts_prefix}[DEFENSE OBSERVE] {content.replace('SENSITIVITY_OBSERVE|', '')}", 4, False

    # Clean fallback (White - 5)
    clean_line = (ts_prefix + content).strip()
    return clean_line, 5, False

def log_tailer_worker():
    last_size = 0
    while state.running:
        try:
            if os.path.exists(LOG_FILE):
                cur_size = os.path.getsize(LOG_FILE)
                if cur_size < last_size:
                    last_size = 0  # File rotated
                
                if cur_size != last_size:
                    with open(LOG_FILE, "r", errors="ignore") as f:
                        if last_size > 0:
                            f.seek(last_size)
                            new_lines = f.readlines()
                            last_size = f.tell()
                            for line in new_lines:
                                parse_log_line(line.strip())
                        else:
                            lines = f.readlines()
                            last_size = f.tell()
                            for line in lines[-80:]:
                                parse_log_line(line.strip())
            time.sleep(0.2)
        except Exception:
            time.sleep(1)

def parse_log_line(line):
    if not line:
        return
    with state.lock:
        if not state.feed_paused:
            state.feed_lines.append(line)
            if len(state.feed_lines) > 300:
                state.feed_lines.pop(0)

        if "EVENT|" in line:
            try:
                parts = line.split("|")
                for p in parts:
                    if p.startswith("EV,"):
                        ev_parts = p.split(",")
                        if len(ev_parts) >= 6:
                            lvl = ev_parts[2]
                            ip = ev_parts[3]
                            threat = ev_parts[4]
                            score = int(ev_parts[5]) if ev_parts[5].isdigit() else 0

                            # Ignore benign system status messages
                            if threat in ["WIFI_ONLINE", "AP_ONLINE", "HEARTBEAT", "NONE"]:
                                state.sentinel_status = "ONLINE (BAIT ACTIVE)"
                            elif threat == "NEW_DEVICE_DISCOVERED":
                                if ip not in IMMUTABLE_WHITELIST_IPS:
                                    state.pending_new_device = ip
                            else:
                                # Genuine security incident event
                                state.events_count += 1
                                if ip not in IMMUTABLE_WHITELIST_IPS:
                                    state.last_attacker = ip
                                    state.last_threat = threat
                                    state.last_score = score
                                    if lvl in ["CRITICAL", "HIGH"] and ip not in state.blocked_ips:
                                        state.blocked_ips.insert(0, f"{ip} [{threat}]")
                                        if len(state.blocked_ips) > 8:
                                            state.blocked_ips.pop()

                    if p.startswith("TS="):
                        state.last_ts = p[3:]
            except Exception:
                pass

        if "LOCKDOWN|" in line:
            state.lockdowns_count += 1
            state.sentinel_status = "FAIL-SECURE REBOOT"
        
        if "CANARY_" in line or "ADMIN_SECRET" in line or "ENV_FILE" in line:
            state.canary_count += 1
            
        if "HIGH_ENTROPY" in line or "NOP_SLED" in line:
            state.entropy_count += 1

        if "[SENTINEL]" in line or "ARDUINO NANO" in line:
            state.sentinel_status = "ONLINE (INTERLINK ACTIVE)"

def draw_colored_footer(stdscr, y, text, w):
    """
    Renders footer without any background, with hotkeys in Yellow Bold (4),
    labels in White (5), and separators in Cyan (1).
    """
    if y < 0:
        return
    text = text.strip()
    start_x = max(0, (w - len(text)) // 2)
    cur_x = start_x

    try:
        sections = text.split("|")
        for i, sec in enumerate(sections):
            sec_str = sec.strip()
            if "[" in sec_str and "]" in sec_str:
                pre, rest = sec_str.split("[", 1)
                key, label = rest.split("]", 1)
                if pre:
                    stdscr.addstr(y, cur_x, pre, curses.color_pair(5))
                    cur_x += len(pre)
                k_str = f"[{key}]"
                stdscr.addstr(y, cur_x, k_str, curses.color_pair(4) | curses.A_BOLD)
                cur_x += len(k_str)
                if label:
                    stdscr.addstr(y, cur_x, label, curses.color_pair(5))
                    cur_x += len(label)
            else:
                stdscr.addstr(y, cur_x, sec_str, curses.color_pair(5))
                cur_x += len(sec_str)

            if i < len(sections) - 1:
                sep = " | "
                stdscr.addstr(y, cur_x, sep, curses.color_pair(1))
                cur_x += len(sep)
    except Exception:
        try:
            stdscr.addstr(y, max(0, (w - len(text)) // 2), text[:w-1], curses.color_pair(5))
        except Exception:
            pass

def draw_dashboard_view(stdscr, w, h, master_ip, esp32_ip):
    # 1. Header Banner - Exact string, Cyan Bold, NO background
    header_title = "A-HIDS TACTICAL C2 COMMAND CONSOLE | ZERO-EMOJI DEFENSE MATRIX"
    stdscr.addstr(0, max(0, (w - len(header_title)) // 2), header_title[:w-1], curses.color_pair(1) | curses.A_BOLD)

    cfg = load_config()
    sens_lvl = cfg.get("sensitivity_level", 2)
    sens_tags = {
        1: ("LVL 1 [RESEARCH / LEARNING]", curses.color_pair(1) | curses.A_BOLD),
        2: ("LVL 2 [BALANCED / ADAPTIVE]", curses.color_pair(4) | curses.A_BOLD),
        3: ("LVL 3 [PARANOID / ZERO-TOLERANCE]", curses.color_pair(3) | curses.A_BOLD)
    }
    sens_name, sens_color = sens_tags.get(sens_lvl, (f"LVL {sens_lvl}", curses.color_pair(1)))

    all_devs = device_manager.get_devices()
    defense_nodes_cnt = sum(1 for d in all_devs if d.get("device_type") == "DEFENSE_NODE")
    feed_mode_str = "FEED: PAUSED [P]" if state.feed_paused else "FEED: LIVE [P]"

    # 2. Metadata Bar (Row 1) - White Dim, NO background
    sub_meta = f" MASTER: {master_ip} | BAIT: {esp32_ip} | POSTURE: {sens_name[:24]} | {feed_mode_str} | NODES: {defense_nodes_cnt} "
    stdscr.addstr(1, 0, sub_meta[:w-1], curses.color_pair(5) | curses.A_DIM)

    # Divider (Row 2) - Cyan Dim
    stdscr.addstr(2, 0, "-" * w, curses.color_pair(1) | curses.A_DIM)

    # Left Column Width: 43 characters
    left_w = 43
    right_col = 45
    right_w = max(20, w - right_col - 2)

    # 3. Box 1: Telemetry & Metrics
    stdscr.addstr(3, 1, "+--- [SYSTEM METRICS & POSTURE] ---+", curses.color_pair(1) | curses.A_BOLD)
    with state.lock:
        ev_cnt = state.events_count
        ld_cnt = state.lockdowns_count
        cn_cnt = state.canary_count
        en_cnt = state.entropy_count
        l_threat = state.last_threat
        l_attk = state.last_attacker
        l_score = state.last_score
        l_ts = state.last_ts
        s_stat = state.sentinel_status
        recent_logs = list(state.feed_lines)
        pending_dev = state.pending_new_device
        status_text = state.status_msg
        status_time = state.status_ts

    stdscr.addstr(4, 2, "Total Probe Events  : ", curses.color_pair(5))
    stdscr.addstr(4, 24, f"{ev_cnt}", curses.color_pair(2) | curses.A_BOLD if ev_cnt > 0 else curses.color_pair(5))

    stdscr.addstr(5, 2, "Canary Tripwires    : ", curses.color_pair(5))
    stdscr.addstr(5, 24, f"{cn_cnt}", curses.color_pair(3) | curses.A_BOLD if cn_cnt > 0 else curses.color_pair(5))

    stdscr.addstr(6, 2, "Hardware Lockdowns  : ", curses.color_pair(5))
    stdscr.addstr(6, 24, f"{ld_cnt}", curses.color_pair(3) | curses.A_BOLD if ld_cnt > 0 else curses.color_pair(5))

    stdscr.addstr(7, 2, "Shellcode Entropy   : ", curses.color_pair(5))
    stdscr.addstr(7, 24, f"{en_cnt}", curses.color_pair(4) | curses.A_BOLD if en_cnt > 0 else curses.color_pair(5))

    stdscr.addstr(8, 2, "Defense Posture     : ", curses.color_pair(5))
    stdscr.addstr(8, 24, f"LVL {sens_lvl}", sens_color)

    # 4. Box 2: Hardware Sentinel Supervisor
    stdscr.addstr(10, 1, "+--- [HARDWARE SENTINEL] ----------+", curses.color_pair(1) | curses.A_BOLD)
    sentinel_color = curses.color_pair(2) | curses.A_BOLD if "ONLINE" in s_stat else curses.color_pair(4) | curses.A_BOLD
    stdscr.addstr(11, 2, "Hardware Watchdog   : ", curses.color_pair(5))
    stdscr.addstr(11, 24, f"{s_stat[:17]}", sentinel_color)

    stdscr.addstr(12, 2, "Serial Interlink    : ", curses.color_pair(5))
    stdscr.addstr(12, 24, "/dev/ttyACM0 (OK)", curses.color_pair(2))

    stdscr.addstr(13, 2, "Last Synchronized   : ", curses.color_pair(5))
    stdscr.addstr(13, 24, f"{l_ts[:17]}", curses.color_pair(5) | curses.A_DIM)

    # 5. Box 3: Active Incident State
    stdscr.addstr(15, 1, "+--- [LATEST THREAT VECTOR] -------+", curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(16, 2, "Incident Class      : ", curses.color_pair(5))
    th_color = curses.color_pair(3) | curses.A_BOLD if l_threat != "NONE (MONITORING)" else curses.color_pair(5)
    stdscr.addstr(16, 24, f"{l_threat[:17]}", th_color)

    stdscr.addstr(17, 2, "Attacker Host IP    : ", curses.color_pair(5))
    attk_color = curses.color_pair(3) | curses.A_BOLD if l_attk != "NONE" else curses.color_pair(5)
    stdscr.addstr(17, 24, f"{l_attk[:17]}", attk_color)

    stdscr.addstr(18, 2, "Cumulative Score    : ", curses.color_pair(5))
    score_color = curses.color_pair(5) if l_score == 0 else (curses.color_pair(4) | curses.A_BOLD if l_score < 50 else curses.color_pair(3) | curses.A_BOLD)
    stdscr.addstr(18, 24, f"{l_score} pts", score_color)

    # 6. Box 4: Zero-Trust NAC & Fleet Review
    stdscr.addstr(20, 1, "+--- [ZERO-TRUST NAC & FLEET] -----+", curses.color_pair(1) | curses.A_BOLD)
    pending_list = [d for d in all_devs if d.get("status") == "PENDING_REVIEW"]

    if pending_list:
        p_dev = pending_list[0]
        stdscr.addstr(21, 2, f"NEW HOST: {p_dev['ip']}", curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(22, 2, f"Vendor  : {p_dev.get('vendor', 'Unknown')[:26]}", curses.color_pair(5))
        stdscr.addstr(23, 2, "[A] Allow Host", curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(23, 20, "[B] Ban Host", curses.color_pair(3) | curses.A_BOLD)
    elif pending_dev:
        stdscr.addstr(21, 2, f"NEW HOST: {pending_dev}", curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(23, 2, "[A] Allow Host", curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(23, 20, "[B] Ban Host", curses.color_pair(3) | curses.A_BOLD)
    else:
        allowed_c = sum(1 for d in all_devs if d.get("status") == "ALLOWED")
        blocked_c = sum(1 for d in all_devs if d.get("status") == "BLOCKED")
        stdscr.addstr(21, 2, f"LAN Hosts   : {len(all_devs)} Total ({allowed_c} OK)", curses.color_pair(2))
        stdscr.addstr(22, 2, f"Quarantine  : {blocked_c} Blocked in Fleet", curses.color_pair(3) | curses.A_BOLD if blocked_c > 0 else curses.color_pair(5))
        stdscr.addstr(23, 2, "Press [D] to open Device Matrix", curses.color_pair(1) | curses.A_DIM)

    # Right Column: Real-time telemetry feed
    feed_title = "+--- [REAL-TIME DEFENSE TELEMETRY & AUDIT STREAM] ---+"
    if state.feed_paused:
        feed_title = "+--- [AUDIT STREAM PAUSED - PRESS P TO RESUME] ---+"
        stdscr.addstr(3, right_col, feed_title[:right_w], curses.color_pair(4) | curses.A_BOLD)
    else:
        stdscr.addstr(3, right_col, feed_title[:right_w], curses.color_pair(1) | curses.A_BOLD)

    # Ekstrak baris log valid dan padatkan tanpa celah kosong
    max_feed_rows = h - 6
    valid_entries = []
    for l in recent_logs:
        fmt_text, c_pair, is_bold = format_feed_line(l)
        if fmt_text:
            valid_entries.append((fmt_text, c_pair, is_bold))

    # Tampilkan entri terbaru di paling bawah
    visible_entries = valid_entries[-max_feed_rows:]
    for idx, (fmt_text, c_pair, is_bold) in enumerate(visible_entries):
        y = 4 + idx
        if y >= h - 2:
            break
        try:
            line_str = fmt_text[:right_w]
            attr = curses.color_pair(c_pair)
            if is_bold:
                attr |= curses.A_BOLD
            stdscr.addstr(y, right_col, line_str, attr)
        except Exception:
            pass

    # Dedicated Full-Width Status Bar (Row h - 2) - No Background
    stdscr.addstr(h - 2, 0, f" [{status_time}]", curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(h - 2, len(status_time) + 3, f" {status_text}"[:w - len(status_time) - 4], curses.color_pair(5) | curses.A_BOLD)

    # Footer Shortcuts Bar (Row h - 1) - Exact String, NO background
    footer = " [Q] Quit | [D] Devices | [L] Posture | [B] Block | [A] Allow | [T] Canary | [S] SQLi | [P] Pause | [C] Clear"
    draw_colored_footer(stdscr, h - 1, footer, w)

def draw_devices_view(stdscr, w, h):
    # Header Banner - Cyan Bold, NO background
    title = "A-HIDS ZERO-TRUST DEVICE ACCESS CONTROL MATRIX"
    stdscr.addstr(0, max(0, (w - len(title)) // 2), title[:w-1], curses.color_pair(1) | curses.A_BOLD)

    devs = device_manager.get_devices()
    total = len(devs)
    allowed_c = sum(1 for d in devs if d.get("status") == "ALLOWED")
    blocked_c = sum(1 for d in devs if d.get("status") == "BLOCKED")
    defense_nodes = sum(1 for d in devs if d.get("device_type") == "DEFENSE_NODE")

    master_ip = get_master_ip()
    sub_meta = f" DEVICES: {total} ({allowed_c} ALLOWED, {blocked_c} BLOCKED, {defense_nodes} ENFORCERS) | C2 SYNC: http://{master_ip}:8000/sync "
    stdscr.addstr(1, 0, sub_meta[:w-1], curses.color_pair(5) | curses.A_DIM)

    stdscr.addstr(2, 0, "=" * w, curses.color_pair(1) | curses.A_DIM)

    # Table Header (Row 3) - Cyan Bold
    header_str = " NUM  STATUS     IP ADDRESS        MAC ADDRESS        VENDOR / ROLE                 SCORE  DEVICE TYPE "
    stdscr.addstr(3, 1, header_str[:w-2], curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(4, 1, "-" * (w - 2), curses.color_pair(1) | curses.A_DIM)

    max_rows = h - 7
    if state.selected_device_idx >= total:
        state.selected_device_idx = max(0, total - 1)

    # Render Device Rows
    for idx, d in enumerate(devs[:max_rows]):
        y = 5 + idx
        is_selected = (idx == state.selected_device_idx)
        st = d.get("status", "UNKNOWN")
        ip = d.get("ip", "N/A")
        mac = d.get("mac", "N/A")
        vendor = d.get("vendor", "N/A")[:28]
        score = d.get("threat_score", 0)
        dtype = d.get("device_type", "UNKNOWN")[:12]

        status_tag = f"{st:<10}"
        row_str = f" [{idx+1:<2}] {status_tag} {ip:<17} {mac:<18} {vendor:<29} {score:<6} {dtype:<12}"

        try:
            if is_selected:
                stdscr.addstr(y, 1, "> ", curses.color_pair(4) | curses.A_BOLD)
                stdscr.addstr(y, 3, row_str[:w-5], curses.color_pair(5) | curses.A_BOLD)
            else:
                if st == "ALLOWED":
                    stdscr.addstr(y, 1, f"  {row_str[:w-4]}", curses.color_pair(2))
                elif st == "BLOCKED":
                    stdscr.addstr(y, 1, f"  {row_str[:w-4]}", curses.color_pair(3) | curses.A_BOLD)
                else:
                    stdscr.addstr(y, 1, f"  {row_str[:w-4]}", curses.color_pair(4) | curses.A_BOLD)
        except Exception:
            pass

    # Dedicated Full-Width Status Bar (Row h - 2) - No Background
    stdscr.addstr(h - 2, 0, f" [{state.status_ts}]", curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(h - 2, len(state.status_ts) + 3, f" {state.status_msg}"[:w - len(state.status_ts) - 4], curses.color_pair(5) | curses.A_BOLD)

    # Footer - Exact Clean Styling, NO background
    footer = " [UP/DOWN] Select | [A] Allow Host | [B] Ban Host | [R] Rescan LAN | [D / ESC] Back to Dashboard"
    draw_colored_footer(stdscr, h - 1, footer, w)

def draw_tui(stdscr):
    try:
        curses.curs_set(0)
    except Exception:
        pass
    stdscr.nodelay(True)
    curses.use_default_colors()

    # Exact 5-Color Tactical Matrix Palette (Zero background fills across all pairs)
    # 1. CYAN    : Headers, Box Titles, Borders, Separators
    # 2. GREEN   : Normal Status, Armed Bait, Whitelisted, OK Counts
    # 3. RED     : Critical Lockdown, Breaches, Blocked Hosts, Alarms
    # 4. YELLOW  : Warnings, Posture Level 2, Pending Reviews, Shortcut Hotkeys
    # 5. WHITE   : Labels, Data Values, Timestamps, Standard Logs
    try:
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        curses.init_pair(5, curses.COLOR_WHITE, -1)
    except Exception:
        pass

    master_ip = get_master_ip()
    cfg = load_config()
    esp32_ip = cfg.get("esp32_ip", "192.168.0.112")

    while state.running:
        try:
            h, w = stdscr.getmaxyx()
            stdscr.erase()

            if h < 22 or w < 80:
                stdscr.addstr(0, 0, f"Terminal too small ({w}x{h}). Require at least 80x22.", curses.color_pair(4) | curses.A_BOLD)
                stdscr.refresh()
                time.sleep(0.3)
                continue

            if state.view_mode == "DEVICES":
                draw_devices_view(stdscr, w, h)
            else:
                draw_dashboard_view(stdscr, w, h, master_ip, esp32_ip)

            stdscr.refresh()

            # Handle Keyboard Input
            ch = stdscr.getch()
            if ch != -1:
                if ch == curses.KEY_RESIZE:
                    continue

                if state.view_mode == "DEVICES":
                    # Device view controls
                    if ch in [curses.KEY_UP, ord('k'), ord('K')]:
                        if state.selected_device_idx > 0:
                            state.selected_device_idx -= 1
                    elif ch in [curses.KEY_DOWN, ord('j'), ord('J')]:
                        devs = device_manager.get_devices()
                        if state.selected_device_idx < len(devs) - 1:
                            state.selected_device_idx += 1
                    elif ch in [ord('a'), ord('A')]:
                        devs = device_manager.get_devices()
                        if 0 <= state.selected_device_idx < len(devs):
                            target_dev = devs[state.selected_device_idx]
                            device_manager.allow_device(target_dev["ip"])
                            set_status(f"[ALLOWED] {target_dev['ip']} whitelisted and access restored across fleet.")
                    elif ch in [ord('b'), ord('B'), ord('x'), ord('X')]:
                        devs = device_manager.get_devices()
                        if 0 <= state.selected_device_idx < len(devs):
                            target_dev = devs[state.selected_device_idx]
                            device_manager.block_device(target_dev["ip"], reason="TUI_OPERATOR_BLOCK")
                            set_status(f"[BLOCKED] {target_dev['ip']} quarantined and isolated across fleet!")
                    elif ch in [ord('r'), ord('R')]:
                        def run_rescan():
                            set_status("[SCANNING] Sweeping subnet for active hosts...")
                            tot, new_d = device_manager.scan_subnet()
                            set_status(f"[SCAN COMPLETE] Total {tot} devices detected ({len(new_d)} new).")
                        threading.Thread(target=run_rescan, daemon=True).start()
                    elif ch in [ord('d'), ord('D'), 27, ord('q'), ord('Q')]: # ESC or D or Q returns to dashboard
                        state.view_mode = "DASHBOARD"

                else:
                    # Dashboard view controls
                    if ch in [ord('q'), ord('Q'), 27]: # ESC or Q
                        state.running = False
                        break
                    elif ch in [ord('d'), ord('D')]:
                        state.view_mode = "DEVICES"
                        state.selected_device_idx = 0
                    elif ch in [ord('l'), ord('L')]:
                        cfg_now = load_config()
                        cur_l = cfg_now.get("sensitivity_level", 2)
                        next_l = (cur_l % 3) + 1
                        cfg_now["sensitivity_level"] = next_l
                        save_config(cfg_now)
                        tag_map = {1: "LEVEL 1 [RESEARCH / LEARNING]", 2: "LEVEL 2 [BALANCED / ADAPTIVE]", 3: "LEVEL 3 [PARANOID / ZERO-TOLERANCE]"}
                        set_status(f"[POSTURE] Switched to {tag_map.get(next_l, next_l)}")
                    elif ch in [ord('p'), ord('P')]:
                        state.feed_paused = not state.feed_paused
                        set_status("[FEED PAUSED] Scrolling frozen for log inspection." if state.feed_paused else "[FEED RESUMED] Streaming live serial telemetry.")
                    elif ch in [ord('c'), ord('C')]:
                        with state.lock:
                            state.feed_lines.clear()
                        set_status("[CLEARED] Telemetry audit feed display buffer flushed.")
                    elif ch in [ord('b'), ord('B')]:
                        all_devs = device_manager.get_devices()
                        pending_list = [d for d in all_devs if d.get("status") == "PENDING_REVIEW"]
                        target = pending_list[0]["ip"] if pending_list else state.pending_new_device
                        if target:
                            device_manager.block_device(target, reason="TUI_OPERATOR_BLOCK")
                            set_status(f"[BLOCKED] {target} quarantined & isolated across fleet.")
                            state.pending_new_device = None
                    elif ch in [ord('a'), ord('A')]:
                        all_devs = device_manager.get_devices()
                        pending_list = [d for d in all_devs if d.get("status") == "PENDING_REVIEW"]
                        target = pending_list[0]["ip"] if pending_list else state.pending_new_device
                        if target:
                            device_manager.allow_device(target)
                            set_status(f"[ALLOWED] {target} whitelisted & fleet access restored.")
                            state.pending_new_device = None
                    elif ch in [ord('t'), ord('T')]:
                        def trig_canary():
                            try:
                                set_status(f"[PROBE DISPATCHED] Sending Canary test tripwire to http://{esp32_ip}/admin_secret")
                                req = urllib.request.Request(f"http://{esp32_ip}/admin_secret", headers={"User-Agent": "A-HIDS-TUI-CanaryProbe"})
                                urllib.request.urlopen(req, timeout=2)
                            except Exception:
                                pass
                        threading.Thread(target=trig_canary, daemon=True).start()
                    elif ch in [ord('s'), ord('S')]:
                        def trig_sqli():
                            try:
                                set_status(f"[PROBE DISPATCHED] Sending SQLi test injection to http://{esp32_ip}/login")
                                data = urllib.parse.urlencode({"user": "admin' OR 1=1--", "pass": "hack"}).encode()
                                req = urllib.request.Request(f"http://{esp32_ip}/login", data=data, headers={"User-Agent": "A-HIDS-TUI-SQLiProbe"})
                                urllib.request.urlopen(req, timeout=2)
                            except Exception:
                                pass
                        threading.Thread(target=trig_sqli, daemon=True).start()

            time.sleep(0.04)

        except KeyboardInterrupt:
            state.running = False
            break

def main():
    # Pastikan background master daemon aktif untuk streaming serial hardware
    ensure_master_daemon()

    t = threading.Thread(target=log_tailer_worker, daemon=True)
    t.start()

    try:
        curses.wrapper(draw_tui)
    except Exception as e:
        print(f"TUI Exit: {e}")
    finally:
        state.running = False

if __name__ == "__main__":
    main()
