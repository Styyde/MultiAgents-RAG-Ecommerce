# backend/sqltools.py
import json
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from langchain.tools import tool
from models import Produit, Stock
from database import Session
from outils_pdf import generer_devis_pdf
from catalogue_service import (
    alternatives_pour,
    quantite_en_stock,
    rechercher_produits,
    trouver_alternatives_en_stock as service_alternatives_en_stock,
)

# Nombre d'alternatives jointes automatiquement à une réponse de rupture
NB_ALTERNATIVES_AUTO = 3

# ============================================
# MAPPING DE NORMALISATION (Source unique de vérité)
# ============================================
NORMALISATION_CATEGORIES = {
    "ordinateur": "Ordinateur",
    "ordinateurs": "Ordinateur",
    "accessoire": "Accessoire",
    "accessoires": "Accessoire",
    "ecran": "Ecran",
    "écran": "Ecran",
    "smartphone": "Smartphone",
    "smartphones": "Smartphone",
    "audio": "Audio",
    "stockage": "Stockage"
}

# ============================================
# SCHÉMAS PYDANTIC (Contrats stricts pour l'Agent)
# ============================================

class RealtimePriceStockQuery(BaseModel):
    recherche: str = Field(
        ...,
        description="Le SKU exact (ex: 'SKU-XXXXXXXX') ou un ou plusieurs mots-clés du nom du produit (ex: 'MacBook Pro')."
    )
    quantite_souhaitee: int = Field(
        default=1,
        ge=1,
        description="Quantité voulue par le client (1 par défaut). Si le stock est inférieur, des alternatives en stock sont jointes."
    )

class AlternativesEnStockQuery(BaseModel):
    recherche: str = Field(
        ...,
        description="SKU exact ou nom du produit de référence (ex: 'Dell XPS 15')."
    )
    limite: int = Field(default=3, ge=1, le=10, description="Nombre maximal d'alternatives.")
    quantite_souhaitee: int = Field(
        default=1,
        ge=1,
        description="Quantité voulue : seules les alternatives ayant au moins ce stock sont proposées."
    )

class ProductByNameQuery(BaseModel):
    nom_produit: str = Field(
        ...,
        description="Mots-clés décrivant le produit recherché. Les termes doivent comporter plus de 2 caractères."
    )

class CategorySQLQuery(BaseModel):
    categorie: str = Field(
        ...,
        description="Le nom de la catégorie demandée. Valeurs acceptées : 'Ordinateur', 'Accessoire', 'Ecran', 'Smartphone', 'Audio', 'Stockage'."
    )
    limit: int = Field(
        default=10,
        description="Nombre maximal de produits à retourner (10 par défaut)."
    )

class ArticleItem(BaseModel):
    description: str = Field(..., description="Le nom ou la description précise du produit.")
    quantite: int = Field(..., ge=1, description="La quantité commandée (minimum 1).")
    prix_unitaire: float = Field(..., ge=0.0, description="Le prix unitaire HT du produit en Dirhams (MAD).")

class DevisSchema(BaseModel):
    nom_client: str = Field(
        ...,
        description="Nom complet du client (ex: 'Younes Snihji'). Ne doit pas être vide."
    )
    liste_articles: List[ArticleItem] = Field(
        ...,
        description="Liste contenant les articles validés par le client, avec leur description, quantité et prix unitaire."
    )

# ============================================
# NOUVEAUX SCHÉMAS PYDANTIC
# ============================================

class BudgetFilterQuery(BaseModel):
    categorie: Optional[str] = Field(
        None,
        description="Optionnel. Catégorie ciblée ('Ordinateur', 'Smartphone', etc.) pour affiner la recherche."
    )
    prix_max: Optional[float] = Field(
        None,
        description="Budget maximum en Dirhams (MAD). Exemple: 15000.0"
    )
    prix_min: Optional[float] = Field(
        None,
        description="Prix minimum en Dirhams (MAD). Exemple: 1000.0"
    )

class StockAlertQuery(BaseModel):
    uniquement_rupture: bool = Field(
        default=False,
        description="Si True, affiche uniquement les stocks à 0. Si False, affiche tous les produits dont la quantité est inférieure ou égale au seuil d'alerte."
    )

class StockUpdateQuery(BaseModel):
    recherche: str = Field(
        ...,
        description="Le SKU exact (ex: 'SKU-XXXXXXXX') ou des mots-clés du nom du produit dont on veut modifier le stock."
    )
    delta: int = Field(
        ...,
        description="Variation de quantité à appliquer au stock actuel. Positif pour une entrée de marchandises (ex: 10), négatif pour une vente/sortie (ex: -2)."
    )


