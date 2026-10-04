# backend/agent_common.py
"""
Briques communes aux agents LLM à outils (agent SQL, agent Neo4j).

- parse_history        : historique reçu de la gateway -> messages LangChain (validé, borné)
- statut_depuis_outils : succès/échec déterminé à partir des RÉSULTATS d'outils, pas du texte du LLM
- garantir_alternatives: si un outil a fourni des alternatives en stock et que le LLM n'en cite
                         aucune, on les ajoute de façon déterministe à la réponse
- construire_reponse   : contrat de réponse unique {success, data, error_code?, message?, outils}
"""
import json
import re
from typing import Any, Iterator, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from catalogue_service import normaliser_texte

MAX_HISTORY_MESSAGES = 12
MAX_MESSAGE_CHARS = 2000

# Clés portant les résultats métier dans les retours d'outils (SQL et graphe)
CLES_RESULTATS = (
    "data", "resultats", "alternatives", "alternatives_en_stock", "recommandations",
    "produits_compatibles", "par_categorie", "resultats_vectoriels_bruts",
)

_SALUTATION = re.compile(r"^\s*(bonjour|salut|hello|hi|coucou)\b", re.IGNORECASE)


def est_salutation_seule(message: str) -> bool:
    """'Bonjour' -> True ; 'Bonjour, une alternative au Dell XPS ?' -> False."""
    return bool(_SALUTATION.match(message)) and len(message.split()) < 4


def parse_history(raw: Any) -> list:
    """Convertit [{"role": "user"|"assistant", "content": str}, ...] en messages LangChain.
    Les entrées invalides sont ignorées (l'historique vient du réseau : on ne lui fait pas confiance)."""
    if not isinstance(raw, list):
        return []
    messages: list[BaseMessage] = []
    for item in raw[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content[:MAX_MESSAGE_CHARS]
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def _charger(observation: Any) -> Any:
    if isinstance(observation, (dict, list)):
        return observation
    try:
        return json.loads(observation)
    except (TypeError, ValueError):
        return None


def observation_a_des_resultats(observation: Any) -> bool:
    data = _charger(observation)
    if not isinstance(data, dict):
        return False  # texte d'exception, outil inconnu, sortie illisible
    if "success" in data:
        return bool(data["success"])
    if "error" in data:
        return False
    presentes = [data[c] for c in CLES_RESULTATS if c in data]
    if presentes:
        return any(bool(v) for v in presentes)
    return set(data) != {"message"}  # ex: {"message": "Aucun produit trouvé ..."}


def statut_depuis_outils(etapes: list) -> tuple:
    """(success, error_code). Sans appel d'outil, la réponse du LLM est considérée valide."""
    if not etapes:
        return True, None
    observations = [obs for _, obs in etapes]
    if any(observation_a_des_resultats(obs) for obs in observations):
        return True, None
    if any(not isinstance(_charger(obs), dict) for obs in observations):
        return False, "TOOL_ERROR"
    return False, "NO_RESULT"


def _parcourir_dicts(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for valeur in obj.values():
            yield from _parcourir_dicts(valeur)
    elif isinstance(obj, list):
        for valeur in obj:
            yield from _parcourir_dicts(valeur)


def _alternatives_proposees(etapes: list) -> list:
    """[(nom du produit de référence, [alternatives])] trouvées dans les retours d'outils."""
    trouvees, deja_vues = [], set()
    for _, observation in etapes:
        for bloc in _parcourir_dicts(_charger(observation)):
            alternatives = bloc.get("alternatives_en_stock")
            if not alternatives:
                continue
            reference = bloc.get("produit_reference") or bloc
            nom_reference = str(reference.get("nom") or reference.get("sku") or "ce produit").split(" - ")[0]
            cle = (nom_reference, tuple(a.get("sku") for a in alternatives))
            if cle not in deja_vues:
                deja_vues.add(cle)
                trouvees.append((nom_reference, alternatives))
    return trouvees


def _format_prix(prix: Optional[float]) -> str:
    return f"{prix:,.0f}".replace(",", " ") if isinstance(prix, (int, float)) else "?"


def garantir_alternatives(reponse: str, etapes: list) -> str:
    texte_normalise = normaliser_texte(reponse)
    ajouts = []
    for nom_reference, alternatives in _alternatives_proposees(etapes):
        citee = any(
            cle and cle in texte_normalise
            for a in alternatives
            for cle in (normaliser_texte(a.get("sku", "")), normaliser_texte(a.get("nom", "")))
        )
        if citee:
            continue
        lignes = [
            f"- {a.get('nom')} ({a.get('marque')}) : {_format_prix(a.get('prix_ht'))} MAD HT, {a.get('stock')} en stock"
            for a in alternatives
        ]
        ajouts.append(f"Alternatives disponibles pour « {nom_reference} » :\n" + "\n".join(lignes))
    if not ajouts:
        return reponse
    return (reponse.rstrip() + "\n\n" + "\n\n".join(ajouts)).strip()


def construire_reponse(resultat: dict) -> dict:
    """Sortie d'AgentExecutor (avec return_intermediate_steps=True) -> contrat JSON de l'agent."""
    texte = resultat.get("output")
    texte = texte if isinstance(texte, str) else str(texte or "")
    etapes = resultat.get("intermediate_steps") or []
    texte = garantir_alternatives(texte, etapes)
    succes, code = statut_depuis_outils(etapes)
    reponse = {
        "success": succes,
        "data": texte,
        "outils": [getattr(action, "tool", "?") for action, _ in etapes],
    }
    if not succes:
        reponse.update(error_code=code, message=texte)
    return reponse
