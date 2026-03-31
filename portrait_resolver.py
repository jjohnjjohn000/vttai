"""
portrait_resolver.py
────────────────────
Résolveur de portraits pour les monstres / PNJ.

Parcourt images/portraits/**/* une seule fois au démarrage et construit un
index normalisé {clé: chemin_absolu}.  Les appels ultérieurs sont instantanés
(lookup dict O(1)).

Normalisation :
  • Suppression des accents (NFD → ASCII)
  • Minuscules
  • Suppression des suffixes numériques de type " 1", " 2" (noms de combat)
  • Suppression de la ponctuation non-alphanumérique (sauf espaces)

Stratégie de correspondance :
  1. Correspondance exacte (après normalisation)
  2. Correspondance exacte sans suffixe numérique  ("Goblin 2" → "Goblin")
  3. Correspondance par inclusion (la requête est contenue dans la clé ou vice-versa)
  4. Correspondance par mots initiaux (la clé commence par les N premiers mots de la requête)

Usage :
    from portrait_resolver import resolve_portrait
    path = resolve_portrait("Ancient Red Dragon")   # → "images/portraits/MM/Ancient Red Dragon.webp"
    path = resolve_portrait("Goblin 3")             # → "images/portraits/MM/Goblin.webp"
    path = resolve_portrait("Inconnu")              # → ""
"""

import os
import re
import unicodedata
from functools import lru_cache
from typing import Optional

# ─── Répertoires racines ─────────────────────────────────────────────────────
_BASE_DIR       = os.path.dirname(__file__)
_TOKENS_ROOT    = os.path.join(_BASE_DIR, "images", "tokens")
_PORTRAITS_ROOT = os.path.join(_BASE_DIR, "images", "portraits")

# Chaque dossier a son propre index :
#   _INDEX_TOKENS   → images/tokens/   (art circulaire pour le canvas)
#   _INDEX_PORTRAITS → images/portraits/ (portrait brut pour les tooltips)
# resolve_token_art()  cherche dans _INDEX_TOKENS puis _INDEX_PORTRAITS en fallback
# resolve_portrait()   cherche dans _INDEX_PORTRAITS uniquement
_SEARCH_ROOTS: list[str] = [_TOKENS_ROOT, _PORTRAITS_ROOT]  # pour is_known_image_path

# ─── Caches internes ─────────────────────────────────────────────────────────
# Index séparés pour les deux dossiers
_INDEX: dict[str, str] = {}          # index global (compat), non utilisé directement
_INDEX_TOKENS:    dict[str, str] = {}  # images/tokens/
_INDEX_PORTRAITS: dict[str, str] = {}  # images/portraits/
_INDEX_BUILT = False
_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}


