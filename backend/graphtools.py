# graphtools.py
import os
import json
import re
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain.tools import tool
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq

from catalogue_service import (
    filtrer_en_stock,
    resoudre_sku,
    trouver_alternatives_en_stock,
)


load_dotenv()

# Les recommandations sont filtrées par le stock SQL APRÈS la requête graphe :
# on demande donc plus de candidats que nécessaire pour pouvoir en écarter.
FACTEUR_SUR_ECHANTILLONNAGE = 3

# ============================================
# CONFIGURATION GLOBALE
# ============================================

class Neo4jConnection:
    """Singleton pour la connexion Neo4j avec Lazy Loading des Embeddings"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD")
        if not self.password:
            raise RuntimeError("NEO4J_PASSWORD non défini : ajoutez-le dans backend/.env")
        # Timeout court : si Neo4j est arrêté, les appelants (ex. calcul des alternatives
        # par l'agent SQL) basculent vite sur leur mode dégradé au lieu de bloquer.
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.username, self.password),
            connection_timeout=float(os.getenv("NEO4J_CONNECTION_TIMEOUT", "5")),
        )

        self.llm = ChatGroq(
            model="openai/gpt-oss-20b",
            temperature=0,
            api_key=os.getenv("GROQ_API_KEY")
        )
        # L'embedding est mis à None au démarrage pour ne pas ralentir l'API
        self._embeddings_model = None
        print("✅ Neo4jConnection initialisée (Sans chargement ML bloquant)")
    
    def get_embeddings_model(self):
        """Lazy Loading : Charge le modèle HF uniquement quand c'est nécessaire."""
        if self._embeddings_model is None:
            print("🧠 Chargement du modèle d'embedding à la volée...")
            from langchain_huggingface import HuggingFaceEmbeddings
            self._embeddings_model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        return self._embeddings_model

    def get_driver(self):
        return self.driver
    
    def close(self):
        if self.driver:
            self.driver.close()

neo4j_conn = Neo4jConnection()

def normalize_value(value: str) -> str:
    if not value:
        return value
    return re.sub(r'\s+', ' ', value.strip())

def _vers_sku(produit: str) -> str:
    """Accepte un SKU ou un nom de produit (ex: repris de l'historique) et renvoie le SKU
    du catalogue ; si rien ne correspond, renvoie l'entrée telle quelle."""
    return resoudre_sku(produit) or produit

# ============================================
# SCHÉMAS PYDANTIC POUR LES OUTILS
# ============================================
class ProductByCharacteristicQuery(BaseModel):
    node_type: str = Field(..., description="Type de nœud : 'Processor', 'RAM', 'Storage', 'Interface', 'Feature', 'Category', 'Brand'")
    value: str = Field(..., description="Valeur exacte ou partielle (ex: 'M3', '16GB', 'USB-C', 'Dell')")
    limit: int = Field(default=6)

class MultiCriteriaRecommendationQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact ou nom du produit de référence")
    limit: int = Field(default=5, description="Nombre maximum de recommandations")
    min_score: float = Field(default=0.45, description="Score composite minimum (entre 0 et 1)")
    same_category_priority: bool = Field(True, description="Donner plus de poids aux produits de la même catégorie")

class SearchQuery(BaseModel):
    query: str = Field(
        ..., 
        description="Requête de recherche. À utiliser UNIQUEMENT lorsque l'utilisateur donne un cas d'usage vague (ex: 'pour faire du montage vidéo', 'écouter de la musique') SANS spécifier de RAM, CPU ou caractéristiques strictes."
    )
    limit: int = Field(default=3, description="Nombre maximum de résultats.")
class AdvancedAlternativesQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact ou nom du produit de référence (ex: 'Dell XPS 15')")
    limit: int = Field(default=3, ge=1, le=10, description="Nombre maximal d'alternatives")
    quantite_souhaitee: int = Field(default=1, ge=1, description="Stock minimal requis pour chaque alternative")

