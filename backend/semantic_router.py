# backend/semantic_router_v2.py
import os
import numpy as np
from sentence_transformers import SentenceTransformer
from typing import Tuple, Dict
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# 1. CONFIGURATION DES ENDPOINTS
# ============================================
AGENT_ENDPOINTS = {
    "catalogue_agent": os.getenv("CATALOGUE_AGENT_URL", "http://localhost:5001/process"),
    "legal_agent": os.getenv("LEGAL_AGENT_URL", "http://localhost:5003/ask"),
    "fast_response": "fast_response_local"
}

# ============================================
# 2. MÉTADONNÉES DE ROUTAGE (AMÉLIORÉES)
# ============================================
ROUTER_METADATA = {
    "catalogue_agent": """
    Expert en catalogue informatique et ventes. Gère uniquement :
    - Prix, stock, disponibilité des produits (ordinateurs, téléphones, accessoires...)
    - Caractéristiques techniques
    - Devis et commandes
    - Promotions
    Exemples: "prix du MacBook", "stock iPhone", "devis pour 3 PC", "quels ordinateurs avez-vous ?"
    """,

    "legal_agent": """
    Expert juridique en droit commercial marocain. Spécialisé dans :
    - Loi n° 15-95 sur le code de commerce
    - Articles de loi (ex: article 6, article 7...)
    - Qualité de commerçant, conditions pour devenir commerçant
    - Nationalité et exercice du commerce au Maroc
    - Droits et obligations des commerçants
    - Garantie légale, SAV, retours, produits défectueux, litiges
    - Toute question juridique ou sur des textes de loi
    Exemples: "article 6 loi 15-95", "qualité de commerçant", "peut-on faire du commerce sans être marocain ?", "droit commercial marocain"
    """,

    "fast_response": """
    Réponses rapides pour salutations et formules de politesse :
    - Bonjour, salut, hello, hi
    - Merci, au revoir, bye
    Répondre directement sans appeler d'agent externe
    """
}

# ============================================
# 3. SEUILS DE CONFIDENCE (ajustés)
# ============================================
THRESHOLDS = {
    "catalogue_agent": 0.48,   # Légèrement baissé pour plus de flexibilité
    "legal_agent": 0.55,       # Baissé pour mieux capter les questions juridiques
    "fast_response": 0.85
}

# ============================================
# 4. LE ROUTEUR SÉMANTIQUE
# ============================================
class ProfessionalSemanticRouter:
    def __init__(self):
        self.encoder = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        self.agent_embeddings = {}
        self._precompute_embeddings()
        logger.info(f"✅ Routeur sémantique initialisé avec {len(self.agent_embeddings)} agents")

    def _precompute_embeddings(self):
        for agent_name, description in ROUTER_METADATA.items():
            embedding = self.encoder.encode(description, normalize_embeddings=True)
            self.agent_embeddings[agent_name] = {
                "embedding": embedding,
                "threshold": THRESHOLDS.get(agent_name, 0.55),
                "endpoint": AGENT_ENDPOINTS.get(agent_name)
            }
            logger.info(f"   - {agent_name} : embedding calculé")

    def route(self, user_query: str) -> Tuple[str, str, float]:
        query_clean = user_query.lower().strip()

        # Détection rapide des salutations
        if any(word in query_clean for word in ["bonjour", "salut", "hello", "hi", "coucou"]) and len(query_clean.split()) < 6:
            return "fast_response", "fast_response_local", 0.95

        # Vectorisation de la question
        query_embedding = self.encoder.encode(user_query, normalize_embeddings=True)

        best_agent = None
        best_score = 0
        best_endpoint = None

        for agent_name, agent_data in self.agent_embeddings.items():
            similarity = float(np.dot(agent_data["embedding"], query_embedding))
            threshold = agent_data["threshold"]

            logger.debug(f"Agent {agent_name}: score={similarity:.3f} (seuil={threshold})")

            if similarity > best_score and similarity >= threshold:
                best_score = similarity
                best_agent = agent_name
                best_endpoint = agent_data["endpoint"]

        # Fallback intelligent
        if best_agent is None:
            # Si la question contient des mots-clés juridiques → forcer legal_agent
            legal_keywords = ["article", "loi", "15-95", "commerçant", "qualité de commerçant", 
                            "droit commercial", "nationalité", "marocain", "code de commerce"]
            if any(kw in query_clean for kw in legal_keywords):
                best_agent = "legal_agent"
                best_endpoint = AGENT_ENDPOINTS["legal_agent"]
                best_score = 0.65
                logger.info(f"🔀 Détection mot-clé juridique → forcé vers legal_agent")
            else:
                best_agent = "catalogue_agent"
                best_endpoint = AGENT_ENDPOINTS["catalogue_agent"]
                best_score = 0.50
                logger.warning(f"⚠️ Fallback sur catalogue_agent")

        logger.info(f"🎯 Routage final: {best_agent} (score: {best_score:.3f})")
        return best_agent, best_endpoint, best_score

    def get_all_agents(self) -> Dict:
        return {
            agent: {
                "endpoint": data["endpoint"],
                "threshold": data["threshold"]
            }
            for agent, data in self.agent_embeddings.items()
        }


# Singleton
semantic_router = ProfessionalSemanticRouter()