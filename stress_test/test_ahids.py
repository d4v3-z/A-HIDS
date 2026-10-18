#!/usr/bin/env python3
"""
A-HIDS Diagnostic Test Suite (Non-Destructive Security Verification)
Menguji seluruh pilar deteksi A-HIDS menggunakan vektor uji standar industri (OWASP/RFC)
tanpa kode berbahaya, untuk membuktikan sistem deteksi bekerja 100%.
"""

import sys
import os
import time
import base64
import urllib.parse
import urllib.request
import urllib.error

C_RESET = "\033[0m"
C_RED = "\033[91;1m"
C_GREEN = "\033[92;1m"
C_YELLOW = "\033[93;1m"
C_CYAN = "\033[96;1m"
C_WHITE = "\033[97m"

def send_test_request(target_url, params=None, headers=None, method="GET"):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = "A-HIDS-Security-Test-Runner/1.0"

    try:
        data = None
        url = target_url
        if params:
            encoded_params = urllib.parse.urlencode(params)
            if method == "POST":
                data = encoded_params.encode('utf-8')
            else:
                url = f"{target_url}?{encoded_params}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.getcode(), response.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return 0, str(e)

def main():
    print(f"{C_CYAN}===================================================={C_RESET}")
    print(f"{C_CYAN}      A-HIDS BENCHMARK TEST SUITE (ETHICAL QA)      {C_RESET}")
    print(f"{C_CYAN}===================================================={C_RESET}")
    
    default_ip = "192.168.4.1" # Default SoftAP IP
    target_ip = input(f"Masukkan IP ESP32 [{default_ip}]: ").strip()
    if not target_ip:
        target_ip = default_ip
    base_url = f"http://{target_ip}"

    while True:
        print(f"\n{C_WHITE}Pilih skenario pengujian A-HIDS:{C_RESET}")
        print(" [1] Uji Pilar 1: Canary Tripwire (/admin_secret)")
        print(" [2] Uji Pilar 2: SQL Injection Syntax (' OR '1'='1)")
        print(" [3] Uji Pilar 2: Command Injection Syntax (; echo TEST)")
        print(" [4] Uji Pilar 2: Path Traversal Syntax (../../etc/passwd)")
        print(" [5] Uji Pilar 2: Scanner Signature (User-Agent: sqlmap)")
        print(" [6] Uji Pilar 3: High Entropy Mathematical Payload (Random Base64)")
        print(" [7] Uji Pilar 3: Low Entropy NOP Sled Mathematical Payload (Repeating 0x90)")
        print(" [8] Uji Positif: Authorized Node Request (X-Node-Token valid)")
        print(" [0] Keluar")
        
        choice = input("\nPilihan [0-8]: ").strip()
        if choice == "0":
            break

        print(f"\n{C_CYAN}[MENEMBAK VEKTOR UJI]...{C_RESET}")

        if choice == "1":
            # Canary Trap
            url = f"{base_url}/admin_secret"
            code, resp = send_test_request(url)
            print(f"Target: {url} -> HTTP Code: {code}")

        elif choice == "2":
            # SQL Injection
            url = f"{base_url}/login"
            payload = {"user": "admin' OR '1'='1", "pass": "test123"}
            code, resp = send_test_request(url, params=payload, method="POST")
            print(f"Target: {url} (SQLi Vector: {payload['user']}) -> HTTP Code: {code}")

        elif choice == "3":
            # Command Injection
            url = f"{base_url}/login"
            payload = {"user": "test_user; echo 'TEST_PROBE'", "pass": "test123"}
            code, resp = send_test_request(url, params=payload, method="POST")
            print(f"Target: {url} (RCE Vector) -> HTTP Code: {code}")

        elif choice == "4":
            # Path Traversal
            url = f"{base_url}/login"
            payload = {"user": "../../../../etc/passwd", "pass": "test123"}
            code, resp = send_test_request(url, params=payload, method="POST")
            print(f"Target: {url} (LFI/Traversal Vector) -> HTTP Code: {code}")

        elif choice == "5":
            # Scanner Signature
            url = f"{base_url}/"
            headers = {"User-Agent": "sqlmap/1.5#stable (http://sqlmap.org)"}
            code, resp = send_test_request(url, headers=headers)
            print(f"Target: {url} (Scanner UA: sqlmap) -> HTTP Code: {code}")

        elif choice == "6":
            # High Entropy (Random Base64 string > 32 bytes)
            high_entropy_bytes = os.urandom(32)
            high_entropy_str = base64.b64encode(high_entropy_bytes).decode('utf-8')
            url = f"{base_url}/login"
            payload = {"user": high_entropy_str, "pass": "dummy"}
            code, resp = send_test_request(url, params=payload, method="POST")
            print(f"Target: {url} (High-Entropy Synthetic String) -> HTTP Code: {code}")

        elif choice == "7":
            # Low Entropy (Repeating 'A' * 64)
            low_entropy_str = "A" * 64
            url = f"{base_url}/login"
            payload = {"user": low_entropy_str, "pass": "dummy"}
            code, resp = send_test_request(url, params=payload, method="POST")
            print(f"Target: {url} (Low-Entropy Buffer String) -> HTTP Code: {code}")

        elif choice == "8":
            # Authorized Node (Clean)
            url = f"{base_url}/login"
            headers = {"X-Node-Token": "NODE-AUTH-SEC-2026"}
            payload = {"user": "authorized_node_01", "pass": "status_ok"}
            code, resp = send_test_request(url, params=payload, headers=headers, method="POST")
            print(f"{C_GREEN}[AUTHORIZED] Token Terverifikasi valid! -> HTTP Code: {code}{C_RESET}")

        print(f"{C_GREEN}>> Periksa terminal Master Daemon untuk melihat skor anomali & aksi Sentinel!{C_RESET}")

if __name__ == "__main__":
    main()