# ============================================
# NOUVEAUX OUTILS SQL RENFORCÉS
# ============================================

@tool(args_schema=BudgetFilterQuery)
def get_produits_par_budget_sql(categorie: Optional[str] = None,
                                 prix_max: Optional[float] = None,
                                 prix_min: Optional[float] = None) -> str:
    """
    [OUTIL FILTRE BUDGET] Recherche des produits en fonction d'une fourchette de prix.

    Exemples d'utilisation par l'agent:
    - "Quels PC à moins de 16000 MAD ?" → {"prix_max": 16000, "categorie": "Ordinateur"}
    - "Produits entre 1000 et 4000 MAD" → {"prix_min": 1000, "prix_max": 4000}
    - "Smartphones à plus de 10000 MAD" → {"prix_min": 10000, "categorie": "Smartphone"}
    """
    with Session() as session:
        query = session.query(Produit)

        # Filtrage par catégorie
        if categorie:
            cat_norm = NORMALISATION_CATEGORIES.get(categorie.lower(), categorie)
            query = query.filter(Produit.categorie == cat_norm)

        # Filtrage par prix max
        if prix_max is not None:
            query = query.filter(Produit.prix_unitaire_ht <= prix_max)

        # Filtrage par prix min
        if prix_min is not None:
            query = query.filter(Produit.prix_unitaire_ht >= prix_min)

        # Validation : au moins un critère de prix
        if prix_max is None and prix_min is None:
            return json.dumps({
                "success": False,
                "error_code": "MISSING_CRITERIA",
                "message": "Veuillez spécifier un prix_max ou un prix_min pour la recherche."
            }, ensure_ascii=False)

        produits = query.order_by(Produit.prix_unitaire_ht.asc()).all()

        if not produits:
            # Message plus informatif
            criteres = []
            if prix_min is not None:
                criteres.append(f"prix ≥ {prix_min} MAD")
            if prix_max is not None:
                criteres.append(f"prix ≤ {prix_max} MAD")
            if categorie:
                criteres.insert(0, f"catégorie '{categorie}'")

            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé avec {', '.join(criteres)}."
            }, ensure_ascii=False)

        resultats = []
        for p in produits:
            stock = session.query(Stock).filter(Stock.id_produit == p.id).first()
            resultats.append({
                "sku": p.sku,
                "nom": p.description,
                "marque": p.marque,
                "prix_ht": p.prix_unitaire_ht,
                "stock": stock.quantite if stock else 0,
                "categorie": p.categorie
            })

        return json.dumps({
            "success": True,
            "data": resultats,
            "total": len(resultats)
        }, ensure_ascii=False)

@tool(args_schema=StockAlertQuery)
def get_alertes_stocks_sql(uniquement_rupture: bool = False) -> str:
    """
    [OUTIL ANALYTIQUE STOCK] Liste les produits en rupture de stock totale (quantité = 0)
    ou en alerte de réapprovisionnement (quantité <= seuil_alerte).
    À utiliser pour les rapports d'inventaire ou pour vérifier l'état de santé du stock général.
    """
    with Session() as session:
        if uniquement_rupture:
            stocks_critiques = session.query(Stock).filter(Stock.quantite == 0).all()
        else:
            stocks_critiques = session.query(Stock).filter(Stock.quantite <= Stock.seuil_alerte).all()

        if not stocks_critiques:
            return json.dumps({"success": True, "message": "Parfait ! Aucun produit n'est en rupture ou sous le seuil d'alerte."}, ensure_ascii=False)

        resultats = []
        for s in stocks_critiques:
            p = s.produit
            resultats.append({
                "sku": p.sku,
                "nom": p.description,
                "stock_actuel": s.quantite,
                "seuil_alerte": s.seuil_alerte,
                "etat": "RUPTURE TOTALE" if s.quantite == 0 else "ALERTE RÉAPPROVISIONNEMENT"
            })
        return json.dumps({"success": True, "data": resultats}, ensure_ascii=False)


