import base64
import hashlib
import json
import os
import platform
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from io import BytesIO

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

API_URL = "https://licenses.example.com"  # Replace before building the distributable.
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OpenTxt")
STATE_PATH = os.path.join(APP_DIR, "state.json")
MODELS = {"OpenAI": ["gpt-4o-mini", "gpt-4o"], "Gemini": ["gemini-3.8-flash"]}

def machine_fingerprint():
    raw = f"{platform.system()}|{platform.machine()}|{platform.node()}|{uuid.getnode()}"
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                raw += "|" + winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            pass
    return hashlib.sha256(raw.encode()).hexdigest()

def post(path, payload, token=None, timeout=12):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API_URL.rstrip("/") + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: detail = json.loads(e.read().decode()).get("error", "Request rejected")
        except Exception: detail = "Request rejected"
        raise RuntimeError(detail) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError("Cannot reach the licensing service. Check your internet connection and try again.") from None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenTxt Assistant")
        self.geometry("760x620")
        self.minsize(620, 480)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.device = machine_fingerprint()
        self.state = self.load_state()
        self.screenshot = None
        self.busy = False
        self.show_workspace = False
        self.build()
        if self.state.get("lease"):
            self.after(100, self.revalidate)
        else:
            self.show_license()

    def load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as f: return json.load(f)
        except (OSError, ValueError): return {}

    def save_state(self):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(self.state, f)
        os.replace(tmp, STATE_PATH)

    def build(self):
        self.root = ttk.Frame(self, padding=20)
        self.root.pack(fill="both", expand=True)
        self.panel = ttk.Frame(self.root)
        self.panel.pack(fill="both", expand=True)

    def clear(self):
        for w in self.panel.winfo_children(): w.destroy()

    def show_license(self, error=""):
        self.clear()
        ttk.Label(self.panel, text="Activate your license", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(self.panel, text="Your license is checked online and is assigned to this computer.", wraplength=600).pack(anchor="w", pady=(0, 16))
        ttk.Label(self.panel, text="License key").pack(anchor="w")
        self.key = ttk.Entry(self.panel, width=52, show="•")
        self.key.pack(anchor="w", pady=6)
        self.status = ttk.Label(self.panel, text=error, foreground="#b42318", wraplength=600)
        self.status.pack(anchor="w", pady=4)
        ttk.Button(self.panel, text="Activate", command=self.activate).pack(anchor="w", pady=10)

    def activate(self):
        key = self.key.get().strip()
        if not key:
            self.status.config(text="Enter the license key provided by the administrator."); return
        self.status.config(text="Checking license…", foreground="#555")
        self.update_idletasks()
        try:
            result = post("/api/activate", {"key": key, "device": self.device})
            self.state.update(lease=result["lease"], expires_at=result["expires_at"])
            self.save_state()
            self.show_main()
            self.after(120000, self.heartbeat)
        except (RuntimeError, KeyError) as e:
            self.status.config(text=str(e), foreground="#b42318")

    def revalidate(self):
        try:
            result = post("/api/heartbeat", {"device": self.device}, self.state["lease"])
            self.state["expires_at"] = result["expires_at"]
            self.save_state()
            self.show_main()
            self.after(120000, self.heartbeat)
        except (RuntimeError, KeyError) as e:
            self.state.pop("lease", None); self.save_state(); self.show_license(str(e))

    def heartbeat(self):
        if not self.state.get("lease"): return
        try:
            result = post("/api/heartbeat", {"device": self.device}, self.state["lease"])
            self.state["expires_at"] = result["expires_at"]
            self.save_state()
            self.after(120000, self.heartbeat)
        except (RuntimeError, KeyError) as e:
            self.state.pop("lease", None)
            self.save_state()
            self.show_license(str(e))
    def show_main(self):
        self.clear()
        top = ttk.Frame(self.panel); top.pack(fill="x")
        ttk.Label(top, text="OpenTxt Assistant", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(top, text="Deactivate", command=self.deactivate).pack(side="right")
        exp = self.state.get("expires_at", "")
        ttk.Label(self.panel, text=f"Licensed until {exp} · This computer", foreground="#555").pack(anchor="w", pady=(3, 14))
        options = ttk.Frame(self.panel); options.pack(fill="x", pady=(0, 10))
        ttk.Label(options, text="Provider").pack(side="left")
        self.provider = tk.StringVar(value=self.state.get("provider", "OpenAI"))
        self.provider_box = ttk.Combobox(options, textvariable=self.provider, values=list(MODELS), state="readonly", width=12)
        self.provider_box.pack(side="left", padx=(7, 18)); self.provider_box.bind("<<ComboboxSelected>>", self.model_choices)
        ttk.Label(options, text="Model").pack(side="left")
        self.model = tk.StringVar(value=self.state.get("model", MODELS[self.provider.get()][0]))
        self.model_box = ttk.Combobox(options, textvariable=self.model, values=MODELS[self.provider.get()], state="readonly", width=22)
        self.model_box.pack(side="left", padx=7)
        ttk.Label(self.panel, text="Question or prompt").pack(anchor="w")
        self.input = tk.Text(self.panel, height=12, wrap="word", undo=True)
        self.input.pack(fill="both", expand=True, pady=(5, 8))
        bar = ttk.Frame(self.panel); bar.pack(fill="x")
        ttk.Button(bar, text="Attach image…", command=self.attach_image).pack(side="left")
        self.image_label = ttk.Label(bar, text="No image attached")
        self.image_label.pack(side="left", padx=8)
        self.ask_button = ttk.Button(bar, text="Ask", command=self.ask)
        self.ask_button.pack(side="right")
        ttk.Label(self.panel, text="Response (displayed for review; nothing is typed or clicked automatically)").pack(anchor="w", pady=(14, 0))
        self.output = tk.Text(self.panel, height=9, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True, pady=(5, 0))

    def model_choices(self, _event=None):
        vals = MODELS.get(self.provider.get(), MODELS["OpenAI"])
        self.model_box.configure(values=vals); self.model.set(vals[0])

    def attach_image(self):
        if ImageGrab is None:
            messagebox.showerror("Image support unavailable", "Pillow is required to capture or attach images."); return
        if messagebox.askyesno("Capture screen", "Capture the current screen now? The image will be sent with your next request."):
            try:
                img = ImageGrab.grab()
                b = BytesIO(); img.save(b, format="JPEG", quality=85)
                self.screenshot = base64.b64encode(b.getvalue()).decode("ascii")
                self.image_label.config(text="Screen capture attached")
            except Exception as e: messagebox.showerror("Capture failed", str(e))
        elif messagebox.askyesno("Choose image", "Would you like to select an image file instead?"):
            path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
            if path:
                try:
                    from PIL import Image
                    img = Image.open(path); img.thumbnail((2000, 2000))
                    b = BytesIO(); img.convert("RGB").save(b, format="JPEG", quality=85)
                    self.screenshot = base64.b64encode(b.getvalue()).decode("ascii")
                    self.image_label.config(text=os.path.basename(path))
                except Exception as e: messagebox.showerror("Image failed", str(e))

    def ask(self):
        prompt = self.input.get("1.0", "end-1c").strip()
        if not prompt and not self.screenshot:
            messagebox.showinfo("Add a prompt", "Enter text or attach an image first."); return
        self.state.update(provider=self.provider.get(), model=self.model.get()); self.save_state()

        self.ask_button.config(state="disabled", text="Working…")
        try:
            result = post("/api/ask", {"provider": self.provider.get().lower(), "model": self.model.get(), "prompt": prompt, "image": self.screenshot}, self.state["lease"], timeout=90)
            self.output.config(state="normal"); self.output.delete("1.0", "end"); self.output.insert("1.0", result.get("answer", "")); self.output.config(state="disabled")
        except RuntimeError as e:
            messagebox.showerror("Request failed", str(e))
            if "license" in str(e).lower() or "revoked" in str(e).lower(): self.revalidate()
        finally: self.ask_button.config(state="normal", text="Ask")

    def deactivate(self):
        try: post("/api/release", {"device": self.device}, self.state.get("lease"))
        except RuntimeError as e:
            if not messagebox.askyesno("Server unavailable", f"Could not contact the server: {e}\nClear the local license anyway?"): return
        self.state.pop("lease", None); self.state.pop("expires_at", None); self.save_state(); self.show_license()

    def close(self):
        self.destroy()

if __name__ == "__main__":
    App().mainloop()




