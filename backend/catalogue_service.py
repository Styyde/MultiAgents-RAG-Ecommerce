# backend/catalogue_service.py
"""
Service catalogue déterministe (aucun LLM), partagé par l'agent SQL et l'agent Neo4j.

Règle de conception : SQLite est la source de vérité pour le prix, le stock et la
catégorie ; Neo4j ne sert qu'à CLASSER les candidats (similarité vectorielle et
caractéristiques techniques partagées).

  - éligibilité d'une alternative : même catégorie, stock >= quantité voulue  -> SQL
  - pertinence d'une alternative  : similarité + specs communes + prix proche -> graphe + SQL

Si Neo4j est indisponible (ou le produit absent du graphe), le classement se replie
sur le seul critère de prix : la fonctionnalité reste disponible, en mode dégradé.
"""
import logging
import re
import unicodedata
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from database import Session
from models import Produit, Stock

logger = logging.getLogger(__name__)

SKU_PATTERN = re.compile(r"^SKU-[A-Z0-9]+$", re.IGNORECASE)

# Pondération du score d'une alternative (somme = 1)
POIDS_SIMILARITE = 0.5
POIDS_SPECS = 0.3
POIDS_PRIX = 0.2
# Nombre de caractéristiques partagées à partir duquel le score "specs" est maximal
SPECS_SATURATION = 5
LIMITE_MAX_ALTERNATIVES = 10

# Relations "caractéristique technique" du graphe (hors marque et catégorie)
RELATIONS_SPECS = ["HAS_PROCESSOR", "HAS_RAM", "HAS_STORAGE", "HAS_INTERFACE", "HAS_FEATURE"]

CYPHER_SIGNAUX_ALTERNATIVES = """
MATCH (ref:Product {sku: $sku})-[:BELONGS_TO]->(:Category)<-[:BELONGS_TO]-(alt:Product)
WHERE alt <> ref
RETURN alt.sku AS sku,
       CASE WHEN ref.embedding IS NULL OR alt.embedding IS NULL THEN 0.0
            ELSE vector.similarity.cosine(ref.embedding, alt.embedding) END AS similarite,
       [(ref)-[r1]->(x)<-[r2]-(alt)
            WHERE type(r1) = type(r2) AND type(r1) IN $relations
            | coalesce(x.name, x.size)] AS specs_communes
"""


# ============================================
# RÉSOLUTION DES PRODUITS (SQL)
# ============================================

def nom_court(description: str) -> str:
    """'PC Portable Dell XPS 15 - Processeur ...' -> 'PC Portable Dell XPS 15'."""
    return (description or "").split(" - ")[0].strip()


def quantite_en_stock(produit: Produit) -> int:
    return produit.stock.quantite if produit.stock else 0


def rechercher_produits(session, recherche: str) -> list:
    """SKU exact (insensible à la casse) ou mots-clés devant TOUS figurer dans la description."""
    terme = (recherche or "").strip()
    requete = session.query(Produit).options(joinedload(Produit.stock))
    if SKU_PATTERN.match(terme):
        produit = requete.filter(func.upper(Produit.sku) == terme.upper()).first()
        return [produit] if produit else []

    mots = [m for m in terme.lower().split() if len(m) > 2]
    if not mots:
        return []
    conditions = [Produit.description.ilike(f"%{mot}%") for mot in mots]
    return requete.filter(*conditions).order_by(Produit.id).all()


def resoudre_sku(recherche: str) -> Optional[str]:
    """Nom ou SKU -> SKU du catalogue, ou None si aucun produit ne correspond."""
    with Session() as session:
        produits = rechercher_produits(session, recherche)
        return produits[0].sku if produits else None


def index_catalogue(session, skus: Iterable) -> dict:
    """{SKU en majuscules: Produit} en une seule requête (stock préchargé)."""
    cles = {str(s).upper() for s in skus if s}
    if not cles:
        return {}
    produits = (
        session.query(Produit)
        .options(joinedload(Produit.stock))
        .filter(func.upper(Produit.sku).in_(cles))
        .all()
    )
    return {p.sku.upper(): p for p in produits}


def fiche_alternative(produit: Produit) -> dict:
    return {
        "sku": produit.sku,
        "nom": nom_court(produit.description),
        "marque": produit.marque,
        "categorie": produit.categorie,
        "prix_ht": produit.prix_unitaire_ht,
        "stock": quantite_en_stock(produit),
    }


# ============================================
# ALTERNATIVES EN STOCK
# ============================================

def _signaux_graphe(sku: str) -> dict:
    """{SKU: {similarite, specs_communes}} pour les produits de même catégorie dans le graphe.
    Lève une exception si Neo4j est injoignable : l'appelant décide du repli."""
    # Import paresseux : évite un import circulaire (graphtools importe ce module)
    # et ne crée la connexion Neo4j que si on en a réellement besoin.
    from graphtools import neo4j_conn

    with neo4j_conn.get_driver().session() as session:
        lignes = session.run(CYPHER_SIGNAUX_ALTERNATIVES, {"sku": sku, "relations": RELATIONS_SPECS}).data()
    return {
        str(l["sku"]).upper(): {
            "similarite": float(l["similarite"] or 0.0),
            "specs_communes": [s for s in (l["specs_communes"] or []) if s],
        }
        for l in lignes
        if l.get("sku")
    }


