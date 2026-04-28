"""
engine_spell_mj.py — Helpers sorts, parseur directives MJ, patterns PNJ/action/sort.

Exporte :
  get_prepared_spell_names  — liste des sorts préparés d'un PJ
  extract_spell_name_llm    — identifie le nom canonique du sort via LLM léger
  is_spell_prepared         — vérifie qu'un sort est bien préparé
  PARSER_SYSTEM             — prompt système du parseur JSON de directives MJ
  DIRECTIVE_PREFILTER       — regex pré-filtre (évite appels LLM inutiles)
  parse_mj_directives       — extrait directives mécaniques d'un message MJ
  build_pnj_patterns        — construit les regex PNJ depuis la liste de PNJ noms
  ACTION_PATTERN            — regex blocs [ACTION] multiligne
  SORT_PATTERN              — regex balise [SORT: ... | Niveau: X | Cible: Y]
  DAMAGE_PATTERN            — regex multi-forme dégâts sur PJ
  PC_NAME_RE                — regex noms joueurs
"""

import re as _re
import threading as _threading_spell

from state_manager import load_state
from app_config    import get_chronicler_config
from llm_config    import build_llm_config, _default_model


# ─── Client LLM partagé pour parse_mj_directives ─────────────────────────────
# Créer un httpx.Client + openai.OpenAI par message MJ provoque une création de
# SSLContext à chaque appel.  ssl.create_default_context() n'est pas thread-safe
# sous Python 3.10 / OpenSSL 3.x → segfault si deux threads l'appellent en
# même temps.  On résout le problème en construisant ces objets une seule fois
# (lazy-init protégé par un lock) et en les réutilisant pour tous les appels.
_PARSER_CLIENT_LOCK = _threading_spell.Lock()
_parser_openai_client = None   # openai.OpenAI singleton (lazy)
_parser_cfg0          = None   # config dict capturé à la première init


# ─── Regex statiques (indépendants de la liste PNJ) ──────────────────────────

# Détecte [SORT: NomDuSort | Niveau: X | Cible: Y]
SORT_PATTERN = _re.compile(
    r'\[SORT\s*:\s*(?P<nom>[^|\]]+?)\s*\|\s*Niveau\s*:\s*(?P<niveau>\d)\s*(?:\|\s*Cible\s*:\s*(?P<cible>[^\]]*?))?\s*\]',
    _re.IGNORECASE
)

# Détecte un ou plusieurs blocs [ACTION] (multiligne).
# Capture optionnelle du champ Type et règle multiligne (Extra Attack).
ACTION_PATTERN = _re.compile(
    r'(?:\*\*)?\[\s*ACTION\s*\](?:\*\*)?\s*'
    r'(?:Type|Action|Type d\'action)\s*:\s*(?P<type>[^\n]*)\s*'
    r'Intention\s*:\s*(?P<intention>.*?)\s*'
    r'R[eéè]gle\s*(?:5e)?\s*:\s*(?P<regle>.*?)\s*'
    r'(?:Cible\s*:\s*(?P<cible>.*?))?(?=\n\s*\n|\[ACTION\]|$)',
    _re.IGNORECASE | _re.DOTALL
)

