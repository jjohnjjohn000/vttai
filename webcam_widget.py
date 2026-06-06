"""
webcam_widget.py

Widget Tkinter permettant d'afficher un flux webcam local,
avec sélection de la caméra (OpenCV).
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import platform
import os

try:
    import cv2
    from PIL import Image, ImageTk
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

def get_camera_name(index):
    """Tente de récupérer le nom matériel de la caméra selon l'OS."""
    if platform.system() == "Linux":
        try:
            path = f"/sys/class/video4linux/video{index}/name"
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
    return f"Caméra {index}"

def get_available_cameras(max_tested=4, active_index=None):
    """Teste les index de caméras et retourne une liste de tuples (index, nom)."""
    if not OPENCV_AVAILABLE:
        return []
    available = []
    for i in range(max_tested):
        # Récupère le nom (ex: "Logitech HD Pro Webcam C920")
        name = get_camera_name(i)
        display_text = f"{name} (Index {i})" if name != f"Caméra {i}" else f"Caméra {i}"
        
        if active_index is not None and i == active_index:
            available.append((i, display_text))
            continue
            
        cap = cv2.VideoCapture(i) 
        if cap is not None and cap.isOpened():
            available.append((i, display_text))
            cap.release()
    return available

class WebcamWidget(tk.Frame):
    """Affiche le flux vidéo avec un recadrage adaptatif."""
    def __init__(self, parent, camera_index=0, width=112, height=148, *args, **kwargs):
        super().__init__(parent, bg="#0d0d1a", width=width, height=height, *args, **kwargs)
        self.pack_propagate(False)
        
        self.width = width
        self.height = height
        self.camera_index = camera_index
        self.cap = None
        self.running = False
        self.imgtk = None
        
        if not OPENCV_AVAILABLE:
            tk.Label(self, text="OpenCV manquant\n(pip install opencv-python)", 
                     bg="#0d0d1a", fg="#ff4444", font=("Arial", 8)).pack(expand=True)
            return

        self.video_label = tk.Label(self, bg="#0d0d1a")
        self.video_label.pack(fill=tk.BOTH, expand=True)
        
        self.thread = None
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        if event.widget == self:
            if event.width > 20 and event.height > 20:
                self.width = event.width
                self.height = event.height

    def start(self):
        if not OPENCV_AVAILABLE or self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            pass

    def set_camera(self, index):
        if not OPENCV_AVAILABLE: return
        was_running = self.running
        self.stop()
        
        if was_running:
            self.after(150, lambda: self._start_with_new_camera(index))
        else:
            self.camera_index = index

    def _start_with_new_camera(self, index):
        self.camera_index = index
        self.start()

    def _capture_loop(self):
        self.cap = cv2.VideoCapture(self.camera_index)
        
        while self.running and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                h, w = frame.shape[:2]
                target_ratio = self.width / self.height
                frame_ratio = w / h
                
                if frame_ratio > target_ratio:
                    new_w = int(h * target_ratio)
                    offset = (w - new_w) // 2
                    cropped = frame[:, offset:offset+new_w]
                else:
                    new_h = int(w / target_ratio)
                    offset = (h - new_h) // 2
                    cropped = frame[offset:offset+new_h, :]
                    
                resized = cv2.resize(cropped, (self.width, self.height))
                cv2image = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                
                try:
                    img = Image.fromarray(cv2image)
                    if self.running:
                        self.video_label.after(0, self._update_image, img)
                except Exception:
                    break
                    
            time.sleep(0.033)
            
        if self.cap:
            self.cap.release()
            self.cap = None

    def _update_image(self, img):
        if self.running and self.video_label.winfo_exists():
            try:
                self.imgtk = ImageTk.PhotoImage(image=img)
                self.video_label.configure(image=self.imgtk)
            except Exception:
                pass


class WebcamSettingsDialog(tk.Toplevel):
    """Fenêtre de dialogue pour détecter et sélectionner la caméra."""
    def __init__(self, parent, current_index, on_select_callback):
        super().__init__(parent)
        self.title("⚙️ Paramètres Webcam")
        self.geometry("260x130")
        self.configure(bg="#1e1e2e")
        self.transient(parent)
        self.grab_set() 
        
        self.on_select = on_select_callback
        
        tk.Label(self, text="Sélectionnez une webcam :", bg="#1e1e2e", fg="white", font=("Arial", 10)).pack(pady=10)
        
        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(self, textvariable=self.combo_var, state="readonly", width=25)
        self.combo.pack(pady=5)
        
        if not OPENCV_AVAILABLE:
            self.combo.set("OpenCV non installé")
            return
            
        self.combo.set("Recherche en cours...")
        threading.Thread(target=self._find_cams, args=(current_index,), daemon=True).start()
        
        btn_frame = tk.Frame(self, bg="#1e1e2e")
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="✔ Appliquer", bg="#4CAF50", fg="white", relief="flat", font=("Arial", 9, "bold"), command=self._apply).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="✕ Annuler", bg="#F44336", fg="white", relief="flat", font=("Arial", 9, "bold"), command=self.destroy).pack(side=tk.LEFT, padx=5)
        
    def _find_cams(self, current_index):
        cams = get_available_cameras(active_index=current_index)
        
        # On crée un dictionnaire { "Nom (Index X)": X } pour retrouver l'index plus tard
        self._cam_map = {display_name: idx for idx, display_name in cams}
        
        values = list(self._cam_map.keys())
        if not values:
            values = ["Aucune caméra détectée"]
            
        def _update_ui():
            if not self.winfo_exists(): return
            self.combo.config(values=values)
            
            # On cherche le nom qui correspond à l'index actuellement utilisé
            current_name = next((name for name, idx in self._cam_map.items() if idx == current_index), None)
            
            if current_name in values:
                self.combo.set(current_name)
            elif values:
                self.combo.set(values[0])
                
        self.after(0, _update_ui)
        
    def _apply(self):
        val = self.combo_var.get()
        # On récupère l'index à partir du nom sélectionné grâce au dictionnaire
        if hasattr(self, '_cam_map') and val in self._cam_map:
            idx = self._cam_map[val]
            self.on_select(idx)
        self.destroy()