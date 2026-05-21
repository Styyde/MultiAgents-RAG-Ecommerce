# backend/app.py (Agent 1 - Service SQL/Catalogue - Version Production Tool Calling)
# Port : 5001

from flask import Flask, request, jsonify
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import json
from dotenv import load_dotenv
from flask_cors import CORS

# ====================== IMPORTS LANGCHAIN CORRIGÉS ======================
from langchain_groq import ChatGroq

# Imports depuis langchain-classic (obligatoire pour les versions récentes)
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.utilities import SQLDatabase
from langchain_community.tools.sql_database.tool import QuerySQLDatabaseTool
from langchain.tools import tool
# =====================================================================

# Imports de votre logique métier
from models import Produit
from database import engine, Session
from outils_pdf import (
    generer_devis_pdf,
    rechercher_produit_avec_stock,
    rechercher_produits_par_categorie,
    verifier_stock_produit,
    lister_categories,
    get_produits_en_rupture,
    get_produits_alternatifs,
)

# Chargement configuration
chemin_env = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv(chemin_env)

app = Flask(__name__)
CORS(app)

# ============================================
# INITIALISATION LANGCHAIN (TOOL CALLING)
# ============================================
llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)

# Base de données pour l'outil SQL de fallback
db = SQLDatabase.from_uri(
    "sqlite:///ecommerce.db",
    include_tables=["T_Produits", "T_Stocks"],
    sample_rows_in_table_info=0
)

# ============================================
# CRÉATION DES OUTILS
# ============================================
@tool
def creer_devis_pdf_tool(parametres_json: str) -> str:
    """
    Génère un devis PDF. Attend un JSON strict : {"nom_client": "...", "articles": [{"description": "...", "quantite": X, "prix_unitaire": Y}]}
    """
    try:
        data = json.loads(parametres_json)
        nom_client = data.get("nom_client", "Client Anonyme")
        articles = data.get("articles", [])
        chemin = generer_devis_pdf(nom_client, articles)
        return f"SUCCÈS : Le devis a bien été généré ici : {chemin}"
    except Exception as e:
        return f"ERREUR : Impossible de générer le devis. Détails: {str(e)}"


sql_query_tool = QuerySQLDatabaseTool(
    db=db,
    description="Exécute une requête SQL SELECT. Ne l'utiliser que si les outils métier ne suffisent pas."
)

tools_personnalises = [
    rechercher_produit_avec_stock,
    rechercher_produits_par_categorie,
    verifier_stock_produit,
    lister_categories,
    get_produits_en_rupture,
    get_produits_alternatifs,
    creer_devis_pdf_tool,
    sql_query_tool
]

# ============================================
# CONFIGURATION DE L'AGENT
# ============================================
SYSTEM_PROMPT = """Tu es l'assistant commercial virtuel du magasin d'informatique de l'EMI. Tu parles TOUJOURS en français.
RÈGLES ABSOLUES :
1. Si l'utilisateur te salue, réponds chaleureusement sans utiliser d'outil.
2. SÉCURITÉ : Tu n'as accès qu'au catalogue et aux stocks. Refuse toute autre demande.
3. Ne propose que des produits EN STOCK. Si un produit est en rupture, utilise get_produits_alternatifs.
SCHÉMA DE LA BASE (pour ton information) :
- T_Produits (id, description, prix_unitaire_ht, categorie, marque)
- T_Stocks (id_produit, quantite, seuil_alerte)
CONSIGNE D'EXÉCUTION :
Utilise les outils à ta disposition pour répondre. Dès que tu as l'information, formule ta réponse finale en français naturel.
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

# Création de l'agent et de l'exécuteur
agent = create_tool_calling_agent(llm, tools_personnalises, prompt)
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools_personnalises,
    verbose=True,
    max_iterations=4,
    handle_parsing_errors=True
)

# Préchauffage
print("⏳ Préchauffage de l'agent Tool Calling...")
try:
    agent_executor.invoke({"input": "Bonjour"})
    print("✅ Agent prêt et sécurisé !")
except Exception as e:
    print(f"⚠️ Préchauffage ignoré: {e}")

# ============================================
# MÉMOIRE DE CONVERSATION
# ============================================
historique_conversation = []

# ============================================
# FONCTION CENTRALE DE TRAITEMENT
# ============================================
def traiter_message(message_brut: str, message_affiche: str = None) -> str:
    if message_affiche is None:
        message_affiche = f"✏️ {message_brut}"
   
    msg_normalise = message_brut.strip().lower().rstrip("?!.")
   
    # Bypass IA pour les salutations
    SALUTATIONS = ["bonjour", "salut", "hello", "hi", "coucou"]
    if any(msg_normalise.startswith(s) for s in SALUTATIONS) and len(msg_normalise.split()) < 4:
        reponse = "Bonjour ! Je suis l'assistant de l'EMI. Que puis-je faire pour vous ?"
        historique_conversation.append((message_affiche, reponse))
        return reponse
   
    # Exécution de l'Agent
    try:
        contexte = "\n".join([f"Client: {q}\nAssistant: {r}" for q, r in historique_conversation[-2:] if r is not None])
        input_enrichi = f"[Contexte récent : {contexte}]\nQuestion : {message_brut}" if contexte else message_brut
       
        reponse_ia = agent_executor.invoke({"input": input_enrichi})
        texte_final = reponse_ia["output"]
       
        historique_conversation.append((message_affiche, texte_final))
        return texte_final
       
    except Exception as e:
        print(f"Erreur critique: {str(e)}")
        reponse_erreur = "Désolé, je rencontre un problème technique pour accéder au catalogue."
        historique_conversation.append((message_affiche, reponse_erreur))
        return reponse_erreur

# ============================================
# ENDPOINTS FLASK
# ============================================
@app.route('/process', methods=['POST'])
def process_message():
    data = request.json
    if not data or 'message' not in data:
        return jsonify({"error": "Message requis"}), 400
   
    message = data['message'].strip()
    if not message:
        return jsonify({"error": "Message vide"}), 400
   
    reponse = traiter_message(message)
    return jsonify({"response": reponse, "success": True})

@app.route('/history', methods=['GET'])
def get_history():
    return jsonify({"history": historique_conversation})

@app.route('/clear-history', methods=['POST'])
def clear_history():
    global historique_conversation
    historique_conversation = []
    return jsonify({"success": True})

@app.route('/produits', methods=['GET'])
def get_produits():
    with Session() as session:
        produits = session.query(Produit).all()
        return jsonify([{
            "id": p.id, 
            "description": p.description, 
            "prix_ht": p.prix_unitaire_ht, 
            "marque": p.marque
        } for p in produits])

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Agent 1 (Catalogue) - Mode Production [Tool Calling]")
    print("   Port: 5001 | Modèle: Llama-3.1-8b-instant")
    print("=" * 60)
    app.run(debug=True, port=5001)