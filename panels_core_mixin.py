"""
panels_core_mixin.py

Contient la base de l'interface et les utilitaires partagés (X11 fixes, collapsible sections, etc.)
ainsi que l'ouverture du panneau de configuration.
"""

import tkinter as tk
from app_config import reload_app_config
from config_panel import open_config_panel as _open_cfg_panel


def _ghost_close(win, root=None):
    """X11 fix : withdraw + ghost au lieu de destroy().
    Évite ~centaines d'appels Tcl synchrones (orig_del) qui gèlent le clavier."""
    try: win.grab_release()
    except Exception: pass
    try: win.selection_clear()
    except Exception: pass
    try:
        win.unbind_all("<MouseWheel>")
        win.unbind_all("<Button-4>")
        win.unbind_all("<Button-5>")
    except Exception: pass
    win.withdraw()
    win.update_idletasks()
    _root = root or win.master
    if not hasattr(_root, "_ghosted_panels"):
        _root._ghosted_panels = []
    _root._ghosted_panels.append(win)


class PanelsCoreMixin:
    """Mixin de base contenant les utilitaires communs d'interface."""

    def _make_collapsible_section(
        self,
        parent,
        title: str,
        key: str,
        title_fg: str = "#c8b8ff",
        title_bg: str = "#12121f",
        section_bg: str | None = None,
    ):
        """
        Wrap a sidebar block in a collapsible section.
        """
        # ── Persistent state ──────────────────────────────────────────────
        if not hasattr(self, "_sidebar_states"):
            # Load saved states from win_state, fall back to all expanded
            self._sidebar_states: dict = self._win_state.get("_sidebar_states", {})

        is_collapsed: bool = self._sidebar_states.get(key, False)
        bg = section_bg or (parent.cget("bg") if hasattr(parent, "cget") else "#1e1e1e")

        # ── Outer container (keeps header + content together as one unit) ─
        outer = tk.Frame(parent, bg=bg)
        outer.pack(fill=tk.X, padx=0, pady=(2, 0))

        # ── Header row ────────────────────────────────────────────────────
        header = tk.Frame(outer, bg=title_bg, cursor="hand2")
        header.pack(fill=tk.X)

        # Toggle arrow — right-aligned
        arrow_var = tk.StringVar(value="▶" if is_collapsed else "▼")
        arrow_lbl = tk.Label(
            header, textvariable=arrow_var,
            bg=title_bg, fg=title_fg,
            font=("Arial", 7), padx=4, cursor="hand2",
        )
        arrow_lbl.pack(side=tk.RIGHT, pady=3)

        # Section title
        title_lbl = tk.Label(
            header, text=title,
            bg=title_bg, fg=title_fg,
            font=("Consolas", 9, "bold"), anchor="w",
        )
        title_lbl.pack(side=tk.LEFT, padx=6, pady=4, fill=tk.X, expand=True)

        # ── Content frame ─────────────────────────────────────────────────
        content = tk.Frame(outer, bg=bg)
        if not is_collapsed:
            content.pack(fill=tk.X, padx=0, pady=0)

        # Thin separator line below header (always visible, acts as border)
        sep = tk.Frame(outer, bg=title_bg, height=1)
        sep.pack(fill=tk.X)

        # ── Toggle logic ──────────────────────────────────────────────────
        def _toggle(event=None):
            if content.winfo_ismapped():
                content.pack_forget()
                arrow_var.set("▶")
                self._sidebar_states[key] = True
            else:
                content.pack(fill=tk.X, padx=0, pady=0)
                arrow_var.set("▼")
                self._sidebar_states[key] = False
            # Persist state
            try:
                self._win_state["_sidebar_states"] = self._sidebar_states
                from window_state import _save_window_state
                _save_window_state(self._win_state)
            except Exception:
                pass

        for w in (header, arrow_lbl, title_lbl):
            w.bind("<Button-1>", _toggle)

        return header, content

    def open_config_panel(self):
        """Ouvre le panneau de configuration général de l'application."""
        def _on_saved(new_cfg):
            """Callback appelé après sauvegarde : recharge la config et met les agents à jour live."""
            reload_app_config()
            # Rebind la touche PTT si elle a changé
            try:
                self._ptt_apply_hotkey()
            except Exception as _e:
                print(f"[Config] Erreur rebind PTT : {_e}")
            # Si les agents sont déjà initialisés, reconstruire leurs prompts immédiatement
            if hasattr(self, "_agents") and self._agents:
                try:
                    self._rebuild_agent_prompts()
                    
                    import autogen
                    import copy
                    
                    if getattr(self, "_combat_llm_active", False):
                        if hasattr(self, "_set_combat_llm"):
                            self._set_combat_llm(True)
                    else:
                        from state_manager import load_state
                        from app_config import get_agent_config, get_chronicler_config
                        from llm_config import build_llm_config, _default_model
                        
                        _char_state = load_state().get("characters", {})
                        
                        for name, agent in self._agents.items():
                            if name == "mj":
                                _chron_cfg = get_chronicler_config()
                                model = _chron_cfg.get("model", _default_model)
                                temp = _chron_cfg.get("temperature", 0.7)
                            else:
                                cs_char = _char_state.get(name, {})
                                model = (cs_char.get("llm_session_override", "")
                                         or cs_char.get("llm", "")
                                         or get_agent_config(name).get("model", "")
                                         or _default_model)
                                temp = get_agent_config(name).get("temperature", 0.7)
                                
                            new_cfg = build_llm_config(model, temperature=temp)
                            old_cfg = agent.llm_config or {}
                            if "tools" in old_cfg: new_cfg["tools"] = copy.deepcopy(old_cfg["tools"])
                            if "functions" in old_cfg: new_cfg["functions"] = copy.deepcopy(old_cfg["functions"])
                            agent.llm_config = new_cfg
                            agent.client = autogen.OpenAIWrapper(
                                **{k: v for k, v in new_cfg.items() if k not in ("functions", "tools")}
                            )

                    self.msg_queue.put({
                        "sender": "⚙️ Config",
                        "text":   "✅ Paramètres (y compris les LLMs) appliqués aux agents en ligne.",
                        "color":  "#aaaacc",
                    })
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"[Config] Erreur mise à jour agents : {e}")

        _open_cfg_panel(
            root       = self.root,
            win_state  = self._win_state,
            track_fn   = self._track_window,
            on_saved   = _on_saved,
        )