# Détecte les annonces de dégâts MJ sur un héros joueur (multi-formes)
DAMAGE_PATTERN = _re.compile(
    r'(?:'
    r'(?P<tgt_a>Kaelen|Elara|Thorne|Lyra)\s+(?:prend|subit|re[çc]oit|perd)\s+(?P<dmg_a>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
    r'|'
    r'(?:inflige|cause|fait|d[eé]al)\s+(?P<dmg_b>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+[àa]\s+(?P<tgt_b>Kaelen|Elara|Thorne|Lyra)'
    r'|'
    r'-\s*(?P<dmg_c>\d+)\s*(?:PV|pv|hp|d[eé]g[aâ]ts?)\s*(?:pour|[àa])?\s*(?P<tgt_c>Kaelen|Elara|Thorne|Lyra)'
    r'|'
    r'tu\s+(?:te\s+)?(?:prend[s]?|subis|re[çc]ois|perds?)\s+(?P<dmg_d>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
    r'|'
    r'(?:lui|leur|vous)\s+(?:inflige|cause|fait|deal)\s+(?P<dmg_e>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)'
    r'|'
    r'(?P<dmg_f>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+[àa]\s+(?P<tgt_f>Kaelen|Elara|Thorne|Lyra)'
    r'|'
    r'(?P<dmg_g>\d+)\s*(?:d[eé]g[aâ]ts?|points?\s*de\s*d[eé]g[aâ]ts?|PV|pv|hp)\s+pour\s+(?P<tgt_g>Kaelen|Elara|Thorne|Lyra)'
    r')',
    _re.IGNORECASE,
)

PC_NAME_RE = _re.compile(r'\b(Kaelen|Elara|Thorne|Lyra)\b', _re.IGNORECASE)

# Pré-filtre : on n'appelle le LLM que si le message du MJ contient
# des indicateurs de directive mécanique (chiffres, mots-clés).
DIRECTIVE_PREFILTER = _re.compile(
    r'\d'
    r'|(?:d[eé]g[aâ]t|pv\b|hp\b|soin|jet|roll'
    r'|sauvegarde|save\b|attaque|touche|rate)',
    _re.IGNORECASE,
)

# Prompt système du parseur LLM de directives MJ
PARSER_SYSTEM = (
    "Tu es un parseur JSON pour D&D 5e. "
    "Analyse le message du MJ et extrais UNIQUEMENT les directives mécaniques "
    "destinées aux personnages joueurs (Kaelen, Elara, Thorne, Lyra).\n"
    "Réponds UNIQUEMENT avec un tableau JSON valide — rien d'autre, "
    "aucun texte avant ni après, aucun markdown.\n\n"
    "Format de chaque directive :\n"
    '{"action":"degats"|"soin"|"jet_sauvegarde"|"jet_competence"|"jet_attaque","cible":"Kaelen"|"Elara"|"Thorne"|"Lyra"|"tous","montant":<int>,"type_degat":<str>,"de":<str>,"bonus":<int>,"dc":<int>,"caracteristique":<str>}\n\n'
    "Champs obligatoires selon l'action :\n"
    "  degats  → cible, montant  (type_degat optionnel)\n"
    "  soin    → cible, montant\n"
    "  jet_sauvegarde → cible, caracteristique, dc\n"
    "  jet_competence → cible, caracteristique  (dc optionnel)\n"
    "  jet_attaque    → cible, de, bonus\n\n"
    "Règles d'inférence de la cible :\n"
    "  - Si un seul PJ est mentionné dans le message (ou via pronom lui/toi), c'est la cible.\n"
    "  - Si le MJ dit 'vous' / 'tout le monde', cible = 'tous'.\n"
    "  - Si aucun PJ identifiable, omets la directive.\n\n"
    "Exemples :\n"
    '  "Thorne prend 7 dégâts de force." → [{"action":"degats","cible":"Thorne","montant":7,"type_degat":"force"}]\n'
    '  "Le fantôme attaque Thorne et lui fait 7 dégâts de force." → [{"action":"degats","cible":"Thorne","montant":7,"type_degat":"force"}]\n'
    '  "Thorne enlève-toi 3 PV." → [{"action":"degats","cible":"Thorne","montant":3}]\n'
    '  "Lyra soigne Kaelen de 14 PV." → [{"action":"soin","cible":"Kaelen","montant":14}]\n'
    '  "Tout le monde fait un jet de Sagesse DC 13." → [{"action":"jet_sauvegarde","cible":"tous","caracteristique":"sagesse","dc":13}]\n'
    '  "Le dragon rugit." → []\n'
)


# ─── Helpers sorts ────────────────────────────────────────────────────────────

