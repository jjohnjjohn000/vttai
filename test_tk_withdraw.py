import tkinter as tk
import time

root = tk.Tk()
root.geometry("200x200")
root.title("Main")

t0 = time.time()

def open_win(name, delay):
    def _do():
        w = tk.Toplevel(root)
        w.withdraw() # Avoid XWayland mapping freeze during creation
        w.title(name)
        w.geometry(f"200x200+{100+delay}+{100+delay}")
        w.configure(bg="#1e1e2e")
        w.after(100, w.deiconify) # Map asynchronously
        print(f"[{name}] Created at {time.time()-t0:.2f}")
    root.after(delay, _do)

open_win("Win1", 300)
open_win("Win2", 600)
open_win("Win3", 900)
open_win("Win4", 1200)

root.after(2000, lambda: print(f"[System] 2s passed, are windows visible?"))
# Avoid 3 min exit hang
import os
root.after(3000, lambda: os._exit(0))
root.mainloop()
