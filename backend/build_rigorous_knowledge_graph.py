import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import json
import uuid
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings

# Chargement des variables d'environnement
load_dotenv()

class RigorousProductKG:
    def __init__(self):
        # 1. Connexion Neo4j
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD")
        if not self.password:
            raise RuntimeError("NEO4J_PASSWORD non défini : ajoutez-le dans backend/.env")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
        
        # 2. Extracteur strict (LLM)
        self.llm = ChatGroq(
            model="openai/gpt-oss-20b",
            temperature=0, # Déterminisme maximal
            api_key=os.getenv("GROQ_API_KEY")
        )
        
        # 3. Embeddings pour SIMILAR_TO (Calculé sur le texte réel uniquement)
        print("🧠 Chargement du modèle d'embedding...")
        self.embeddings_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.vector_dim = 384 

    def close(self):
        self.driver.close()

    def setup_schema(self):
        """Initialise le schéma strict avec contraintes d'unicité."""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n") # Reset de développement
            
            constraints = [
                "CREATE CONSTRAINT sku IF NOT EXISTS FOR (p:Product) REQUIRE p.sku IS UNIQUE",
                "CREATE CONSTRAINT brand IF NOT EXISTS FOR (b:Brand) REQUIRE b.name IS UNIQUE",
                "CREATE CONSTRAINT cat IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
                "CREATE CONSTRAINT cpu IF NOT EXISTS FOR (pr:Processor) REQUIRE pr.name IS UNIQUE",
                "CREATE CONSTRAINT ram IF NOT EXISTS FOR (r:RAM) REQUIRE r.size IS UNIQUE",
                "CREATE CONSTRAINT storage IF NOT EXISTS FOR (s:Storage) REQUIRE s.size IS UNIQUE",
                "CREATE CONSTRAINT interface IF NOT EXISTS FOR (i:Interface) REQUIRE i.name IS UNIQUE",
                "CREATE CONSTRAINT feat IF NOT EXISTS FOR (f:Feature) REQUIRE f.name IS UNIQUE"
            ]
            for query in constraints:
                session.run(query)
                
            # Index vectoriel pour les recherches sémantiques de l'Agent
            session.run(f"""
                CREATE VECTOR INDEX product_v_index IF NOT EXISTS
                FOR (p:Product) ON (p.embedding)
                OPTIONS {{indexConfig: {{
                  `vector.dimensions`: {self.vector_dim},
                  `vector.similarity_function`: 'cosine'
                }}}}
            """)
            print("✅ Schéma rigoureux et index vectoriels initialisés.")

    def strict_extract(self, text: str) -> dict:
        """Extrait UNIQUEMENT les données explicitement mentionnées."""
        prompt = f"""
        Tu es un extracteur de données strict. Analyse cette chaîne de caractères : "{text}"
        
        Règles CRITIQUES :
        1. N'invente RIEN. Si une information n'est pas explicitement écrite, mets null.
        2. Pour 'interfaces', isole les technologies de connexion mentionnées (ex: "USB-C", "HDMI", "Wireless", "Bluetooth").
        
        Retourne UNIQUEMENT un JSON respectant ce format exact :
        {{
            "processor": "Ex d'extraction: 'M3'. Si non mentionné: null",
            "ram": "Ex d'extraction: '16GB'. Si non mentionné: null",
            "storage": "Ex d'extraction: '1To'. Si non mentionné: null",
            "interfaces": ["Lliste des ports/connexions détectés explicitement (ex: ['USB-C', 'HDMI'])"],
            "features": ["Caractéristiques secondaires mentionnées (ex: ['Sans fil', 'Mécanique'])"]
        }}
        """
        try:
            response = self.llm.invoke(prompt).content.strip()
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            return json.loads(response)
        except Exception:
            return {"processor": None, "ram": None, "storage": None, "interfaces": [], "features": []}

    def inject_products(self, products_data):
        """Phase 1 : Injection des faits bruts vérifiables."""
        for item in products_data:
            sku=item["sku"]   
            
            # Extraction sans invention
            specs = self.strict_extract(item['desc'])
            
            # Embedding basé uniquement sur la chaîne d'origine fournie
            embedding = self.embeddings_model.embed_query(item['desc'])
            
            with self.driver.session() as session:
                session.execute_write(self._create_entities, sku, item, specs, embedding)
            print(f"📦 Facteurs injectés pour : {item['desc']}")

    @staticmethod
    def _create_entities(tx, sku, item, specs, embedding):
        # Création du cœur (Exclusion stricte de qte et prix)
        tx.run("""
            MERGE (b:Brand {name: $brand})
            MERGE (c:Category {name: $category})
            MERGE (p:Product {sku: $sku})
            SET p.name = $name, p.embedding = $embedding
            MERGE (p)-[:MANUFACTURED_BY]->(b)
            MERGE (p)-[:BELONGS_TO]->(c)
        """, {"sku": sku, "name": item["desc"], "brand": item["marque"], "category": item["cat"], "embedding": embedding})
        
        # Liaisons conditionnelles (Uniquement si non null)
        if specs.get("processor"):
            tx.run("MATCH (p:Product {sku: $sku}) MERGE (n:Processor {name: $val}) MERGE (p)-[:HAS_PROCESSOR]->(n)", {"sku": sku, "val": specs["processor"]})
        if specs.get("ram"):
            tx.run("MATCH (p:Product {sku: $sku}) MERGE (n:RAM {size: $val}) MERGE (p)-[:HAS_RAM]->(n)", {"sku": sku, "val": specs["ram"]})
        if specs.get("storage"):
            tx.run("MATCH (p:Product {sku: $sku}) MERGE (n:Storage {size: $val}) MERGE (p)-[:HAS_STORAGE]->(n)", {"sku": sku, "val": specs["storage"]})
            
        # Modélisation des interfaces (Ports / Connectivités)
        for inter in specs.get("interfaces", []):
            if inter:
                tx.run("""
                    MATCH (p:Product {sku: $sku})
                    MERGE (i:Interface {name: $inter_name})
                    MERGE (p)-[:HAS_INTERFACE]->(i)
                """, {"sku": sku, "inter_name": str(inter).upper()})

        # Caractéristiques réelles
        for feat in specs.get("features", []):
            if feat:
                tx.run("MATCH (p:Product {sku: $sku}) MERGE (f:Feature {name: $feat}) MERGE (p)-[:HAS_FEATURE]->(f)", {"sku": sku, "feat": str(feat).title()})

    def generate_logical_links(self):
        """Phase 2 : Génération de relations basées sur des règles topologiques strictes."""
        with self.driver.session() as session:
            
            # 1. POTENTIAL_ALTERNATIVE : Même catégorie, sous-segment ou nom proche (Exclus les écrans vs PC)
            session.run("""
                MATCH (p1:Product)-[:BELONGS_TO]->(c:Category)<-[:BELONGS_TO]-(p2:Product)
                WHERE p1 <> p2 
                  AND NOT (p1)-[:MANUFACTURED_BY]->(:Brand)<-[:MANUFACTURED_BY]-(p2)
                MERGE (p1)-[:POTENTIAL_ALTERNATIVE]->(p2)
            """)
            
            # 2. COMPATIBLE_WITH : Basé sur le partage réel d'une Interface/Port !
            session.run("""
                MATCH (p1:Product)-[:HAS_INTERFACE]->(i:Interface)<-[:HAS_INTERFACE]-(p2:Product)
                WHERE p1 <> p2
                MERGE (p1)-[:COMPATIBLE_WITH]->(p2)
            """)
            
            # 3. SIMILAR_TO : Uniquement si score de similarité sémantique élevé (> 0.75)
            session.run("""
                MATCH (p1:Product), (p2:Product)
                WHERE id(p1) < id(p2)
                WITH p1, p2, vector.similarity.cosine(p1.embedding, p2.embedding) AS score
                WHERE score > 0.75
                MERGE (p1)-[r:SIMILAR_TO]->(p2) SET r.score = score
                MERGE (p2)-[r2:SIMILAR_TO]->(p1) SET r2.score = score
            """)
            print("🔗 Relations POTENTIAL_ALTERNATIVE, COMPATIBLE_WITH (via interfaces) et SIMILAR_TO générées.")

# --- DATA ---
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
if __name__ == "__main__":
    kg = RigorousProductKG()
    try:
        kg.setup_schema()
        kg.inject_products(produits_data)
        kg.generate_logical_links()
        print("\n🚀 Knowledge Graph de production configuré avec succès.")
    finally:
        kg.close()
