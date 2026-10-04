import os
import json
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from langchain.tools import tool
from database import engine
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import List, Dict

# ============================================
# 1. FONCTION MÉTIER (Génération PDF pure)
# ============================================
def generer_devis_pdf_metier(nom_client: str, liste_articles: list) -> str:
    """Génère un Devis PDF physique et retourne le chemin du fichier."""
    dossier_sortie = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'outputs'))
    if not os.path.exists(dossier_sortie):
        os.makedirs(dossier_sortie)

    date_jour = datetime.now().strftime("%Y%m%d_%H%M%S")
    nom_fichier = f"DEVIS_{nom_client.replace(' ', '')}_{date_jour}.pdf"
    chemin_complet = os.path.join(dossier_sortie, nom_fichier)

    doc = SimpleDocTemplate(chemin_complet, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>MAGASIN INFORMATIQUE EMI</b>", styles['Title']))
    elements.append(Paragraph("Devis Officiel", styles['Heading2']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"<b>Client :</b> {nom_client}", styles['Normal']))
    elements.append(Paragraph(f"<b>Date :</b> {datetime.now().strftime('%d/%m/%Y')}", styles['Normal']))
    elements.append(Spacer(1, 20))

    donnees_tableau = [["Description", "Quantité", "Prix Unitaire HT", "Total Ligne HT"]]
    total_ht = 0

    for article in liste_articles:
        desc = article.get("description", "Produit Inconnu")
        qte = int(article.get("quantite", 1))
        pu = float(article.get("prix_unitaire", 0.0))
        ligne_ht = qte * pu
        total_ht += ligne_ht
        donnees_tableau.append([desc, str(qte), f"{pu:.2f} MAD", f"{ligne_ht:.2f} MAD"])

    tva = total_ht * 0.20
    total_ttc = total_ht + tva

    donnees_tableau.append(["", "", "Sous-total HT", f"{total_ht:.2f} MAD"])
    donnees_tableau.append(["", "", "TVA (20%)", f"{tva:.2f} MAD"])
    donnees_tableau.append(["", "", "TOTAL TTC", f"{total_ttc:.2f} MAD"])

    t = Table(donnees_tableau)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#004b87')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
    ]))
    elements.append(t)
    doc.build(elements)
    
    return chemin_complet



# ============================================
# 2. WRAPPER POUR IMPORT DANS sqltools.py
# ============================================
def generer_devis_pdf(nom_client: str, liste_articles: list) -> str:
    return generer_devis_pdf_metier(nom_client, liste_articles)


# ============================================
# 3. OUTIL LANGCHAIN POUR L'AGENT
# ============================================
class DevisSchema(BaseModel):
    nom_client: str = Field(
        description="Nom complet du client (ex: 'Younes Snihji'). Ne doit pas être vide."
    )
    liste_articles: List[Dict] = Field(
        description="Liste d'objets. Chaque objet DOIT contenir exactement: 'description' (str), 'quantite' (int) et 'prix_unitaire' (float). Exemple: [{'description': 'MacBook Pro', 'quantite': 1, 'prix_unitaire': 22000}]"
    )


@tool(args_schema=DevisSchema)
def creer_devis_pdf_tool(nom_client: str, liste_articles: List[Dict]) -> str:
    """
    [OUTIL DE GÉNÉRATION DE DEVIS OFFICIEL]
    À utiliser UNIQUEMENT lorsque le client valide explicitement sa commande ou demande formellement un devis écrit en PDF.
    Prend le nom du client et la liste complète des articles validés avec leurs quantités et prix unitaires HT.
    """
    try:
        chemin_pdf = generer_devis_pdf_metier(nom_client, liste_articles)
        return json.dumps({
            "statut": "Succès",
            "message": f"Le devis PDF a été généré avec succès pour {nom_client}.",
            "chemin_fichier": chemin_pdf
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "statut": "Erreur",
            "message": f"Échec de la génération du devis : {str(e)}"
        }, ensure_ascii=False)


# ============================================
# 4. OUTILS SQL
# ============================================

@tool
def rechercher_produit_avec_stock(nom: str) -> str:
    """
    [OUTIL PRINCIPAL DE RECHERCHE DE PRODUIT]
    À utiliser en premier choix pour toute recherche de produit spécifique.
    """
    with engine.connect() as conn:
        query = text("""
            SELECT p.id, p.description, p.prix_unitaire_ht, p.categorie, p.marque, COALESCE(s.quantite, 0) as quantite
            FROM T_Produits p
            LEFT JOIN T_Stocks s ON p.id = s.id_produit
            WHERE p.description LIKE :nom
        """)
        result = conn.execute(query, {"nom": f"%{nom}%"}).fetchall()
    if not result:
        return json.dumps([])
    produits = [dict(row._mapping) for row in result]
    return json.dumps(produits, ensure_ascii=False)


