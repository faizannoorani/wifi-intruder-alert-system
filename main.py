

import threading
import time
import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
import nmap
from config import get_connection

# ── Sound: sudo-safe alarm ─────────────────────────────────────────────────────
# Problem: sudo se run karne par pygame PulseAudio socket nahi dhundh pata.
# Fix: PULSE_SERVER env variable set karo + fallback ke liye paplay/aplay use karo.

alarm_playing = False
alarm_lock = threading.Lock()
alarm_thread = None

# PulseAudio socket path set karo (sudo ke andar bhi normal user ka socket mile)
_real_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
_pulse_socket = f"/run/user/{os.getuid()}/pulse/native"

# Agar sudo se run ho raha hai toh real user ka UID dhundho
if os.environ.get("SUDO_USER"):
    try:
        import pwd
        _real_uid = pwd.getpwnam(os.environ["SUDO_USER"]).pw_uid
        _pulse_socket = f"/run/user/{_real_uid}/pulse/native"
    except Exception:
        pass

os.environ["PULSE_SERVER"] = f"unix:{_pulse_socket}"

# Pygame init karo fixed environment ke saath
pygame_ok = False
alarm_sound = None

try:
    import pygame
    pygame.mixer.init()
    alarm_sound = pygame.mixer.Sound("alarms.wav")
    pygame_ok = True
    print("✅ Pygame audio initialized OK")
except Exception as e:
    print(f"⚠️  Pygame warning: {e}")
    print("    Fallback: paplay/aplay use karega")


def _beep_fallback():
    """Pygame na chale to system beep ya paplay se alarm bajao."""
    wav_file = os.path.join(os.path.dirname(__file__), "alarms.wav")
    while alarm_playing:
        try:
            if os.path.exists(wav_file):
                # paplay (PulseAudio) try karo
                env = os.environ.copy()
                subprocess.run(
                    ["paplay", wav_file],
                    env=env, timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                # Last resort: terminal bell
                subprocess.run(["tput", "bel"], timeout=2,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        time.sleep(0.5)


def play_alarm():
    global alarm_playing, alarm_thread
    with alarm_lock:
        if alarm_playing:
            return
        alarm_playing = True

        if pygame_ok and alarm_sound:
            alarm_sound.play(-1)
        else:
            # Background thread mein fallback chalao
            alarm_thread = threading.Thread(target=_beep_fallback, daemon=True)
            alarm_thread.start()


def stop_alarm():
    global alarm_playing
    with alarm_lock:
        alarm_playing = False
        if pygame_ok and alarm_sound:
            alarm_sound.stop()
        # fallback thread khud band ho jayega (alarm_playing=False check karta hai)


# ── GUI setup ──────────────────────────────────────────────────────────────────
root = tk.Tk()
root.title("Network Device Monitor")
root.geometry("700x550")

tk.Label(root, text="Network Device Monitor", font=("Arial", 16, "bold")).pack(pady=8)

frame = tk.Frame(root)
frame.pack(fill="both", expand=True, padx=10)

columns = ("IP", "MAC", "Status", "Trusted")
tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
for col in columns:
    tree.heading(col, text=col)
    tree.column(col, width=160, anchor="center")

tree.tag_configure("trusted",   background="#d4edda", foreground="#155724")
tree.tag_configure("untrusted", background="#f8d7da", foreground="#721c24")
tree.tag_configure("new",       background="#fff3cd", foreground="#856404")

scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)
tree.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

# Bottom buttons
btn_frame = tk.Frame(root)
btn_frame.pack(pady=8)


def trust_selected():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("Select device", "Please select a device first.")
        return
    item = tree.item(selected[0])
    mac = item["values"][1]
    if mac == "Unknown":
        messagebox.showwarning("Cannot trust", "Device has no MAC address.")
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE devices SET trusted=TRUE WHERE mac=%s", (mac,))
    conn.commit()
    cursor.close()
    conn.close()
    messagebox.showinfo("Trusted", f"{mac} marked as trusted.")
    check_and_stop_alarm()


def check_and_stop_alarm():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM devices WHERE trusted=FALSE AND status='online'"
    )
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    if count == 0:
        stop_alarm()