@tool(args_schema=StockUpdateQuery)
def modifier_stock_produit_sql(recherche: str, delta: int) -> str:
    """
    [OUTIL MISE À JOUR STOCK] Ajuste la quantité en stock d'un produit après une vente
    ou une entrée de marchandises. Utiliser un delta positif pour une entrée, négatif pour une sortie/vente.
    """
    with Session() as session:
        produits = rechercher_produits(session, recherche)
        produit = produits[0] if produits else None

        if not produit:
            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé pour '{recherche}'"
            }, ensure_ascii=False)

        stock = session.query(Stock).filter(Stock.id_produit == produit.id).first()
        if not stock:
            return json.dumps({
                "success": False,
                "error_code": "STOCK_NOT_FOUND",
                "message": f"Aucune ligne de stock associée au produit '{produit.description}'"
            }, ensure_ascii=False)

        nouvelle_quantite = stock.quantite + delta
        if nouvelle_quantite < 0:
            return json.dumps({
                "success": False,
                "error_code": "INSUFFICIENT_STOCK",
                "message": f"Stock insuffisant pour '{produit.description}' (actuel: {stock.quantite}, demandé: {delta})"
            }, ensure_ascii=False)

        stock.quantite = nouvelle_quantite
        session.commit()

        return json.dumps({
            "success": True,
            "data": {
                "sku": produit.sku,
                "nom": produit.description,
                "nouveau_stock": stock.quantite
            }
        }, ensure_ascii=False)


def _etat_disponibilite(session, produit: Produit, quantite_souhaitee: int = 1) -> dict:
    """Prix/stock d'un produit ; si la quantité voulue n'est pas disponible, les alternatives
    en stock sont jointes ici, de façon déterministe, sans dépendre d'un second choix d'outil du LLM."""
    quantite_souhaitee = max(1, quantite_souhaitee)
    stock = quantite_en_stock(produit)
    if stock >= quantite_souhaitee:
        statut = "EN_STOCK"
    elif stock > 0:
        statut = "STOCK_INSUFFISANT"
    else:
        statut = "RUPTURE"

    etat = {
        "sku": produit.sku,
        "nom": produit.description,
        "marque": produit.marque,
        "prix_ht": produit.prix_unitaire_ht,
        "stock": stock,
        "en_stock": stock > 0,
        "quantite_souhaitee": quantite_souhaitee,
        "disponible": statut == "EN_STOCK",
        "statut": statut,
    }
    if statut != "EN_STOCK":
        etat.update(alternatives_pour(session, produit, NB_ALTERNATIVES_AUTO, quantite_souhaitee))
    return etat


@tool(args_schema=RealtimePriceStockQuery)
def get_realtime_price_stock(recherche: str, quantite_souhaitee: int = 1) -> str:
    """
    [OUTIL PRIORITAIRE] Récupère le prix HT et l'état des stocks en temps réel.
    Fonctionne aussi bien avec un SKU exact qu'avec des mots-clés de recherche textuelle.
    À utiliser systématiquement avant toute confirmation de prix ou de commande.
    Si le produit est en rupture (ou si la quantité voulue dépasse le stock), la réponse
    contient `alternatives_en_stock` : des produits équivalents réellement disponibles.
    """
    with Session() as session:
        produits = rechercher_produits(session, recherche)
        if not produits:
            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé pour '{recherche}'"
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "data": _etat_disponibilite(session, produits[0], quantite_souhaitee)
        }, ensure_ascii=False)


@tool(args_schema=ProductByNameQuery)
def get_product_price_stock_by_name(nom_produit: str) -> str:
    """
    [OUTIL RECHERCHE MULTI-MOTS] Recherche des produits par des correspondances de mots clés.
    À utiliser spécifiquement si 'get_realtime_price_stock' n'a renvoyé aucun résultat direct.
    Exige que tous les mots clés saisis soient inclus dans la description du produit.
    Les produits en rupture sont accompagnés de leurs `alternatives_en_stock`.
    """
    with Session() as session:
        mots = [m for m in nom_produit.lower().split() if len(m) > 2]
        if not mots:
            return json.dumps({
                "success": False,
                "error_code": "INVALID_QUERY",
                "message": "Termes de recherche trop courts (minimum 3 caractères)."
            }, ensure_ascii=False)

        produits = rechercher_produits(session, nom_produit)

        if not produits:
            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé pour '{nom_produit}'"
            }, ensure_ascii=False)

        resultats = [_etat_disponibilite(session, p) for p in produits]
        return json.dumps({"success": True, "data": resultats}, ensure_ascii=False)


