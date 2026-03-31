import tkinter as tk
import json

def _load_window_state():
    try:
        with open("window_state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

root = tk.Tk()
_win_state = _load_window_state()
print("State loaded size:", len(str(_win_state)))
root.after(0, lambda: print("Deferred init ran!"))
print("Entering mainloop...")
root.after(1000, root.destroy)
root.mainloop()
print("Exited mainloop")