tk.Button(btn_frame, text="✔ Trust Selected",
          command=trust_selected, bg="#28a745", fg="white",
          padx=12, pady=4).pack(side="left", padx=8)

tk.Button(btn_frame, text="🔕 Stop Alarm",
          command=stop_alarm, bg="#dc3545", fg="white",
          padx=12, pady=4).pack(side="left", padx=8)

status_var = tk.StringVar(value="Status: Waiting for first scan…")
tk.Label(root, textvariable=status_var, font=("Arial", 10), fg="gray").pack(pady=4)


# ── Database helpers ───────────────────────────────────────────────────────────

def get_all_devices():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT mac, ip, trusted, status FROM devices")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return {mac: {"ip": ip, "trusted": bool(trusted), "status": status}
            for mac, ip, trusted, status in rows}


def upsert_device(mac, ip):
    if mac == "Unknown":
        return True, False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, trusted FROM devices WHERE mac=%s", (mac,))
    row = cursor.fetchone()
    if row:
        device_id, trusted = row
        cursor.execute(
            "UPDATE devices SET ip=%s, status='online' WHERE id=%s",
            (ip, device_id)
        )
        is_new, is_trusted = False, bool(trusted)
    else:
        cursor.execute(
            "INSERT INTO devices (mac, ip, trusted, status) VALUES (%s,%s,FALSE,'online')",
            (mac, ip)
        )
        is_new, is_trusted = True, False
    conn.commit()
    cursor.close()
    conn.close()
    return is_trusted, is_new


def mark_offline(active_macs):
    if not active_macs:
        return
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join(["%s"] * len(active_macs))
    cursor.execute(
        f"UPDATE devices SET status='offline' WHERE mac NOT IN ({placeholders})",
        list(active_macs)
    )
    conn.commit()
    cursor.close()
    conn.close()


# ── Network scanning ───────────────────────────────────────────────────────────
def scan_network():
    import subprocess, re
    result = subprocess.run(
        ["arp-scan", "--localnet"],
        capture_output=True, text=True
    )
    devices = []
    for line in result.stdout.splitlines():
        match = re.match(r'([\d.]+)\s+([\da-f:]{17})', line, re.IGNORECASE)
        if match:
            ip  = match.group(1)
            mac = match.group(2).upper()
            devices.append((ip, mac))
    return devices

# ── GUI update ─────────────────────────────────────────────────────────────────                        

def refresh_gui(scan_results):
    active_macs = set()
    threat_found = False

    for ip, mac in scan_results:
        is_trusted, is_new = upsert_device(mac, ip)
        if mac != "Unknown":
            active_macs.add(mac)
        if not is_trusted:
            threat_found = True

    mark_offline(active_macs)

    tree.delete(*tree.get_children())
    all_db = get_all_devices()

    for mac, info in all_db.items():
        ip      = info["ip"]
        trusted = info["trusted"]
        status  = info.get("status", "unknown")

        status_label = "🟢 Online" if status == "online" else "⚫ Offline"
        trust_label  = "✔ Trusted" if trusted else "✘ Unknown"
        tag = "trusted" if trusted else ("untrusted" if status == "online" else "new")

        tree.insert("", "end", values=(ip, mac, status_label, trust_label), tags=(tag,))

    if threat_found:
        play_alarm()
    else:
        stop_alarm()

    now = time.strftime("%H:%M:%S")
    status_var.set(f"Last scan: {now}  |  Devices: {len(all_db)}")


# ── Background scan loop ───────────────────────────────────────────────────────

def scan_loop():
    while True:
        try:
            results = scan_network()
            root.after(0, lambda r=results: refresh_gui(r))
        except Exception as e:
            root.after(0, lambda err=e: status_var.set(f"Scan error: {err}"))
        time.sleep(15)


thread = threading.Thread(target=scan_loop, daemon=True)
thread.start()

root.mainloop()          