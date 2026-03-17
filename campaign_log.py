"""
campaign_log.py — Journal chronologique persistant de la campagne D&D.

Architecture
────────────
  campaign_log.json
    └─ "entries" : liste de blocs archivés, chacun contenant :
         id             → identifiant unique  "clog_001"
         session_range  → [session_debut, session_fin]
         date_archived  → ISO-8601
         summary        → texte du résumé narratif
         keywords       → liste de mots-clés pour la recherche sémantique
         characters     → personnages héros présents dans ce bloc
         locations      → lieux mentionnés
         importance     → 1 (mineur) | 2 (notable) | 3 (critique)
         auto_archived  → True si archivé automatiquement, False si manuel
         agent_reads    → dict {char_name: timestamp} — suivi par agent

Fonctionnement
──────────────
• Les résumés de session (session_logs) restent dans campaign_state.json
  pour les N sessions récentes (horizon glissant, RECENT_SESSION_WINDOW).
• Quand la fenêtre est dépassée, les plus anciens sont compactés en un
  bloc et migrés dans campaign_log.json via CampaignLog.archive_sessions().
• Chaque agent peut interroger le journal indépendamment via
  get_relevant_prompt(context_text, char_name) — seules les entrées
  dont les mots-clés matchent le contexte courant sont injectées,
  avec un compteur par agent pour savoir ce qu'il a déjà « lu ».
• Les agents reçoivent un bloc compact en tête de prompt (get_toc_prompt)
  et peuvent charger des entrées spécifiques à la demande.

API publique
────────────
  log = CampaignLog()                         # charge campaign_log.json
  log.archive_sessions(entries, session_nums) # archive des sessions
  log.add_entry(summary, keywords, ...)       # ajout manuel
  log.search(keywords)                        # recherche par mots-clés
  log.get_relevant_prompt(context, char_name) # bloc formaté pour un agent
  log.get_full_history_prompt()               # tout le journal (Chroniqueur)
  log.get_toc_prompt()                        # table des matières compacte

  # Helpers module-level (utilisés par state_manager)
  get_campaign_log()                          # singleton
  get_campaign_log_prompt(context, char_name, max_entries)
  get_full_campaign_history_prompt()
  auto_archive_if_needed(state)               # appelé par session_mixin
"""

import os
import json
import re
import threading
import datetime
import uuid
from typing import Optional

# ── Constantes ────────────────────────────────────────────────────────────────
CAMPAIGN_LOG_FILE      = "campaign_log.json"
RECENT_SESSION_WINDOW  = 4     # nombre de sessions à garder « récentes » dans
                                # campaign_state.json avant archivage automatique
ARCHIVE_BATCH_SIZE     = 3     # sessions compactées ensemble lors de l'archivage auto
MAX_ENTRIES_IN_PROMPT  = 3     # max entrées injectées dans un prompt d'agent
KEYWORD_MIN_LEN        = 4     # longueur min d'un mot-clé auto-extrait

# ── Mots-clés connus : personnages, lieux, objets, thèmes de la campagne ──────
# Enrichis automatiquement par la détection de noms propres au moment de l'archivage.
_KNOWN_KEYWORDS: set[str] = {
    # Personnages héros
    "Kaelen", "Elara", "Thorne", "Lyra",
    # PNJs récurrents
    "Strahd", "Ireena", "Ismark", "Ezmerelda", "Eva", "Rahadin",
    "Donavich", "Dori", "Dmitri", "Krezkov", "Vargas", "Vallakovich",
    # Lieux
    "Barovia", "Vallaki", "Krezk", "Argynvostholt", "Ravenloft",
    "Moulin", "Phare", "Manoir", "Taverne",
    # Thèmes
    "vampire", "serment", "cosmique", "Tatyana", "tarot", "prophétie",
    "Vistani", "revenants", "sorcières", "hags", "mort-vivant",
    "magie", "sort", "artefact", "quête", "combat", "sanctuaire",
    "malédiction", "lumière", "ténèbres",
}

_lock = threading.Lock()
_singleton: Optional["CampaignLog"] = None


# ── Utilitaires d'extraction de mots-clés ─────────────────────────────────────

