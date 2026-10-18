import urllib.request
import urllib.parse
import socket
import time

esp_ip = "192.168.0.112"

print("[+] STARTING MULTI-VECTOR HARDENING STRESS TEST FROM LXC 123...")

# 1. NOP Sled Attack (Low Entropy Shellcode Primer)
print("[*] Vector 1: NOP Sled Memory Overflow Prep")
try:
    data = urllib.parse.urlencode({"buf": "\x90" * 80}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Status:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(1.5)

# 2. Windows Path Traversal (boot.ini)
print("[*] Vector 2: Windows Path Traversal (boot.ini)")
try:
    data = urllib.parse.urlencode({"cfg": "..\\..\\..\\boot.ini"}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Status:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(1.5)

# 3. Automated Masscan / Recon Probe
print("[*] Vector 3: Automated Scanner Header (Masscan)")
try:
    req = urllib.request.Request("http://" + esp_ip + "/", headers={"User-Agent": "masscan/1.3"})
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Status:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(1.5)

# 4. Rapid TCP Stream Burst (15 Sockets)
print("[*] Vector 4: Rapid TCP SYN/Stream Burst (15 connections)")
success_conns = 0
for i in range(15):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect((esp_ip, 80))
        s.send(b"GET / HTTP/1.1\r\nHost: " + esp_ip.encode() + b"\r\n\r\n")
        s.close()
        success_conns += 1
    except Exception:
        pass
print("    [-] Sent " + str(success_conns) + "/15 rapid TCP connections")

print("[+] Stress test run completed.")