class ProductBySpecsQuery(BaseModel):
    processor: Optional[str] = Field(None, description="Processeur (ex: 'M3', 'Intel i7').")
    ram: Optional[str] = Field(None, description="Quantité de RAM (ex: '16GB', '32GB').")
    storage: Optional[str] = Field(None, description="Capacité de stockage (ex: '512GB', '1To').")
    interface: Optional[str] = Field(None, description="Port de connexion (ex: 'USB-C', 'HDMI').")
    limit: int = Field(default=5, description="Nombre maximum de résultats.")

class ProductSimilarityQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact ou nom du produit de référence.")
    min_score: float = Field(default=0.7, description="Score minimum (0.7 par défaut).")
    limit: int = Field(default=3, description="Nombre d'alternatives.")

class CategoryQuery(BaseModel):
    category_name: str = Field(..., description="Nom de la catégorie (ex: 'Ordinateur', 'Smartphone').")
    limit: int = Field(default=5, description="Nombre max à retourner.")

class BrandQuery(BaseModel):
    brand_name: str = Field(..., description="Nom exact de la marque (ex: 'Apple', 'Dell').")
    limit: int = Field(default=5, description="Nombre max à retourner.")

class CompatibilityQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact ou nom du produit pour vérifier les compatibilités matérielles.")
    limit: int = Field(default=5, description="Nombre max.")

class AlternativesQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact du produit.")
    limit: int = Field(default=3, description="Nombre d'alternatives.")

class ProductInfoQuery(BaseModel):
    product_sku: str = Field(..., description="SKU exact ou nom du produit.")

class HybridSearchQuery(BaseModel):
    query: str = Field(..., description="Requête hybride complexe.")
    limit: int = Field(default=5, description="Nombre max.")
class DynamicTraversalQuery(BaseModel):
    product_name: str = Field(..., description="Nom du produit de base (ex: 'iPhone', 'Dell XPS'). Peut être partiel.")
    shared_characteristic: str = Field(..., description="Type de caractéristique partagée. Valeurs autorisées : 'Processor', 'RAM', 'Storage', 'Interface', 'Brand', 'Category'.")
    same_category_only: bool = Field(default=True, description="Restreindre aux produits de la même catégorie.")
    limit: int = Field(default=5)

# ============================================
# OUTILS (AVEC REQUÊTES OPTIMISÉES)
# ============================================

