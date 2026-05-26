import io
import time
import cProfile
import pstats
import tkinter as tk
import sys
sys.path.append("/home/wa/VTTAI2")

from main import DnDApp

def stress_test():
    root = tk.Tk()
    root.geometry("800x600")
    
    app = DnDApp(root)
    # The app UI setup runs here, but it withdraws windows etc.
    # Let's wait a bit for it to settle
    root.update()

    print("Now profiling open_combat_tracker...")
    pr = cProfile.Profile()
    pr.enable()
    
    app.open_combat_tracker()
    
    # We must call update to process the events that open the window!
    root.update()
    
    pr.disable()
    
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumtime')
    ps.print_stats(30)
    
    with open("ct_profile.txt", "w") as f:
        f.write(s.getvalue())
    print("Wrote profile to ct_profile.txt")
    
    root.destroy()

if __name__ == "__main__":
    stress_test()
