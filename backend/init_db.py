import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# On importe la Base et nos tables depuis le fichier models.py qu'on a créé
from models import Base, Produit, Stock, Client
# 1. Connexion partagée avec les agents (même fichier quel que soit le répertoire courant)
from database import engine, Session

# 2. Création de toutes les tables dans la base de données
Base.metadata.create_all(engine)

# 3. Création d'une session pour interagir avec la base
session = Session()

# 4. Fonction pour remplir avec des données de test
def peupler_base():
    # On vérifie s'il y a déjà des produits pour ne pas les créer en double
    if session.query(Produit).count() == 0:
        print("⏳ Ajout des produits de test en cours...")

        # --- DATA CORRIGÉES ---
        produits_data = [
        {
            "sku": "SKU-382C8957", 
            "desc": "PC Portable Dell XPS 15 - Processeur Intel Core i7-13700H, 16GB RAM DDR5, Stockage 512GB NVMe SSD, écran 15.6 pouces 4K, ports de connexion HDMI et USB-C. Idéal pour le montage vidéo, le gaming et les applications lourdes.", 
            "prix": 15000.0, "cat": "Ordinateur", "marque": "Dell", "qte": 10
        },
        {
             "sku": "SKU-D6E2319A", 
             "desc": "MacBook Pro M3 - Processeur Apple M3 avec 8 cœurs CPU et 10 cœurs GPU, 16GB RAM de mémoire unifiée, Stockage 512GB SSD, écran Liquid Retina XDR 14 pouces, ports de connexion HDMI et USB-C. Parfait pour le développement.", 
             "prix": 22000.0, "cat": "Ordinateur", "marque": "Apple", "qte": 5
        },
        {
            "sku": "SKU-F49B1A22", 
            "desc": "Souris Sans Fil Logitech MX Master 3 - Capteur 4000 DPI, 7 boutons, molette MagSpeed, connectivité sans fil Bluetooth et USB. Idéale pour la productivité.", 
            "prix": 1200.0, "cat": "Accessoire", "marque": "Logitech", "qte": 50
        },
        {
             "sku": "SKU-B91A4C78", 
             "desc": "Clavier Mécanique Keychron K2 - 84 touches, rétroéclairage RGB, switchs Gateron, connectivité sans fil Bluetooth et filaire USB, compatible Mac/Windows.", 
             "prix": 1100.0, "cat": "Accessoire", "marque": "Keychron", "qte": 20
        },
        {
            "sku": "SKU-A5D8E210", 
            "desc": "Ecran Dell UltraSharp 27 - 27 pouces 4K UHD, panneau IPS, 99% sRGB, ports d'interface HDMI, DisplayPort et USB-C, hauteur réglable.", 
            "prix": 4500.0, "cat": "Ecran", "marque": "Dell", "qte": 15
        },
       {
            "sku": "SKU-C7F3B6D9", 
            "desc": "iPhone 15 Pro - Écran 6.1 pouces, Processeur A17 Pro, 8GB RAM, Stockage 256GB, appareil photo triple 48MP, connectivité USB-C, Dynamic Island.", 
            "prix": 13000.0, "cat": "Smartphone", "marque": "Apple", "qte": 8
        },
        {
            "sku": "SKU-E1A9D4B2", 
            "desc": "Samsung Galaxy S24 Ultra - Écran 6.8 pouces, Processeur Snapdragon 8 Gen 3, 12GB RAM, Stockage 512GB, connectivité USB-C, stylet S Pen intégré, caméra 200MP, batterie 5000mAh.", 
            "prix": 14000.0, "cat": "Smartphone", "marque": "Samsung", "qte": 6
        },
        {
            "sku": "SKU-8B2C5F61", 
            "desc": "Casque Sony WH-1000XM5 - Réduction de bruit active, autonomie 30h, son haute résolution, connectivité sans fil Bluetooth multipoint, confort optimal.", 
            "prix": 3500.0, "cat": "Audio", "marque": "Sony", "qte": 12
        },
        {
            "sku": "SKU-D2F8A4E7", 
            "desc": "Disque Dur Externe SSD 1To SanDisk - Vitesse de transfert jusqu'à 1000 Mo/s, Stockage 1To SSD, connectivité USB 3.2, résistant aux chocs, format compact.", 
            "prix": 1300.0, "cat": "Stockage", "marque": "SanDisk", "qte": 30
       },
       {
            "sku": "SKU-6C9D1B3F", 
            "desc": "Câble USB-C vers HDMI - Support de résolution 4K à 60Hz, cordon tressé, longueur 2m, ports d'interface USB-C et HDMI, compatible avec ordinateurs et téléphones.", 
            "prix": 250.0, "cat": "Accessoire", "marque": "Belkin", "qte": 100
       }
    ]
        for data in produits_data:
            # Création du produit
            nouveau_produit = Produit(
                description=data["desc"],
                prix_unitaire_ht=data["prix"],
                categorie=data["cat"],
                marque=data["marque"],
                sku=data["sku"]
            )
            
            # Création du stock associé
            nouveau_stock = Stock(
                quantite=data["qte"],
                seuil_alerte=5,
                produit=nouveau_produit # On lie le stock au produit
            )
            
            session.add(nouveau_produit)
            session.add(nouveau_stock)

        # Enregistrement définitif dans la base de données
        session.commit()
        print("✅ Base de données initialisée et produits ajoutés avec succès !")
    else:
        print("ℹ️ La base de données contient déjà des produits.")

if __name__ == '__main__':
    peupler_base()
