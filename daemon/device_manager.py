#!/usr/bin/env python3
"""
A-HIDS Native Network Device Discovery & Zero-Trust Access Controller
---------------------------------------------------------------------
1. Passive L2 Discovery: Intercepts real-time netlink neighbor events (ip monitor neigh).
2. Active L2/L3 Discovery: Non-intrusive ARP/UDP sweep to discover silent devices.
3. Vendor Fingerprinting: Offline MAC OUI matching (nmap-mac-prefixes + fallback).
4. Zero-Trust Access State: Whitelist (ALLOWED), Blacklist (BLOCKED), PENDING_REVIEW.
5. Fleet Enforcement: Automatic UDP broadcast dispatch for instant iptables drop/restore.
"""

import os
import sys
import time
import json
import socket
import select
import subprocess
import threading
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEVICES_FILE = os.path.join(SCRIPT_DIR, "known_devices.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "ahids_config.json")
PREFIX_FILE = "/usr/share/nmap/nmap-mac-prefixes"

FALLBACK_OUIs = {
    "8C94DF": "Espressif Inc.",
    "246F28": "Espressif Inc.",
    "30AEA4": "Espressif Inc.",
    "240AC4": "Espressif Inc.",
    "A4CF12": "Espressif Inc.",
    "EC6260": "Espressif Inc.",
    "DC4F22": "Espressif Inc.",
    "4022D8": "Espressif Inc.",
    "48E729": "Espressif Inc.",
    "7CDFA1": "Espressif Inc.",
    "B827EB": "Raspberry Pi Foundation",
    "DCA632": "Raspberry Pi Foundation",
    "E45F01": "Raspberry Pi Foundation",
    "28CDC1": "Raspberry Pi Foundation",
    "525400": "QEMU / Proxmox Virtual NIC",
    "BC2411": "Proxmox / KVM Virtual NIC",
    "00163E": "Xen / LXC Virtual NIC",
    "40AE30": "TP-Link Technologies",
    "F43909": "Hewlett Packard Enterprise",
    "A483E7": "Apple, Inc.",
    "F01898": "Apple, Inc.",
    "ACDE48": "Apple, Inc.",
    "3C22FB": "Apple, Inc.",
    "9801A7": "Apple, Inc.",
    "1867B0": "Samsung Electronics",
    "649A12": "Samsung Electronics",
    "EC8914": "Samsung Electronics",
    "00E04C": "Realtek Semiconductor",
    "001B21": "Intel Corporation",
    "64CC2E": "Xiaomi Communications"
}

def is_valid_ipv4(ip):
    if not ip or not isinstance(ip, str) or ip.count('.') != 3:
        return False
    parts = ip.split('.')
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

def is_valid_mac(mac):
    if not mac or not isinstance(mac, str):
        return False
    mac = mac.lower().strip()
    if mac in ["00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "unknown", "none", "master_host"]:
        return mac == "master_host"
    if mac.startswith("01:00:5e") or mac.startswith("33:33:"):
        return False
    return (mac.count(':') == 5 or mac.count('-') == 5) and len(mac) == 17

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"broadcast_port": 9999, "secret_key": "SECRET_MASTER_NANO_2026"}

class DeviceManager:
    def __init__(self, devices_file=DEVICES_FILE):
        self.devices_file = devices_file
        self.lock = threading.Lock()
        self.oui_cache = {}
        self._load_oui_database()
        self.devices = self._load_devices()
        self.monitor_thread = None
        self.running = False
        self.on_new_device_cb = None

    def _load_oui_database(self):
        """Loads MAC prefix database if available, caches in memory."""
        self.oui_cache.update(FALLBACK_OUIs)
        if os.path.exists(PREFIX_FILE):
            try:
                with open(PREFIX_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            parts = line.split(None, 1)
                            if len(parts) == 2:
                                prefix = parts[0].upper().replace(":", "").replace("-", "")
                                if len(prefix) == 6:
                                    self.oui_cache[prefix] = parts[1]
            except Exception:
                pass

    def resolve_vendor(self, mac):
        if not mac or mac in ["UNKNOWN", "00:00:00:00:00:00"]:
            return "Unknown Device"
        clean_prefix = mac.replace(":", "").replace("-", "").upper()[:6]
        return self.oui_cache.get(clean_prefix, "Generic LAN Device")

    def resolve_hostname(self, ip):
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return "N/A"

    def _load_devices(self):
        if os.path.exists(self.devices_file):
            try:
                with open(self.devices_file, "r") as f:
                    data = json.load(f)
                    valid_devs = {k: v for k, v in data.items() if is_valid_ipv4(k)}
                    if valid_devs:
                        return valid_devs
            except Exception:
                pass

        # Default seed
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        initial = {
            "192.168.0.1": {
                "ip": "192.168.0.1",
                "mac": "40:ae:30:11:40:66",
                "vendor": "TP-Link Technologies (Gateway)",
                "hostname": "router.lan",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "GATEWAY"
            },
            "192.168.0.110": {
                "ip": "192.168.0.110",
                "mac": "MASTER_HOST",
                "vendor": "Parrot OS Security Master",
                "hostname": "master-c2",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "MASTER_C2"
            },
            "192.168.0.112": {
                "ip": "192.168.0.112",
                "mac": "8c:94:df:7c:c4:f8",
                "vendor": "Espressif Inc.",
                "hostname": "esp32-bait",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "HONEYPOT_BAIT"
            },
            "192.168.0.200": {
                "ip": "192.168.0.200",
                "mac": "f4:39:09:20:32:48",
                "vendor": "Hewlett Packard Enterprise",
                "hostname": "pve.lan",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "PROXMOX_HOST"
            },
            "192.168.0.114": {
                "ip": "192.168.0.114",
                "mac": "bc:24:11:14:14:14",
                "vendor": "Proxmox / KVM Virtual NIC",
                "hostname": "tester-lxc103",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "DEFENSE_NODE"
            },
            "192.168.0.111": {
                "ip": "192.168.0.111",
                "mac": "bc:24:11:11:11:11",
                "vendor": "Proxmox / KVM Virtual NIC",
                "hostname": "debian-vm110",
                "first_seen": now,
                "last_seen": now,
                "status": "ALLOWED",
                "threat_score": 0,
                "device_type": "DEFENSE_NODE"
            }
        }
        self._save_devices_unlocked(initial)
        return initial

    def _save_devices_unlocked(self, data=None):
        if data is None:
            data = self.devices
        try:
            # Filter valid IPv4 keys before writing
            clean_data = {k: v for k, v in data.items() if is_valid_ipv4(k)}
            with open(self.devices_file, "w") as f:
                json.dump(clean_data, f, indent=2)
        except Exception as e:
            print(f"[ERR] Failed to save devices: {e}")

    def save(self):
        with self.lock:
            self._save_devices_unlocked()

    def _reload_if_modified(self):
        if os.path.exists(self.devices_file):
            try:
                mtime = os.path.getmtime(self.devices_file)
                if not hasattr(self, "_last_mtime") or mtime > self._last_mtime:
                    self._last_mtime = mtime
                    with open(self.devices_file, "r") as f:
                        data = json.load(f)
                    valid_devs = {k: v for k, v in data.items() if is_valid_ipv4(k)}
                    if valid_devs:
                        self.devices = valid_devs
            except Exception:
                pass

    def get_devices(self):
        with self.lock:
            self._reload_if_modified()
            return [d for d in self.devices.values() if is_valid_ipv4(d.get("ip"))]

    def get_device(self, ip_or_mac):
        with self.lock:
            self._reload_if_modified()
            if ip_or_mac in self.devices and is_valid_ipv4(ip_or_mac):
                return dict(self.devices[ip_or_mac])
            for d in self.devices.values():
                if d.get("mac", "").lower() == ip_or_mac.lower() and is_valid_ipv4(d.get("ip")):
                    return dict(d)
        return None

    def register_device(self, ip, mac, vendor=None, hostname=None, status="PENDING_REVIEW"):
        if not is_valid_ipv4(ip) or not is_valid_mac(mac):
            return None, False

        mac = mac.lower()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            if ip in self.devices:
                dev = self.devices[ip]
                dev["last_seen"] = now
                if mac and dev.get("mac") in ["UNKNOWN", ""]:
                    dev["mac"] = mac
                if vendor and dev.get("vendor") in ["Unknown Device", "Generic LAN Device"]:
                    dev["vendor"] = vendor
                self._save_devices_unlocked()
                return dev, False

            # Check if this MAC existed under another IP
            for old_ip, d in list(self.devices.items()):
                if d.get("mac", "").lower() == mac and old_ip != ip:
                    # IP changed
                    d["ip"] = ip
                    d["last_seen"] = now
                    del self.devices[old_ip]
                    self.devices[ip] = d
                    self._save_devices_unlocked()
                    return d, False

            # Brand new device!
            if not vendor:
                vendor = self.resolve_vendor(mac)
            if not hostname:
                hostname = self.resolve_hostname(ip)

            new_dev = {
                "ip": ip,
                "mac": mac,
                "vendor": vendor,
                "hostname": hostname,
                "first_seen": now,
                "last_seen": now,
                "status": status,
                "threat_score": 0,
                "device_type": "UNKNOWN_NODE"
            }
            self.devices[ip] = new_dev
            self._save_devices_unlocked()
            return new_dev, True

    def register_defense_node(self, ip, hostname="N/A", os_name="Linux", mac="N/A"):
        """Enrolls or updates a trusted endpoint defender node in the fleet registry."""
        if not is_valid_ipv4(ip):
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            if ip in self.devices:
                dev = self.devices[ip]
                dev["last_seen"] = now
                dev["device_type"] = "DEFENSE_NODE"
                dev["status"] = "ALLOWED"
                if hostname and hostname != "N/A":
                    dev["hostname"] = hostname
                if os_name and os_name != "N/A":
                    dev["vendor"] = f"A-HIDS Fleet Defender ({os_name})"
                self._save_devices_unlocked()
                return dev
            else:
                new_dev = {
                    "ip": ip,
                    "mac": mac,
                    "vendor": f"A-HIDS Fleet Defender ({os_name})",
                    "hostname": hostname,
                    "first_seen": now,
                    "last_seen": now,
                    "status": "ALLOWED",
                    "threat_score": 0,
                    "device_type": "DEFENSE_NODE"
                }
                self.devices[ip] = new_dev
                self._save_devices_unlocked()
                return new_dev

    def allow_device(self, target):
        """Whitelist device & broadcast unblock signal to fleet."""
        target_ip = None
        with self.lock:
            if target in self.devices:
                target_ip = target
            else:
                for ip, d in self.devices.items():
                    if d.get("mac", "").lower() == target.lower():
                        target_ip = ip
                        break

            if not target_ip:
                # Add on the fly as allowed if valid IP
                target_ip = target
                self.devices[target_ip] = {
                    "ip": target_ip,
                    "mac": "MANUAL_WHITELIST",
                    "vendor": "Manually Allowed Host",
                    "hostname": "N/A",
                    "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "ALLOWED",
                    "threat_score": 0,
                    "device_type": "TRUSTED_CLIENT"
                }

            dev = self.devices[target_ip]
            dev["status"] = "ALLOWED"
            dev["threat_score"] = 0
            self._save_devices_unlocked()

        # Broadcast restore command
        self._broadcast_access_change(target_ip, "RESTORE_ACCESS")
        return dev

    def block_device(self, target, reason="MANUAL_ADMIN_BLOCK"):
        """Quarantine/Ban device & broadcast lockdown signal to fleet."""
        target_ip = None
        with self.lock:
            if target in self.devices:
                target_ip = target
            else:
                for ip, d in self.devices.items():
                    if d.get("mac", "").lower() == target.lower():
                        target_ip = ip
                        break

            if not target_ip:
                target_ip = target
                self.devices[target_ip] = {
                    "ip": target_ip,
                    "mac": "MANUAL_QUARANTINE",
                    "vendor": "Manually Blocked Host",
                    "hostname": "N/A",
                    "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "BLOCKED",
                    "threat_score": 100,
                    "device_type": "ROGUE_HOST"
                }

            dev = self.devices[target_ip]
            dev["status"] = "BLOCKED"
            dev["threat_score"] = 100
            self._save_devices_unlocked()

        # Broadcast lockdown command
        self._broadcast_access_change(target_ip, "SYSTEM_LOCKDOWN", reason)
        return dev

    def _broadcast_access_change(self, target_ip, command, reason="SECURITY_OFFICER_OVERRIDE"):
        cfg = load_config()
        port = cfg.get("broadcast_port", 9999)
        secret_key = cfg.get("secret_key", "SECRET_MASTER_NANO_2026")
        
        if command == "RESTORE_ACCESS":
            recipe = {
                "strategy": "RESTORE_ACCESS",
                "action_summary": f"Hapus isolasi untuk {target_ip}",
                "target_ip": target_ip
            }
            threat_info = {"type": "MANUAL_ALLOW", "level": "INFO", "attacker_ip": target_ip}
        else:
            recipe = {
                "strategy": "IP_ISOLATION_AND_DROP",
                "action_summary": f"Blokir IP {target_ip} pada seluruh armada endpoint",
                "target_ip": target_ip,
                "target_ports": [],
                "kill_active_connections": True,
                "quarantine_level": "STRICT_LOCKDOWN"
            }
            threat_info = {"type": reason, "level": "CRITICAL", "attacker_ip": target_ip}

        payload = {
            "version": "2.0",
            "source": "A-HIDS-DEVICE-CONTROLLER",
            "command": command,
            "timestamp": datetime.now().isoformat(),
            "threat": threat_info,
            "countermeasure": recipe,
            "auth_key": secret_key
        }

        try:
            raw = json.dumps(payload).encode("utf-8")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(raw, ("<broadcast>", port))
            s.sendto(raw, ("255.255.255.255", port))
            s.close()
        except Exception:
            pass

    def scan_subnet(self, subnet_base="192.168.0"):
        """Fast concurrent probe across subnet to populate ARP neighbor cache."""
        def probe_ip(ip):
            try:
                # Fast UDP probe to port 53 or 80
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.05)
                s.sendto(b"AHIDS_DISCOVERY", (ip, 9999))
                s.close()
            except Exception:
                pass

        threads = []
        for i in range(1, 255):
            ip = f"{subnet_base}.{i}"
            t = threading.Thread(target=probe_ip, args=(ip,), daemon=True)
            threads.append(t)
            t.start()
            if len(threads) >= 64:
                for th in threads:
                    th.join(timeout=0.1)
                threads = []
        for th in threads:
            th.join(timeout=0.1)

        time.sleep(0.3)

        # Parse ARP table from /proc/net/arp & ip neigh
        new_devices = []
        try:
            out = subprocess.check_output(["ip", "neigh", "show"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                parts = line.strip().split()
                if not parts or parts[0] == "Deleted":
                    continue
                # e.g.: 192.168.0.155 dev wlp1s0 lladdr 52:54:00:12:34:56 REACHABLE
                if len(parts) >= 5 and "lladdr" in parts:
                    ip = parts[0]
                    mac = parts[parts.index("lladdr") + 1]
                    if is_valid_ipv4(ip) and is_valid_mac(mac):
                        dev, is_new = self.register_device(ip, mac)
                        if is_new and dev:
                            new_devices.append(dev)
        except Exception:
            pass

        return len(self.get_devices()), new_devices

    def start_passive_monitor(self, on_new_device_callback=None):
        if self.running:
            return
        self.running = True
        self.on_new_device_cb = on_new_device_callback

        def monitor_worker():
            while self.running:
                proc = None
                try:
                    proc = subprocess.Popen(
                        ["ip", "monitor", "neigh"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1
                    )
                    while self.running:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        parts = line.strip().split()
                        if not parts or parts[0] == "Deleted":
                            continue
                        # Formats:
                        # 192.168.0.155 dev wlp1s0 lladdr 52:54:00:... REACHABLE
                        if len(parts) >= 5 and "lladdr" in parts:
                            ip = parts[0]
                            mac = parts[parts.index("lladdr") + 1]
                            if is_valid_ipv4(ip) and is_valid_mac(mac):
                                dev, is_new = self.register_device(ip, mac)
                                if is_new and self.on_new_device_cb:
                                    try:
                                        self.on_new_device_cb(dev)
                                    except Exception as cb_err:
                                        print(f"[MONITOR CB ERR] {cb_err}")
                except Exception as e:
                    time.sleep(2)
                finally:
                    if proc:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                time.sleep(1)

        self.monitor_thread = threading.Thread(target=monitor_worker, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        self.running = False

# Singleton instance
device_manager = DeviceManager()

if __name__ == "__main__":
    print("Testing A-HIDS Native Device Manager...")
    dm = DeviceManager()
    print(f"Loaded {len(dm.devices)} seeded devices.")
    print("\nScanning local subnet...")
    total, new_devs = dm.scan_subnet()
    print(f"Subnet scan completed. Total devices in registry: {total}. Discovered new: {len(new_devs)}")
    for d in dm.get_devices():
        print(f"[{d['status']:<7}] {d['ip']:<15} | {d['mac']:<17} | {d['vendor']:<25} | Type: {d['device_type']}")
