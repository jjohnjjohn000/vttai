import tkinter as tk
import json

root = tk.Tk()
print(f"Screen width: {root.winfo_screenwidth()}")
print(f"Screen height: {root.winfo_screenheight()}")

try:
    with open("window_state.json", "r", encoding="utf-8") as f:
        saved = json.load(f).get("main")
        w = min(saved['w'], root.winfo_screenwidth() - 20)
        h = min(saved['h'], root.winfo_screenheight() - 70)
        print(f"Clamped size expected: {w}x{h}")
        root.geometry(f"{w}x{h}")
        print("Applied size geometry!")
except Exception as e:
    print(e)

root.after(1000, root.destroy)
print("Entering mainloop")
root.mainloop()
print("Done")