def get_prepared_spell_names(char_name: str) -> list:
    """Retourne la liste des noms de sorts préparés pour l'affichage (inclut domaine/serment)."""
    try:
        state = load_state()
        char_data = state.get("characters", {}).get(char_name, {})
        prepared = list(char_data.get("spells_prepared", []))
        
        c_class = char_data.get("class", "")
        c_sub = char_data.get("subclass", "")
        c_level = char_data.get("level", 1)
        
        if c_class and c_sub:
            from class_data import get_subclass_spells
            domain_spells = get_subclass_spells(c_class, c_sub, c_level)
            for ds in domain_spells:
                if ds not in prepared:
                    prepared.append(ds)
                    
        return prepared
    except Exception:
        return []


def extract_spell_name_llm(intention: str, char_name: str) -> str:
    """
    Utilise un LLM léger pour identifier le nom canonique du sort lancé.

    Stratégie LLM + vérification codée :
      - Le LLM reçoit le texte brut de l'intention ET la liste des sorts préparés.
      - Il retourne UNIQUEMENT le nom exact du sort tel qu'il apparaît dans la liste,
        ou "AUCUN" s'il ne reconnaît aucun sort.
      - La vérification reste entièrement codée dans is_spell_prepared.
    """
    prepared = get_prepared_spell_names(char_name)
    if not prepared:
        return intention.strip()[:50]

    spell_list = ", ".join(prepared)
    prompt = (
        f"Tu es un assistant de règles D&D 5e. "
        f"Voici la liste des sorts préparés de {char_name} : {spell_list}.\n\n"
        f"Dans ce texte d'action : \"{intention}\"\n\n"
        f"Quel sort de la liste est lancé ? "
        f"Réponds UNIQUEMENT avec le nom exact du sort tel qu'il apparaît dans la liste. "
        f"Si aucun sort de la liste n'est mentionné, réponds : AUCUN. "
        f"Aucune explication, aucune ponctuation supplémentaire."
    )
    try:
        import autogen as _ag
        _chron = get_chronicler_config()
        _model = _chron.get("model", _default_model)
        _cfg   = build_llm_config(_model, temperature=0.0)
        client = _ag.OpenAIWrapper(config_list=_cfg["config_list"])
        response = client.create(messages=[{"role": "user", "content": prompt}])
        raw = (response.choices[0].message.content or "").strip()
        
        # Nettoyage des balises de réflexion (modèles reasoning type Gemma/DeepSeek)
        raw = _re.sub(r'<(thought|think)>.*?</\1>\s*', '', raw, flags=_re.IGNORECASE | _re.DOTALL)
        
        raw = _re.sub(r"^```[a-z]*\s*", "", raw)
        raw = _re.sub(r"\s*```$", "", raw.strip()).strip()
        if raw.upper() == "AUCUN" or not raw:
            return ""
        return raw
    except Exception as e:
        print(f"[SpellExtract] Erreur LLM : {e}")
        return intention.strip()[:50]


def is_spell_prepared(char_name: str, spell_name: str) -> bool:
    """
    Retourne True si spell_name correspond à un sort préparé du personnage.

    Stratégie de correspondance (par ordre de priorité) :
      1. Égalité exacte après normalisation Unicode + lowercase.
      2. Le nom du JSON est contenu dans la saisie (substring).
      3. La saisie est contenue dans le nom du JSON.

    Les cantrips (level=0) sont TOUJOURS autorisés.
    Si le personnage n'a pas de liste de sorts définie → non restrictif (True).
    """
    import unicodedata as _ud

    def _norm(s: str) -> str:
        nfkd = _ud.normalize("NFKD", s)
        ascii_str = "".join(c for c in nfkd if not _ud.combining(c))
        return " ".join(ascii_str.lower().split())

    try:
        state = load_state()
        char_data = state.get("characters", {}).get(char_name, {})
        
        # Le champ brut de la sauvegarde
        raw_spells_prepared = char_data.get("spells_prepared", None)
        if raw_spells_prepared is None:
            return True   # champ absent → pas de restriction

        # Construit la liste (inclus les sorts de domaine)
        spell_names = get_prepared_spell_names(char_name)

        needle = _norm(spell_name.strip())
        if not needle:
            return True

        # Essayer de récupérer le niveau via spell_data (cantrips toujours OK)
        try:
            from spell_data import get_spell as _gs, load_spells as _ls
            _ls()
        except Exception:
            _gs = lambda n: None

        for name in spell_names:
            sp_name_n = _norm(name)
            if not sp_name_n:
                continue
            match = (
                sp_name_n == needle
                or (len(sp_name_n) >= 5 and sp_name_n in needle)
                or (len(needle) >= 5 and needle in sp_name_n)
            )
            if match:
                sp_data = _gs(name)
                if sp_data and int(sp_data.get("level", 1)) == 0:
                    return True
                return True

        return False
    except Exception:
        return True   # en cas d'erreur, ne pas bloquer