def _extract_keywords(text: str) -> list[str]:
    """
    Extrait automatiquement des mots-clés pertinents depuis un texte narratif.

    Stratégie (ordre de priorité) :
      1. Mots connus dans _KNOWN_KEYWORDS (insensible à la casse)
      2. Noms propres (majuscule, ≥ 4 chars, pas en début de phrase)
      3. Mots significatifs fréquents (≥ 5 chars, > 2 occurrences)
    Résultat dédupliqué et trié par importance.
    """
    if not text:
        return []

    found: dict[str, int] = {}   # keyword → score

    text_lower = text.lower()
    words_raw  = re.findall(r"[A-Za-zÀ-ÿ''\-]+", text)

    # ── 1. Mots-clés connus ─────────────────────────────────────────────────
    for kw in _KNOWN_KEYWORDS:
        if kw.lower() in text_lower:
            found[kw] = found.get(kw, 0) + 3

    # ── 2. Noms propres heuristiques ─────────────────────────────────────────
    # Un nom propre est un mot commençant par une majuscule qui n'est PAS
    # en début de phrase (on filtre les mots après ». « ou en début de ligne).
    sentences = re.split(r"[.!?]\s+", text)
    for sentence in sentences:
        toks = re.findall(r"[A-Za-zÀ-ÿ''\-]+", sentence)
        for i, tok in enumerate(toks):
            if i == 0:  # premier mot de la phrase → skip (majuscule normale)
                continue
            if len(tok) < KEYWORD_MIN_LEN:
                continue
            if tok[0].isupper() and tok not in found:
                found[tok] = found.get(tok, 0) + 2

    # ── 3. Mots fréquents significatifs ─────────────────────────────────────
    STOP_FR = {
        "dans", "avec", "pour", "mais", "plus", "tout", "cette", "comme",
        "leur", "leurs", "elles", "dont", "être", "avoir", "fait", "après",
        "depuis", "entre", "sans", "sous", "vers", "chez", "lors", "pendant",
        "alors", "aussi", "même", "ainsi", "donc", "bien", "encore", "très",
        "tous", "toutes", "ceux", "celles", "celui", "celle", "autre", "autres",
        "quand", "avant", "après", "parce", "comment", "quête", "sont",
    }
    freq: dict[str, int] = {}
    for w in words_raw:
        wl = w.lower()
        if len(wl) >= 5 and wl not in STOP_FR:
            freq[wl] = freq.get(wl, 0) + 1
    for wl, cnt in freq.items():
        if cnt > 2:
            # Reconstruire avec la casse d'origine
            orig = next((w for w in words_raw if w.lower() == wl), wl)
            found[orig] = found.get(orig, 0) + cnt

    # ── Trier par score décroissant, dédupliquer, retourner ──────────────────
    sorted_kws = sorted(found.items(), key=lambda x: -x[1])
    seen_lower: set[str] = set()
    result: list[str] = []
    for kw, _ in sorted_kws:
        kl = kw.lower()
        if kl not in seen_lower:
            seen_lower.add(kl)
            result.append(kw)
        if len(result) >= 25:  # cap à 25 mots-clés par entrée
            break
    return result


def _score_entry(entry: dict, query_keywords: list[str]) -> int:
    """Score de pertinence d'une entrée par rapport à une liste de mots-clés."""
    entry_kws  = {k.lower() for k in entry.get("keywords", [])}
    entry_text = entry.get("summary", "").lower()
    score = 0
    for qk in query_keywords:
        qkl = qk.lower()
        if qkl in entry_kws:
            score += 3
        elif qkl in entry_text:
            score += 1
    # Bonus importance
    score += entry.get("importance", 1) - 1
    return score


# ── Classe principale ─────────────────────────────────────────────────────────

