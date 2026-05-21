import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from langchain.tools import tool
from database import engine
from sqlalchemy import text
import json

# ============================================
# GÉNÉRATION DE DEVIS PDF
# ============================================

def generer_devis_pdf(nom_client, liste_articles):
    """
    Génère un Devis PDF et retourne le chemin du fichier.
    liste_articles doit être au format : [{"description": "MacBook", "quantite": 1, "prix_unitaire": 22000}]
    """
    # 1. Créer le dossier outputs s'il n'existe pas
    dossier_sortie = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'outputs'))
    if not os.path.exists(dossier_sortie):
        os.makedirs(dossier_sortie)

    # 2. Nommer le fichier comme demandé (DEVIS_ID_DATE.pdf)
    date_jour = datetime.now().strftime("%Y%m%d_%H%M%S")
    nom_fichier = f"DEVIS_{nom_client.replace(' ', '')}_{date_jour}.pdf"
    chemin_complet = os.path.join(dossier_sortie, nom_fichier)

    # 3. Préparer le document
    doc = SimpleDocTemplate(chemin_complet, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # --- EN-TÊTE ---
    elements.append(Paragraph("<b>MAGASIN INFORMATIQUE EMI</b>", styles['Title']))
    elements.append(Paragraph("Devis Officiel", styles['Heading2']))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"<b>Client :</b> {nom_client}", styles['Normal']))
    elements.append(Paragraph(f"<b>Date :</b> {datetime.now().strftime('%d/%m/%Y')}", styles['Normal']))
    elements.append(Spacer(1, 20))

    # --- LE TABLEAU DES PRODUITS ---
    donnees_tableau = [["Description", "Quantité", "Prix Unitaire HT", "Total Ligne HT"]]
    total_ht = 0

    for article in liste_articles:
        desc = article.get("description", "Produit Inconnu")
        qte = article.get("quantite", 1)
        pu = article.get("prix_unitaire", 0.0)
        ligne_ht = qte * pu
        total_ht += ligne_ht
        
        donnees_tableau.append([desc, str(qte), f"{pu:.2f} €", f"{ligne_ht:.2f} €"])

    # --- CALCULS FINANCIERS (TVA 20%) ---
    tva = total_ht * 0.20
    total_ttc = total_ht + tva

    donnees_tableau.append(["", "", "Sous-total HT", f"{total_ht:.2f} €"])
    donnees_tableau.append(["", "", "TVA (20%)", f"{tva:.2f} €"])
    donnees_tableau.append(["", "", "TOTAL TTC", f"{total_ttc:.2f} €"])

    # --- DESIGN DU TABLEAU ---
    style_tableau = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#004b87')),  # Bleu EMI
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        # Lignes de totaux en gras
        ('FONTNAME', (2,-3), (-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (2,-1), (-1,-1), colors.lightgrey),
    ])
    
    t = Table(donnees_tableau)
    t.setStyle(style_tableau)
    elements.append(t)

    # --- GÉNÉRATION ---
    doc.build(elements)
    return chemin_complet


# ============================================
# OUTILS SQL PRÉDÉFINIS POUR L'AGENT
# ============================================

@tool
def rechercher_produit_avec_stock(nom: str) -> str:
    """
    Recherche des produits dont la description contient le mot-clé (nom) et retourne leurs informations avec le stock.
    Utilise une jointure entre T_Produits et T_Stocks.
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
    Retourne tous les produits d'une catégorie donnée avec leur stock.
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
    Vérifie le stock disponible pour un produit spécifique (recherche par nom).
    Retourne le nom du produit et la quantité en stock.
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
    Retourne la liste de toutes les catégories de produits disponibles.
    """
    with engine.connect() as conn:
        query = text("SELECT DISTINCT categorie FROM T_Produits")
        result = conn.execute(query).fetchall()
    categories = [row[0] for row in result]
    return json.dumps(categories, ensure_ascii=False)


@tool
def get_produits_en_rupture() -> str:
    """
    Retourne la liste des produits dont le stock est égal à 0 ou inférieur au seuil d'alerte.
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
    Propose jusqu'à 3 produits alternatifs dans la même catégorie, en excluant un produit spécifique.
    Retourne les produits avec leur prix et stock.
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
# TEST RAPIDE
# ============================================
if __name__ == "__main__":
    print("Test de génération de PDF...")
    articles_test = [
        {"description": "MacBook Pro M3", "quantite": 1, "prix_unitaire": 22000},
        {"description": "Souris Sans Fil Logitech", "quantite": 2, "prix_unitaire": 1200}
    ]
    chemin = generer_devis_pdf("Younes Snihji", articles_test)
    print(f"✅ Succès ! PDF généré ici : {chemin}")