# ─── Classes avec Ritual Casting (D&D 5e) ─────────────────────────────────────
_RITUAL_CASTER_CLASSES = frozenset({"wizard", "cleric", "druid", "bard"})


def can_ritual_cast(char_name: str, spell_name: str) -> bool:
    """
    Retourne True si le personnage peut lancer le sort en tant que rituel.

    Conditions D&D 5e :
      1. La classe du personnage possède le trait Ritual Casting.
      2. Le sort est marqué comme ritual dans les données de sorts.

    Gère les noms français ET anglais grâce à plusieurs stratégies de lookup.
    """
    try:
        state = load_state()
        char_data = state.get("characters", {}).get(char_name, {})
        char_class = char_data.get("class", "").lower().strip()
        if char_class not in _RITUAL_CASTER_CLASSES:
            return False

        from spell_data import get_spell as _gs, load_spells as _ls, _SPELL_DATA
        _ls()

        # Stratégie 1 : lookup direct (nom anglais)
        sp = _gs(spell_name)
        if sp and sp.get("ritual", False):
            return True

        # Stratégie 2 : match partiel sur tous les sorts (nom FR → données EN)
        q = spell_name.lower().strip()
        for _key, _sp in _SPELL_DATA.items():
            if not _sp.get("ritual", False):
                continue
            # Match si le nom FR contient le mot-clé anglais ou vice-versa
            en_name = _sp.get("name", "").lower()
            if q in en_name or en_name in q:
                return True
            # Match partiel : premiers mots communs (ex: "detect" dans "détect")
            q_words = q.split()
            en_words = en_name.split()
            if any(qw[:4] == ew[:4] for qw in q_words for ew in en_words if len(qw) >= 4 and len(ew) >= 4):
                return True

        # Stratégie 3 : vérifier la description du sort préparé dans campaign_state
        #               (la description FR contient souvent "ritual" ou "rituel")
        for prep_spell in char_data.get("spells", []):
            prep_name = prep_spell.get("name", "")
            if prep_name.lower().strip() == q:
                desc = prep_spell.get("description", "").lower()
                if "ritual" in desc or "rituel" in desc:
                    return True
                break

        return False
    except Exception as e:
        print(f"[RitualCast] Erreur : {e}")
        return False


