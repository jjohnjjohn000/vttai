"""
quest_tracker_mixin.py — Mise à jour automatique des quêtes via le Chroniqueur IA.

Fournit QuestTrackerMixin à injecter dans DnDApp :
  - process_quests_with_llm : analyse le chat + les logs, met à jour les quêtes

Le Chroniqueur reçoit :
  - La liste complète des quêtes actives (id, titre, objectifs, statut)
  - La transcription de la session en cours (groupchat.messages)
  - Le résumé global de la campagne (session_summary)
  - Les logs des sessions précédentes (get_session_logs_prompt)

Il retourne un JSON strictement structuré que le mixin applique directement
dans campaign_state.json, puis rafraîchit le journal de quêtes s'il est ouvert.

Prérequis sur l'instance hôte :
  self.msg_queue, self.groupchat, self.root
"""

import json
import re
import threading

from app_config    import get_chronicler_config
from llm_config    import build_llm_config, _default_model
from state_manager import (
    load_state, get_quests, save_quests,
    get_session_logs_prompt,
)


# ─── Prompt système du Chroniqueur pour les quêtes ────────────────────────────

_QUEST_SYSTEM_PROMPT = """\
Tu es le Chroniqueur IA d'une campagne D&D 5e. Ta tâche est d'analyser \
la transcription d'une session et de mettre à jour le journal de quêtes du groupe.

RÈGLES ABSOLUES :
1. Ne modifie QUE ce qui est clairement confirmé par la transcription.
2. Ne marque un objectif "done: true" que si le groupe l'a accompli de façon \
   explicite pendant la session.
3. Ne passe une quête en "completed" ou "failed" que si c'est sans ambiguïté.
4. Tu peux ajouter de nouvelles quêtes découvertes pendant la session.
5. Tu peux ajouter ou modifier les "notes" d'une quête pour refléter les \
   derniers développements.
6. Réponds UNIQUEMENT avec du JSON valide, sans texte avant ni après, \
   sans balises markdown, sans commentaires.

FORMAT DE RÉPONSE (JSON strict) :
{
  "updates": [
    {
      "id": "q1",
      "status": "active",
      "objectives": [
        {"index": 0, "done": true},
        {"index": 1, "done": false}
      ],
      "notes": "Note mise à jour (optionnel, chaîne vide si inchangé)"
    }
  ],
  "new_quests": [
    {
      "title": "Titre de la nouvelle quête",
      "status": "active",
      "category": "Secondaire",
      "description": "Description courte.",
      "objectives": [
        {"text": "Premier objectif", "done": false}
      ],
      "notes": ""
    }
  ],
  "summary": "Résumé en 2-3 phrases des modifications effectuées."
}

- "updates" : liste des quêtes existantes à modifier. Inclure uniquement celles \
  ayant réellement changé. Champs optionnels : status, objectives, notes.
- "new_quests" : liste des nouvelles quêtes à créer. Peut être vide [].
- "summary"   : bref résumé des changements pour le MJ.

Statuts valides : "active", "completed", "failed".
"""

# ─── Prompt système du Chroniqueur pour l'ajout d'une quête ───────────────────

_QUEST_ADD_SYSTEM_PROMPT = """\
Tu es le Chroniqueur IA d'une campagne D&D 5e. Le MJ te donne une description \
libre d'une nouvelle quête. Tu dois l'analyser et retourner un JSON structuré.

RÈGLES :
1. Déduis un titre concis et évocateur.
2. Choisis une catégorie parmi : "Principale", "Secondaire", "Personnelle", "Exploration".
3. Rédige une description courte (1-2 phrases).
4. Décompose la quête en objectifs clairs et vérifiables (2 à 5 objectifs).
5. Ajoute des notes si la description du MJ contient des indices ou détails utiles.
6. Réponds UNIQUEMENT avec du JSON valide, sans texte avant ni après, \
   sans balises markdown, sans commentaires.

FORMAT DE RÉPONSE (JSON strict) :
{
  "title": "Titre de la quête",
  "category": "Secondaire",
  "description": "Description courte.",
  "objectives": [
    {"text": "Premier objectif", "done": false},
    {"text": "Deuxième objectif", "done": false}
  ],
  "notes": ""
}
"""


