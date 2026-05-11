import tkinter as tk
import time

root = tk.Tk()
root.geometry("200x200")
root.title("Main")

t0 = time.time()

def open_win(name, delay):
    def _do():
        w = tk.Toplevel(root)
        w.title(name)
        w.geometry(f"200x200+{100+delay}+{100+delay}")
        w.configure(bg="#1e1e2e")
        print(f"[{name}] Opened at {time.time()-t0:.2f}")
    root.after(delay, _do)

open_win("Win1", 300)
open_win("Win2", 600)
open_win("Win3", 900)
open_win("Win4", 1200)

root.after(2000, lambda: print(f"[System] 2s passed, are windows visible?"))
root.after(4000, root.destroy)
root.mainloop()
print("Success")