def validate_bonus_action_rule(char_name: str, spell_name: str, spell_level: int, cast_time_raw: list, turn_spells: list) -> tuple[bool, str]:
    """
    Vérifie la règle des actions bonus (PHB 5e) :
    'You can't cast another spell during the same turn, except for a cantrip with a casting time of 1 action.'
    """
    # ── Vérifier si on est en combat ──
    try:
        from combat_tracker_state import COMBAT_STATE
        if not COMBAT_STATE.get("active", False):
            # Hors combat, le "tour" n'existe pas vraiment (6 secondes), 
            # on lève la restriction pour le roleplay et l'exploration.
            return True, ""
    except ImportError:
        pass

    if not cast_time_raw:
        return True, ""
    
    # Extrait l'unité du temps de lancement (ex: 'action', 'bonus', 'reaction')
    unit = cast_time_raw[0].get("unit", "")
    
    is_ba = (unit == "bonus")
    has_ba = any(s.get("cast_time_unit") == "bonus" for s in turn_spells)
    
    if is_ba:
        for s in turn_spells:
            if s.get("level", 0) > 0 or s.get("cast_time_unit") != "action":
                return False, (
                    f"[RÈGLE 5e] Vous avez déjà lancé le sort {s.get('name')} ce tour-ci. "
                    f"Si vous lancez un sort en Action Bonus, tous vos autres sorts de ce tour MUST être des tours de magie coûtant 1 action classique."
                )
    else:
        if has_ba:
            if spell_level > 0 or unit != "action":
                return False, (
                    f"[RÈGLE 5e] Vous avez utilisé un sort en Action Bonus ce tour-ci. "
                    f"Vous ne pouvez plus lancer d'autre sort à ce tour, à l'exception d'un tour de magie coûtant 1 action."
                )
                
    return True, ""


# Unités de temps d'incantation compatibles avec le combat (D&D 5e PHB p. 202)
_COMBAT_CAST_UNITS = frozenset({"action", "bonus", "reaction", "round"})

def validate_cast_time_in_combat(spell_name: str, cast_time_raw: list) -> tuple[bool, str]:
    """
    Vérifie que le temps d'incantation d'un sort est compatible avec le combat.

    D&D 5e — seuls les temps suivants sont utilisables pendant un round :
      • 1 action
      • 1 action bonus
      • 1 réaction
      • 1 round  (ex : Counterspell si pré-déclenché)

    Tout temps exprimé en minutes, heures ou jours est invalide en combat.
    Cas particuliers :
      • Les sorts rituels (+10 min) sont également invalides sauf hors-combat
        — mais la vérification du rituel est déjà gérée ailleurs ; ici on
        bloque toute unité ≥ minute.

    Retourne (True, "") si le temps est compatible, sinon (False, message_erreur).
    """
    if not cast_time_raw:
        return True, ""          # absence de données → non restrictif

    t    = cast_time_raw[0]
    unit = t.get("unit", "").lower().strip()
    n    = t.get("number", 1)

    if unit in _COMBAT_CAST_UNITS:
        return True, ""

    # Construire un libellé lisible pour l'unité
    _unit_labels = {
        "minute": f"{n} minute{'s' if n > 1 else ''}",
        "hour":   f"{n} heure{'s' if n > 1 else ''}",
        "day":    f"{n} jour{'s' if n > 1 else ''}",
    }
    cast_label = _unit_labels.get(unit, f"{n} {unit}")

    return False, (
        f"[RÈGLE 5e — TEMPS D'INCANTATION]\n"
        f"Le sort « {spell_name} » a un temps d'incantation de {cast_label}.\n"
        f"Il ne peut PAS être lancé pendant un combat (round = 6 secondes).\n"
        f"Ce sort ne peut être utilisé qu'en dehors d'un round de combat.\n\n"
        f"Choisis une autre action : attaque, tour de magie, ou un sort "
        f"dont le temps d'incantation est 1 action / 1 action bonus / 1 réaction."
    )


# ─── PNJ patterns ─────────────────────────────────────────────────────────────