@tool
def rechercher_produits_par_categorie(categorie: str) -> str:
    """
    [OUTIL DE RECHERCHE PAR CATÉGORIE]
    À utiliser UNIQUEMENT pour une demande de gamme générale.
    Catégories valides: 'Ordinateur', 'Accessoire', 'Ecran', 'Smartphone', 'Audio', 'Stockage'
    """
    with engine.connect() as conn:
        query = text("""
            SELECT p.id, p.description, p.prix_unitaire_ht, p.categorie, p.marque, COALESCE(s.quantite, 0) as quantite
            FROM T_Produits p
            LEFT JOIN T_Stocks s ON p.id = s.id_produit
            WHERE p.categorie = :categorie
        """)
        result = conn.execute(query, {"categorie": categorie}).fetchall()
    produits = [dict(row._mapping) for row in result]
    return json.dumps(produits, ensure_ascii=False)


@tool
def verifier_stock_produit(nom_produit: str) -> str:
    """
    [OUTIL DE VÉRIFICATION DE STOCK]
    Vérifie la quantité disponible d'un produit spécifique.
    """
    with engine.connect() as conn:
        query = text("""
            SELECT p.description, COALESCE(s.quantite, 0) as quantite
            FROM T_Produits p
            LEFT JOIN T_Stocks s ON p.id = s.id_produit
            WHERE p.description LIKE :nom
            LIMIT 1
        """)
        row = conn.execute(query, {"nom": f"%{nom_produit}%"}).fetchone()
    if not row:
        return json.dumps({"erreur": "Produit non trouvé"})
    return json.dumps({"description": row.description, "quantite": row.quantite}, ensure_ascii=False)


@tool
def lister_categories() -> str:
    """
    [OUTIL LISTE DES CATÉGORIES]
    Retourne toutes les catégories disponibles.
    """
    with engine.connect() as conn:
        query = text("SELECT DISTINCT categorie FROM T_Produits")
        result = conn.execute(query).fetchall()
    categories = [row[0] for row in result]
    return json.dumps(categories, ensure_ascii=False)


@tool
def get_produits_en_rupture() -> str:
    """
    [OUTIL PRODUITS EN RUPTURE]
    Liste les produits avec stock critique ou nul.
    """
    with engine.connect() as conn:
        query = text("""
            SELECT p.description, s.quantite, s.seuil_alerte
            FROM T_Produits p
            JOIN T_Stocks s ON p.id = s.id_produit
            WHERE s.quantite = 0 OR s.quantite < s.seuil_alerte
        """)
        result = conn.execute(query).fetchall()
    ruptures = [{"description": row[0], "quantite": row[1], "seuil_alerte": row[2]} for row in result]
    return json.dumps(ruptures, ensure_ascii=False)


@tool
def get_produits_alternatifs(categorie: str, exclure_nom: str) -> str:
    """
    [OUTIL PRODUITS ALTERNATIFS]
    Propose jusqu'à 3 produits alternatifs dans la même catégorie.
    """
    with engine.connect() as conn:
        query = text("""
            SELECT p.description, p.prix_unitaire_ht, COALESCE(s.quantite, 0) as quantite
            FROM T_Produits p
            LEFT JOIN T_Stocks s ON p.id = s.id_produit
            WHERE p.categorie = :categorie AND p.description NOT LIKE :exclure
            LIMIT 3
        """)
        result = conn.execute(query, {"categorie": categorie, "exclure": f"%{exclure_nom}%"}).fetchall()
    alternatifs = [{"description": row[0], "prix": row[1], "quantite": row[2]} for row in result]
    return json.dumps(alternatifs, ensure_ascii=False)


# ============================================
# 5. TEST
# ============================================
if __name__ == "__main__":
    print("Test de génération de PDF...")
    articles_test = [
        {"description": "MacBook Pro M3", "quantite": 1, "prix_unitaire": 22000},
        {"description": "Souris Sans Fil Logitech", "quantite": 2, "prix_unitaire": 1200}
    ]
    chemin = generer_devis_pdf_metier("Younes Snihji", articles_test)
    print(f"✅ Succès ! PDF généré ici : {chemin}")