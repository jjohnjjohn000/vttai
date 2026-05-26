import io
import pstats
import cProfile
import tkinter as tk

import sys
sys.path.append("/home/wa/VTTAI2")

from state_manager import load_state
from combat_tracker import CombatTracker

def main():
    root = tk.Tk()
    
    # We will simulate the app object marginally
    class DummyApp:
        pass
    app = DummyApp()
    app._combat_map_win = None
    app.msg_queue = None

    print("Profiling CombatTracker instantiation...")
    pr = cProfile.Profile()
    pr.enable()
    ct = CombatTracker(root, app=app, state_loader=load_state)
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumtime')
    ps.print_stats(30)
    print(s.getvalue())

if __name__ == "__main__":
    main()
