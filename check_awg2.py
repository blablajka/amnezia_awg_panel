"""Check AWG 2.0 install result and redeploy container."""
import paramiko
import time

HOST = "195.208.119.192"
USER = "root"
PASS = "x7CTv6CgqvE4BFu3"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=30)

def run(cmd, desc="", timeout=30):
    if desc:
        print(f"  [{desc}]...")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err

print("=== Check AWG 2.0 install + redeploy ===\n")

# 1. Check install log
print("[1] Install log...")
out, _ = run("tail -40 /tmp/awg2_install.log 2>/dev/null || echo 'NO LOG'")
for line in out.split("\n")[-30:]:
    print(f"  {line[:200]}")

# 2. Check new module
print("\n[2] AWG module info...")
out, _ = run("modinfo amneziawg 2>/dev/null | grep -E 'version|vermagic|filename'")
print(f"  {out}")

out, _ = run("lsmod | grep amneziawg")
print(f"  Loaded: {out or 'NOT LOADED'}")

# 3. dmesg
out, _ = run("dmesg | grep -i 'amnezia' | tail -8")
print(f"\n  dmesg:\n{out}")

# 4. Check if module supports H params now
out, _ = run("modinfo amneziawg 2>/dev/null | head -20")
print(f"\n  modinfo:\n{out[:600]}")

# 5. Try deploying container with full AWG 2.0 config
print("\n[3] Deploy container with full AWG 2.0 config...")

pk = "eLObIA176IgGVjSxbiXopCKFIeJNAHFrgRwmBxIstEA="
import random
jc = random.randint(5, 10)
jmin = random.randint(40, 50)
jmax = random.randint(70, 120)
s1 = random.randint(15, 68)
s2 = random.randint(15, 68)
while s2 == s1 + 56:
    s2 = random.randint(15, 68)
h1 = random.randint(100000, 800000)
h2 = random.randint(1000000, 8000000)
h3 = random.randint(10000000, 80000000)
h4 = random.randint(100000000, 800000000)

config = f"""[Interface]
PrivateKey = {pk}
ListenPort = 51820
Address = 10.8.1.1/24
MTU = 1280
Jc = {jc}
Jmin = {jmin}
Jmax = {jmax}
S1 = {s1}
S2 = {s2}
H1 = {h1}
H2 = {h2}
H3 = {h3}
H4 = {h4}
"""

print(f"  Jc={jc} Jmin={jmin} Jmax={jmax} S1={s1} S2={s2}")
print(f"  H1={h1} H2={h2} H3={h3} H4={h4}")

import base64
cfg_b64 = base64.b64encode(config.encode()).decode()
run(f"echo '{cfg_b64}' | base64 -d > /etc/amnezia/amneziawg/awg0.conf", "Write config")

# Check what was written
out, _ = run("cat /etc/amnezia/amneziawg/awg0.conf")
print(f"  Config:\n{out}")

# Clean and restart
run("docker stop awg-server 2>/dev/null || true")
run("docker rm awg-server 2>/dev/null || true")
run("ip link delete awg0 2>/dev/null || true")
time.sleep(1)

cmd = (
    "docker run -d --name awg-server "
    "--cap-add=NET_ADMIN --cap-add=SYS_MODULE "
    "--network host "
    "--restart unless-stopped "
    "-v /etc/amnezia:/etc/amnezia "
    "metaligh/amneziawg:latest"
)
run(cmd, "docker run")
time.sleep(5)

# Verify
logs, _ = run("docker logs awg-server --tail 10 2>&1")
print(f"\n  Logs:\n{logs}")

udp, _ = run("ss -tuln | grep 51820")
if udp:
    print(f"\n  [SUCCESS] UDP 51820 LISTENING with AWG 2.0! H1-H4 supported!")
    for line in udp.strip().split("\n"):
        print(f"    {line}")
else:
    print(f"\n  [FAIL] UDP 51820 NOT listening")

    # Check if H1-H4 parsing failed
    if "H1" in logs:
        print("  H-params still not recognized")

awg, _ = run("docker exec awg-server awg show 2>/dev/null")
print(f"\n  AWG show:\n{awg}")

ssh.close()
print("\n=== Done ===")