@tool(args_schema=SearchQuery)
def recherche_semantique_produits(query: str, limit: int = 3) -> str:
    """[OUTIL DE RECOURS] Recherche vectorielle. N'utiliser que si l'utilisateur décrit un usage vague (sans specs)."""
    driver = neo4j_conn.get_driver()
    embeddings_model = neo4j_conn.get_embeddings_model()
    query_embedding = embeddings_model.embed_query(query)
    
    with driver.session() as session:
        try:
            result = session.run("""
                CALL db.index.vector.queryNodes('product_v_index', $limit, $embedding)
                YIELD node, score
                WHERE score > 0.4
                
                WITH node, score
                MATCH (node)-[:MANUFACTURED_BY]->(b:Brand)
                MATCH (node)-[:BELONGS_TO]->(c:Category)
                
                RETURN node.sku AS sku,
                       node.name AS name,
                       b.name AS brand,
                       c.name AS category,
                       score AS similarity,
                       [(node)-[:HAS_PROCESSOR]->(proc) | proc.name] AS processors,
                       [(node)-[:HAS_RAM]->(ram) | ram.size] AS rams,
                       [(node)-[:HAS_STORAGE]->(st) | st.size] AS storages,
                       [(node)-[:HAS_INTERFACE]->(inter) | inter.name] AS interfaces
                ORDER BY score DESC
            """, {"embedding": query_embedding, "limit": limit})
            
            products = [dict(r) for r in result]
            return json.dumps({"type": "recherche_semantique", "resultats": products}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": "Erreur vectorielle", "details": str(e)})


@tool(args_schema=ProductBySpecsQuery)
def rechercher_par_caracteristiques(
    processor: Optional[str] = None,
    ram: Optional[str] = None,
    storage: Optional[str] = None,
    interface: Optional[str] = None,
    limit: int = 5
) -> str:
    """Recherche multi-critères exploitant les relations du graphe."""
    driver = neo4j_conn.get_driver()
    params = {"limit": limit}
    conditions = []

    if processor:
        proc_norm = normalize_value(processor)
        conditions.append("EXISTS { MATCH (p)-[:HAS_PROCESSOR]->(proc:Processor) WHERE proc.name =~ $proc_pattern }")
        params["proc_pattern"] = f"(?i).*{re.escape(proc_norm)}.*"

    if ram:
        ram_norm = normalize_value(ram)
        conditions.append("EXISTS { MATCH (p)-[:HAS_RAM]->(r:RAM) WHERE r.size =~ $ram_pattern }")
        params["ram_pattern"] = f"(?i).*{re.escape(ram_norm)}.*"

    if storage:
        storage_norm = normalize_value(storage)
        conditions.append("EXISTS { MATCH (p)-[:HAS_STORAGE]->(s:Storage) WHERE s.size =~ $storage_pattern }")
        params["storage_pattern"] = f"(?i).*{re.escape(storage_norm)}.*"

    if interface:
        interface_norm = normalize_value(interface)
        conditions.append("EXISTS { MATCH (p)-[:HAS_INTERFACE]->(i:Interface) WHERE i.name =~ $interface_pattern }")
        params["interface_pattern"] = f"(?i).*{re.escape(interface_norm)}.*"

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    query = f"""
        MATCH (p:Product)
        WHERE {where_clause}
        
        WITH p LIMIT $limit
        MATCH (p)-[:MANUFACTURED_BY]->(b:Brand)
        MATCH (p)-[:BELONGS_TO]->(c:Category)
        
        RETURN 
            p.sku AS sku,
            p.name AS name,
            b.name AS brand,
            c.name AS category,
            [(p)-[:HAS_PROCESSOR]->(proc) | proc.name] AS processors,
            [(p)-[:HAS_RAM]->(r) | r.size] AS rams,
            [(p)-[:HAS_STORAGE]->(s) | s.size] AS storages,
            [(p)-[:HAS_INTERFACE]->(i) | i.name] AS interfaces,
            [(p)-[:HAS_FEATURE]->(f) | f.name] AS features
    """

    with driver.session() as session:
        try:
            result = session.run(query, params)
            products = [dict(r) for r in result]
            return json.dumps({
                "type": "recherche_par_caracteristiques",
                "resultats": products,
                "critères_utilises": [k for k in ["processor","ram","storage","interface"] if locals().get(k)]
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

@tool(args_schema=MultiCriteriaRecommendationQuery)
def recommander_produits_similaires_multi_criteres(
    product_sku: str, 
    limit: int = 5, 
    min_score: float = 0.45,
    same_category_priority: bool = True
) -> str:
    """
    Recommandation avancée multi-critères :
    Combine similarité vectorielle + caractéristiques partagées + relations du graphe.
    Ne renvoie que des produits EN STOCK, avec prix et stock issus de la base SQL.
    """
    driver = neo4j_conn.get_driver()
    product_sku = _vers_sku(product_sku)

    with driver.session() as session:
        try:
            result = session.run("""
                MATCH (p1:Product {sku: $sku})
                MATCH (p1)-[:BELONGS_TO]->(cat1:Category)
                MATCH (p1)-[:MANUFACTURED_BY]->(brand1:Brand)
                
                // Pré-filtrage drastique de l'espace cartésien
                MATCH (p2:Product)
                WHERE p2 <> p1
                
                // Extraction immédiate du score vectoriel pour éliminer les candidats aberrants
                WITH p1, p2, cat1, brand1,
                     vector.similarity.cosine(p1.embedding, p2.embedding) AS vector_score
                
                // On ne calcule les sous-graphes que si le score vectoriel minimal ou la structure est prometteuse
                OPTIONAL MATCH (p1)-[:HAS_PROCESSOR]->(proc1:Processor)<-[:HAS_PROCESSOR]-(p2)
                OPTIONAL MATCH (p1)-[:HAS_RAM]->(ram1:RAM)<-[:HAS_RAM]-(p2)
                OPTIONAL MATCH (p1)-[:HAS_STORAGE]->(sto1:Storage)<-[:HAS_STORAGE]-(p2)
                OPTIONAL MATCH (p1)-[:HAS_INTERFACE]->(int1:Interface)<-[:HAS_INTERFACE]-(p2)
                OPTIONAL MATCH (p1)-[:HAS_FEATURE]->(feat1:Feature)<-[:HAS_FEATURE]-(p2)
                
                WITH p1, p2, cat1, brand1, vector_score,
                     count(DISTINCT proc1) AS shared_processor,
                     count(DISTINCT ram1) AS shared_ram,
                     count(DISTINCT sto1) AS shared_storage,
                     count(DISTINCT int1) AS shared_interfaces,
                     count(DISTINCT feat1) AS shared_features,
                     CASE 
                         WHEN EXISTS((p2)-[:BELONGS_TO]->(cat1)) THEN 1.0 
                         ELSE 0.3 
                     END AS category_bonus
                
                OPTIONAL MATCH (p1)-[r:SIMILAR_TO]->(p2)
                OPTIONAL MATCH (p1)-[:POTENTIAL_ALTERNATIVE]->(p2)
                OPTIONAL MATCH (p1)-[:COMPATIBLE_WITH]->(p2)
                
                WITH p2, 
                     vector_score,
                     (shared_processor * 0.25 + shared_ram * 0.20 + shared_storage * 0.20 + 
                      shared_interfaces * 0.15 + shared_features * 0.10) AS specs_score,
                     CASE 
                         WHEN r IS NOT NULL THEN 0.9
                         WHEN EXISTS((p1)-[:POTENTIAL_ALTERNATIVE]->(p2)) THEN 0.7
                         WHEN EXISTS((p1)-[:COMPATIBLE_WITH]->(p2)) THEN 0.6
                         ELSE 0.0 
                     END AS relation_score,
                     category_bonus
                
                WITH p2,
                     (vector_score * 0.35) +
                     (specs_score * 0.35) + 
                     (relation_score * 0.20) +
                     (category_bonus * 0.10) AS final_score
                
                WHERE final_score >= $min_score
                
                WITH p2, final_score
                ORDER BY final_score DESC
                LIMIT $limit
                
                MATCH (p2)-[:MANUFACTURED_BY]->(b:Brand)
                MATCH (p2)-[:BELONGS_TO]->(c:Category)
                
                RETURN 
                    p2.sku AS sku,
                    p2.name AS name,
                    b.name AS brand,
                    c.name AS category,
                    round(final_score, 3) AS score,
                    [(p2)-[:HAS_PROCESSOR]->(proc) | proc.name] AS processors,
                    [(p2)-[:HAS_RAM]->(r) | r.size] AS rams,
                    [(p2)-[:HAS_STORAGE]->(s) | s.size] AS storages,
                    [(p2)-[:HAS_INTERFACE]->(i) | i.name] AS interfaces
            """, {
                "sku": product_sku,
                "limit": limit * FACTEUR_SUR_ECHANTILLONNAGE,
                "min_score": min_score
            })

            recommendations, exclues = filtrer_en_stock([dict(record) for record in result], limit)
            return json.dumps({
                "type": "recommandation_multi_criteres",
                "produit_reference": product_sku,
                "recommandations": recommendations,
                "nb_resultats": len(recommendations),
                "nb_exclus_rupture": exclues
            }, ensure_ascii=False)
            
        except Exception as e:
            return json.dumps({
                "error": "Erreur lors de la recommandation multi-critères",
                "details": str(e)
            })      

@tool(args_schema=ProductSimilarityQuery)
def trouver_produits_similaires(product_sku: str, min_score: float = 0.7, limit: int = 3) -> str:
    """[OUTIL DE RECOMMANDATION] Trouve des produits similaires via les relations SIMILAR_TO du graphe.
    Ne renvoie que des produits EN STOCK, avec prix et stock issus de la base SQL."""
    driver = neo4j_conn.get_driver()
    product_sku = _vers_sku(product_sku)
    with driver.session() as session:
        result = session.run("""
            MATCH (p1:Product {sku: $sku})-[r:SIMILAR_TO]->(p2:Product)
            WHERE r.score > $min_score

            WITH p2, r.score AS similarity
            ORDER BY similarity DESC
            LIMIT $limit
            MATCH (p2)-[:MANUFACTURED_BY]->(b:Brand)
            MATCH (p2)-[:BELONGS_TO]->(c:Category)
            RETURN p2.sku AS sku, p2.name AS name, b.name AS brand, c.name AS category, similarity
            ORDER BY similarity DESC
        """, {"sku": product_sku, "min_score": min_score, "limit": limit * FACTEUR_SUR_ECHANTILLONNAGE})
        resultats, exclues = filtrer_en_stock([dict(r) for r in result], limit)
        return json.dumps({"resultats": resultats, "nb_exclus_rupture": exclues}, ensure_ascii=False)

@tool(args_schema=CategoryQuery)
def lister_produits_par_categorie(category_name: str, limit: int = 5) -> str:
    """Liste tous les produits d'une catégorie."""
    driver = neo4j_conn.get_driver()
    with driver.session() as session:
        clean_cat = category_name[:-1] if category_name.lower().endswith('s') else category_name
        result = session.run("""
            MATCH (c:Category)
            WHERE c.name =~ '(?i)' + $category + '.*'
            MATCH (p:Product)-[:BELONGS_TO]->(c)
            
            WITH p, c LIMIT $limit
            MATCH (p)-[:MANUFACTURED_BY]->(b:Brand)
            RETURN p.sku AS sku, p.name AS name, b.name AS brand, c.name AS category
        """, {"category": clean_cat, "limit": limit})
        return json.dumps({"resultats": [dict(r) for r in result]}, ensure_ascii=False)

@tool(args_schema=BrandQuery)
def lister_produits_par_marque(brand_name: str, limit: int = 5) -> str:
    """Liste tous les produits d'une marque spécifique."""
    driver = neo4j_conn.get_driver()
    with driver.session() as session:
        result = session.run("""
            MATCH (b:Brand)
            WHERE b.name =~ '(?i)' + $brand + '.*'
            MATCH (p:Product)-[:MANUFACTURED_BY]->(b)
            
            WITH p, b LIMIT $limit
            MATCH (p)-[:BELONGS_TO]->(c:Category)
            RETURN p.sku AS sku, p.name AS name, b.name AS brand, c.name AS category
        """, {"brand": normalize_value(brand_name), "limit": limit})
        return json.dumps({"resultats": [dict(r) for r in result]}, ensure_ascii=False)

@tool(args_schema=CompatibilityQuery)
def verifier_compatibilite_produit(product_sku: str, limit: int = 5) -> str:
    """Trouve les produits compatibles via interfaces partagées."""
    driver = neo4j_conn.get_driver()
    product_sku = _vers_sku(product_sku)
    with driver.session() as session:
        result = session.run("""
            MATCH (p1:Product {sku: $sku})-[:COMPATIBLE_WITH]->(p2:Product)
            
            WITH DISTINCT p2 LIMIT $limit
            MATCH (p2)-[:MANUFACTURED_BY]->(b:Brand)
            MATCH (p2)-[:BELONGS_TO]->(c:Category)
            RETURN p2.sku AS sku, p2.name AS name, b.name AS brand, c.name AS category
        """, {"sku": product_sku, "limit": limit})
        return json.dumps({"produits_compatibles": [dict(r) for r in result]}, ensure_ascii=False)

@tool(args_schema=AdvancedAlternativesQuery)
def trouver_alternatives_produit(product_sku: str, limit: int = 3, quantite_souhaitee: int = 1) -> str:
    """Alternatives EN STOCK à un produit (ex: produit en rupture) : même catégorie, classées par
    similarité vectorielle, caractéristiques techniques partagées et proximité de prix.
    Prix et stocks proviennent de la base SQL (temps réel)."""
    resultat = trouver_alternatives_en_stock(product_sku, limit, quantite_souhaitee)
    return json.dumps(resultat, ensure_ascii=False)


@tool(args_schema=ProductInfoQuery)
def get_informations_produit(product_sku: str) -> str:
    """Récupère TOUTES les informations d'un produit (Fiche technique exhaustive)."""
    driver = neo4j_conn.get_driver()
    product_sku = _vers_sku(product_sku)
    with driver.session() as session:
        result = session.run("""
            MATCH (p:Product {sku: $sku})
            MATCH (p)-[:MANUFACTURED_BY]->(b:Brand)
            MATCH (p)-[:BELONGS_TO]->(c:Category)
            RETURN p.sku AS sku, p.name AS name, b.name AS brand, c.name AS category,
                   [(p)-[:HAS_PROCESSOR]->(proc:Processor) | proc.name] AS processors,
                   [(p)-[:HAS_RAM]->(ram:RAM) | ram.size] AS rams,
                   [(p)-[:HAS_STORAGE]->(st:Storage) | st.size] AS storages,
                   [(p)-[:HAS_INTERFACE]->(inter:Interface) | inter.name] AS interfaces,
                   [(p)-[:HAS_FEATURE]->(feat:Feature) | feat.name] AS features
        """, {"sku": product_sku}).single()
        if result:
            return json.dumps(dict(result), ensure_ascii=False)
        return json.dumps({"error": "Produit non trouvé"})

@tool
def get_statistiques_catalogue() -> str:
    """Récupère des statistiques globales sur le catalogue."""
    driver = neo4j_conn.get_driver()
    with driver.session() as session:
        stats = session.run("MATCH (p:Product) RETURN count(p) AS total_products").single()
        by_category = session.run("MATCH (c:Category)<-[:BELONGS_TO]-(p:Product) RETURN c.name AS category, count(p) AS count").data()
        return json.dumps({"total": stats["total_products"], "par_categorie": by_category}, ensure_ascii=False)


@tool(args_schema=DynamicTraversalQuery)
def trouver_produits_par_traversabilite_dynamique(
    product_name: str,
    shared_characteristic: str,
    same_category_only: bool = True,
    limit: int = 5
) -> str:
    """
    [OUTIL PRINCIPAL] Permet de traverser dynamiquement le graphe pour trouver des produits 
    qui partagent une caractéristique spécifique avec un produit cible.
    """
    driver = neo4j_conn.get_driver()
    
    char_map = {
        "processor": {"label": "Processor", "rel": "HAS_PROCESSOR"},
        "ram": {"label": "RAM", "rel": "HAS_RAM"},
        "storage": {"label": "Storage", "rel": "HAS_STORAGE"},
        "interface": {"label": "Interface", "rel": "HAS_INTERFACE"},
        "brand": {"label": "Brand", "rel": "MANUFACTURED_BY"},
        "category": {"label": "Category", "rel": "BELONGS_TO"}
    }
    
    target_config = char_map.get(shared_characteristic.lower())
    if not target_config:
        return json.dumps({"error": f"Caractéristique '{shared_characteristic}' non prise en charge."})

    target_label = target_config["label"]
    target_rel = target_config["rel"]

    with driver.session() as session:
        # Remplacement de la relation anonyme par une relation typée dynamiquement de manière sécurisée
        query = f"""
            MATCH (p1:Product)
            WHERE p1.name =~ $name_regex
            
            MATCH (p1)-[:{target_rel}]->(shared:{target_label})<-[:{target_rel}]-(p2:Product)
            WHERE p1 <> p2
            
            WITH p1, p2, shared
            MATCH (p1)-[:BELONGS_TO]->(c1:Category)
            MATCH (p2)-[:BELONGS_TO]->(c2:Category)
            WHERE ($same_category = false OR c1 = c2)
            
            RETURN 
                p1.name AS produit_source,
                p2.sku AS sku, 
                p2.name AS name, 
                c2.name AS category,
                labels(shared)[0] AS type_partage,
                coalesce(shared.name, shared.size) AS valeur_partage
            LIMIT $limit
        """
        try:
            result = session.run(query, {
                "name_regex": f"(?i).*{product_name}.*",
                "same_category": same_category_only,
                "limit": limit
            })
            
            products = [dict(r) for r in result]
            
            if not products:
                return json.dumps({"message": f"Aucun produit trouvé partageant la caractéristique '{target_label}' avec '{product_name}'."})
                
            return json.dumps({
                "type": "traversabilite_dynamique",
                "analyse": f"Produits partageant le même {target_label} que le {products[0]['produit_source']}",
                "resultats": products
            }, ensure_ascii=False)
            
        except Exception as e:
            return json.dumps({"error": "Erreur lors de la traversée du graphe", "details": str(e)})

@tool(args_schema=ProductByCharacteristicQuery)
def rechercher_produits_par_caracteristique(node_type: str, value: str, limit: int = 6) -> str:
    """Recherche en partant directement d'un nœud de caractéristique (très graph-native)."""
    driver = neo4j_conn.get_driver()
    node_type = node_type.strip().title()
    
    with driver.session() as session:
        result = session.run(f"""
            MATCH (n:{node_type})
            WHERE n.name = $value OR (n.size IS NOT NULL AND n.size = $value)
            MATCH (n)<-[r]-(p:Product)
            
            WITH p, n, r LIMIT $limit
            MATCH (p)-[:MANUFACTURED_BY]->(b:Brand)
            MATCH (p)-[:BELONGS_TO]->(c:Category)
            
            RETURN 
                p.sku AS sku,
                p.name AS name,
                b.name AS brand,
                c.name AS category,
                $value AS critere_recherche,
                type(r) AS relation_utilisee
        """, {"value": value, "limit": limit})
        
        products = [dict(r) for r in result]
        return json.dumps({
            "type": "recherche_par_caracteristique",
            "critere": f"{node_type}: {value}",
            "resultats": products
        }, ensure_ascii=False)
    
@tool(args_schema=HybridSearchQuery)
def recherche_hybride(query: str, limit: int = 5) -> str:
    """Recherche combinant texte et sémantique."""
    driver = neo4j_conn.get_driver()
    embeddings_model = neo4j_conn.get_embeddings_model()
    query_embedding = embeddings_model.embed_query(query)
    
    with driver.session() as session:
        try:
            vector_results = session.run("""
                CALL db.index.vector.queryNodes('product_v_index', $limit, $embedding)
                YIELD node, score
                WHERE score > 0.3
                RETURN node.sku AS sku, score AS relevance
            """, {"embedding": query_embedding, "limit": limit}).data()
            
            return json.dumps({"resultats_vectoriels_bruts": vector_results}, ensure_ascii=False)
        except Exception as e:
             return json.dumps({"error": str(e)})

TOOLS_LIST = [
    trouver_produits_par_traversabilite_dynamique,
    rechercher_par_caracteristiques,
    recherche_semantique_produits,
    trouver_produits_similaires,
    lister_produits_par_categorie,
    lister_produits_par_marque,
    verifier_compatibilite_produit,
    trouver_alternatives_produit,
    recommander_produits_similaires_multi_criteres,
    get_informations_produit,
    get_statistiques_catalogue,
    recherche_hybride,
    rechercher_produits_par_caracteristique
]

if __name__ == "__main__":
    neo4j_conn.close()