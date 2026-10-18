#!/usr/bin/env python3
"""
A-HIDS High-Impact Stress & Resilience Benchmark Suite
Designed for live presentation verification and breaking-point analysis:
 1. Concurrency & Throughput Stress (10, 25, 50 concurrent requests)
 2. Mixed Traffic Segregation (Legitimate Node vs Active Attacker under load)
 3. Rapid Churn & Hardware Reset Resilience (MTTR & Sentinel Stability)
"""

import sys
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

TARGET_IP = "10.211.137.55"
BASE_URL = f"http://{TARGET_IP}"
LOG_FILE = "/home/mametz/Download/siber/logs/incident_forensic.log"

C_RESET = "\033[0m"
C_RED = "\033[91;1m"
C_GREEN = "\033[92;1m"
C_YELLOW = "\033[93;1m"
C_CYAN = "\033[96;1m"
C_MAGENTA = "\033[95;1m"
C_WHITE = "\033[97m"

def send_http(url, params=None, headers=None, method="GET", timeout=4):
    if headers is None:
        headers = {}
    if "User-Agent" not in headers:
        headers["User-Agent"] = "A-HIDS-StressTest/2.0"

    t0 = time.time()
    try:
        data = None
        if params:
            encoded_params = urllib.parse.urlencode(params)
            if method == "POST":
                data = encoded_params.encode('utf-8')
            else:
                url = f"{url}?{encoded_params}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency = (time.time() - t0) * 1000
            return response.getcode(), latency, "OK"
    except urllib.error.HTTPError as e:
        latency = (time.time() - t0) * 1000
        return e.code, latency, f"HTTP_{e.code}"
    except Exception as e:
        latency = (time.time() - t0) * 1000
        return 0, latency, str(e)[:30]

def wait_for_esp32_online(timeout=25):
    start = time.time()
    while time.time() - start < timeout:
        code, lat, err = send_http(f"{BASE_URL}/", timeout=1.5)
        if code == 200:
            return True, time.time() - start
        time.sleep(0.5)
    return False, time.time() - start

# =========================================================================
# STAGE 1: CONCURRENCY & THROUGHPUT STRESS TEST
# =========================================================================
def run_concurrency_stress(concurrency_levels=[10, 25, 50]):
    print(f"\n{C_CYAN}======================================================================{C_RESET}")
    print(f"{C_WHITE}[STAGE 1] CONCURRENCY & THROUGHPUT STRESS (lwIP & Sliding Window){C_RESET}")
    print(f"{C_CYAN}======================================================================{C_RESET}")
    print(f"Tujuan: Menguji batas konkruensi soket TCP ESP32 & akumulasi skor Sliding Window.")

    stage1_results = {}

    for c in concurrency_levels:
        print(f"\n {C_YELLOW}>>> Menembakkan {c} request HTTP simultan (Concurrency = {c})...{C_RESET}")
        
        url = f"{BASE_URL}/login"
        payload = {"user": "stress_probe", "pass": "test"}

        latencies = []
        status_counts = {}
        t_start = time.time()

        with ThreadPoolExecutor(max_workers=c) as executor:
            futures = [
                executor.submit(send_http, url, params=payload, method="POST", timeout=3.5)
                for _ in range(c)
            ]
            for f in as_completed(futures):
                code, lat, status = f.result()
                latencies.append(lat)
                status_counts[status] = status_counts.get(status, 0) + 1

        total_time = time.time() - t_start
        rps = c / total_time if total_time > 0 else 0
        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        min_lat = min(latencies) if latencies else 0
        max_lat = max(latencies) if latencies else 0

        print(f"     Total Waktu   : {total_time:.2f} detik ({rps:.1f} req/detik)")
        print(f"     Latensi       : Min {min_lat:.1f}ms | Avg {avg_lat:.1f}ms | Max {max_lat:.1f}ms")
        print(f"     Status Breakdown:")
        for st, count in sorted(status_counts.items()):
            pct = (count / c) * 100
            color = C_GREEN if "HTTP_401" in st or "OK" in st else C_RED
            print(f"       - {color}{st:15}{C_RESET}: {count:2d}/{c} ({pct:.1f}%)")

        success_count = sum(cnt for st, cnt in status_counts.items() if "HTTP" in st or "OK" in st)
        stage1_results[c] = {
            "total": c,
            "success": success_count,
            "avg_lat": avg_lat,
            "rps": rps,
            "breakdown": status_counts
        }

        # Beri jeda kecil agar buffer TCP pulih
        time.sleep(1.0)

    return stage1_results

