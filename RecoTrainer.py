# RecoTrainer — GUI-only trainer (no profanity, no console output)
# Features:
#   - Set Home (from X,Y)
#   - Load Walkable JSON (A* pathing)
#   - Set Target NPC IDs
#   - Burst Heal on EXP gain (hook or poll)
#   - Auto-heal by HP threshold
#   - Combat tap at TPS when current NPC matches target list
#   - Patrol/Wander within radius using walkable
#   - Uses addresses from original script by default until you update them in GUI
# Dependencies: frida, pyautogui
import os, json, threading, time, queue, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import heapq
import pyautogui
try:
    import frida
except Exception:
    frida = None

APP_TITLE = "RecoTrainer"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "PROCESS_NAME": "endor.exe",
    "ATTACH_MODE": "process",
    "ADDR_EXP": "0x0",
    "ADDR_EXP_HOOK": "0x577414",
    "ADDR_HP": "0x0",
    "ADDR_X": "0x0",
    "ADDR_Y": "0x0",
    "ADDR_CURRENT_NPC_ID": "0x0",
    "KEY_ATTACK": "ctrl",
    "KEY_HEAL": "f1",
    "HEAL_BURST_TAPS": 10,
    "HEAL_BURST_GAP": 0.03,
    "HP_AUTOHEAL_THRESHOLD": 50,
    "HP_AUTOHEAL_TAPS": 1,
    "HP_AUTOHEAL_GAP": 0.05,
    "HEAL_ON_EXP_GAIN": True,
    "ATTACK_TPS": 8,
    "RETARGET_MS": 600,
    "MOVE_TILE_TIME": 0.12,
    "PATROL_RADIUS": 18,
    "PATROL_IDLE_MS": 400,
    "HOME_X": 0,
    "HOME_Y": 0,
    "TARGET_NPC_IDS": [],
    "WALKABLE_PATH": "",
    "ENABLE_COMBAT": True,
    "ENABLE_PATROL": True,
    "ENABLE_HP_AUTOHEAL": True,
    "LEGACY_SCRIPT_PATH": "CeraBot v13.py",
    "GUI_THEME": "dark"
}

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def hx(x):
    try:
        if isinstance(x, str) and x.lower().startswith("0x"):
            return int(x, 16)
        return int(x)
    except Exception:
        return 0