def build_pnj_patterns(PNJ_NAMES: list) -> dict:
    """
    Construit tous les patterns regex de détection PNJ depuis la liste de noms.
    Retourne un dict avec les clés :
      pnj_dialogue_re, pnj_narrative_re, pnj_narrative_inv_re, pnj_vocative_re
    et une fonction pnj_pattern_search(text) -> bool.
    """
    escaped = [_re.escape(n) for n in PNJ_NAMES]
    joined  = '|'.join(escaped)

    # Forme 1 : dialogue inventé  NomPNJ : ... ou «
    pnj_dialogue_re = _re.compile(
        r'(?:^|\n)\s*(?:' + joined + r')\s*(?::|«|\u201c)',
        _re.IGNORECASE | _re.MULTILINE
    )
    # Forme 2 : description narrative  NomPNJ (+ mots optionnels) + verbe
    # Le groupe (?:\s+\S+){0,3} permet de capturer les noms composés comme
    # "Ezmerelda d'Avenir jette" où 1-2 mots séparent le nom du verbe.
    _VERBE_LIST = (
        r"dit|r[eé]pond|soupire|murmure|ajoute|s.exclame|lance|"
        r"explique|observe|regarde|se tourne|.change|hoche|fronce|esquisse|"
        r"croise|saisit|tend|pose|s.essui|sourit|grimace|cligne|l[eè]ve|"
        r"baisse|s.approche|recule|marche|court|s.arr[eê]te|"
        r"jette|fixe|toise|scrute|adresse|poursuit|glisse|tranche|"
        r"frotte|[eé]tire|acquiesce|examine|[eé]change|tourne|"
        r"plisse|tressaille|chancelle|d[eé]glutit|grommelle|ricane|"
        r"renifle|pouffe|[eé]met|pousse|l[aâ]che|interrompt|reprend|"
        r"conclut|insiste|h[eé]site|bafouille|semble|para[iî]t|"
        r"se r[ae]id|se d[eé]tend|se penche|se redresse|se l[eè]ve|"
        r"se tait|se retourne|se fige|s.immobilise|se lev|"
        r"a une voix|a l.air|a un|fait un|prend|devient|reste"
    )
    pnj_narrative_re = _re.compile(
        r'\b(?:' + joined + r')\b'
        r'(?:\s+\S+){0,3}'
        r'\s+(?:' + _VERBE_LIST + r')',
        _re.IGNORECASE
    )
    # Forme 2b : possessif PNJ après dialogue inventé
    # Détecte "Sa voix est", "Son regard", "Ses yeux" quand précédé d'un nom PNJ
    # dans le même message — signe que l'agent décrit le PNJ en détail.
    pnj_possessive_re = _re.compile(
        r'(?:^|\n)\s*(?:sa voix|son regard|ses yeux|son visage|son ton|'
        r'sa main|ses mains|son expression|sa posture|son attitude|'
        r'il s.approche|elle s.approche|il se tourne|elle se tourne|'
        r'il hoche|elle hoche|il acquiesce|elle acquiesce)',
        _re.IGNORECASE | _re.MULTILINE
    )

    # Forme 3 : verbe + NomPNJ inversé (après guillemets)
    pnj_narrative_inv_re = _re.compile(
        r'(?:dit|r[eé]pond|soupire|murmure|ajoute|s.exclame|lance|explique|'
        r'observe|grommelle|reprend|conclut|insiste|h[eé]site|ricane|'
        r'poursuit|glisse|l[aâ]che|tranche|conc[eè]de|admet|reconna[iî]t|'
        r'bafouille|interrompt|s.exclame|s.esclaffe)\s+'
        r'(?:' + joined + r')\b',
        _re.IGNORECASE
    )
    # Forme 4 : vocatif  NomPNJ, ...
    pnj_vocative_re = _re.compile(
        r'(?:,\s*|«\s*)(?:' + joined + r')\s*,',
        _re.IGNORECASE
    )

    def pnj_pattern_search(text: str) -> bool:
        """Retourne True si le texte contient une violation de type PNJ."""
        if (pnj_dialogue_re.search(text)
                or pnj_narrative_re.search(text)
                or pnj_narrative_inv_re.search(text)
                or pnj_possessive_re.search(text)):
            return True
        if pnj_vocative_re.search(text):
            import re as _re_voc
            last_q = max((m.end() for m in _re_voc.finditer(r'[?]', text)), default=-1)
            if last_q == -1:
                return False
            after_q = _re_voc.sub(r"^[\s\u00bb\u00ab\"\']+", "", text[last_q:].strip())
            if len(after_q) > 30:
                if (pnj_narrative_re.search(after_q)
                        or pnj_narrative_inv_re.search(after_q)
                        or pnj_dialogue_re.search(after_q)):
                    return True
        return False

    # Compatibilité avec l'ancien usage _pnj_pattern.search(...)
    class _PnjPatternCompat:
        def search(self, text):
            return pnj_pattern_search(text) or None

    return {
        "pnj_dialogue_re":      pnj_dialogue_re,
        "pnj_narrative_re":     pnj_narrative_re,
        "pnj_narrative_inv_re": pnj_narrative_inv_re,
        "pnj_possessive_re":    pnj_possessive_re,
        "pnj_vocative_re":      pnj_vocative_re,
        "pnj_pattern":          _PnjPatternCompat(),
        "pnj_pattern_search":   pnj_pattern_search,
    }


