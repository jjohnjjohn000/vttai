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

from state_manager import load_state
from app_config    import get_chronicler_config
from llm_config    import build_llm_config, _default_model


# ─── Regex statiques (indépendants de la liste PNJ) ──────────────────────────

# Détecte [SORT: NomDuSort | Niveau: X | Cible: Y]
SORT_PATTERN = _re.compile(
    r'\[SORT\s*:\s*(?P<nom>[^|\]]+?)\s*\|\s*Niveau\s*:\s*(?P<niveau>\d)\s*(?:\|\s*Cible\s*:\s*(?P<cible>[^\]]*?))?\s*\]',
    _re.IGNORECASE
)

# Détecte un ou plusieurs blocs [ACTION] (multiligne).
# Capture optionnelle du champ Type et règle multiligne (Extra Attack).
ACTION_PATTERN = _re.compile(
    r'\[ACTION\][ \t]*\n'
    r'(?:[ \t]*Type[ \t]*:[ \t]*(?P<type>[^\n]+)\n)?'
    r'[ \t]*Intention[ \t]*:[ \t]*(?P<intention>[^\n]+)\n'
    r'[ \t]*R[eè]gle 5e[ \t]*:[ \t]*(?P<regle>.+?)\n'
    r'[ \t]*Cible[ \t]*:[ \t]*(?P<cible>[^\n]+)',
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
    """Retourne la liste des noms de sorts préparés pour l'affichage."""
    try:
        state = load_state()
        return list(state.get("characters", {}).get(char_name, {})
                    .get("spells_prepared", []))
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
        spell_names = (
            state.get("characters", {})
            .get(char_name, {})
            .get("spells_prepared", None)
        )
        if spell_names is None:
            return True   # champ absent → pas de restriction

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
    # Forme 2 : description narrative  NomPNJ + verbe
    pnj_narrative_re = _re.compile(
        r'\b(?:' + joined + r')\b'
        r"\s+(?:dit|r[eé]pond|soupire|murmure|ajoute|s.exclame|lance|"
        r"explique|observe|regarde|se tourne|.change|hoche|fronce|esquisse|"
        r"croise|saisit|tend|pose|s.essui|sourit|grimace|cligne|l[eè]ve|"
        r"baisse|s.approche|recule|marche|court|s.arr[eê]te|"
        r"frotte|jette|[eé]tire|acquiesce|examine|[eé]change|tourne|"
        r"plisse|tressaille|chancelle|d[eé]glutit|grommelle|ricane|"
        r"renifle|pouffe|[eé]met|pousse|l[aâ]che|interrompt|reprend|"
        r"conclut|insiste|h[eé]site|bafouille|semble|para[iî]t|"
        r"se r[ae]id|se d[eé]tend|se penche|se redresse|se l[eè]ve|"
        r"se tait|se retourne|se fige|s.immobilise|se lev)",
        _re.IGNORECASE
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
                or pnj_narrative_inv_re.search(text)):
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
        "arcanes":"intelligence","histoire":"intelligence",
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
        import httpx as _httpx
        import openai as _openai
        _ac  = get_agent_config_fn("Thorne")   # modèle le plus léger
        _cfg0 = build_llm_config(
            _ac.get("model") or default_model_str, temperature=0
        )["config_list"][0]
        _http = _httpx.Client()
        _oa   = _openai.OpenAI(
            api_key    = _cfg0["api_key"],
            base_url   = str(_cfg0.get("base_url", "https://api.openai.com/v1")),
            http_client= _http,
        )
        from llm_config import _SSL_LOCK as _psl
        with _psl:
            _resp = _oa.chat.completions.create(
                model    = _cfg0["model"],
                messages = [
                    {"role": "system", "content": PARSER_SYSTEM},
                    {"role": "user",   "content": mj_text},
                ],
                temperature = 0,
                max_tokens  = 400,
            )
        _http.close()
        _raw = _resp.choices[0].message.content.strip()
        _raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", _raw).strip()
        _parsed = _json.loads(_raw)
        if isinstance(_parsed, list):
            return _parsed
    except Exception as _pe:
        print(f"[MJParser] Erreur LLM : {_pe}")
    return []
