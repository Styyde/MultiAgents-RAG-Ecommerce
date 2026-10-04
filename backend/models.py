from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

# C'est la classe de base de SQLAlchemy qui permet de transformer 
# nos classes Python en tables SQL
Base = declarative_base()

class Produit(Base):
    __tablename__ = 'T_Produits'

    id = Column(Integer, primary_key=True)
    sku = Column(String, unique=True, nullable=False, index=True)
    description = Column(String, nullable=False)
    prix_unitaire_ht = Column(Float, nullable=False)
    categorie = Column(String)
    marque = Column(String)

    # Relation avec le stock (1 produit a 1 stock)
    stock = relationship("Stock", back_populates="produit", uselist=False, cascade="all, delete")

class Stock(Base):
    __tablename__ = 'T_Stocks'

    # Ici, l'id du produit sert aussi de clé primaire pour le stock
    id_produit = Column(Integer, ForeignKey('T_Produits.id'), primary_key=True)
    quantite = Column(Integer, nullable=False, default=0)
    seuil_alerte = Column(Integer, default=5)

    # Relation retour vers le produit
    produit = relationship("Produit", back_populates="stock")

class Client(Base):
    __tablename__ = 'T_Clients'

    id_client = Column(Integer, primary_key=True)
    nom = Column(String, nullable=False)
    prenom = Column(String)
    email = Column(String, unique=True, nullable=False)
    tel = Column(String)

    # Un client peut avoir plusieurs commandes
    commandes = relationship("Commande", back_populates="client")

class Commande(Base):
    __tablename__ = 'T_Commandes'

    id_cmd = Column(Integer, primary_key=True)
    date = Column(String, nullable=False) # On stocke la date sous forme de texte "YYYY-MM-DD"
    id_client = Column(Integer, ForeignKey('T_Clients.id_client'))
    statut = Column(String, default="EN_ATTENTE")
    montant_ttc = Column(Float)

    # Relations
    client = relationship("Client", back_populates="commandes")
    lignes = relationship("LigneCmd", back_populates="commande", cascade="all, delete")

class LigneCmd(Base):
    __tablename__ = 'T_Lignes_Cmd'

    id = Column(Integer, primary_key=True)
    id_cmd = Column(Integer, ForeignKey('T_Commandes.id_cmd'))
    id_produit = Column(Integer, ForeignKey('T_Produits.id'))
    quantite = Column(Integer, nullable=False)
    prix_ht_unitaire = Column(Float, nullable=False) # Le prix au moment de l'achat

    # Relation retour vers la commande
    commande = relationship("Commande", back_populates="lignes")