def _build_quest_user_prompt(quests: list, chat_history: str,
                              old_summary: str, sessions_prompt: str) -> str:
    """Construit le prompt utilisateur envoyé au Chroniqueur."""
    # Sérialise les quêtes actives + terminées récentes (contexte utile)
    quest_lines = []
    for q in quests:
        status    = q.get("status", "active")
        objs      = q.get("objectives", [])
        obj_lines = "\n".join(
            f"    [{i}] {'✓' if o.get('done') else '○'} {o.get('text', '')}"
            for i, o in enumerate(objs)
        )
        quest_lines.append(
            f"[ID: {q['id']}] [{status.upper()}] {q['title']}\n"
            f"  Catégorie : {q.get('category', '?')}\n"
            f"  Description : {q.get('description', '')}\n"
            f"  Objectifs :\n{obj_lines}\n"
            f"  Notes actuelles : {q.get('notes', '')}"
        )

    quest_block   = "\n\n".join(quest_lines) or "Aucune quête enregistrée."
    history_block = chat_history.strip() or "Aucune transcription disponible."

    parts = [
        "=== QUÊTES ACTUELLES ===",
        quest_block,
        "",
        "=== RÉSUMÉ GLOBAL DE LA CAMPAGNE ===",
        old_summary.strip() or "Aucun résumé.",
    ]

    if sessions_prompt.strip():
        parts += ["", "=== SESSIONS PRÉCÉDENTES ===", sessions_prompt.strip()]

    parts += [
        "",
        "=== TRANSCRIPTION DE LA SESSION EN COURS ===",
        history_block,
        "",
        "Analyse la transcription et mets à jour les quêtes selon les RÈGLES ABSOLUES.",
    ]

    return "\n".join(parts)