# ─── Parseur de directives MJ ─────────────────────────────────────────────────

def parse_mj_directives(mj_text: str,
                         PLAYER_NAMES: list,
                         char_mechanics: dict,
                         get_agent_config_fn,
                         default_model_str: str) -> list:
    """
    Extrait les directives mécaniques d'un message MJ.
    Stratégie en deux passes :
      1. Regex rapide : couvre les cas simples sans appel LLM.
      2. LLM (OpenAI SDK) pour les cas ambigus/complexes.
    Retourne une liste de dicts (vide si aucune directive).
    """
    import json as _json

    if not DIRECTIVE_PREFILTER.search(mj_text):
        return []

    # ── Passe 1 : regex sans LLM ─────────────────────────────────────
    _PLAYER_SET = {n.lower() for n in PLAYER_NAMES}
    _NAME_CANON = {n.lower(): n for n in PLAYER_NAMES}
    _CARAC_MAP  = {
        "force":"force","str":"force",
        "dextérité":"dextérité","dex":"dextérité",
        "constitution":"constitution","con":"constitution",
        "intelligence":"intelligence","int":"intelligence",
        "sagesse":"sagesse","wis":"sagesse","sag":"sagesse",
        "charisme":"charisme","cha":"charisme",
    }
    _SKILL_MAP = {
        "athlétisme":"force","acrobaties":"dextérité",
        "discrétion":"dextérité","escamotage":"dextérité",
        "arcanes":"intelligence","arcane":"intelligence","arcana":"intelligence","histoire":"intelligence",
        "investigation":"intelligence","nature":"intelligence","religion":"intelligence",
        "dressage":"sagesse","médecine":"sagesse","perception":"sagesse",
        "perspicacité":"sagesse","survie":"sagesse",
        "tromperie":"charisme","intimidation":"charisme",
        "persuasion":"charisme","représentation":"charisme",
    }
    _txt_low = mj_text.lower()
    _results_regex = []

    def _find_target(text):
        for pname in _PLAYER_SET:
            if pname in text.lower():
                return _NAME_CANON[pname]
        if any(w in text.lower() for w in ("vous","tout le monde","chacun","groupe")):
            return "tous"
        return None

    # Jet de sauvegarde / compétence caractéristique
    _jet_re = _re.search(
        r'jet\s+(?:de\s+)?(constitution|force|dextérité|sagesse|intelligence|charisme|'
        r'con\b|str\b|dex\b|wis\b|int\b|cha\b|sag\b)'
        r'(?:[^D]*(?:DC|cd|dd)\s*(\d+))?',
        _txt_low, _re.IGNORECASE)
    if _jet_re:
        raw_carac = _jet_re.group(1).lower().strip()
        carac = _CARAC_MAP.get(raw_carac, raw_carac)
        dc_raw = _jet_re.group(2)
        dc = int(dc_raw) if dc_raw else None
        tgt = _find_target(mj_text)
        if tgt:
            d = {"action": "jet_sauvegarde", "cible": tgt,
                 "caracteristique": carac, "de": "1d20", "bonus": 0}
            if dc:
                d["dc"] = dc
            _results_regex.append(d)

    # Jet de compétence
    if not _results_regex:
        _skill_pattern = (
            r'jet\s+(?:de\s+|d["\'])?('
            + '|'.join(_SKILL_MAP.keys()) + r')'
            r'(?:[^D]*(?:DC|cd)\s*(\d+))?'
        )
        _skill_re = _re.search(_skill_pattern, _txt_low, _re.IGNORECASE)
        if _skill_re:
            skill_name = _skill_re.group(1).lower().strip()
            carac = _SKILL_MAP.get(skill_name, skill_name)
            dc_raw = _skill_re.group(2)
            dc = int(dc_raw) if dc_raw else None
            tgt = _find_target(mj_text)
            if tgt:
                d = {"action": "jet_competence", "cible": tgt,
                     "caracteristique": carac, "de": "1d20", "bonus": 0}
                if dc:
                    d["dc"] = dc
                _results_regex.append(d)

    # Dégâts numériques explicites
    _dmg_re = _re.search(
        r'(\d+)\s*(?:d[eé]g[aâ]ts?|pv\b|points?\s*de\s*vie|hp\b)'
        r'(?:\s+(?:de\s+)?(\w+))?',
        _txt_low)
    if _dmg_re and not _results_regex:
        montant = int(_dmg_re.group(1))
        type_d  = _dmg_re.group(2) or ""
        tgt = _find_target(mj_text)
        if tgt:
            _results_regex.append({"action": "degats", "cible": tgt,
                                   "montant": montant, "type_degat": type_d})

    # Soin explicite
    _soin_re = _re.search(
        r'(?:soign|récup[eè]r|regagn|rend)[^\d]*(\d+)\s*(?:pv\b|points?|hp\b)?',
        _txt_low)
    if _soin_re and not _results_regex:
        montant = int(_soin_re.group(1))
        tgt = _find_target(mj_text)
        if tgt:
            _results_regex.append({"action": "soin", "cible": tgt,
                                   "montant": montant})

    if _results_regex:
        return _results_regex

    # ── Passe 2 : LLM (OpenAI SDK) ──────────────────────────────────────
    try:
        import openai as _openai
        global _parser_openai_client, _parser_cfg0

        # Lazy-init thread-safe : on crée le client openai une seule fois.
        # Le lock garantit qu'on n'entre pas deux fois dans le bloc init
        # depuis deux threads distincts, ce qui créerait deux SSLContexts
        # simultanément → segfault.
        if _parser_openai_client is None:
            with _PARSER_CLIENT_LOCK:
                if _parser_openai_client is None:   # double-checked locking
                    _ac   = get_agent_config_fn("Thorne")
                    _cfg0 = build_llm_config(
                        _ac.get("model") or default_model_str, temperature=0
                    )["config_list"][0]
                    _parser_cfg0 = _cfg0
                    _parser_openai_client = _openai.OpenAI(
                        api_key  = _cfg0["api_key"],
                        base_url = str(_cfg0.get("base_url", "https://api.openai.com/v1")),
                    )

        _oa = _parser_openai_client
        _model_name = _parser_cfg0["model"]

        from llm_config import _SSL_LOCK as _psl
        with _psl:
            _resp = _oa.chat.completions.create(
                model    = _model_name,
                messages =[
                    {"role": "system", "content": PARSER_SYSTEM},
                    {"role": "user",   "content": mj_text},
                ],
                temperature = 0,
                max_tokens  = 400,
            )
        _raw = _resp.choices[0].message.content.strip()
        
        # Nettoyage des balises de réflexion
        _raw = _re.sub(r'<(thought|think)>.*?</\1>\s*', '', _raw, flags=_re.IGNORECASE | _re.DOTALL)
        
        _raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw).strip()
        _parsed = _json.loads(_raw)
        if isinstance(_parsed, list):
            return _parsed
    except Exception as _pe:
        print(f"[MJParser] Erreur LLM : {_pe}")
    return []