def score_alternative(similarite: float, nb_specs_communes: int, prix_reference: float, prix_alternative: float) -> float:
    score_specs = min(nb_specs_communes, SPECS_SATURATION) / SPECS_SATURATION
    if prix_reference and prix_reference > 0:
        score_prix = max(0.0, 1.0 - abs(prix_alternative - prix_reference) / prix_reference)
    else:
        score_prix = 0.0
    return POIDS_SIMILARITE * similarite + POIDS_SPECS * score_specs + POIDS_PRIX * score_prix


def alternatives_pour(session, reference: Produit, limite: int = 3, quantite_min: int = 1) -> dict:
    """Alternatives à `reference` ayant au moins `quantite_min` unités en stock, triées par pertinence."""
    limite = max(1, min(int(limite), LIMITE_MAX_ALTERNATIVES))
    quantite_min = max(1, int(quantite_min))

    try:
        signaux = _signaux_graphe(reference.sku)
    except Exception as exc:  # Neo4j arrêté, timeout, index manquant...
        logger.warning("Graphe indisponible pour les alternatives de %s (%s) : repli sur le catalogue SQL", reference.sku, exc)
        signaux = {}

    # Univers des candidats : défini par SQL (catégorie + stock réel), jamais par le graphe.
    candidats = (
        session.query(Produit)
        .join(Stock, Stock.id_produit == Produit.id)
        .options(joinedload(Produit.stock))
        .filter(
            Produit.categorie == reference.categorie,
            Produit.id != reference.id,
            Stock.quantite >= quantite_min,
        )
        .all()
    )

    alternatives = []
    for produit in candidats:
        signal = signaux.get(produit.sku.upper(), {"similarite": 0.0, "specs_communes": []})
        fiche = fiche_alternative(produit)
        fiche["ecart_prix_ht"] = round(produit.prix_unitaire_ht - reference.prix_unitaire_ht, 2)
        fiche["caracteristiques_communes"] = signal["specs_communes"]
        fiche["score"] = round(
            score_alternative(
                signal["similarite"],
                len(signal["specs_communes"]),
                reference.prix_unitaire_ht,
                produit.prix_unitaire_ht,
            ),
            3,
        )
        alternatives.append(fiche)

    # Tri déterministe : score, puis prix le plus proche, puis SKU
    alternatives.sort(key=lambda a: (-a["score"], abs(a["ecart_prix_ht"]), a["sku"]))
    return {
        "alternatives_en_stock": alternatives[:limite],
        "source_classement": "graphe" if signaux else "catalogue_sql",
    }


def trouver_alternatives_en_stock(recherche: str, limite: int = 3, quantite_souhaitee: int = 1) -> dict:
    """Point d'entrée commun aux deux agents : produit de référence (nom ou SKU) -> alternatives en stock."""
    with Session() as session:
        produits = rechercher_produits(session, recherche)
        if not produits:
            return {
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé pour '{recherche}'",
            }
        reference = produits[0]
        stock = quantite_en_stock(reference)
        return {
            "success": True,
            "produit_reference": {
                "sku": reference.sku,
                "nom": nom_court(reference.description),
                "prix_ht": reference.prix_unitaire_ht,
                "stock": stock,
                "disponible": stock >= max(1, quantite_souhaitee),
            },
            **alternatives_pour(session, reference, limite, quantite_souhaitee),
        }


def filtrer_en_stock(lignes: list, limite: int) -> tuple:
    """Enrichit des résultats du graphe avec prix/stock SQL et retire ceux qui ne sont pas vendables.

    Retourne (lignes_en_stock[:limite], nb_exclues). Un produit présent dans le graphe
    mais absent du catalogue SQL est exclu : on ne peut ni le chiffrer ni le vendre.
    """
    with Session() as session:
        index = index_catalogue(session, (l.get("sku") for l in lignes))
        en_stock, exclues = [], 0
        for ligne in lignes:
            produit = index.get(str(ligne.get("sku", "")).upper())
            if produit is None or quantite_en_stock(produit) <= 0:
                exclues += 1
                continue
            if len(en_stock) < limite:
                en_stock.append({
                    **ligne,
                    "prix_ht": produit.prix_unitaire_ht,
                    "stock": quantite_en_stock(produit),
                })
        return en_stock, exclues


# ============================================
# OUTILS DE COMPARAISON DE TEXTE
# ============================================

def normaliser_texte(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation : 'MacBook Pro (M3)' -> 'macbookprom3'."""
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", sans_accents.lower())
