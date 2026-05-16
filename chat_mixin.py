"""
chat_mixin.py — ChatMixin : gestion du panneau de chat et de l'audio.

Ce fichier est le point d'entrée principal qui combine les 5 sous-mixins :
  - ChatMixinCore        (Affichage de base, purge, menus contextuels)
  - ChatMixinQueue       (Audio worker, process_queue, TTS)
  - ChatMixinSpellsMap   (Tagging de sorts, map pointers, relais)
  - ChatMixinConfirms    (Widgets de confirmation MJ pour sorts, résultats, actions)
  - ChatMixinSkillNpc    (Widgets pour les jets de compétence et outils PNJ)
"""

import json

from chat_mixin_core       import ChatMixinCore
from chat_mixin_queue      import ChatMixinQueue
from chat_mixin_spells_map import ChatMixinSpellsMap
from chat_mixin_confirms   import ChatMixinConfirms
from chat_mixin_skill_npc  import ChatMixinSkillNpc


class ChatMixin(
    ChatMixinCore,
    ChatMixinQueue,
    ChatMixinSpellsMap,
    ChatMixinConfirms,
    ChatMixinSkillNpc
):
    """Mixin unifié pour DnDApp regroupant toutes les fonctionnalités du panneau de chat."""

    # ─── Mémoire persistante : détection *mots-clés* ─────────────────────────

    def _check_and_update_memories(self, keywords: list, full_text: str):
        """
        Appelé dans un thread daemon quand le MJ écrit *mot-clé* dans le chat.

        Pour chaque mot-clé :
          1. Cherche dans les mémoires existantes par titre ou tag.
          2. Si trouvé ET que le message ou les archives apportent de nouvelles infos → met à jour.
          3. Si non trouvé → crée une nouvelle mémoire en puisant dans les archives de la campagne.
        """
        from state_manager import (
            get_memories, add_memory, update_memory,
            MEMORY_CATEGORIES, get_session_logs_prompt, get_full_campaign_history_prompt
        )

        def _call_claude(prompt):
            import re as _re
            try:
                import autogen as _ag
                from llm_config import build_llm_config, _default_model
                from app_config import get_chronicler_config

                _chron = get_chronicler_config()
                _model = _chron.get("model", _default_model)
                _cfg   = build_llm_config(_model, temperature=0.2)
                client = _ag.OpenAIWrapper(config_list=_cfg["config_list"])

                response = client.create(messages=[
                    {"role": "user", "content": prompt}
                ])
                raw = (response.choices[0].message.content or "").strip()

                raw = _re.sub(r"^```(?:json)?\s*", "", raw)
                raw = _re.sub(r"\s*```$", "", raw.strip())
                return raw.strip()

            except Exception as e:
                print(f"[Memory] Erreur LLM mémoire : {e}")
                return ""

        existing = get_memories(importance_min=1, visible_only=False)
        updated_ids = []
        created_titles = []

        # Récupération de tout l'historique de la campagne pour le Chroniqueur
        history_archived = get_full_campaign_history_prompt()
        history_recent   = get_session_logs_prompt(max_sessions=50)
        campaign_context = f"{history_archived}\n{history_recent}".strip()
        if not campaign_context:
            campaign_context = "(Aucune archive disponible pour le moment)"

        for kw in keywords:
            kw_clean = kw.strip()
            kw_lower = kw_clean.lower()
            if not kw_clean:
                continue

            # ── 1. Recherche d'une mémoire existante ──────────────────────
            match = None
            for m in existing:
                if m["titre"].lower() == kw_lower:
                    match = m
                    break
            if not match:
                for m in existing:
                    if kw_lower in m["titre"].lower() or m["titre"].lower() in kw_lower:
                        match = m
                        break
            if not match:
                for m in existing:
                    for tag in m.get("tags", []):
                        if len(tag) >= 3 and tag.lower() == kw_lower:
                            match = m
                            break
                    if match:
                        break

            if match:
                # ── 2. Mise à jour enrichie par l'historique ────────────────
                prompt_update = (
                    f"Tu es le Chroniqueur IA d'une campagne D&D. Ton rôle est de tenir à jour "
                    f"STRICTEMENT CE QUE LE GROUPE DE PERSONNAGES SAIT.\n\n"
                    f"Le MJ vient de mentionner '*{kw_clean}*' dans ce message :\n\"{full_text}\"\n\n"
                    f"Voici ce que le groupe savait déjà sur '{match['titre']}' :\n{match['contenu']}\n\n"
                    f"Voici les archives complètes de la campagne :\n"
                    f"---\n{campaign_context}\n---\n\n"
                    f"Y a-t-il dans le message du MJ OU dans les archives de la campagne des informations "
                    f"concernant '{match['titre']}' qui ne sont pas déjà dans sa mémoire actuelle ?\n"
                    f"Si oui, fusionne ces informations (du message ou des archives) avec l'ancien contenu.\n"
                    f"RÈGLE ABSOLUE : N'invente RIEN. Base-toi UNIQUEMENT sur le message et les archives. "
                    f"Ne fais pas de méta-jeu. Formule le texte de façon concise.\n\n"
                    f"Réponds avec un JSON UNIQUEMENT (sans markdown) : "
                    f"{{\"new_info\": true, \"updated_content\": \"<Contenu fusionné et enrichi, max 4 phrases>\", "
                    f"\"updated_tags\": [\"tag1\",\"tag2\"], \"importance\": 2}}\n"
                    f"Utilise 1, 2 ou 3 pour l'importance.\n"
                    f"S'il n'y a absolument rien de nouveau à ajouter à la fiche, réponds exactement : {{\"new_info\": false}}"
                )
                result = _call_claude(prompt_update)
                try:
                    data = json.loads(result)
                    if data.get("new_info"):
                        update_memory(
                            match["id"],
                            contenu=data.get("updated_content", match["contenu"]),
                            tags=data.get("updated_tags", match.get("tags", [])),
                            importance=int(data.get("importance", match.get("importance", 2))),
                        )
                        existing = get_memories(importance_min=1, visible_only=False)
                        updated_ids.append(match["titre"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            else:
                # ── 3. Création enrichie par l'historique ───────────────────
                cats_list = ", ".join(MEMORY_CATEGORIES.keys())
                prompt_create = (
                    f"Tu es le Chroniqueur IA d'une campagne D&D. Ton rôle est de consigner "
                    f"STRICTEMENT CE QUE LE GROUPE DE PERSONNAGES SAIT.\n\n"
                    f"Le MJ vient de mentionner pour la première fois '*{kw_clean}*' dans ce message :\n\"{full_text}\"\n\n"
                    f"Voici les archives complètes de la campagne :\n"
                    f"---\n{campaign_context}\n---\n\n"
                    f"Crée une fiche mémoire exhaustive pour '{kw_clean}'.\n"
                    f"Fouille dans les archives de la campagne fournies ci-dessus ET dans le message du MJ "
                    f"pour extraire TOUT ce que le groupe sait à son sujet.\n"
                    f"RÈGLE ABSOLUE : Résume UNIQUEMENT les informations déduites de ces textes. N'invente RIEN. "
                    f"Ne fais pas de méta-jeu (ne révèle pas de secrets s'ils ne sont pas dans le texte).\n\n"
                    f"Catégories disponibles : {cats_list}.\n"
                    f"Réponds avec un JSON UNIQUEMENT (sans markdown) :\n"
                    f"{{\"categorie\": \"<cat>\", \"titre\": \"<titre precis>\", "
                    f"\"contenu\": \"<Ce que le groupe sait sur le sujet, 1 à 4 phrases>\", "
                    f"\"tags\": [\"tag1\",\"tag2\"], \"importance\": 2}}\n"
                )
                result = _call_claude(prompt_create)
                try:
                    data = json.loads(result)
                    cat = data.get("categorie", "evenement")
                    if cat not in MEMORY_CATEGORIES:
                        cat = "evenement"
                    add_memory(
                        categorie=cat,
                        titre=data.get("titre", kw_clean),
                        contenu=data.get("contenu", full_text[:200]),
                        tags=data.get("tags", [kw_clean]),
                        importance=int(data.get("importance", 2)),
                    )
                    existing = get_memories(importance_min=1, visible_only=False)
                    created_titles.append(data.get("titre", kw_clean))
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    print(f"[Memory] Erreur création mémoire '{kw_clean}': {e}")

        # ── Notification dans le chat ──────────────────────────────────────
        parts = []
        if updated_ids:
            parts.append(f"Mises à jour : {', '.join(updated_ids)}")
        if created_titles:
            parts.append(f"Nouvelles entrées : {', '.join(created_titles)}")
        if parts and hasattr(self, "msg_queue"):
            self.msg_queue.put({
                "sender": "📌 Mémoire",
                "text":   " | ".join(parts),
                "color":  "#888844",
            })

# ─── Injection des liens de dégâts (Damage Links) ──────────────────────────────

try:
    from damage_link_ui_handler import (
        _handle_damage_link as _handle_damage_link,
        _open_damage_popup  as _open_damage_popup,
    )

    ChatMixin._handle_damage_link = _handle_damage_link
    ChatMixin._open_damage_popup  = _open_damage_popup
except ImportError:
    pass