# =========================================================================
# STAGE 2: MIXED TRAFFIC SEGREGATION (VALID VS MALICIOUS)
# =========================================================================
def run_mixed_traffic_test():
    print(f"\n{C_CYAN}======================================================================{C_RESET}")
    print(f"{C_WHITE}[STAGE 2] MIXED TRAFFIC SEGREGATION (Authenticated Node vs Attacker){C_RESET}")
    print(f"{C_CYAN}======================================================================{C_RESET}")
    print(f"Tujuan: Membuktikan bahwa node resmi bertoken TIDAK terblokir saat honeypot dibombardir.")

    num_requests = 20
    results_valid = []
    results_attack = []

    def task_valid(i):
        headers = {"X-Node-Token": "NODE-AUTH-SEC-2026"}
        payload = {"user": f"iot_node_{i}", "pass": "status_heartbeat"}
        code, lat, status = send_http(f"{BASE_URL}/login", params=payload, headers=headers, method="POST", timeout=3)
        return "VALID", code, lat, status

    def task_attack(i):
        headers = {"User-Agent": "sqlmap/1.7"}
        payload = {"user": f"probe_{i}' OR 1=1--", "pass": "hack"}
        code, lat, status = send_http(f"{BASE_URL}/login", params=payload, headers=headers, method="POST", timeout=3)
        return "ATTACK", code, lat, status

    print(f" >>> Menembakkan 10 request valid & 10 request serangan secara interleaved (berbarengan)...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(10):
            futures.append(executor.submit(task_valid, i))
            futures.append(executor.submit(task_attack, i))

        for f in as_completed(futures):
            t_type, code, lat, status = f.result()
            if t_type == "VALID":
                results_valid.append((code, lat, status))
            else:
                results_attack.append((code, lat, status))

    valid_success = sum(1 for c, _, _ in results_valid if c == 200 or c == 401)
    attack_detected = sum(1 for c, _, _ in results_attack if c == 401)

    print(f"     Node Resmi (Valid) Diterima : {C_GREEN}{valid_success}/10{C_RESET}")
    print(f"     Serangan Diidentifikasi/Blok: {C_YELLOW}{attack_detected}/10{C_RESET}")

    is_segregated = valid_success >= 7 # Mentolerir socket saturation minor
    return is_segregated

# =========================================================================
# STAGE 3: RAPID CHURN & HARDWARE RESET RESILIENCE (MTTR BENCHMARK)
# =========================================================================
def run_rapid_churn_test(cycles=3):
    print(f"\n{C_CYAN}======================================================================{C_RESET}")
    print(f"{C_WHITE}[STAGE 3] RAPID CHURN & HARDWARE RESET RESILIENCE (MTTR Benchmark){C_RESET}")
    print(f"{C_CYAN}======================================================================{C_RESET}")
    print(f"Tujuan: Mengukur MTTR (Mean Time To Recovery) Sentinel & ESP32 saat di-reset bertubi-tubi.")

    recovery_times = []

    for i in range(1, cycles + 1):
        print(f"\n {C_MAGENTA}>>> Siklus Reset #{i}: Menembakkan Canary Trap (/.env)...{C_RESET}")
        code, lat, status = send_http(f"{BASE_URL}/.env", timeout=3)
        print(f"     Pemicu Lockdown: HTTP {code} ({status})")

        print(f"     Sentinel Uno mengeksekusi Hardware Hard-Reset...")
        t_trigger = time.time()
        time.sleep(1.0) # Waktu fisik reset pin EN

        is_online, duration = wait_for_esp32_online(timeout=20)
        if is_online:
            actual_recovery = time.time() - t_trigger
            recovery_times.append(actual_recovery)
            print(f"     {C_GREEN}✔ ESP32 Steril & Kembali Online dalam {actual_recovery:.2f} detik!{C_RESET}")
        else:
            print(f"     {C_RED}✖ Timeout pemulihan pada siklus #{i}!{C_RESET}")

    avg_mttr = sum(recovery_times) / len(recovery_times) if recovery_times else 0
    print(f"\n {C_CYAN}>>> Hasil Uji Resiliensi Hardware:{C_RESET}")
    print(f"     Siklus Sukses : {len(recovery_times)}/{cycles}")
    print(f"     Mean Recovery : {C_GREEN}{avg_mttr:.2f} detik (MTTR){C_RESET}")

    return len(recovery_times) == cycles, avg_mttr

# =========================================================================
# MAIN DRIVER
# =========================================================================
def main():
    print(f"{C_CYAN}######################################################################{C_RESET}")
    print(f"{C_WHITE}#          A-HIDS COMPREHENSIVE STRESS & RESILIENCE SUITE            #{C_RESET}")
    print(f"{C_CYAN}######################################################################{C_RESET}")
    print(f"Target Honeypot: {BASE_URL}")

    online, _ = wait_for_esp32_online(timeout=5)
    if not online:
        print(f"{C_RED}[FATAL] ESP32 tidak merespons di {TARGET_IP}. Pastikan device aktif.{C_RESET}")
        sys.exit(1)

    print(f"{C_GREEN}[READY] ESP32 Aktif & Siap Menghadapi Stress Test!{C_RESET}")

    # Stage 1
    s1_results = run_concurrency_stress(concurrency_levels=[10, 25, 50])

    # Pastikan stabil sebelum Stage 2
    time.sleep(2)

    # Stage 2
    s2_pass = run_mixed_traffic_test()

    # Pastikan stabil sebelum Stage 3
    time.sleep(2)

    # Stage 3
    s3_pass, mttr = run_rapid_churn_test(cycles=3)

    # REKAPITULASI AKHIR
    print(f"\n{C_CYAN}======================================================================{C_RESET}")
    print(f"{C_WHITE}                    RINGKASAN BENCHMARK STRESS TEST                   {C_RESET}")
    print(f"{C_CYAN}======================================================================{C_RESET}")
    print(f" 1. Batas Konkruensi TCP:")
    for c, r in s1_results.items():
        ratio = (r['success'] / r['total']) * 100
        color = C_GREEN if ratio >= 80 else (C_YELLOW if ratio >= 50 else C_RED)
        print(f"    - Concurrency {c:2d} : {color}{r['success']}/{r['total']} ({ratio:.0f}% dilayani){C_RESET} | Latensi: {r['avg_lat']:.1f}ms | Throughput: {r['rps']:.1f} rps")

    print(f" 2. Segregasi Trafik Valid : {'✅ ' + C_GREEN + 'LULUS (Node resmi tidak ikut terblokir)' if s2_pass else '❌ ' + C_RED + 'GAGAL'}{C_RESET}")
    print(f" 3. Siklus Hard-Reset MTTR : {'✅ ' + C_GREEN + f'LULUS (Rata-rata pemulihan {mttr:.2f} detik)' if s3_pass else '❌ ' + C_RED + 'GAGAL'}{C_RESET}")
    print(f"{C_CYAN}======================================================================{C_RESET}\n")

if __name__ == "__main__":
    main()