# ─── Normalisation ────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """
    Normalise un nom de monstre pour la comparaison :
      1. Décomposition Unicode → suppression des diacritiques
      2. Minuscules
      3. Suppression du suffixe numérique final (" 1", " 2", etc.)
      4. Suppression des caractères non-alphanumériques (sauf espaces)
      5. Collapse des espaces multiples
    """
    # 1. Décomposition NFD puis suppression des combinaisons non-ASCII
    nfd = unicodedata.normalize("NFD", name)
    ascii_str = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    # 2. Minuscules
    s = ascii_str.lower()
    # 3. Suppression du suffixe numérique final ("gobelin 3" → "gobelin")
    s = re.sub(r"\s+\d+\s*$", "", s).strip()
    # 4. Suppression ponctuation (sauf espaces)
    s = re.sub(r"[^\w\s]", " ", s)
    # 5. Collapse espaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ─── Construction de l'index ──────────────────────────────────────────────────

def _build_index() -> None:
    """
    Construit deux index séparés :
      _INDEX_TOKENS    ← images/tokens/   (art de token avec cadre, pour le canvas)
      _INDEX_PORTRAITS ← images/portraits/ (portrait brut, pour les tooltips)
    Appelée une seule fois (lazy).
    """
    global _INDEX_BUILT
    if _INDEX_BUILT:
        return
    _INDEX_BUILT = True

    for target_dict, root, label in [
        (_INDEX_TOKENS,    _TOKENS_ROOT,    "tokens"),
        (_INDEX_PORTRAITS, _PORTRAITS_ROOT, "portraits"),
    ]:
        if not os.path.isdir(root):
            print(f"[PortraitResolver] Dossier introuvable (ignoré) : {root}")
            continue
        count = 0
        for dirpath, _dirs, files in os.walk(root):
            for fname in files:
                stem, ext = os.path.splitext(fname)
                if ext.lower() not in _EXTENSIONS:
                    continue
                key = _normalize(stem)
                full_path = os.path.join(dirpath, fname)
                if key not in target_dict:
                    target_dict[key] = full_path
                    count += 1
        print(f"[PortraitResolver] {count} {label} indexés depuis {root}")


# ─── Résolution ───────────────────────────────────────────────────────────────

def _search_index(index: dict, name: str, label: str) -> str:
    """
    Cherche `name` dans `index` avec les 4 stratégies de correspondance.
    Retourne le chemin absolu trouvé ou "".
    """
    query = _normalize(name)

    # ── 1. Correspondance exacte ──────────────────────────────────────────────
    if query in index:
        path = index[query]
        print(f"[PortraitResolver] ✓ exact [{label}] '{name}' → {path}")
        return path

    # ── 2. Nom sans suffixe numérique ────────────────────────────────────────
    raw_stripped = re.sub(r"\s+\d+\s*$", "", name.strip())
    if raw_stripped != name.strip():
        key2 = _normalize(raw_stripped)
        if key2 in index:
            path = index[key2]
            print(f"[PortraitResolver] ✓ stripped [{label}] '{raw_stripped}' → {path}")
            return path

    # ── 3. Correspondance par inclusion ──────────────────────────────────────
    for key, path in index.items():
        if key == query:
            continue
        if query in key or key in query:
            longer  = max(len(query), len(key))
            shorter = min(len(query), len(key))
            if shorter / longer >= 0.70:
                print(f"[PortraitResolver] ~ inclusion [{label}] '{name}' → {path}")
                return path

    # ── 4. Correspondance par mots initiaux ──────────────────────────────────
    query_words = query.split()
    if len(query_words) >= 2:
        prefix = " ".join(query_words[:2])
        for key, path in index.items():
            if key.startswith(prefix):
                print(f"[PortraitResolver] ~ prefix [{label}] '{name}' → {path}")
                return path

    return ""


def resolve_portrait(name: str) -> str:
    """
    Retourne le chemin absolu du **portrait brut** (images/portraits/) pour
    un affichage en tooltip.  Ne cherche PAS dans images/tokens/.

    Paramètres
    ----------
    name : str
        Nom du monstre/PNJ, suffixes numériques ignorés ("Gobelin 3" → "Gobelin").

    Retourne
    --------
    str  Chemin absolu, ou "" si introuvable.
    """
    if not name or not name.strip():
        return ""
    _build_index()
    result = _search_index(_INDEX_PORTRAITS, name, "portrait")
    if not result:
        print(f"[PortraitResolver] ✗ aucun portrait pour '{name}'")
    return result


def resolve_token_art(name: str) -> str:
    """
    Retourne le chemin absolu de l'**art de token** (images/tokens/) à afficher
    sur le canvas de combat.  Cherche d'abord dans images/tokens/, puis dans
    images/portraits/ en fallback si aucun art spécifique n'existe.

    Paramètres
    ----------
    name : str
        Nom du monstre/PNJ tel qu'il apparaît dans le tracker.

    Retourne
    --------
    str  Chemin absolu, ou "" si introuvable.
    """
    if not name or not name.strip():
        return ""
    _build_index()
    # 1. Art dédié dans images/tokens/
    result = _search_index(_INDEX_TOKENS, name, "token")
    if result:
        return result
    # 2. Fallback : portrait brut (sera rogné en cercle par _make_circular_portrait)
    result = _search_index(_INDEX_PORTRAITS, name, "portrait→fallback")
    if not result:
        print(f"[PortraitResolver] ✗ aucun art de token pour '{name}'")
    return result


def resolve_portrait_cached(name: str) -> str:
    """Variante mémoïsée de resolve_portrait (tooltip de survol)."""
    return _resolve_portrait_cached(name)


def resolve_token_art_cached(name: str) -> str:
    """Variante mémoïsée de resolve_token_art (rendu canvas)."""
    return _resolve_token_art_cached(name)


@lru_cache(maxsize=512)
def _resolve_portrait_cached(name: str) -> str:
    return resolve_portrait(name)


@lru_cache(maxsize=512)
def _resolve_token_art_cached(name: str) -> str:
    return resolve_token_art(name)


def invalidate_cache() -> None:
    """Vide les index et les caches LRU (si les dossiers images changent)."""
    global _INDEX_BUILT
    _INDEX_TOKENS.clear()
    _INDEX_PORTRAITS.clear()
    _INDEX_BUILT = False
    _resolve_portrait_cached.cache_clear()
    _resolve_token_art_cached.cache_clear()


def is_known_image_path(path: str) -> bool:
    """
    Retourne True si path provient d'un des dossiers d'images gérés
    (images/tokens/ ou images/portraits/).
    Remplace les vérifications _in_portraits() dispersées dans le code.
    """
    if not path:
        return False
    abs_path = os.path.abspath(path)
    for root in _SEARCH_ROOTS:
        if abs_path.startswith(os.path.abspath(root)):
            return True
    return False