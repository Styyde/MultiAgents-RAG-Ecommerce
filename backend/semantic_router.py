import os
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from typing import Tuple, Dict, Optional
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# 1. CONFIGURATION DES ENDPOINTS
# ============================================
AGENT_ENDPOINTS = {
    "sql_agent": os.getenv("SQL_AGENT_URL", "http://localhost:5001/process"),
    "neo4j_agent": os.getenv("NEO4J_AGENT_URL", "http://localhost:5005/process"),
    "legal_agent": os.getenv("LEGAL_AGENT_URL", "http://localhost:5003/ask"),
    "fast_response": "fast_response_local"
}

# ============================================
# 2. MÉTADONNÉES DE ROUTAGE (Optimisées pour l'Embedding)
# ============================================
ROUTER_METADATA = {
    "sql_agent": """
    Questions factuelles sur la base de données relationnelle, les prix, les tarifs, 
    les coûts, le stock disponible, la quantité restante, la rupture de stock, la livraison,
    les commandes, les factures, les devis.
    Quel est le prix exact de ce produit ? Combien en reste-t-il en stock ? Fais-moi un devis.
    """,
    "neo4j_agent": """
    Recherche par graphe, caractéristiques techniques, spécifications, processeur, RAM, stockage, USB-C.
    Trouver des alternatives, des produits similaires, faire des comparaisons. 
    Vérifier la compatibilité matérielle, trouver des accessoires. Un ordinateur puissant pour le gaming.
    """,
    "legal_agent": """
    Expertise juridique, droit commercial marocain, législation, articles de loi, 
    code de commerce, statut de commerçant, actes de commerce, tribunal de commerce.
    """,
    "fast_response": """
    Salutations, politesses, bonjour, salut, merci beaucoup, au revoir, à bientôt.
    """
}

# ============================================
# 3. SEUILS DE CONFIDENCE
# ============================================
THRESHOLDS = {
    "sql_agent": 0.55,
    "neo4j_agent": 0.45,
    "legal_agent": 0.52,
    "fast_response": 0.80
}

# ============================================
# 4. RÈGLES MÉTIER (Mots-clés stricts via Regex)
# Note: Suppression des verbes génériques ("montre", "affiche", "list")
# ============================================
SQL_KEYWORDS = [r"prix", r"coût", r"tarif", r"stock\b", r"disponible", r"quantité",
                r"commande", r"livraison", r"facture", r"combien coûte", r"reste-t-il",
                r"devis", r"rupture"]

# Salutation = mot entier (sinon "hi" capture "machine", "chiffre"... et détourne la question)
GREETING_PATTERN = re.compile(r"\b(bonjour|salut|hello|hi|coucou)\b")

NEO4J_KEYWORDS = [r"alternative\w*", r"similaire\w*", r"compatible\w*", r"recommand\w*", 
                  r"semblable", r"remplacement", r"mieux", r"comparer", r"vs\b", 
                  r"accessoire\w*", r"processeur", r"\bram\b", r"stockage", r"interface",
                  r"puissant", r"gaming", r"pouces"]

LEGAL_KEYWORDS = [r"article", r"loi\b", r"15-95", r"commerçant", r"droit commercial", 
                  r"code de commerce", r"preuve", r"acte mixte", r"tribunal"]

# ============================================
# 5. LE ROUTEUR SÉMANTIQUE
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

    def _count_matches(self, query: str, keywords: list) -> int:
        """Compte le nombre de mots-clés exacts trouvés dans la requête via Regex."""
        return sum(1 for kw in keywords if re.search(r'\b' + kw + r'\b', query))

    def route(self, user_query: str, dernier_agent: Optional[str] = None) -> Tuple[str, str, float]:
        """`dernier_agent` : agent ayant traité le tour précédent de la session. Il n'est utilisé
        que lorsque l'intention est floue (ex. « et celui-là ? »), pour garder le fil de la conversation."""
        query_clean = user_query.lower().strip()

        # 1. Salutations rapides (Court-circuit)
        if GREETING_PATTERN.search(query_clean) and len(query_clean.split()) < 6:
            return "fast_response", "fast_response_local", 0.99
        
        # 2. Comptage des intentions par mots-clés
        sql_hits = self._count_matches(query_clean, SQL_KEYWORDS)
        neo4j_hits = self._count_matches(query_clean, NEO4J_KEYWORDS)
        legal_hits = self._count_matches(query_clean, LEGAL_KEYWORDS)

        # Si un agent domine clairement au niveau des mots-clés, on route directement
        if legal_hits > 0 and legal_hits > sql_hits and legal_hits > neo4j_hits:
            logger.info(f"🎯 Règle métier stricte → legal_agent ({legal_hits} hits)")
            return "legal_agent", AGENT_ENDPOINTS["legal_agent"], 0.95
            
        if sql_hits > neo4j_hits:
            logger.info(f"🎯 Règle métier stricte → sql_agent ({sql_hits} hits)")
            return "sql_agent", AGENT_ENDPOINTS["sql_agent"], 0.95
            
        if neo4j_hits > sql_hits:
            logger.info(f"🎯 Règle métier stricte → neo4j_agent ({neo4j_hits} hits)")
            return "neo4j_agent", AGENT_ENDPOINTS["neo4j_agent"], 0.95

        # 3. Si égalité ou aucun mot-clé, on passe au routage sémantique pur
        query_embedding = self.encoder.encode(user_query, normalize_embeddings=True)
        best_agent = None
        best_score = 0.0
        
        for agent_name, agent_data in self.agent_embeddings.items():
            # Exclure fast_response du routage sémantique complexe
            if agent_name == "fast_response":
                continue
                
            similarity = float(np.dot(agent_data["embedding"], query_embedding))
            if similarity > best_score:
                best_score = similarity
                best_agent = agent_name
        
        # 4. Fallback intelligent basé sur les seuils configurés
        if best_agent and best_score >= self.agent_embeddings[best_agent]["threshold"]:
            logger.info(f"🎯 Routage sémantique → {best_agent} (score: {best_score:.3f})")
            return best_agent, AGENT_ENDPOINTS[best_agent], best_score
            
        # Intention floue au sein d'une conversation : c'est très probablement une question
        # de suivi, on reste sur l'agent du tour précédent.
        if dernier_agent in AGENT_ENDPOINTS and dernier_agent != "fast_response":
            logger.info(f"↩️ Score faible ({best_score:.2f}) : suite de conversation → {dernier_agent}")
            return dernier_agent, AGENT_ENDPOINTS[dernier_agent], best_score

        # Fallback par défaut si l'intention est vraiment floue :
        # on garde l'agent le mieux scoré plutôt que de forcer neo4j_agent,
        # sinon toute question légale mal classée finit systématiquement sur neo4j.
        fallback_agent = best_agent or "neo4j_agent"
        logger.warning(f"⚠️ Score trop bas ({best_score:.2f} pour {fallback_agent}). Fallback vers {fallback_agent} malgré tout.")
        return fallback_agent, AGENT_ENDPOINTS[fallback_agent], best_score

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