def _strip_json_fences(text: str) -> str:
    """Retire les balises ```json ... ``` si le LLM les a quand même ajoutées."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$",         "", text)
    return text.strip()


class QuestTrackerMixin:
    """Mixin pour DnDApp — mise à jour automatique des quêtes via LLM."""

    # ─── Point d'entrée public ────────────────────────────────────────────────

    def process_quests_with_llm(self):
        """
        Lance l'analyse des quêtes dans un thread daemon.
        Peut être appelé depuis n'importe quel thread Tk (bouton UI).
        """
        self.msg_queue.put({
            "sender": "📜 Chroniqueur",
            "text":   "Analyse des quêtes en cours… Le Chroniqueur consulte la transcription.",
            "color":  "#c8b8ff",
        })
        threading.Thread(
            target=self._run_quest_analysis,
            daemon=True,
            name="quest-tracker",
        ).start()

    # ─── Worker (thread daemon) ───────────────────────────────────────────────

    def add_quest_via_llm(self, raw_description: str):
        """Envoie une description libre au Chroniqueur pour créer une quête structurée."""
        try:
            import uuid as _uuid
            import autogen as _ag

            # ── 1. Contexte : quêtes existantes (pour éviter les doublons) ────
            quests = get_quests()
            existing = "\n".join(
                f"  - [{q.get('status','?')}] {q['title']}"
                for q in quests
            ) or "Aucune quête existante."

            user_prompt = (
                f"=== QUÊTES EXISTANTES (pour éviter les doublons) ===\n"
                f"{existing}\n\n"
                f"=== DESCRIPTION DE LA NOUVELLE QUÊTE (par le MJ) ===\n"
                f"{raw_description}\n\n"
                f"Analyse cette description et retourne le JSON structuré de la quête."
            )

            # ── 2. Appel LLM ─────────────────────────────────────────────────
            chron_cfg = get_chronicler_config()
            llm_cfg   = build_llm_config(
                chron_cfg.get("model", _default_model),
                temperature=chron_cfg.get("temperature", 0.2),
            )
            client   = _ag.OpenAIWrapper(config_list=llm_cfg["config_list"])
            response = client.create(messages=[
                {"role": "system", "content": _QUEST_ADD_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ])

            raw_text = (response.choices[0].message.content or "").strip()
            clean    = _strip_json_fences(raw_text)

            # ── 3. Parser le JSON ────────────────────────────────────────────
            try:
                data = json.loads(clean)
            except json.JSONDecodeError as e:
                self.msg_queue.put({
                    "sender": "⚠️ Chroniqueur",
                    "text":   f"Réponse JSON invalide du Chroniqueur : {e}\n\n"
                              f"Réponse brute :\n{raw_text[:500]}",
                    "color":  "#F44336",
                })
                return

            # ── 4. Créer l'entrée de quête ───────────────────────────────────
            title = data.get("title", raw_description[:60]).strip()
            new_id = f"q_{_uuid.uuid4().hex[:6]}"
            entry = {
                "id":          new_id,
                "title":       title,
                "status":      "active",
                "category":    data.get("category", "Secondaire"),
                "description": data.get("description", ""),
                "objectives":  data.get("objectives", []),
                "notes":       data.get("notes", ""),
            }
            quests.append(entry)
            save_quests(quests)

            # ── 5. Rapport dans le chat ──────────────────────────────────────
            obj_lines = "\n".join(
                f"  ○ {o.get('text', '?')}" for o in entry["objectives"]
            )
            self.msg_queue.put({
                "sender": "📜 Chroniqueur",
                "text":   (
                    f"✅ Nouvelle quête ajoutée !\n\n"
                    f"🗺️ [{entry['category']}] {title}\n"
                    f"   {entry['description']}\n"
                    f"   Objectifs :\n{obj_lines}"
                    + (f"\n   📝 Notes : {entry['notes']}" if entry["notes"] else "")
                ),
                "color":  "#c8b8ff",
            })

            # ── 6. Rafraîchir le journal de quêtes s'il est ouvert ──────────
            self.root.after(0, self._maybe_refresh_quest_journal)

            # ── 7. MAJ prompts agents pour refléter la nouvelle quête ────────
            if getattr(self, '_agents', None):
                self.root.after(100, self._rebuild_agent_prompts)

        except Exception as e:
            import traceback
            self.msg_queue.put({
                "sender": "⚠️ Chroniqueur",
                "text":   f"Erreur ajout quête : {e}\n{traceback.format_exc()[-300:]}",
                "color":  "#F44336",
            })

    def _run_quest_analysis(self):
        """Appelle le LLM, applique les mises à jour, rafraîchit l'UI."""
        try:
            import autogen as _ag

            # ── 1. Extraire l'historique de session ───────────────────────────
            chat_history = ""
            if self.groupchat:
                for msg in self.groupchat.messages:
                    name    = msg.get("name", "Inconnu")
                    content = msg.get("content", "")
                    if content and not str(content).startswith("[RÉSULTAT SYSTÈME]"):
                        chat_history += f"{name}: {content}\n"

            # ── 2. Charger le contexte ────────────────────────────────────────
            state         = load_state()
            old_summary   = state.get("session_summary", "")
            sessions_txt  = get_session_logs_prompt(max_sessions=3)
            quests        = get_quests()  # toutes les quêtes (actives + complétées)

            # ── 3. Construire les prompts ─────────────────────────────────────
            user_prompt = _build_quest_user_prompt(
                quests, chat_history, old_summary, sessions_txt
            )

            # ── 4. Appel LLM (Chroniqueur) ────────────────────────────────────
            chron_cfg = get_chronicler_config()
            llm_cfg   = build_llm_config(
                chron_cfg.get("model", _default_model),
                temperature=chron_cfg.get("temperature", 0.2),   # basse pour la précision
            )
            client   = _ag.OpenAIWrapper(config_list=llm_cfg["config_list"])
            response = client.create(messages=[
                {"role": "system", "content": _QUEST_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ])

            raw_text = (response.choices[0].message.content or "").strip()
            clean    = _strip_json_fences(raw_text)

            # ── 5. Parser le JSON ─────────────────────────────────────────────
            try:
                data = json.loads(clean)
            except json.JSONDecodeError as e:
                self.msg_queue.put({
                    "sender": "⚠️ Chroniqueur",
                    "text":   f"Réponse JSON invalide du Chroniqueur : {e}\n\n"
                              f"Réponse brute :\n{raw_text[:500]}",
                    "color":  "#F44336",
                })
                return

            # ── 6. Appliquer les mises à jour ─────────────────────────────────
            changes = self._apply_quest_updates(quests, data)

            # ── 7. Sauvegarder ────────────────────────────────────────────────
            save_quests(quests)

            # ── 8. Rapport dans le chat ───────────────────────────────────────
            summary = data.get("summary", "").strip()
            if not changes and not data.get("new_quests"):
                report = "Aucune modification de quête détectée dans la transcription."
            else:
                report = summary or f"{len(changes)} modification(s) appliquée(s)."
                if changes:
                    report += "\n\n" + "\n".join(f"  • {c}" for c in changes)

            self.msg_queue.put({
                "sender": "📜 Chroniqueur",
                "text":   f"✅ Journal de quêtes mis à jour.\n\n{report}",
                "color":  "#c8b8ff",
            })

            # ── 9. Rafraîchir le journal de quêtes s'il est ouvert ────────────
            self.root.after(0, self._maybe_refresh_quest_journal)

        except Exception as e:
            import traceback
            self.msg_queue.put({
                "sender": "⚠️ Chroniqueur",
                "text":   f"Erreur analyse quêtes : {e}\n{traceback.format_exc()[-300:]}",
                "color":  "#F44336",
            })

    # ─── Application des mises à jour ─────────────────────────────────────────

    def _apply_quest_updates(self, quests: list, data: dict) -> list:
        """
        Applique les updates et new_quests sur la liste de quêtes en place.
        Retourne une liste de chaînes décrivant chaque changement pour le rapport.
        """
        import uuid as _uuid
        changes = []

        # ── Mises à jour des quêtes existantes ───────────────────────────────
        quest_by_id = {q["id"]: q for q in quests}

        for upd in data.get("updates", []):
            qid = upd.get("id", "")
            q   = quest_by_id.get(qid)
            if q is None:
                changes.append(f"⚠ Quête ID inconnue ignorée : {qid}")
                continue

            # Statut
            new_status = upd.get("status", "")
            if new_status and new_status in ("active", "completed", "failed"):
                if new_status != q.get("status"):
                    old_st = q.get("status", "?")
                    q["status"] = new_status
                    label = {"completed": "✅ complétée", "failed": "❌ échouée",
                             "active": "🔄 réactivée"}.get(new_status, new_status)
                    changes.append(f"{q['title']} → {label} (était : {old_st})")

            # Objectifs
            obj_updates = upd.get("objectives", [])
            for ou in obj_updates:
                idx  = ou.get("index")
                done = ou.get("done")
                if idx is None or done is None:
                    continue
                objs = q.get("objectives", [])
                if 0 <= idx < len(objs):
                    if objs[idx].get("done") != done:
                        objs[idx]["done"] = done
                        icon = "✓" if done else "○"
                        changes.append(
                            f"{q['title']} / obj.{idx} [{icon}] : {objs[idx].get('text','?')[:60]}"
                        )

            # Notes
            new_notes = upd.get("notes", None)
            if new_notes is not None and new_notes != q.get("notes", ""):
                q["notes"] = new_notes
                # (pas de changement visible dans le chat pour les notes — silencieux)

        # ── Nouvelles quêtes ─────────────────────────────────────────────────
        for nq in data.get("new_quests", []):
            title = nq.get("title", "").strip()
            if not title:
                continue
            new_id = f"q_{_uuid.uuid4().hex[:6]}"
            entry  = {
                "id":          new_id,
                "title":       title,
                "status":      nq.get("status", "active"),
                "category":    nq.get("category", "Secondaire"),
                "description": nq.get("description", ""),
                "objectives":  nq.get("objectives", []),
                "notes":       nq.get("notes", ""),
            }
            quests.append(entry)
            changes.append(f"➕ Nouvelle quête : {title}")

        return changes

    # ─── Rafraîchissement du journal de quêtes ────────────────────────────────

    def _maybe_refresh_quest_journal(self):
        """Si le journal de quêtes est ouvert, le referme et le rouvre pour
        afficher les nouvelles données. Doit être appelé depuis le thread Tk."""
        win = getattr(self, "_quest_journal_win", None)
        if win is None:
            return
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass
        self._quest_journal_win = None
        # Rouvre immédiatement avec les données fraîches
        self.root.after(100, self.open_quest_journal)
