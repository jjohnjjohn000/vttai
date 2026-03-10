import json
import random
import re
import os
import threading
import uuid

STATE_FILE = "campaign_state.json"
state_lock = threading.Lock()

# ============================================================
# --- SYSTÈME DE MÉMOIRES CATÉGORISÉES ---
# ============================================================

MEMORY_CATEGORIES = {
    "lieu":          {"label": "Lieu",          "icon": "📍"},
    "personnage":    {"label": "Personnage",     "icon": "👤"},
    "objet_magique": {"label": "Objet magique",  "icon": "✨"},
    "legende":       {"label": "Légende / Lore", "icon": "📜"},
    "menace":        {"label": "Menace",         "icon": "⚔️"},
    "evenement":     {"label": "Événement",      "icon": "📅"},
    "relation":      {"label": "Relation",       "icon": "🤝"},
    "rumeur":        {"label": "Rumeur",         "icon": "🗣️"},
}

# importance : 1 = mineur, 2 = notable, 3 = critique
DEFAULT_MEMORIES = [
    {
        "id": "mem_krezk",
        "categorie": "lieu",
        "titre": "Krezk",
        "contenu": (
            "Village fortifié niché dans les montagnes, gouverné par le Maire Dmitri Krezkov. "
            "Les portes sont gardées et les étrangers doivent se justifier pour entrer. "
            "Au nord du village se trouve une mare stagnante avec une faible aura de magie d'abjuration — "
            "Kaelen pense qu'un serment brisé en ce lieu est à l'origine du mal qui ronge la région."
        ),
        "tags": ["Krezk", "village", "mare", "abjuration", "serment"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_vallaki",
        "categorie": "lieu",
        "titre": "Vallaki",
        "contenu": (
            "Ville ceinte de palissades, gouvernée par le Baron Vargas Vallakovich. "
            "Les portes sont désormais closes au groupe — une tentative d'entrée a échoué. "
            "La ville organise des fêtes obligatoires pour maintenir le moral, sous peine d'emprisonnement."
        ),
        "tags": ["Vallaki", "ville", "baron", "portes"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_argynvostholt",
        "categorie": "lieu",
        "titre": "Argynvostholt",
        "contenu": (
            "Manoir hanté autrefois habité par l'Ordre du Dragon d'Argent. "
            "Occupé aujourd'hui par des Revenants chevaliers morts. "
            "Son phare émet une lumière visible depuis Krezk. "
            "Le groupe a pour mission secondaire d'en renforcer les défenses. Score de défense actuel : 10/100."
        ),
        "tags": ["Argynvostholt", "manoir", "revenants", "phare", "défense"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_barovia_village",
        "categorie": "lieu",
        "titre": "Village de Barovia",
        "contenu": (
            "Village d'origine du groupe. Dominé par la peur de Strahd. "
            "La taverne du Sang de la Vigne était leur point de départ. "
            "Ismark et Ireena Kolyana y résident — c'est là qu'a débuté la quête d'escorte."
        ),
        "tags": ["Barovia", "village", "taverne", "Ismark", "Ireena"],
        "importance": 1,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_strahd",
        "categorie": "personnage",
        "titre": "Strahd von Zarovich",
        "contenu": (
            "Seigneur vampire de Barovie, présumé mort après les événements récents. "
            "Convoitait Ireena Kolyana, qu'il percevait comme la réincarnation de Tatyana, son amour perdu. "
            "Son existence semble n'être qu'un symptôme d'un mal cosmique plus profond."
        ),
        "tags": ["Strahd", "vampire", "seigneur", "mort présumée", "Tatyana"],
        "importance": 3,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_ireena",
        "categorie": "personnage",
        "titre": "Ireena Kolyana",
        "contenu": (
            "Sœur d'Ismark, portant les marques de morsures vampiriques de Strahd. "
            "Son état se détériore : elle est en proie à un conflit intérieur avec l'âme de Tatyana. "
            "La Détection du Bien et du Mal n'a révélé aucune influence externe — conflit purement spirituel. "
            "A refusé de se rendre à la mare sacrée lors de la dernière session."
        ),
        "tags": ["Ireena", "morsure", "Tatyana", "conflit spirituel"],
        "importance": 3,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_ismark",
        "categorie": "personnage",
        "titre": "Ismark Kolyanovich",
        "contenu": (
            "Frère aîné d'Ireena, noble de Barovia. A chargé le groupe de protéger sa sœur. "
            "S'est montré furieux quand Elara lui a demandé de convaincre Ireena d'aller à la mare malgré son état fragile. "
            "Est reparti seul vers le camp."
        ),
        "tags": ["Ismark", "noble", "Barovia", "frère d'Ireena"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_ezmerelda",
        "categorie": "personnage",
        "titre": "Ezmerelda d'Avenir",
        "contenu": (
            "Amie d'Ireena, chasseuse de monstres réputée. "
            "Sa localisation actuelle est inconnue — la retrouver est l'un des objectifs de la quête principale."
        ),
        "tags": ["Ezmerelda", "chasseuse", "introuvable"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_madam_eva",
        "categorie": "personnage",
        "titre": "Madam Eva",
        "contenu": (
            "Vieille diseuse de bonne aventure Vistani, probablement liée à des forces cosmiques. "
            "A tiré les cartes du Tarot de Barovie pour le groupe lors d'une session précédente."
        ),
        "tags": ["Madam Eva", "Vistani", "tarot", "prophétie"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_lettre_cachetee",
        "categorie": "objet_magique",
        "titre": "Lettre cachetée",
        "contenu": (
            "Lettre trouvée et toujours en possession du groupe. Son contenu reste inconnu. "
            "Origine et destinataire non identifiés."
        ),
        "tags": ["lettre", "mystère", "scellée"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_poches_donavich",
        "categorie": "objet_magique",
        "titre": "Contenu des poches du prêtre Donavich",
        "contenu": (
            "Objets trouvés sur le prêtre Donavich, en possession de Thorne. "
            "Nature exacte non encore inventoriée en détail."
        ),
        "tags": ["Donavich", "prêtre", "objets", "Thorne"],
        "importance": 1,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_mal_cosmique",
        "categorie": "legende",
        "titre": "Le Mal Cosmique de Barovie",
        "contenu": (
            "Mission divine du groupe : guérir un mal cosmique dont Strahd n'était qu'un symptôme. "
            "Kaelen croit qu'un 'serment brisé' à la mare sacrée de Krezk en est la source. "
            "Origine et nature exactes encore inconnues."
        ),
        "tags": ["mal cosmique", "serment", "mission divine", "Krezk"],
        "importance": 3,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_tatyana",
        "categorie": "legende",
        "titre": "Tatyana et la réincarnation",
        "contenu": (
            "Tatyana était l'amour de Strahd, décédée tragiquement. "
            "Strahd croyait qu'Ireena était sa réincarnation. "
            "L'âme de Tatyana semble maintenant en conflit actif avec la psyché d'Ireena."
        ),
        "tags": ["Tatyana", "réincarnation", "Ireena", "Strahd", "âme"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_hags_moulin",
        "categorie": "menace",
        "titre": "Les Sorcières du Vieux Moulin",
        "contenu": (
            "Des hags (guenaudes) habitent le vieux moulin et seraient liées à des disparitions d'enfants. "
            "Les Héros de l'Aube Brisée les considèrent comme très puissantes. "
            "Le groupe n'a pas encore investigué."
        ),
        "tags": ["hags", "moulin", "sorcières", "enfants", "danger"],
        "importance": 2,
        "session_ajout": 1,
        "visible": True,
    },
    {
        "id": "mem_dori",
        "categorie": "evenement",
        "titre": "Mort de Dori, fils du prêtre Donavich",
        "contenu": (
            "Dori, fils du prêtre Donavich, était devenu un mort-vivant. "
            "Le groupe l'a éliminé. Quête secondaire complétée."
        ),
        "tags": ["Dori", "Donavich", "mort-vivant", "éliminé"],
        "importance": 1,
        "session_ajout": 1,
        "visible": True,
    },
]

# Voix Edge-TTS disponibles pour les PNJs (liste prédéfinie raisonnable)
AVAILABLE_VOICES = [
    "fr-FR-HenriNeural",
    "fr-FR-DeniseNeural",
    "fr-FR-EloiseNeural",
    "fr-FR-AlainNeural",
    "fr-FR-BrigitteNeural",
    "fr-FR-CelesteNeural",
    "fr-FR-ClaudeNeural",
    "fr-FR-CoralieNeural",
    "fr-FR-JeromeNeural",
    "fr-FR-JosephineNeural",
    "fr-FR-MauriceNeural",
    "fr-FR-YvesNeural",
    "fr-FR-YvetteNeural",
    "fr-BE-CharlineNeural",
    "fr-BE-GerardNeural",
    "fr-CH-ArianeNeural",
    "fr-CH-FabriceNeural",
]

# ============================================================
# --- SORTS PAR DÉFAUT (niveau 15) ---
# ============================================================
# structure : {"name", "level" (0=tour), "school", "prepared", "description"}

DEFAULT_SPELLS = {
    "Kaelen": [  # Paladin niv 15 — slots 4/3/3/1
        {"name": "Soin des blessures",       "level": 1, "school": "Évocation",     "prepared": True,  "description": "1d8 + mod. PV restaurés au toucher."},
        {"name": "Faveur divine",             "level": 1, "school": "Transmutation", "prepared": True,  "description": "Bonus 1d4 aux jets d'attaque pendant 1 minute."},
        {"name": "Bouclier de la foi",        "level": 1, "school": "Abjuration",    "prepared": True,  "description": "+2 CA pendant 10 minutes (concentration)."},
        {"name": "Restauration partielle",    "level": 2, "school": "Abjuration",    "prepared": True,  "description": "Supprime une maladie ou une condition (aveuglé, assourdi…)."},
        {"name": "Pas brumeux",               "level": 2, "school": "Invocation",    "prepared": True,  "description": "Téléportation jusqu'à 9m dans une brume argentée (bonus action)."},
        {"name": "Lumière du jour",           "level": 3, "school": "Évocation",     "prepared": True,  "description": "Sphère de lumière brillante de 18m, dissipe les ténèbres magiques."},
        {"name": "Protection contre l'énergie","level": 3,"school": "Abjuration",    "prepared": True,  "description": "Résistance à un type de dégâts (acide, feu, foudre…) – concentration."},
        {"name": "Bannissement",              "level": 4, "school": "Abjuration",    "prepared": True,  "description": "Cible CHA DC 18 ou bannie dans un espace de demi-plan (concentration)."},
    ],
    "Elara": [  # Mage niv 15 — slots 4/3/3/3/2/1/1/1
        {"name": "Prestidigitation",          "level": 0, "school": "Transmutation", "prepared": True,  "description": "Effets mineurs : sons, odeurs, taches, flamme, nettoyage."},
        {"name": "Trait de feu",              "level": 0, "school": "Évocation",     "prepared": True,  "description": "Attaque à distance : 1d10 feu."},
        {"name": "Projectile magique",        "level": 1, "school": "Évocation",     "prepared": True,  "description": "3 dards de force (1d4+1 chacun), frappe automatique."},
        {"name": "Armure du mage",            "level": 1, "school": "Abjuration",    "prepared": True,  "description": "CA de base = 13 + DEX pendant 8h."},
        {"name": "Détection de la magie",     "level": 1, "school": "Divination",    "prepared": True,  "description": "Détecte toute magie à 9m pendant 10 min (ritual/concentration)."},
        {"name": "Feuilles mortes",           "level": 2, "school": "Transmutation", "prepared": False, "description": "Vitesse de chute réduite, atterrissage sans dégât."},
        {"name": "Boule de feu",              "level": 3, "school": "Évocation",     "prepared": True,  "description": "8d6 feu, sphère de 6m — DEX DC 16 pour demi."},
        {"name": "Contresort",               "level": 3, "school": "Abjuration",    "prepared": True,  "description": "Réaction : annule un sort de niv ≤3 automatiquement, sinon jet d'arcane."},
        {"name": "Dissipation de la magie",  "level": 3, "school": "Abjuration",    "prepared": True,  "description": "Supprime un effet magique de niv ≤3, sinon jet d'arcane DC 10+niv."},
        {"name": "Portail dimensionnel",      "level": 4, "school": "Invocation",    "prepared": True,  "description": "Téléportation jusqu'à 500m vers un lieu connu."},
        {"name": "Mur de force",              "level": 5, "school": "Évocation",     "prepared": True,  "description": "Mur ou sphère invisible et indestructible (concentration 10 min)."},
        {"name": "Désintégration",            "level": 6, "school": "Transmutation", "prepared": True,  "description": "10d6+40 dégâts de force — CON DC 16, réduit en poussière si mort."},
        {"name": "Téléportation",             "level": 7, "school": "Invocation",    "prepared": True,  "description": "Transporte jusqu'à 8 créatures vers un lieu connu (erreur possible)."},
        {"name": "Mot de pouvoir : Étourdissement","level": 8,"school": "Enchantement","prepared": True,"description": "Étourdit une cible ≤150 PV actuel jusqu'à ce qu'elle réussisse un jet de CON."},
    ],
    "Thorne": [],  # Voleur — pas de sorts
    "Lyra": [  # Clerc de la Vie niv 15 — slots 4/3/3/3/2/1/1/1
        {"name": "Lumière",                   "level": 0, "school": "Évocation",     "prepared": True,  "description": "Lumière brillante de 6m, toucher, 1 heure."},
        {"name": "Résistance",                "level": 0, "school": "Abjuration",    "prepared": True,  "description": "+1d4 à un jet de sauvegarde (concentration, avant la fin du tour)."},
        {"name": "Soin des blessures",        "level": 1, "school": "Évocation",     "prepared": True,  "description": "1d8 + mod. PV restaurés au toucher."},
        {"name": "Bénédiction",               "level": 1, "school": "Enchantement",  "prepared": True,  "description": "3 créatures gagnent +1d4 aux attaques et jets de sauvegarde (concentration)."},
        {"name": "Bouclier de la foi",        "level": 1, "school": "Abjuration",    "prepared": True,  "description": "+2 CA pendant 10 minutes (concentration)."},
        {"name": "Parole curative",           "level": 2, "school": "Évocation",     "prepared": True,  "description": "1d4 + mod. PV restaurés en action bonus (portée 18m)."},
        {"name": "Revigorer",                 "level": 2, "school": "Nécromancie",   "prepared": True,  "description": "Stabilise + 1 PV à une créature à 0 PV dans les 6m (bonus action)."},
        {"name": "Soins de groupe",           "level": 3, "school": "Évocation",     "prepared": True,  "description": "3d8+5 PV à 6 créatures de ton choix dans un rayon de 9m."},
        {"name": "Protection contre la mort", "level": 4, "school": "Abjuration",    "prepared": True,  "description": "Immunité aux dégâts nécrotiques et à la réduction du max PV, 8h."},
        {"name": "Restauration suprême",      "level": 5, "school": "Abjuration",    "prepared": True,  "description": "Supprime charme, pétrification, malédiction, réduction de stats ou réduction max PV."},
        {"name": "Sanctification",            "level": 5, "school": "Évocation",     "prepared": True,  "description": "Aura d'énergie divine : +1d4 dégâts à tous types, un type de créature désavantagé."},
        {"name": "Guérison",                  "level": 6, "school": "Évocation",     "prepared": True,  "description": "70 PV restaurés + fin de toutes les maladies et conditions négatives."},
        {"name": "Résurrection",              "level": 7, "school": "Nécromancie",   "prepared": True,  "description": "Ramène un mort à la vie (max 100 ans). 300 gp de diamants. Affaibli 4 jours."},
    ],
}


DEFAULT_NPCS = [
    {"name": "Ismark",    "voice": "fr-FR-AlainNeural",    "speed": "+0%",  "color": "#a0c4ff"},
    {"name": "Ireena",    "voice": "fr-FR-CelesteNeural",  "speed": "+5%",  "color": "#ffc8dd"},
    {"name": "Strahd",    "voice": "fr-FR-ClaudeNeural",   "speed": "-5%",  "color": "#c77dff"},
    {"name": "Madam Eva", "voice": "fr-FR-BrigitteNeural", "speed": "-10%", "color": "#e9c46a"},
    {"name": "Rahadin",   "voice": "fr-FR-JeromeNeural",   "speed": "+0%",  "color": "#ff6b6b"},
]

def load_state():
    with state_lock:
        if not os.path.exists(STATE_FILE):
            initial_state = {
                "session_summary": "Aucun résumé pour le moment.",
                "defense_argynvostholt": 10,
                "npcs": DEFAULT_NPCS,
                "quests": DEFAULT_QUESTS,
                "scene_context": DEFAULT_SCENE.copy(),
                "characters": {
                    "Kaelen": {"llm": "gemini-2.5-pro", "hp": 140, "max_hp": 140, "spell_slots": {"1": 4, "2": 3, "3": 3, "4": 1}, "spells": DEFAULT_SPELLS["Kaelen"]},
                    "Elara":  {"llm": "gemini-2.5-pro", "hp": 95,  "max_hp": 95,  "spell_slots": {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2, "6": 1, "7": 1, "8": 1}, "spells": DEFAULT_SPELLS["Elara"]},
                    "Thorne": {"llm": "groq/llama-4-scout-17b", "hp": 105, "max_hp": 105, "spell_slots": {}, "spells": []},
                    "Lyra":   {"llm": "gemini-2.5-pro", "hp": 110, "max_hp": 110, "spell_slots": {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2, "6": 1, "7": 1, "8": 1}, "spells": DEFAULT_SPELLS["Lyra"]},
                },
                "memories": DEFAULT_MEMORIES,
                "calendar": DEFAULT_CALENDAR.copy(),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(initial_state, f, indent=4, ensure_ascii=False)
            return initial_state

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        dirty = False
        # Migration : ajoute les PNJs si absents d'un ancien fichier
        if "npcs" not in state:
            state["npcs"] = DEFAULT_NPCS
            dirty = True

        # Migration : ajoute les quêtes si absentes
        if "quests" not in state:
            state["quests"] = DEFAULT_QUESTS
            dirty = True

        if "scene_context" not in state:
            state["scene_context"] = DEFAULT_SCENE.copy()
            dirty = True

        # Migration : ajoute les mémoires si absentes
        if "memories" not in state:
            state["memories"] = DEFAULT_MEMORIES
            dirty = True

        # Migration : ajoute les sorts si absents par personnage
        for char_name, default_sp in DEFAULT_SPELLS.items():
            char_data = state.get("characters", {}).get(char_name, {})
            if "spells" not in char_data:
                char_data["spells"] = default_sp
                dirty = True

        # Migration : ajoute le calendrier si absent
        if "calendar" not in state:
            state["calendar"] = DEFAULT_CALENDAR.copy()
            dirty = True

        if dirty:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4, ensure_ascii=False)

        return state

# ============================================================
# --- CALENDRIER BAROVIEN ---
# ============================================================

BAROVIAN_MONTHS = [
    "Yinyavr", "Fivral", "Mart", "Apryl", "Mai", "Eyune",
    "Eyule", "Avgust", "Sintyavr", "Octyavr", "Noyavr", "Dekavr",
]
DAYS_PER_MONTH = 28   # Chaque mois = 1 cycle lunaire complet

# Jours de la semaine baroviens (7 jours)
BAROVIAN_WEEKDAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

# Phases lunaires — jour 1 = Nouvelle Lune, cycle de 28 jours
# (chaque mois commence et finit avec la même phase)
def lunar_phase(day: int) -> tuple:
    """Retourne (icône, nom_court, nom_long) pour le jour du mois donné (1-28)."""
    d = ((day - 1) % 28) + 1
    if d == 1:             return ("🌑", "NL",  "Nouvelle Lune")
    if 2  <= d <= 6:       return ("🌒", "CC",  "Croissant Naissant")
    if d == 7:             return ("🌓", "PQ",  "Premier Quartier")
    if 8  <= d <= 13:      return ("🌔", "GC",  "Gibbeuse Croissante")
    if d == 14:            return ("🌕", "PL",  "Pleine Lune")
    if 15 <= d <= 20:      return ("🌖", "GD",  "Gibbeuse Décroissante")
    if d == 21:            return ("🌗", "DQ",  "Dernier Quartier")
    if 22 <= d <= 27:      return ("🌘", "CD",  "Croissant Décroissant")
    return                        ("🌑", "NL",  "Nuit sans Lune")   # jour 28

DEFAULT_CALENDAR = {
    "year":  351,   # An 351 du règne de Strahd
    "month": 9,     # Sintyavr
    "day":   15,    # Pleine Lune
    "notes": {},    # {"351-9-15": "Arrivée à Vallaki", ...}
}

def get_calendar() -> dict:
    """Retourne le calendrier actuel."""
    state = load_state()
    cal = state.get("calendar", DEFAULT_CALENDAR.copy())
    cal.setdefault("notes", {})
    return cal

def save_calendar(cal: dict):
    """Sauvegarde le calendrier."""
    state = load_state()
    state["calendar"] = cal
    save_state(state)

def advance_day(n: int = 1):
    """Avance de n jours (gère le changement de mois/année). Retourne le nouveau calendrier."""
    cal = get_calendar()
    day, month, year = cal["day"], cal["month"], cal["year"]
    day += n
    while day > DAYS_PER_MONTH:
        day -= DAYS_PER_MONTH
        month += 1
        if month > 12:
            month = 1
            year += 1
    while day < 1:
        day += DAYS_PER_MONTH
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    cal["day"], cal["month"], cal["year"] = day, month, year
    save_calendar(cal)
    return cal

def get_calendar_prompt() -> str:
    """Bloc d'injection pour les system prompts des agents."""
    cal  = get_calendar()
    day, month_idx, year = cal["day"], cal["month"], cal["year"]
    month_name = BAROVIAN_MONTHS[month_idx - 1]
    icon, _, phase_long = lunar_phase(day)
    # Note du jour si présente
    note_key = f"{year}-{month_idx}-{day}"
    note_txt = cal.get("notes", {}).get(note_key, "")
    lines = [
        f"\n\n--- DATE BAROVIENNE ---",
        f"📅 {day} {month_name}, An {year}  |  {icon} {phase_long}",
    ]
    if note_txt:
        lines.append(f"📌 Note du jour : {note_txt}")
    lines.append(
        "Cette date te situe dans le temps. Adapte tes références aux événements récents "
        "et à la saison (automne en Barovie = brume, jours courts, tension accrue)."
    )
    return "\n".join(lines)

def save_state(state):
    with state_lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4, ensure_ascii=False)

def get_npcs() -> list:
    """Retourne la liste des PNJs avec leurs configs voix."""
    state = load_state()
    return state.get("npcs", [])

def save_npcs(npcs: list):
    """Sauvegarde la liste des PNJs."""
    state = load_state()
    state["npcs"] = npcs
    save_state(state)

def get_group_npcs() -> list:
    """Retourne la liste des PNJs actuellement dans le groupe (avec fiche monstre optionnelle)."""
    state = load_state()
    return state.get("group_npcs", [])

def save_group_npcs(npcs: list):
    """Sauvegarde les PNJs du groupe."""
    state = load_state()
    state["group_npcs"] = npcs
    save_state(state)

def update_summary(new_summary: str):
    """Met à jour le résumé global de la campagne dans le JSON."""
    state = load_state()
    state["session_summary"] = new_summary
    save_state(state)

def roll_dice(character_name: str, dice_type: str, bonus: int) -> str:
    try:
        match = re.match(r'(\d+)d(\d+)', dice_type.lower().strip())
        if not match:
            return f"Erreur MJ : Format de dé invalide. Utilisez 'XdY' (ex: 1d20)."
        num_dice = int(match.group(1))
        sides = int(match.group(2))
        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        total = sum(rolls) + bonus
        return f"[RÉSULTAT SYSTÈME] {character_name} a lancé {dice_type} + {bonus}. Dés: {rolls}. Total = {total}"
    except Exception as e:
        return f"Erreur MJ lors du lancer de dé : {str(e)}"

def use_spell_slot(character_name: str, level: str) -> str:
    state = load_state()
    level = str(level)
    if character_name not in state["characters"]:
        return f"Erreur MJ : Personnage {character_name} introuvable."
    if level not in state["characters"][character_name]["spell_slots"]:
        return f"Erreur MJ : Niveau de sort {level} invalide ou non possédé."
    current_slots = state["characters"][character_name]["spell_slots"][level]
    if current_slots > 0:
        state["characters"][character_name]["spell_slots"][level] -= 1
        save_state(state)
        return f"[RÉSULTAT SYSTÈME] Succès. {character_name} a utilisé un sort de niveau {level}. Reste: {current_slots - 1}."
    else:
        return f"[RÉSULTAT SYSTÈME] ÉCHEC : {character_name} n'a plus d'emplacement de sort de niveau {level} !"

def update_hp(character_name: str, amount: int) -> str:
    state = load_state()
    if character_name not in state["characters"]:
        return f"Erreur MJ : Personnage {character_name} introuvable."
    current_hp = state["characters"][character_name]["hp"]
    max_hp = state["characters"][character_name]["max_hp"]
    new_hp = max(0, min(current_hp + amount, max_hp))
    state["characters"][character_name]["hp"] = new_hp
    save_state(state)
    action = "soigné" if amount > 0 else "blessé"
    return f"[RÉSULTAT SYSTÈME] {character_name} a été {action} de {abs(amount)}. PV actuels : {new_hp}/{max_hp}."

# ============================================================
# --- JOURNAL DE QUÊTES ---
# ============================================================

QUEST_STATUSES = ["active", "completed", "failed"]

DEFAULT_QUESTS = [
    {
        "id": "q1",
        "title": "Escorter Ireena jusqu'à Vallaki",
        "status": "active",
        "category": "Principale",
        "description": "Ismark a demandé au groupe de protéger sa sœur Ireena Kolyana et de la conduire en sécurité à Vallaki, loin de l'emprise de Strahd.",
        "objectives": [
            {"text": "Parler à Ismark à la taverne du Sang de la Vigne", "done": True},
            {"text": "Escorter Ireena hors de Barovia", "done": False},
            {"text": "Atteindre Vallaki sains et saufs", "done": False},
        ],
        "notes": "Strahd convoite Ireena. Chaque nuit rallonge le danger."
    },
    {
        "id": "q2",
        "title": "Défendre Argynvostholt",
        "status": "active",
        "category": "Secondaire",
        "description": "Le manoir des Revenants doit être tenu contre les forces de Strahd. La défense actuelle est insuffisante.",
        "objectives": [
            {"text": "Évaluer les défenses du manoir", "done": False},
            {"text": "Recruter ou préparer des renforts", "done": False},
        ],
        "notes": ""
    },
]

def get_quests() -> list:
    """Retourne toutes les quêtes."""
    state = load_state()
    return state.get("quests", [])

def save_quests(quests: list):
    """Sauvegarde la liste des quêtes."""
    state = load_state()
    state["quests"] = quests
    save_state(state)

def get_active_quests_prompt() -> str:
    """
    Génère un bloc de texte formaté pour injection dans les system prompts des agents.
    Ne retourne que les quêtes actives avec leurs objectifs non-complétés.
    """
    quests = get_quests()
    active = [q for q in quests if q.get("status") == "active"]
    if not active:
        return ""

    lines = ["\n\n--- JOURNAL DE QUÊTES (À GARDER EN TÊTE) ---"]
    for q in active:
        lines.append(f"\n🗺️ [{q.get('category','?')}] {q['title']}")
        lines.append(f"   {q.get('description','')}")
        pending = [o['text'] for o in q.get('objectives', []) if not o.get('done')]
        if pending:
            lines.append("   Objectifs en cours :")
            for obj in pending:
                lines.append(f"   • {obj}")
        if q.get("notes"):
            lines.append(f"   ⚠️ Note : {q['notes']}")
    lines.append("\nCes quêtes définissent ce que ton personnage cherche à accomplir. "
                 "Tes questions et actions doivent refléter ces priorités.")
    return "\n".join(lines)

# ============================================================
# --- CONTEXTE DE SCÈNE ---
# ============================================================

DEFAULT_SCENE = {
    "lieu":       "Village de Barovia — Taverne du Sang de la Vigne",
    "ambiance":   "Sombre et silencieuse. Les villageois évitent le regard des étrangers.",
    "heure":      "Soir",
    "meteo":      "Brume dense, pas de lune visible.",
    "npcs_presents": ["Ismark Kolyanovich", "Arik le Barman"],
    "objets_notables": ["Une lettre cachetée sur la table", "Des armes rouillées derrière le comptoir"],
    "menaces":    "Des espions de Strahd pourraient surveiller la taverne.",
    "notes_mj":   "",
    "location_image": "",   # Chemin absolu ou relatif vers une image du lieu (PNG/JPG/WEBP)
}

def get_scene() -> dict:
    """Retourne le contexte de scène actuel."""
    state = load_state()
    scene = state.get("scene_context", DEFAULT_SCENE.copy())
    # Migration : assure que location_image existe dans les anciennes sauvegardes
    scene.setdefault("location_image", "")
    return scene

def save_scene(scene: dict):
    """Sauvegarde le contexte de scène."""
    state = load_state()
    state["scene_context"] = scene
    save_state(state)


import base64 as _base64
import mimetypes as _mimetypes

def get_location_image_base64() -> tuple[str, str] | None:
    """
    Retourne (media_type, base64_data) pour l'image du lieu actuel,
    ou None si aucune image n'est définie ou le fichier est introuvable.
    
    Formats supportés : PNG, JPEG, WEBP, GIF.
    """
    scene = get_scene()
    img_path = scene.get("location_image", "").strip()
    if not img_path or not os.path.isfile(img_path):
        return None
    
    mime, _ = _mimetypes.guess_type(img_path)
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        # Fallback : force jpeg pour les extensions non reconnues
        ext = os.path.splitext(img_path)[1].lower()
        mime = {"jpg": "image/jpeg", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    
    try:
        with open(img_path, "rb") as f:
            data = _base64.b64encode(f.read()).decode("utf-8")
        return mime, data
    except Exception as e:
        print(f"[location_image] Erreur lecture image : {e}")
        return None

def get_scene_prompt() -> str:
    """
    Génère le bloc d'injection pour les system prompts des agents.
    Décrit la scène présente de façon concrète et actionnable.
    """
    s = get_scene()

    npcs = s.get("npcs_presents", [])
    objets = s.get("objets_notables", [])

    lines = ["\n\n--- CONTEXTE DE LA SCÈNE ACTUELLE ---"]
    lines.append(f"📍 Lieu     : {s.get('lieu', '?')}")
    lines.append(f"🕐 Heure    : {s.get('heure', '?')}   |   🌫️ Météo : {s.get('meteo', '?')}")
    lines.append(f"🎭 Ambiance : {s.get('ambiance', '?')}")

    if npcs:
        lines.append(f"👥 PNJs présents : {', '.join(npcs)}")
    else:
        lines.append("👥 PNJs présents : aucun")

    if objets:
        lines.append(f"🔍 Éléments notables : {', '.join(objets)}")

    if s.get("menaces"):
        lines.append(f"⚠️ Menaces / Tension : {s['menaces']}")

    lines.append(
        "\nTon personnage perçoit cet environnement. "
        "Tes réactions, questions et actions doivent être cohérentes avec ce contexte immédiat. "
        "Ne décris pas d'éléments absents de cette liste."
    )
    return "\n".join(lines)
# ============================================================
# --- MÉMOIRES CATÉGORISÉES ---
# ============================================================

def get_memories(categorie: str = None, importance_min: int = 1, visible_only: bool = True) -> list:
    """
    Retourne les mémoires filtrées par catégorie et importance minimale.
    Par défaut ne retourne que les mémoires visibles.
    """
    state = load_state()
    mems = state.get("memories", [])
    if visible_only:
        mems = [m for m in mems if m.get("visible", True)]
    if categorie:
        mems = [m for m in mems if m.get("categorie") == categorie]
    mems = [m for m in mems if m.get("importance", 1) >= importance_min]
    return mems


def save_memories(memories: list):
    """Sauvegarde la liste complète des mémoires."""
    state = load_state()
    state["memories"] = memories
    save_state(state)


def add_memory(
    categorie: str,
    titre: str,
    contenu: str,
    tags: list = None,
    importance: int = 2,
    session_ajout: int = 0,
) -> dict:
    """
    Crée et persiste une nouvelle mémoire.
    Retourne l'entrée créée.
    Lève ValueError si la catégorie est invalide.
    """
    if categorie not in MEMORY_CATEGORIES:
        valides = ", ".join(MEMORY_CATEGORIES.keys())
        raise ValueError(f"Catégorie '{categorie}' invalide. Valides : {valides}")

    entry = {
        "id": f"mem_{uuid.uuid4().hex[:8]}",
        "categorie": categorie,
        "titre": titre,
        "contenu": contenu,
        "tags": tags or [],
        "importance": max(1, min(3, importance)),
        "session_ajout": session_ajout,
        "visible": True,
    }
    state = load_state()
    state.setdefault("memories", []).append(entry)
    save_state(state)
    return entry


def update_memory(mem_id: str, **kwargs) -> bool:
    """
    Met à jour les champs d'une mémoire existante par son id.
    Champs modifiables : titre, contenu, tags, importance, visible, categorie, session_ajout.
    Retourne True si trouvée et mise à jour, False sinon.
    """
    ALLOWED = {"titre", "contenu", "tags", "importance", "visible", "categorie", "session_ajout"}
    state = load_state()
    for mem in state.get("memories", []):
        if mem["id"] == mem_id:
            for k, v in kwargs.items():
                if k in ALLOWED:
                    mem[k] = v
            save_state(state)
            return True
    return False


def delete_memory(mem_id: str) -> bool:
    """
    Supprime définitivement une mémoire par son id.
    Retourne True si supprimée, False si introuvable.
    """
    state = load_state()
    before = len(state.get("memories", []))
    state["memories"] = [m for m in state.get("memories", []) if m["id"] != mem_id]
    if len(state["memories"]) < before:
        save_state(state)
        return True
    return False


def set_memory_visibility(mem_id: str, visible: bool) -> bool:
    """
    Cache ou révèle une mémoire sans la supprimer.
    Utile pour masquer temporairement un secret aux agents joueurs.
    """
    return update_memory(mem_id, visible=visible)


def get_memories_prompt(
    categories: list = None,
    importance_min: int = 1,
    max_per_category: int = None,
) -> str:
    """
    Génère un bloc formaté pour injection dans les system prompts des agents.

    Paramètres :
      categories      – liste de clés de MEMORY_CATEGORIES à inclure.
                        Si None, toutes les catégories sont incluses.
      importance_min  – filtre les mémoires en dessous de ce seuil (1-3).
      max_per_category – limite le nombre de mémoires par catégorie
                        (utile pour les prompts courts).

    Retourne une chaîne vide si aucune mémoire ne correspond.
    """
    cats = categories or list(MEMORY_CATEGORIES.keys())
    lines = ["\n\n--- MÉMOIRES DU GROUPE (CE QUE TU SAIS) ---"]
    any_content = False

    for cat_key in cats:
        if cat_key not in MEMORY_CATEGORIES:
            continue
        mems = get_memories(categorie=cat_key, importance_min=importance_min)
        if not mems:
            continue

        if max_per_category:
            # Priorité aux plus importantes, puis aux plus récentes
            mems = sorted(mems, key=lambda m: (-m.get("importance", 1), -m.get("session_ajout", 0)))
            mems = mems[:max_per_category]

        meta = MEMORY_CATEGORIES[cat_key]
        lines.append(f"\n{meta['icon']} {meta['label'].upper()}")
        for m in mems:
            imp_stars = "★" * m.get("importance", 1) + "☆" * (3 - m.get("importance", 1))
            lines.append(f"  [{imp_stars}] {m['titre']}")
            lines.append(f"    {m['contenu']}")
            if m.get("tags"):
                lines.append(f"    Tags : {', '.join(m['tags'])}")
        any_content = True

    if not any_content:
        return ""

    lines.append(
        "\nCes mémoires représentent ce que ton personnage sait du monde. "
        "Appuie-toi dessus pour poser des questions pertinentes, prendre des décisions cohérentes, "
        "et réagir aux situations avec le vécu de ton personnage."
    )
    return "\n".join(lines)


def get_memories_prompt_compact(importance_min: int = 2) -> str:
    """
    Version condensée : une ligne par mémoire, idéale pour les prompts à tokens limités.
    N'inclut que les mémoires d'importance >= importance_min (défaut : notable ou critique).
    """
    mems = get_memories(importance_min=importance_min)
    if not mems:
        return ""

    lines = ["\n\n--- MÉMOIRES CLÉS ---"]
    by_cat: dict = {}
    for m in mems:
        by_cat.setdefault(m["categorie"], []).append(m)

    for cat_key, entries in by_cat.items():
        meta = MEMORY_CATEGORIES.get(cat_key, {"icon": "•", "label": cat_key})
        for m in entries:
            lines.append(f"  {meta['icon']} {m['titre']} : {m['contenu'][:120]}{'…' if len(m['contenu']) > 120 else ''}")

    return "\n".join(lines)

# ============================================================
# --- MÉMOIRES CONTEXTUELLES (injection dynamique) ---
# ============================================================

def get_contextual_memories_prompt(
    text: str,
    already_active_ids: set | None = None,
) -> tuple[str, set]:
    """
    Détecte les mémoires pertinentes pour un texte donné en cherchant des
    correspondances avec les titres et les tags de chaque mémoire.

    Paramètres :
      text               – le texte à analyser (message MJ ou joueur).
      already_active_ids – IDs déjà injectés ce tour, pour éviter les doublons.

    Retourne :
      (bloc_formaté, set_des_nouveaux_ids_matchés)
      bloc_formaté est vide si aucune nouvelle mémoire n'est détectée.
    """
    if already_active_ids is None:
        already_active_ids = set()

    text_lower = text.lower()
    all_mems   = get_memories(importance_min=1, visible_only=True)

    matched: list[dict] = []
    new_ids: set = set()

    for m in all_mems:
        if m["id"] in already_active_ids:
            continue

        # Correspondance sur le titre (exact ou sous-chaîne)
        if m["titre"].lower() in text_lower:
            matched.append(m)
            new_ids.add(m["id"])
            continue

        # Correspondance sur les tags (chaque tag ≥ 4 caractères pour éviter
        # les faux positifs sur des mots trop courts comme "lu", "de", "à")
        for tag in m.get("tags", []):
            if len(tag) >= 4 and tag.lower() in text_lower:
                matched.append(m)
                new_ids.add(m["id"])
                break

    if not matched:
        return "", new_ids

    # Trier par importance décroissante puis par catégorie
    matched.sort(key=lambda m: (-m.get("importance", 1), m.get("categorie", "")))

    lines = ["\n\n--- MÉMOIRES ACTIVÉES PAR LE CONTEXTE ---"]
    lines.append("(Ces informations viennent d'être mentionnées — utilise-les pour enrichir tes réactions.)")

    for m in matched:
        meta      = MEMORY_CATEGORIES.get(m["categorie"], {"icon": "•", "label": m["categorie"]})
        imp_stars = "★" * m.get("importance", 1) + "☆" * (3 - m.get("importance", 1))
        lines.append(f"\n{meta['icon']} [{imp_stars}] {m['titre']}")
        lines.append(f"  {m['contenu']}")
        if m.get("tags"):
            lines.append(f"  Tags : {', '.join(m['tags'])}")

    return "\n".join(lines), new_ids