class GUILogger:
    def __init__(self, text_widget: ScrolledText):
        import queue
        self.q = queue.Queue()
        self.text = text_widget
        self._stop = False

    def write(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.q.put(f"[{ts}] {msg}\n")

    def pump(self):
        if self._stop: return
        try:
            while True:
                line = self.q.get_nowait()
                self.text.insert(tk.END, line)
                self.text.see(tk.END)
        except queue.Empty:
            pass
        self.text.after(60, self.pump)

    def stop(self):
        self._stop = True

class Mem:
    def __init__(self, gui_log: GUILogger, cfg: dict):
        self.cfg = cfg
        self.logger = gui_log
        self.session = None
        self.script = None
        self.exp_gain_callback = None

    def attach(self):
        if frida is None:
            self.logger.write("Frida not installed. Memory polling only (no hook).")
            return False
        target = self.cfg["PROCESS_NAME"]
        try:
            self.session = frida.attach(target)
            js = self._build_script()
            self.script = self.session.create_script(js)
            self.script.on("message", self._on_message)
            self.script.load()
            if hx(self.cfg["ADDR_EXP_HOOK"]) != 0:
                self.script.exports.tryhookexp(hex(hx(self.cfg["ADDR_EXP_HOOK"])))
                self.logger.write("EXP hook ready.")
            else:
                self.logger.write("EXP hook disabled; using EXP value polling if ADDR_EXP set.")
            self.logger.write(f"Attached to {target}.")
            return True
        except Exception as e:
            self.logger.write(f"Attach failed: {e}")
            return False

    def _build_script(self):
        js = """
        var expHooked = false;
        rpc.exports = {
            readu32: function(addr) {
                try { return Memory.readU32(ptr(addr)); } catch(e) { return 0; }
            },
            readi32: function(addr) {
                try { return Memory.readS32(ptr(addr)); } catch(e) { return 0; }
            },
            readu16: function(addr) {
                try { return Memory.readU16(ptr(addr)); } catch(e) { return 0; }
            },
            tryhookexp: function(addrStr) {
                try {
                    if (expHooked) return true;
                    var p = ptr(addrStr);
                    Interceptor.attach(p, {
                        onLeave: function(retval) {
                            send({ t: 'exp' });
                        }
                    });
                    expHooked = true;
                    return true;
                } catch(e) {
                    send({ t: 'log', m: 'EXP hook failed: ' + e });
                    return false;
                }
            }
        };
        """
        return js

    def _on_message(self, message, data):
        if message.get("type") == "send":
            payload = message.get("payload") or {}
            t = payload.get("t")
            if t == "exp":
                if self.exp_gain_callback: self.exp_gain_callback()
            elif t == "log":
                self.logger.write(payload.get("m",""))

    def read_u32(self, addr_hex: str):
        if not self.script: return 0
        a = hx(addr_hex)
        return int(self.script.exports.readu32(hex(a))) if a else 0

    def read_i32(self, addr_hex: str):
        if not self.script: return 0
        a = hx(addr_hex)
        return int(self.script.exports.readi32(hex(a))) if a else 0

def astar(start, goal, passable):
    if start == goal: return [start]
    sx, sy = start; gx, gy = goal
    open_set = []
    heapq.heappush(open_set, (0, start))
    came = {}; g = {start:0}
    def h(a,b): return abs(a[0]-b[0]) + abs(a[1]-b[1])
    while open_set:
        _, cur = heapq.heappop(open_set)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return path
        cx, cy = cur
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nb = (cx+dx, cy+dy)
            if nb not in passable: continue
            cost = g[cur] + 1
            if nb not in g or cost < g[nb]:
                g[nb] = cost
                f = cost + h(nb, goal)
                came[nb] = cur
                heapq.heappush(open_set, (f, nb))
    return []

class BotController:
    def __init__(self, logger, cfg, gui_signal):
        self.logger = logger
        self.cfg = cfg
        self.gui_signal = gui_signal
        self.mem = Mem(logger, cfg)
        self.stop_evt = threading.Event()
        self.walkable = set()
        self.exp_prev = None
        self.hp_cur = 0
        self.pos = (0,0)
        self.target_npc_id = 0

    def try_import_legacy_addresses(self):
        path = self.cfg.get("LEGACY_SCRIPT_PATH", "CeraBot v13.py")
        if not os.path.exists(path): 
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
        except Exception:
            return False
        def setif(cur, new):
            return new if (str(cur).strip().lower()=="0x0" and isinstance(new, str) and new.lower().startswith("0x")) else cur
        res = _extract_addresses(txt)
        changed = False
        for key in ("ADDR_EXP","ADDR_EXP_HOOK","ADDR_HP","ADDR_X","ADDR_Y","ADDR_CURRENT_NPC_ID"):
            old = self.cfg.get(key, "0x0")
            new = res.get(key, "0x0")
            upd = setif(old, new)
            if upd != old:
                self.cfg[key] = upd; changed = True
        if changed:
            self.logger.write("Imported addresses from legacy script.")
        return changed

    def attach(self):
        self.try_import_legacy_addresses()
        ok = self.mem.attach()
        if ok and self.cfg.get("HEAL_ON_EXP_GAIN", True):
            self.mem.exp_gain_callback = self.on_exp_gain
        return ok

    def on_exp_gain(self):
        if self.cfg.get("HEAL_ON_EXP_GAIN", True):
            taps = int(self.cfg["HEAL_BURST_TAPS"]); gap = float(self.cfg["HEAL_BURST_GAP"])
            self.key_tap(self.cfg["KEY_HEAL"], times=taps, gap=gap)
            self.logger.write(f"EXP gain → burst heal x{taps}.")

    def key_tap(self, k, times=1, gap=0.03):
        try:
            for _ in range(times):
                pyautogui.press(k)
                time.sleep(gap)
        except Exception as e:
            self.logger.write(f"Key tap error: {e}")

    def read_state_once(self):
        hp = self.mem.read_i32(self.cfg["ADDR_HP"])
        x  = self.mem.read_i32(self.cfg["ADDR_X"])
        y  = self.mem.read_i32(self.cfg["ADDR_Y"])
        exp= self.mem.read_u32(self.cfg["ADDR_EXP"]) if hx(self.cfg["ADDR_EXP"])!=0 else None
        npc= self.mem.read_i32(self.cfg["ADDR_CURRENT_NPC_ID"])

        if hp is not None: 
            self.hp_cur = hp; self.gui_signal("hp", str(hp))
        if x is not None and y is not None:
            self.pos = (x,y); self.gui_signal("pos", f"{x},{y}")
        if npc is not None:
            self.target_npc_id = npc; self.gui_signal("npc", str(npc))
        if exp is not None:
            if self.exp_prev is not None and exp > self.exp_prev:
                self.on_exp_gain()
            self.exp_prev = exp

    def set_home_from_current(self):
        x,y = self.pos
        self.cfg["HOME_X"] = int(x); self.cfg["HOME_Y"] = int(y)
        self.logger.write(f"Home = ({x},{y})")

    def load_walkable(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.logger.write(f"Walkable load error: {e}")
            return False
        s = set()
        if isinstance(data, dict) and "walkable" in data:
            for pair in data["walkable"]:
                if isinstance(pair, (list,tuple)) and len(pair)==2:
                    s.add((int(pair[0]), int(pair[1])))
        elif isinstance(data, list):
            for pair in data:
                if isinstance(pair, (list,tuple)) and len(pair)==2:
                    s.add((int(pair[0]), int(pair[1])))
        else:
            self.logger.write("Unsupported walkable JSON format.")
            return False
        self.walkable = s
        self.cfg["WALKABLE_PATH"] = path
        self.logger.write(f"Walkable loaded: {len(s)} tiles.")
        return True

    def in_targets(self):
        tid = int(self.target_npc_id)
        return tid in set(int(x) for x in self.cfg.get("TARGET_NPC_IDS", []))

    def start(self):
        self.stop_evt = threading.Event()
        threading.Thread(target=self._poll_thread, daemon=True).start()
        threading.Thread(target=self._combat_thread, daemon=True).start()
        threading.Thread(target=self._patrol_thread, daemon=True).start()
        if self.cfg.get("ENABLE_HP_AUTOHEAL", True):
            threading.Thread(target=self._autoheal_thread, daemon=True).start()
        self.logger.write("Trainer started.")

    def stop(self):
        if hasattr(self, "stop_evt"):
            self.stop_evt.set()
            self.logger.write("Trainer stopping...")

    def _poll_thread(self):
        self.logger.write("Poller running.")
        while not self.stop_evt.is_set():
            try:
                self.read_state_once()
            except Exception as e:
                self.logger.write(f"Poll error: {e}")
            time.sleep(max(0.05, float(self.cfg["RETARGET_MS"])/1000.0))

    def _combat_thread(self):
        self.logger.write("Combat loop running.")
        gap = 1.0/float(clamp(int(self.cfg["ATTACK_TPS"]),1,25))
        while not self.stop_evt.is_set():
            if self.cfg.get("ENABLE_COMBAT", True) and self.in_targets():
                self.key_tap(self.cfg["KEY_ATTACK"])
                time.sleep(gap)
            else:
                time.sleep(0.05)

    def _autoheal_thread(self):
        self.logger.write("Auto-heal loop running.")
        while not self.stop_evt.is_set():
            if self.cfg.get("ENABLE_HP_AUTOHEAL", True):
                try:
                    if int(self.hp_cur) <= int(self.cfg["HP_AUTOHEAL_THRESHOLD"]):
                        self.key_tap(self.cfg["KEY_HEAL"], times=int(self.cfg["HP_AUTOHEAL_TAPS"]), gap=float(self.cfg["HP_AUTOHEAL_GAP"]))
                        self.logger.write(f"HP {self.hp_cur} <= {self.cfg['HP_AUTOHEAL_THRESHOLD']} → heal.")
                except Exception as e:
                    self.logger.write(f"Auto-heal error: {e}")
            time.sleep(0.1)

    def _patrol_thread(self):
        self.logger.write("Patrol loop running.")
        while not self.stop_evt.is_set():
            if not self.cfg.get("ENABLE_PATROL", True):
                time.sleep(0.25); continue
            if self.in_targets():
                time.sleep(0.1); continue
            if not self.walkable:
                time.sleep(0.3); continue
            hx, hy = int(self.cfg["HOME_X"]), int(self.cfg["HOME_Y"])
            r = int(self.cfg["PATROL_RADIUS"])
            candidates = [(x,y) for (x,y) in self.walkable if abs(x-hx)<=r and abs(y-hy)<=r]
            if not candidates:
                time.sleep(0.4); continue
            import random
            goal = random.choice(candidates)
            path = astar(self.pos, goal, self.walkable)
            if len(path) <= 1:
                time.sleep(0.2); continue
            self.logger.write(f"Patrol → {goal} ({len(path)} steps)")
            move_dt = float(self.cfg["MOVE_TILE_TIME"])
            for i in range(1, len(path)):
                if self.stop_evt.is_set(): break
                if self.in_targets(): break
                cx, cy = path[i-1]; nx, ny = path[i]
                dx, dy = nx-cx, ny-cy
                if dx == 1:  pyautogui.press("right")
                elif dx == -1: pyautogui.press("left")
                elif dy == 1:  pyautogui.press("down")
                elif dy == -1: pyautogui.press("up")
                time.sleep(move_dt)
            time.sleep(max(0.05, float(self.cfg["PATROL_IDLE_MS"])/1000.0))

def _extract_addresses(src:str):
    found = {
        "ADDR_EXP": "0x0",
        "ADDR_EXP_HOOK": "0x0",
        "ADDR_HP": "0x0",
        "ADDR_X": "0x0",
        "ADDR_Y": "0x0",
        "ADDR_CURRENT_NPC_ID": "0x0"
    }
    for m in re.finditer(r'(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(0x[0-9A-Fa-f]+)\b', src):
        name, hxv = m.group(1), m.group(2)
        lname = name.lower()
        def setif(k):
            if found[k] == "0x0":
                found[k] = hxv
        if "exp" in lname and "hook" in lname: setif("ADDR_EXP_HOOK")
        elif "exp" in lname: setif("ADDR_EXP")
        elif ("hp" in lname or "health" in lname): setif("ADDR_HP")
        elif lname in ("x","addr_x") or ("pos" in lname and "x" in lname) or ("player" in lname and "x" in lname): setif("ADDR_X")
        elif lname in ("y","addr_y") or ("pos" in lname and "y" in lname) or ("player" in lname and "y" in lname): setif("ADDR_Y")
        elif ("npc" in lname and "id" in lname): setif("ADDR_CURRENT_NPC_ID")
    lines = src.splitlines()
    for i,l in enumerate(lines):
        for m in re.finditer(r'Interceptor\.attach\(\s*ptr\(\s*[\'"](0x[0-9A-Fa-f]+)[\'"]\s*\)', l):
            hxv = m.group(1)
            ctx = "\n".join(lines[max(0,i-2): i+3]).lower()
            if "exp" in ctx and found["ADDR_EXP_HOOK"] == "0x0":
                found["ADDR_EXP_HOOK"] = hxv
    return found

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x680")
        self.root.configure(bg="#0b0b0b")
        self.cfg = self.load_config()
        self.log = self._build_layout()
        self.logger = GUILogger(self.log)
        self.ctrl = BotController(self.logger, self.cfg, self._set_small_status)
        self.logger.pump()

    def _build_layout(self):
        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        left = tk.Frame(self.root, bg="#121212", bd=1, relief=tk.SOLID)
        left.grid(row=0, column=0, sticky="ns")
        right = tk.Frame(self.root, bg="#0b0b0b")
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(left, text="RecoTrainer Controls", fg="#fff", bg="#121212", font=("Segoe UI", 13, "bold")).pack(fill="x", padx=10, pady=10)

        f_proc = tk.LabelFrame(left, text="Attach", fg="#ddd", bg="#121212")
        f_proc.pack(fill="x", padx=10, pady=6)
        tk.Label(f_proc, text="Process Name", fg="#ddd", bg="#121212").grid(row=0, column=0, sticky="w")
        self.e_proc = tk.Entry(f_proc, width=20); self.e_proc.grid(row=0, column=1, padx=4, pady=4); self.e_proc.insert(0, self.cfg["PROCESS_NAME"])
        tk.Label(f_proc, text="Attach Mode", fg="#ddd", bg="#121212").grid(row=1, column=0, sticky="w")
        self.c_mode = ttk.Combobox(f_proc, values=["process","window"], width=18, state="readonly")
        self.c_mode.grid(row=1, column=1, padx=4, pady=4); self.c_mode.set(self.cfg["ATTACH_MODE"])
        tk.Button(f_proc, text="Attach", command=self._on_attach, bg="#1e1e1e", fg="#fff").grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)

        f_addr = tk.LabelFrame(left, text="Addresses", fg="#ddd", bg="#121212"); f_addr.pack(fill="x", padx=10, pady=6)
        self.addr_vars = {}
        for i,(label,key) in enumerate([("EXP (value)","ADDR_EXP"),("EXP Hook (func)","ADDR_EXP_HOOK"),("HP","ADDR_HP"),("X","ADDR_X"),("Y","ADDR_Y"),("Current NPC ID","ADDR_CURRENT_NPC_ID")]):
            tk.Label(f_addr, text=label, fg="#ddd", bg="#121212").grid(row=i, column=0, sticky="w")
            e = tk.Entry(f_addr, width=22); e.grid(row=i, column=1, padx=4, pady=2); e.insert(0, self.cfg.get(key,"0x0")); self.addr_vars[key] = e

        f_keys = tk.LabelFrame(left, text="Keys & Heal", fg="#ddd", bg="#121212"); f_keys.pack(fill="x", padx=10, pady=6)
        def add_row(frame, r, label, val, width=18):
            tk.Label(frame, text=label, fg="#ddd", bg="#121212").grid(row=r, column=0, sticky="w")
            e = tk.Entry(frame, width=width); e.grid(row=r, column=1, padx=4, pady=2); e.insert(0,val); return e
        self.e_attack = add_row(f_keys, 0, "Attack Key", self.cfg["KEY_ATTACK"])
        self.e_heal   = add_row(f_keys, 1, "Heal Key", self.cfg["KEY_HEAL"])
        self.e_burst  = add_row(f_keys, 2, "Burst Taps", str(self.cfg["HEAL_BURST_TAPS"]))
        self.e_bgap   = add_row(f_keys, 3, "Burst Gap", str(self.cfg["HEAL_BURST_GAP"]))
        self.e_hp_th  = add_row(f_keys, 4, "HP Threshold", str(self.cfg["HP_AUTOHEAL_THRESHOLD"]))
        self.e_hp_tap = add_row(f_keys, 5, "HP Heal Taps", str(self.cfg["HP_AUTOHEAL_TAPS"]))
        self.e_hp_gap = add_row(f_keys, 6, "HP Heal Gap", str(self.cfg["HP_AUTOHEAL_GAP"]))

        f_tgt = tk.LabelFrame(left, text="Targets & Patrol", fg="#ddd", bg="#121212"); f_tgt.pack(fill="x", padx=10, pady=6)
        self.e_tps = add_row(f_tgt, 0, "Attack TPS", str(self.cfg["ATTACK_TPS"]))
        self.e_ret = add_row(f_tgt, 1, "Retarget ms", str(self.cfg["RETARGET_MS"]))
        self.e_step= add_row(f_tgt, 2, "Move tile sec", str(self.cfg["MOVE_TILE_TIME"]))
        self.e_rad = add_row(f_tgt, 3, "Patrol radius", str(self.cfg["PATROL_RADIUS"]))
        self.e_pms = add_row(f_tgt, 4, "Patrol idle ms", str(self.cfg["PATROL_IDLE_MS"]))

        f_tog = tk.LabelFrame(left, text="Toggles", fg="#ddd", bg="#121212"); f_tog.pack(fill="x", padx=10, pady=6)
        self.v_combat = tk.BooleanVar(value=self.cfg["ENABLE_COMBAT"])
        self.v_patrol = tk.BooleanVar(value=self.cfg["ENABLE_PATROL"])
        self.v_autoheal = tk.BooleanVar(value=self.cfg["ENABLE_HP_AUTOHEAL"])
        self.v_expburst = tk.BooleanVar(value=self.cfg.get("HEAL_ON_EXP_GAIN", True))
        for txt, var in [("Combat", self.v_combat), ("Patrol", self.v_patrol), ("HP Auto-Heal", self.v_autoheal), ("Burst Heal on EXP", self.v_expburst)]:
            tk.Checkbutton(f_tog, text=txt, variable=var, bg="#121212", fg="#ddd", selectcolor="#121212").pack(anchor="w")

        f_act = tk.LabelFrame(left, text="Actions", fg="#ddd", bg="#121212"); f_act.pack(fill="x", padx=10, pady=6)
        tk.Button(f_act, text="Save Config", command=self._on_save, bg="#1e1e1e", fg="#fff").grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        tk.Button(f_act, text="Set Home = Current", command=self._on_set_home, bg="#1e1e1e", fg="#fff").grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        tk.Button(f_act, text="Load Walkable...", command=self._on_load_walkable, bg="#1e1e1e", fg="#fff").grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        tk.Button(f_act, text="Edit NPC IDs...", command=self._on_edit_npc_ids, bg="#1e1e1e", fg="#fff").grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        tk.Button(f_act, text="Import From Legacy", command=self._on_import_legacy, bg="#294a7a", fg="#fff").grid(row=2, column=0, sticky="ew", padx=4, pady=8)
        tk.Button(f_act, text="Start", command=self._on_start, bg="#224e22", fg="#fff").grid(row=2, column=1, sticky="ew", padx=4, pady=8)
        tk.Button(f_act, text="Stop", command=self._on_stop, bg="#6b1a1a", fg="#fff").grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=8)

        top = tk.Frame(right, bg="#0b0b0b"); top.pack(fill="x", padx=10, pady=(12,6))
        self.lb_pos = tk.Label(top, text="Pos: -, -", fg="#9fe29f", bg="#0b0b0b"); self.lb_pos.pack(side="left", padx=8)
        self.lb_hp  = tk.Label(top, text="HP: -", fg="#ffbdbd", bg="#0b0b0b"); self.lb_hp.pack(side="left", padx=8)
        self.lb_npc = tk.Label(top, text="NPC: -", fg="#c3d9ff", bg="#0b0b0b"); self.lb_npc.pack(side="left", padx=8)
        self.lb_hook= tk.Label(top, text="Hook: off", fg="#ddd", bg="#0b0b0b"); self.lb_hook.pack(side="left", padx=8)

        log = ScrolledText(right, height=28, bg="#101010", fg="#e6e6e6", insertbackground="#eee", font=("Consolas", 10))
        log.pack(fill="both", expand=True, padx=10, pady=6)
        return log

    def _set_small_status(self, key, value):
        if key == "hp": self.lb_hp.config(text=f"HP: {value}")
        elif key == "pos": self.lb_pos.config(text=f"Pos: {value}")
        elif key == "npc": self.lb_npc.config(text=f"NPC: {value}")

    def _apply_from_ui(self):
        self.cfg["PROCESS_NAME"] = self.e_proc.get().strip()
        self.cfg["ATTACH_MODE"] = self.c_mode.get().strip()
        for k,e in self.addr_vars.items(): self.cfg[k] = e.get().strip()
        self.cfg["KEY_ATTACK"] = self.e_attack.get().strip().lower()
        self.cfg["KEY_HEAL"] = self.e_heal.get().strip().lower()
        self.cfg["HEAL_BURST_TAPS"] = int(self.e_burst.get().strip())
        self.cfg["HEAL_BURST_GAP"] = float(self.e_bgap.get().strip())
        self.cfg["HP_AUTOHEAL_THRESHOLD"] = int(self.e_hp_th.get().strip())
        self.cfg["HP_AUTOHEAL_TAPS"] = int(self.e_hp_tap.get().strip())
        self.cfg["HP_AUTOHEAL_GAP"] = float(self.e_hp_gap.get().strip())
        self.cfg["ATTACK_TPS"] = int(self.e_tps.get().strip())
        self.cfg["RETARGET_MS"] = int(self.e_ret.get().strip())
        self.cfg["MOVE_TILE_TIME"] = float(self.e_step.get().strip())
        self.cfg["PATROL_RADIUS"] = int(self.e_rad.get().strip())
        self.cfg["PATROL_IDLE_MS"] = int(self.e_pms.get().strip())

    def _on_attach(self):
        self._apply_from_ui(); self.save_config(self.cfg)
        ok = self.ctrl.attach()
        self.lb_hook.config(text="Hook: ready" if ok else "Hook: error", fg="#9fe29f" if ok else "#ff6e6e")

    def _on_save(self):
        self._apply_from_ui(); self.save_config(self.cfg); self.logger.write("Config saved.")

    def _on_set_home(self):
        self.ctrl.set_home_from_current(); self.save_config(self.cfg)

    def _on_load_walkable(self):
        p = filedialog.askopenfilename(title="Select walkable JSON", filetypes=[("JSON","*.json")])
        if not p: return
        if self.ctrl.load_walkable(p): self.save_config(self.cfg)

    def _on_edit_npc_ids(self):
        dlg = tk.Toplevel(self.root); dlg.title("Target NPC IDs"); dlg.configure(bg="#121212")
        tk.Label(dlg, text="Comma-separated NPC IDs", fg="#fff", bg="#121212").pack(anchor="w", padx=10, pady=8)
        e = tk.Text(dlg, width=36, height=6, bg="#0f0f0f", fg="#fff", insertbackground="#fff"); e.pack(fill="both", expand=True, padx=10, pady=8)
        cur = ",".join(str(x) for x in self.cfg.get("TARGET_NPC_IDS", [])); e.insert("1.0", cur)
        def save_ids():
            txt = e.get("1.0","end").strip(); arr = []
            for part in txt.split(","):
                part = part.strip()
                if not part: continue
                try: arr.append(int(part))
                except: pass
            self.cfg["TARGET_NPC_IDS"] = arr; self.save_config(self.cfg)
            self.logger.write(f"Saved {len(arr)} NPC IDs."); dlg.destroy()
        tk.Button(dlg, text="Save", command=save_ids, bg="#224e22", fg="#fff").pack(pady=8)

    def _on_import_legacy(self):
        path = self.cfg.get("LEGACY_SCRIPT_PATH","CeraBot v13.py")
        p = filedialog.askopenfilename(title="Select legacy script", initialfile=path, filetypes=[("Python","*.py")])
        if not p: return
        self.cfg["LEGACY_SCRIPT_PATH"] = p
        changed = self.ctrl.try_import_legacy_addresses()
        for k,e in self.addr_vars.items():
            e.delete(0, tk.END); e.insert(0, self.cfg.get(k,"0x0"))
        if changed:
            self.save_config(self.cfg); self.logger.write("Imported from legacy and updated config.")

    def _on_start(self):
        self._apply_from_ui(); self.save_config(self.cfg); self.ctrl.start()

    def _on_stop(self):
        self.ctrl.stop()

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(DEFAULT_CONFIG, f, indent=2)
        with open(CONFIG_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        updated = False
        for k,v in DEFAULT_CONFIG.items():
            if k not in data: data[k]=v; updated=True
        # First-run legacy import if addresses are 0x0
        if all(str(data.get(k,"0x0")).lower()=="0x0" for k in ("ADDR_EXP","ADDR_HP","ADDR_X","ADDR_Y","ADDR_CURRENT_NPC_ID")):
            path = data.get("LEGACY_SCRIPT_PATH","CeraBot v13.py")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    found = _extract_addresses(txt)
                    for k in ("ADDR_EXP","ADDR_EXP_HOOK","ADDR_HP","ADDR_X","ADDR_Y","ADDR_CURRENT_NPC_ID"):
                        if str(data.get(k,"0x0")).lower()=="0x0" and found.get(k,"0x0").lower()!="0x0":
                            data[k] = found[k]; updated=True
                except Exception:
                    pass
        if updated:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
        return data

    def save_config(self, data):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)

def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

import re, time

if __name__ == "__main__":
    main()