class CampaignLog:
    """
    Journal chronologique persistant de la campagne.

    Thread-safe : toutes les opérations d'écriture utilisent _lock.
    """

    def __init__(self, filepath: str = CAMPAIGN_LOG_FILE):
        self._filepath = filepath
        self._data: dict = self._load()

    # ── Persistance ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        """Charge campaign_log.json (crée un fichier vide si inexistant)."""
        with _lock:
            if os.path.exists(self._filepath):
                try:
                    with open(self._filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # Migration : garantir les champs top-level
                    data.setdefault("version", 1)
                    data.setdefault("entries", [])
                    data.setdefault("meta", {})
                    return data
                except Exception as e:
                    print(f"[CampaignLog] Erreur chargement : {e}")
            return {"version": 1, "entries": [], "meta": {}}

    def _save(self):
        """Sauvegarde le journal (doit être appelé avec _lock tenu)."""
        try:
            with open(self._filepath, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[CampaignLog] Erreur sauvegarde : {e}")

    def reload(self):
        """Recharge depuis le fichier (utile après une modif externe)."""
        self._data = self._load()

    # ── Propriétés ────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[dict]:
        return self._data.get("entries", [])

    def entry_count(self) -> int:
        return len(self.entries)

    # ── Ajout d'entrées ───────────────────────────────────────────────────────

    def add_entry(
        self,
        summary:       str,
        session_range: list[int],
        keywords:      Optional[list[str]] = None,
        characters:    Optional[list[str]] = None,
        locations:     Optional[list[str]] = None,
        importance:    int  = 2,
        auto_archived: bool = True,
        label:         str  = "",
    ) -> dict:
        """
        Ajoute une entrée au journal chronologique.

        summary       : texte narratif du bloc
        session_range : [session_début, session_fin]  ex: [1, 3]
        keywords      : liste de mots-clés — auto-extraits si None
        characters    : noms des PJ présents — auto-détectés si None
        locations     : lieux mentionnés — auto-détectés si None
        importance    : 1 (mineur) | 2 (notable) | 3 (critique)
        auto_archived : True = archivage automatique, False = manuel
        label         : titre court optionnel (ex: "Sessions 1-3 — Arrivée à Barovia")
        """
        if not summary or not summary.strip():
            raise ValueError("CampaignLog.add_entry : summary ne peut pas être vide.")

        # Auto-extraction si non fournis
        kws   = keywords   or _extract_keywords(summary)
        chars = characters or [n for n in ("Kaelen","Elara","Thorne","Lyra") if n in summary]
        locs  = locations  or []

        # Numéro d'entrée séquentiel
        existing_ids = [e.get("id", "") for e in self.entries]
        n = len(self.entries) + 1
        while f"clog_{n:03d}" in existing_ids:
            n += 1
        entry_id = f"clog_{n:03d}"

        entry = {
            "id":            entry_id,
            "label":         label or f"Sessions {session_range[0]}–{session_range[-1]}",
            "session_range": session_range,
            "date_archived": datetime.datetime.now().isoformat(timespec="seconds"),
            "summary":       summary.strip(),
            "keywords":      kws,
            "characters":    chars,
            "locations":     locs,
            "importance":    importance,
            "auto_archived": auto_archived,
            "agent_reads":   {},   # {char_name: ISO-timestamp dernière lecture}
        }

        with _lock:
            self._data["entries"].append(entry)
            self._save()

        print(f"[CampaignLog] Nouvelle entrée archivée : {entry_id} "
              f"(sessions {session_range}, {len(kws)} mots-clés)")
        return entry

    def archive_sessions(
        self,
        session_entries: list[dict],
        summary:         Optional[str] = None,
        label:           str = "",
        importance:      int = 2,
    ) -> Optional[dict]:
        """
        Compacte une liste de session_logs (format campaign_state) en une
        entrée de journal.

        session_entries : liste de dicts {"session": int, "date": str, "resume": str}
        summary         : texte pré-généré — si None, les résumés sont concaténés
        label           : titre court
        importance      : importance globale du bloc

        Retourne l'entrée créée, ou None si session_entries est vide.
        """
        if not session_entries:
            return None

        sessions_nums = [e["session"] for e in session_entries]
        session_range = [min(sessions_nums), max(sessions_nums)]

        if summary:
            full_text = summary
        else:
            parts = []
            for e in sorted(session_entries, key=lambda x: x["session"]):
                parts.append(
                    f"═══ Session {e['session']} ({e.get('date','?')}) ═══\n{e['resume']}"
                )
            full_text = "\n\n".join(parts)

        if not label:
            label = (
                f"Session {session_range[0]}"
                if session_range[0] == session_range[1]
                else f"Sessions {session_range[0]}–{session_range[1]}"
            )

        return self.add_entry(
            summary       = full_text,
            session_range = session_range,
            label         = label,
            importance    = importance,
            auto_archived = True,
        )

    # ── Mise à jour d'une entrée ──────────────────────────────────────────────

    def update_entry(self, entry_id: str, **fields) -> bool:
        """Modifie les champs d'une entrée existante. Retourne True si trouvée."""
        with _lock:
            for entry in self._data["entries"]:
                if entry["id"] == entry_id:
                    for k, v in fields.items():
                        entry[k] = v
                    self._save()
                    return True
        return False

    def mark_read(self, entry_id: str, char_name: str):
        """Enregistre que char_name a « lu » cette entrée (pour le suivi agent)."""
        with _lock:
            for entry in self._data["entries"]:
                if entry["id"] == entry_id:
                    entry.setdefault("agent_reads", {})[char_name] = \
                        datetime.datetime.now().isoformat(timespec="seconds")
                    self._save()
                    return

    # ── Recherche ─────────────────────────────────────────────────────────────

    def search(
        self,
        keywords:    list[str],
        min_score:   int = 1,
        max_results: int = 10,
        char_name:   Optional[str] = None,
    ) -> list[dict]:
        """
        Recherche les entrées dont les mots-clés matchent la requête.

        keywords    : mots-clés à chercher (insensible à la casse)
        min_score   : score minimum de pertinence
        max_results : nombre max de résultats
        char_name   : si fourni, trie les résultats non encore lus par cet agent
                      en premier

        Retourne une liste d'entrées triées par score décroissant.
        """
        if not keywords:
            return list(self.entries)[:max_results]

        scored = []
        for entry in self.entries:
            s = _score_entry(entry, keywords)
            if s >= min_score:
                scored.append((s, entry))

        # Tri : score desc, puis non-lu par char_name en premier
        def sort_key(item):
            score, entry = item
            unread_bonus = 0
            if char_name:
                reads = entry.get("agent_reads", {})
                if char_name not in reads:
                    unread_bonus = 100  # non lu → priorité
            return -(score + unread_bonus)

        scored.sort(key=sort_key)
        return [e for _, e in scored[:max_results]]

    def get_by_session(self, session_num: int) -> list[dict]:
        """Retourne toutes les entrées contenant la session donnée."""
        return [
            e for e in self.entries
            if e["session_range"][0] <= session_num <= e["session_range"][1]
        ]

    def get_by_id(self, entry_id: str) -> Optional[dict]:
        """Retourne une entrée par son id, ou None."""
        for e in self.entries:
            if e["id"] == entry_id:
                return e
        return None

    # ── Génération de blocs de prompt ────────────────────────────────────────

    def get_toc_prompt(self) -> str:
        """
        Table des matières compacte du journal — injectée en tête de prompt
        pour que les agents sachent ce qui est archivé sans tout charger.

        Format :
          --- CHRONIQUES DE LA CAMPAGNE (TABLE DES MATIÈRES) ---
          [clog_001] Sessions 1–3 · Arrivée à Barovia · mots-clés : Strahd, Ireena…
          [clog_002] Sessions 4–6 · La Route vers Vallaki · mots-clés : hags, moulin…
        """
        if not self.entries:
            return ""

        lines = [
            "\n\n╔══════════════════════════════════════════════════════",
            "║  CHRONIQUES DE LA CAMPAGNE — TABLE DES MATIÈRES",
            "║  (Archives complètes disponibles — tu peux demander un bloc spécifique)",
            "╠══════════════════════════════════════════════════════",
        ]
        for e in self.entries:
            imp_stars = "★" * e.get("importance", 2) + "☆" * (3 - e.get("importance", 2))
            kws_preview = ", ".join(e.get("keywords", [])[:8])
            lines.append(
                f"║  [{e['id']}] {imp_stars}  {e.get('label','?')}  ·  {kws_preview}"
            )
        lines.append("╚══════════════════════════════════════════════════════")
        return "\n".join(lines)

    def get_relevant_prompt(
        self,
        context_text: str = "",
        char_name:    str = "",
        max_entries:  int = MAX_ENTRIES_IN_PROMPT,
        mark_as_read: bool = True,
    ) -> str:
        """
        Génère un bloc de prompt contenant les entrées du journal les plus
        pertinentes pour le contexte courant.

        context_text : texte de la scène / dernier message — sert à extraire
                       les mots-clés de recherche automatiquement
        char_name    : nom du personnage qui reçoit ce prompt (pour le suivi)
        max_entries  : nombre max d'entrées à injecter
        mark_as_read : si True, marque les entrées sélectionnées comme lues

        Si aucun match pertinent, retourne une chaîne vide.
        """
        if not self.entries:
            return ""

        # Extraire les mots-clés du contexte courant
        query_kws = _extract_keywords(context_text) if context_text else []

        if query_kws:
            relevant = self.search(
                keywords    = query_kws,
                min_score   = 1,
                max_results = max_entries,
                char_name   = char_name,
            )
        else:
            # Sans contexte : renvoyer les N plus importantes
            relevant = sorted(
                self.entries,
                key=lambda e: -e.get("importance", 1),
            )[:max_entries]

        if not relevant:
            return ""

        if mark_as_read and char_name:
            for e in relevant:
                self.mark_read(e["id"], char_name)

        lines = [
            "\n\n╔══════════════════════════════════════════════════════",
            "║  MÉMOIRE LONGUE DURÉE — CHRONIQUES ARCHIVÉES",
            "║  [RÉFÉRENCE SILENCIEUSE — ne pas réciter, enrichir les réponses]",
            "╠══════════════════════════════════════════════════════",
        ]
        for e in relevant:
            imp_stars = "★" * e.get("importance", 2) + "☆" * (3 - e.get("importance", 2))
            lines.append(f"║")
            lines.append(f"║  {imp_stars}  {e.get('label','?')}  [{e['id']}]")
            lines.append(f"║  Mots-clés : {', '.join(e.get('keywords', [])[:10])}")
            lines.append(f"║  ─────────────────────────────────────────────────")
            # Insérer le texte avec préfixe "║  " sur chaque ligne
            for line in e["summary"].splitlines():
                lines.append(f"║  {line}")
        lines.append("╚══════════════════════════════════════════════════════")
        return "\n".join(lines)

    def get_full_history_prompt(self) -> str:
        """
        Retourne tout le journal archivé — utilisé par le Chroniqueur IA
        pour avoir la vue complète de la campagne lors de la génération de résumé.
        """
        if not self.entries:
            return ""

        lines = ["\n\n=== JOURNAL COMPLET DE LA CAMPAGNE ==="]
        for e in self.entries:
            lines.append(f"\n{'─'*60}")
            lines.append(f"  {e.get('label','?')}  (sessions {e['session_range'][0]}–{e['session_range'][1]})")
            lines.append(f"  Archivé le : {e.get('date_archived','?')}")
            lines.append(f"  Mots-clés  : {', '.join(e.get('keywords', []))}")
            lines.append(f"{'─'*60}")
            lines.append(e["summary"])
        lines.append(f"\n{'═'*60}")
        return "\n".join(lines)

    # ── Diagnostic ────────────────────────────────────────────────────────────

    def summary_stats(self) -> dict:
        """Retourne des statistiques de base sur le journal."""
        entries = self.entries
        if not entries:
            return {"count": 0}
        all_sessions = []
        for e in entries:
            r = e.get("session_range", [0, 0])
            all_sessions.extend(range(r[0], r[1] + 1))
        return {
            "count":            len(entries),
            "sessions_covered": sorted(set(all_sessions)),
            "total_chars":      sum(len(e.get("summary","")) for e in entries),
            "keywords_unique":  len({k for e in entries for k in e.get("keywords",[])}),
        }


# ── Singleton module-level ────────────────────────────────────────────────────

def get_campaign_log() -> CampaignLog:
    """Retourne le singleton CampaignLog (chargé depuis CAMPAIGN_LOG_FILE)."""
    global _singleton
    if _singleton is None:
        _singleton = CampaignLog()
    return _singleton


# ── Helpers pour injection dans les prompts ───────────────────────────────────

def get_campaign_log_prompt(
    context_text: str = "",
    char_name:    str = "",
    max_entries:  int = MAX_ENTRIES_IN_PROMPT,
) -> str:
    """
    Raccourci : retourne le bloc de mémoire longue durée pertinent pour un agent.
    À appeler depuis autogen_engine.py / state_manager.py.

    Si le journal est vide ou sans correspondance, retourne "".
    """
    return get_campaign_log().get_relevant_prompt(
        context_text = context_text,
        char_name    = char_name,
        max_entries  = max_entries,
    )


def get_campaign_toc_prompt() -> str:
    """Retourne la table des matières compacte du journal archivé."""
    return get_campaign_log().get_toc_prompt()


def get_full_campaign_history_prompt() -> str:
    """Retourne tout le journal archivé (pour le Chroniqueur IA)."""
    return get_campaign_log().get_full_history_prompt()


# ── Auto-archivage ────────────────────────────────────────────────────────────

def auto_archive_if_needed(
    state:         dict,
    save_state_fn: callable,
    summary_fn:    Optional[callable] = None,
) -> bool:
    """
    Vérifie si les session_logs de campaign_state dépassent RECENT_SESSION_WINDOW.
    Si oui, archive les plus anciens en bloc dans campaign_log.json et les retire
    de session_logs.

    state         : dict du campaign_state chargé
    save_state_fn : fonction save_state(state) pour persister après modification
    summary_fn    : callable optionnel(sessions_list) → str pour générer un résumé
                    LLM du bloc au lieu de la concaténation brute
    
    Retourne True si un archivage a eu lieu.
    """
    logs = state.get("session_logs", [])

    if len(logs) <= RECENT_SESSION_WINDOW:
        return False

    # Nombre de sessions à archiver : tout ce qui dépasse la fenêtre,
    # arrondi au ARCHIVE_BATCH_SIZE le plus proche
    n_to_archive = len(logs) - RECENT_SESSION_WINDOW
    # Arrondir par batch
    n_to_archive = max(ARCHIVE_BATCH_SIZE,
                       (n_to_archive // ARCHIVE_BATCH_SIZE) * ARCHIVE_BATCH_SIZE)
    n_to_archive = min(n_to_archive, len(logs))  # sécurité

    to_archive = logs[:n_to_archive]
    remaining  = logs[n_to_archive:]

    # Optionnel : générer un résumé LLM du bloc
    summary_text = None
    if summary_fn:
        try:
            summary_text = summary_fn(to_archive)
        except Exception as e:
            print(f"[CampaignLog] Erreur génération résumé LLM pour archivage : {e}")

    # Archiver
    campaign_log = get_campaign_log()
    entry = campaign_log.archive_sessions(
        session_entries = to_archive,
        summary         = summary_text,
        importance      = 2,
    )

    # Mettre à jour le state
    state["session_logs"] = remaining
    save_state_fn(state)

    if entry:
        print(f"[CampaignLog] Auto-archivage : {len(to_archive)} sessions → {entry['id']}")

    return True


def archive_single_session(
    session_entry: dict,
    summary_fn:    Optional[callable] = None,
    importance:    int = 2,
) -> Optional[dict]:
    """
    Archive une session unique directement (utile pour le premier archivage manuel).
    
    session_entry : dict {"session": int, "date": str, "resume": str}
    summary_fn    : callable optionnel() → str pour un résumé LLM de cette session
    """
    summary_text = None
    if summary_fn:
        try:
            summary_text = summary_fn([session_entry])
        except Exception as e:
            print(f"[CampaignLog] Erreur génération résumé LLM session unique : {e}")

    campaign_log = get_campaign_log()
    return campaign_log.archive_sessions(
        session_entries = [session_entry],
        summary         = summary_text,
        importance      = importance,
    )