@tool(args_schema=AlternativesEnStockQuery)
def trouver_alternatives_en_stock(recherche: str, limite: int = 3, quantite_souhaitee: int = 1) -> str:
    """
    [OUTIL ALTERNATIVES] Propose des produits de remplacement EN STOCK pour un produit donné
    (même catégorie, classés par similarité technique et proximité de prix).
    À utiliser quand le client demande explicitement une alternative, un équivalent ou un
    produit de remplacement. Prix et stocks proviennent de la base SQL (temps réel).
    """
    resultat = service_alternatives_en_stock(recherche, limite, quantite_souhaitee)
    return json.dumps(resultat, ensure_ascii=False)


@tool(args_schema=CategorySQLQuery)
def get_produits_par_categorie_sql(categorie: str, limit: int = 10) -> str:
    """
    [OUTIL RECHERCHE PAR CATÉGORIE] Extrait les produits appartenant à une catégorie spécifique.
    Retourne la liste des références assortie de leurs prix et stocks réels.
    La catégorie passée en paramètre est automatiquement nettoyée et normalisée en interne.
    """
    cat_norm = NORMALISATION_CATEGORIES.get(categorie.lower(), categorie)
    with Session() as session:
        produits = session.query(Produit).filter(Produit.categorie == cat_norm).limit(limit).all()
        if not produits:
            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit dans la catégorie '{categorie}'"
            }, ensure_ascii=False)

        resultats = []
        for p in produits:
            stock = session.query(Stock).filter(Stock.id_produit == p.id).first()
            resultats.append({
                "sku": p.sku,
                "nom": p.description,
                "marque": p.marque,
                "prix_ht": p.prix_unitaire_ht,
                "stock": stock.quantite if stock else 0,
                "en_stock": (stock.quantite > 0) if stock else False
            })
        return json.dumps({"success": True, "data": resultats}, ensure_ascii=False)

# backend/sqltools.py - Ajouter cet outil

class BrandQuery(BaseModel):
    marque: str = Field(..., description="Nom de la marque (ex: 'Apple', 'Samsung', 'Dell')")
    limit: int = Field(default=20, description="Nombre maximum de produits à retourner")

@tool(args_schema=BrandQuery)
def get_produits_par_marque_sql(marque: str, limit: int = 20) -> str:
    """
    [OUTIL RECHERCHE PAR MARQUE] Liste tous les produits d'une marque spécifique.
    À utiliser pour les questions comme "quels sont les produits Apple ?" ou "liste des produits Samsung".
    """
    with Session() as session:
        produits = session.query(Produit).filter(Produit.marque.ilike(f"%{marque}%")).limit(limit).all()
        if not produits:
            return json.dumps({
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "message": f"Aucun produit trouvé pour la marque '{marque}'"
            }, ensure_ascii=False)

        resultats = []
        for p in produits:
            stock = session.query(Stock).filter(Stock.id_produit == p.id).first()
            resultats.append({
                "nom": p.description,
                "prix_ht": p.prix_unitaire_ht,
                "stock": stock.quantite if stock else 0,
                "sku": p.sku
            })
        return json.dumps({
            "success": True,
            "data": resultats
        }, ensure_ascii=False)

@tool(args_schema=DevisSchema)
def creer_devis_pdf_tool(nom_client: str, liste_articles: List[ArticleItem]) -> str:
    """
    [OUTIL DE GÉNÉRATION DE DEVIS PDF] Génère un document PDF formel et légal pour une commande.
    À n'activer UNIQUEMENT que lorsque le client demande explicitement une trace écrite ou valide un panier d'achat.
    Prend le nom complet du client et la liste d'objets contenant 'description', 'quantite' et 'prix_unitaire'.
    """
    try:
        # Conversion explicite des objets Pydantic en dictionnaires standards attendus par le script métier
        articles_dict = [item.model_dump() for item in liste_articles]
        chemin = generer_devis_pdf(nom_client, articles_dict)
        return json.dumps({
            "success": True,
            "data": {
                "message": f"SUCCÈS : Devis PDF correctement généré pour {nom_client}.",
                "chemin": chemin
            }
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error_code": "PDF_GENERATION_ERROR",
            "message": str(e)
        }, ensure_ascii=False)


# Liste consolidée exportable pour l'agent
SQL_TOOLS = [
    get_realtime_price_stock,
    get_product_price_stock_by_name,
    trouver_alternatives_en_stock,
    get_produits_par_categorie_sql,
    creer_devis_pdf_tool,
    get_produits_par_marque_sql,
    get_produits_par_budget_sql,
    get_alertes_stocks_sql,
    modifier_stock_produit_sql
]
