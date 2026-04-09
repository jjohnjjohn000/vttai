"""
chat_log_writer.py — Journal narratif de session.

Écrit dans logs/session_YYYYMMDD_HHMMSS.log un fichier texte brut
contenant chaque message narratif reçu par les agents joueurs :
  - Tous les messages de Kaelen / Elara / Thorne / Lyra
  - Tous les messages du MJ (Alexis_Le_MJ)
  - Les résultats de jets de dés visibles

Sont EXCLUS :
  - Les messages de rôle « tool » / « system »
  - Tout contenu commençant par [RÉSULTAT SYSTÈME]
  - Le contenu [SILENCE]
  - Les blocs [ACTION] mécaniques purs (conservé si accompagné de roleplay)

Le fichier est ouvert en mode append — on peut appeler log_message()
depuis n'importe quel thread sans risque de corruption grâce au verrou interne.

Usage :
    from chat_log_writer import ChatLogWriter
    _chat_log = ChatLogWriter()          # au démarrage de run_autogen
    _chat_log.log_message(name, content) # dans patched_receive
    _chat_log.close()                    # en fin de session (optionnel)
"""

import os
import threading
import datetime
import re


# Patterns de nettoyage
_SYSTEM_PREFIX   = re.compile(r'^\s*\[RÉSULTAT SYSTÈME', re.IGNORECASE)
_ACTION_ONLY     = re.compile(r'^\s*\[ACTION\]', re.IGNORECASE)
_SILENCE         = re.compile(r'^\s*\[SILENCE\]\s*$', re.IGNORECASE)

# Patterns pour le filtrage TTS
_ACTION_BLOCK_RE = re.compile(
    r'\[ACTION\].*?(?=\n\n|\[ACTION\]|$)',
    re.DOTALL | re.IGNORECASE,
)
_ERR_SYSTEM_RE = re.compile(r'\[Erreur système.*?\]', re.DOTALL | re.IGNORECASE)


def strip_mechanical_blocks(text: str) -> str:
    """Supprime les blocs mécaniques et [Erreur système] du texte avant envoi au TTS.
    Le roleplay narratif est conservé intégralement."""# Patterns pour le filtrage TTS
_ACTION_BLOCK_RE = re.compile(
    r'\[ACTION\].*?(?=\n\n|\[ACTION\]|$)',
    re.DOTALL | re.IGNORECASE,
)
_ERR_SYSTEM_RE = re.compile(r'\[Erreur système.*?\]', re.DOTALL | re.IGNORECASE)
_RULE_BLOCK_RE = re.compile(r'\[RÈGLES DU BLOC ACTION[^\]]*\](?:\s*•[^\n]*)*', re.IGNORECASE)


def strip_mechanical_blocks(text: str) -> str:
    """Supprime les blocs mécaniques et[Erreur système] du texte avant envoi au TTS.
    Le roleplay narratif est conservé intégralement."""
    text = _RULE_BLOCK_RE.sub('', text)
    text = _ACTION_BLOCK_RE.sub('', text)
    text = _ERR_SYSTEM_RE.sub('', text)
    return text.strip()

# Noms des agents joueurs + MJ
_NARRATIVE_SENDERS = frozenset({
    "Kaelen", "Elara", "Thorne", "Lyra", "Alexis_Le_MJ", "Alexis Le MJ",
})

# Séparateur de section dans le log
_HR = "─" * 72


class ChatLogWriter:
    """Écrit le journal narratif de session dans un fichier .log horodaté."""

    def __init__(self, log_dir: str = "logs"):
        self._lock    = threading.Lock()
        self._file    = None
        self._path    = ""
        self._log_dir = log_dir
        self._open()

    # ── Ouverture ──────────────────────────────────────────────────────────────

    def _open(self):
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = os.path.join(self._log_dir, f"session_{ts}.log")
            self._file = open(self._path, "a", encoding="utf-8", buffering=1)
            header = (
                f"{_HR}\n"
                f"  SESSION D&D — {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"  Fichier : {self._path}\n"
                f"{_HR}\n\n"
            )
            self._file.write(header)
            self._file.flush()
            print(f"[ChatLog] Journal ouvert → {self._path}")
        except Exception as e:
            print(f"[ChatLog] Impossible d'ouvrir le journal : {e}")
            self._file = None

    # ── Filtre ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _should_log(name: str, content: str) -> bool:
        """Retourne True si ce message doit apparaître dans le journal narratif."""
        if name not in _NARRATIVE_SENDERS:
            return False
        c = str(content or "").strip()
        if not c:
            return False
        if _SILENCE.match(c):
            return False
        if _SYSTEM_PREFIX.match(c):
            return False
        # Blocs purement mécaniques sans roleplay → exclus
        if _ACTION_ONLY.match(c) and len(c) < 300:
            lines = [l.strip() for l in c.splitlines() if l.strip()]
            if all(l.startswith(("[ACTION]", "Type", "Intention", "Règle", "Cible",
                                  "Action", "Bonus", "Mouvement", "Réaction",
                                  "⚔️")) for l in lines):
                return False
        return True

    # ── Écriture ───────────────────────────────────────────────────────────────

    def log_message(self, name: str, content: str):
        """Ajoute un message narratif au journal (thread-safe)."""
        if not self._should_log(name, content):
            return
        if self._file is None:
            return

        display_name = "Alexis (MJ)" if "Alexis" in name else name
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {display_name} :\n{content.strip()}\n\n"

        with self._lock:
            try:
                self._file.write(line)
                self._file.flush()
            except Exception as e:
                print(f"[ChatLog] Erreur écriture : {e}")

    def log_dice(self, character: str, formula: str, result: str):
        """Ajoute un résultat de jet de dés au journal."""
        if self._file is None:
            return
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] 🎲 {character} → {formula} : {result}\n\n"
        with self._lock:
            try:
                self._file.write(line)
                self._file.flush()
            except Exception as e:
                print(f"[ChatLog] Erreur dés : {e}")

    def log_section(self, label: str):
        """Insère un séparateur de section (ex: début de combat, fin de session)."""
        if self._file is None:
            return
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"\n{_HR}\n  [{ts}] {label}\n{_HR}\n\n"
        with self._lock:
            try:
                self._file.write(line)
                self._file.flush()
            except Exception as e:
                print(f"[ChatLog] Erreur section : {e}")

    # ── Fermeture ──────────────────────────────────────────────────────────────

    def close(self):
        with self._lock:
            if self._file:
                try:
                    self._file.write(f"\n{_HR}\n  FIN DE SESSION\n{_HR}\n")
                    self._file.close()
                except Exception:
                    pass
                finally:
                    self._file = None

    @property
    def path(self) -> str:
        return self._path