"""
image_broadcast_mixin.py — Injection multimodale d'images de lieu aux agents Gemini.

Fournit ImageBroadcastMixin à injecter dans DnDApp :
  - _is_multimodal_agent     : True si l'agent utilise un modèle Gemini (vision)
  - _broadcast_location_image: envoie l'image du lieu actuel à tous les agents compatibles

Prérequis sur l'instance hôte :
  self.msg_queue, self.audio_queue, self._agents
"""

import threading
import concurrent.futures

from state_manager  import get_location_image_base64, get_scene
from agent_logger   import log_llm_start, log_llm_end, log_tts_start


class ImageBroadcastMixin:
    """Mixin pour DnDApp — diffusion multimodale des images de lieu."""

    # ─── Détection du support vision ────────────────────────────────────────

    @staticmethod
    def _is_multimodal_agent(agent) -> bool:
        """Retourne True si l'agent utilise un modèle Gemini (supporte la vision)."""
        try:
            configs = agent.llm_config.get("config_list", [])
            if not configs:
                return False
            model = configs[0].get("model", "")
            return model.startswith("gemini-")
        except Exception:
            return False

    # ─── Diffusion de l'image ────────────────────────────────────────────────

    def _broadcast_location_image(self, announce: bool = True):
        """
        Envoie l'image du lieu actuel à tous les agents multimodaux (Gemini).
        Chaque agent voit l'image et décrit brièvement ce que son personnage perçoit.

        announce=True  → affiche un message système dans le chat avant l'envoi.
        announce=False → injection silencieuse (ex: au démarrage de scène).
        """
        if not self._agents:
            self.msg_queue.put({
                "sender": "⚠️ Système",
                "text": "Agents non initialisés — lancez la partie d'abord.",
                "color": "#FF9800"
            })
            return

        img_data = get_location_image_base64()
        if img_data is None:
            self.msg_queue.put({
                "sender": "⚠️ Système",
                "text": "Aucune image de lieu définie. Ajoutez-en une via ✏️ Scène Active.",
                "color": "#FF9800"
            })
            return

        media_type, b64 = img_data
        scene = get_scene()
        lieu = scene.get("lieu", "ce lieu")

        if announce:
            self.msg_queue.put({
                "sender": "🖼️ Système",
                "text": f"📸 Image du lieu envoyée aux agents multimodaux : {lieu}",
                "color": "#81c784"
            })

        def _send_to_agent(name, agent):
            if not self._is_multimodal_agent(agent):
                return  # Thorne (Groq) ne reçoit pas l'image

            try:
                import autogen as _ag
                client = _ag.OpenAIWrapper(config_list=agent.llm_config["config_list"])

                system_msg = agent.system_message or ""
                prompt_text = (
                    f"[IMAGE DU LIEU — CONTEXTE VISUEL PRIVÉ]\n"
                    f"Le MJ te montre une illustration de l'endroit où se trouve ton groupe : {lieu}.\n"
                    f"En UNE phrase courte de roleplay, décris ce que {name} perçoit ou ressent en voyant ce lieu. "
                    f"Ne pose pas de question. Reste dans le personnage. "
                    f"Si l'image ne correspond pas exactement à la scène décrite, adapte ta perception au contexte narratif."
                )

                log_llm_start(name, prompt_text, context="image")
                response = client.create(messages=[
                    {"role": "system", "content": system_msg},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{b64}"
                                }
                            }
                        ]
                    }
                ])

                text = (response.choices[0].message.content or "").strip()
                log_llm_end(name, response_preview=text)
                if text and text != "[SILENCE]":
                    color = self.CHAR_COLORS.get(name, "#e0e0e0")
                    self.msg_queue.put({"sender": name, "text": text, "color": color})
                    log_tts_start(name, text)
                    self.audio_queue.put((text, name))

            except Exception as e:
                log_llm_end(name, error=str(e))
                self.msg_queue.put({
                    "sender": f"⚠️ Image ({name})",
                    "text": f"Échec envoi image : {e}",
                    "color": "#F44336"
                })

        def _run_all():
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futures = [
                    ex.submit(_send_to_agent, name, agent)
                    for name, agent in self._agents.items()
                ]
                for f in concurrent.futures.as_completed(futures):
                    try: f.result()
                    except Exception: pass

        threading.Thread(target=_run_all, daemon=True).start()
