import tkinter as tk
print("Creating Tk...")
root = tk.Tk()
print("Tk created.")
root.after(0, lambda: print("Deferred inner!"))
root.after(1000, root.destroy)
print("Entering mainloop...")
root.mainloop()
print("Exited mainloop")
