from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
# On importe la Base et nos tables depuis le fichier models.py qu'on a créé
from models import Base, Produit, Stock, Client

# 1. Connexion à la base de données SQLite (le fichier va se créer tout seul)
engine = create_engine('sqlite:///ecommerce.db', echo=False)

# 2. Création de toutes les tables dans la base de données
Base.metadata.create_all(engine)

# 3. Création d'une session pour interagir avec la base
Session = sessionmaker(bind=engine)
session = Session()

# 4. Fonction pour remplir avec des données de test
def peupler_base():
    # On vérifie s'il y a déjà des produits pour ne pas les créer en double
    if session.query(Produit).count() == 0:
        print("⏳ Ajout des produits de test en cours...")

        produits_data = [
            {"desc": "PC Portable Dell XPS 15", "prix": 15000.0, "cat": "Ordinateur", "marque": "Dell", "qte": 10},
            {"desc": "MacBook Pro M3", "prix": 22000.0, "cat": "Ordinateur", "marque": "Apple", "qte": 5},
            {"desc": "Souris Sans Fil Logitech MX Master 3", "prix": 1200.0, "cat": "Accessoire", "marque": "Logitech", "qte": 50},
            {"desc": "Clavier Mécanique Keychron K2", "prix": 1100.0, "cat": "Accessoire", "marque": "Keychron", "qte": 20},
            {"desc": "Ecran Dell UltraSharp 27", "prix": 4500.0, "cat": "Ecran", "marque": "Dell", "qte": 15},
            {"desc": "iPhone 15 Pro", "prix": 13000.0, "cat": "Smartphone", "marque": "Apple", "qte": 8},
            {"desc": "Samsung Galaxy S24 Ultra", "prix": 14000.0, "cat": "Smartphone", "marque": "Samsung", "qte": 6},
            {"desc": "Casque Sony WH-1000XM5", "prix": 3500.0, "cat": "Audio", "marque": "Sony", "qte": 12},
            {"desc": "Disque Dur Externe SSD 1To SanDisk", "prix": 1300.0, "cat": "Stockage", "marque": "SanDisk", "qte": 30},
            {"desc": "Câble USB-C vers HDMI", "prix": 250.0, "cat": "Accessoire", "marque": "Belkin", "qte": 100}
        ]

        for data in produits_data:
            # Création du produit
            nouveau_produit = Produit(
                description=data["desc"],
                prix_unitaire_ht=data["prix"],
                categorie=data["cat"],
                marque=data["marque"]
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