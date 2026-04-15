from flask import Flask, jsonify, request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Produit
import os
from dotenv import load_dotenv
import json
from langchain.tools import tool
from outils_pdf import generer_devis_pdf # Ton fichier de la dernière fois

# IMPORTS IA & MÉMOIRE

from langchain_groq import ChatGroq
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent, SQLDatabaseToolkit

# 1. Charger la clé API
chemin_env = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
load_dotenv(chemin_env)

# 2. Initialisation de Flask
chemin_frontend = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
app = Flask(__name__, static_folder=chemin_frontend, static_url_path='/')

engine = create_engine('sqlite:///ecommerce.db', connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)

# 3. INITIALISATION DU CERVEAU IA ET DE LA MÉMOIRE
llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)

# On limite la vue à la table des produits pour économiser les tokens
db = SQLDatabase.from_uri("sqlite:///ecommerce.db", include_tables=["T_Produits", "T_Stocks"])
toolkit = SQLDatabaseToolkit(db=db, llm=llm)

# Initialisation de la mémoire LangChain
historique_conversation = []

# --- L'OUTIL DE GÉNÉRATION DE DEVIS ---
# --- L'OUTIL DE GÉNÉRATION DE DEVIS (CORRIGÉ) ---
@tool
def creer_devis_pdf(parametres_json: str) -> str:
    """
    Génère un devis PDF pour un client.
    L'Action Input DOIT ÊTRE UN JSON STRICT avec deux clés : "nom_client" et "articles".
    Exemple exact de ce que tu dois envoyer dans Action Input :
    {"nom_client": "Client Anonyme", "articles": [{"description": "MacBook Pro M3", "quantite": 1, "prix_unitaire": 22000}]}
    """
    import json
    try:
        # On lit le JSON envoyé par l'IA
        data = json.loads(parametres_json)
        
        # On extrait les données
        nom_client = data.get("nom_client", "Client Anonyme")
        articles = data.get("articles", [])
        
        # On exécute ta fonction (issue de outils_pdf.py)
        chemin = generer_devis_pdf(nom_client, articles)
        return f"SUCCÈS : Le devis a bien été généré ici : {chemin}"
        
    except json.JSONDecodeError:
        return "ERREUR : L'Action Input n'est pas un JSON valide. Tu dois envoyer un dictionnaire JSON avec 'nom_client' et 'articles'."
    except Exception as e:
        return f"ERREUR : Impossible de générer le devis. Détails : {str(e)}"
# 4. CRÉATION DE L'AGENT COMMERCIAL SQL

# 4. CRÉATION DE L'AGENT COMMERCIAL SQL
# 4. CRÉATION DE L'AGENT COMMERCIAL SQL
PREFIX_AGENT = """Tu es l'assistant commercial virtuel du magasin d'informatique de l'EMI. Tu parles TOUJOURS en français.

RÈGLES ABSOLUES :
1. Si l'utilisateur te salue ou demande ton nom, réponds directement sans utiliser d'outils.
2. Si le message de l'utilisateur est incompréhensible (ex: "hjfvn"), n'utilise AUCUN outil et réponds : "Je n'ai pas bien compris."
3. SÉCURITÉ : Tu n'as accès qu'au catalogue et aux stocks. Refuse toute autre demande.

RÈGLES MÉTIER (STOCKS ET ALTERNATIVES) - TRÈS IMPORTANT :
- Ne fais JAMAIS de "SELECT *" avec un JOIN. Sélectionne uniquement les colonnes utiles (description, prix_unitaire_ht, quantite).
 SI LE CLIENT DEMANDE UN DEVIS : Tu DOIS utiliser l'outil 'creer_devis_pdf'.
  * Vérifie le prix et la disponibilité en base de données avant.
  * Formate ton Action Input EXACTEMENT comme un JSON valide contenant "nom_client" et "articles".
  * Quand l'outil te répond SUCCÈS, dis au client : "Votre devis a bien été généré ! Je me tiens à votre disposition si vous souhaitez le valider."
  DÈS QUE l'outil répond "SUCCÈS...", TU DOIS IMMÉDIATEMENT ARRÊTER D'UTILISER DES OUTILS et répondre EXACTEMENT : "Final Answer: Votre devis a bien été généré ! Je me tiens à votre disposition si vous souhaitez le valider." Ne vérifie plus rien après le succès.
- Pour vérifier le stock, fais une jointure SQL (JOIN) entre T_Produits (id) et T_Stocks (id_produit).
- SI LE PRODUIT N'EXISTE PAS : Déduis toi-même sa catégorie logique (ex: un Galaxy S90 est un 'Smartphone', un Dell est un 'Ordinateur'). Ensuite, fais une requête pour trouver jusqu'à 3 alternatives de cette MÊME catégorie ayant du stock (quantite > 0).
- SI LE PRODUIT EXISTE MAIS EST EN RUPTURE (quantite = 0) : Propose 3 alternatives de la même catégorie avec du stock.
- Formule ta réponse poliment : "Je suis désolé, le [Produit] n'est pas disponible. Cependant, je vous propose ces alternatives : [Liste avec prix et stock]".

FORMAT DE RÉPONSE :
Thought: [Ton raisonnement]
Final Answer: [Ta réponse finale en français]
"""

agent_executor = create_sql_agent(
    llm=llm,
    toolkit=toolkit, 
    extra_tools=[creer_devis_pdf], # <-- NOUVEAU : On ajoute ton outil PDF !
    agent_type="zero-shot-react-description",
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=8,
    
    prefix=PREFIX_AGENT
)

@app.route('/', methods=['GET'])
def accueil():
    return app.send_static_file('index.html')

@app.route('/api/produits', methods=['GET'])
def obtenir_produits():
    session = Session()
    produits_db = session.query(Produit).all()
    liste_produits = [{"id": p.id, "description": p.description, "prix_ht": p.prix_unitaire_ht, "marque": p.marque} for p in produits_db]
    session.close()
    return jsonify(liste_produits)

@app.route('/api/chat', methods=['POST'])
def discuter():
    donnees = request.json
    message_utilisateur = donnees.get('message', '')
    msg_normalise = message_utilisateur.strip().lower().rstrip("?!.")

    # --- ÉTAPE 1 : PRÉ-FILTRAGE (Économie d'API) ---
    SALUTATIONS = ["bonjour", "salut", "hello", "hi", "cc", "coucou", "ca va", "comment vas-tu"]
    REMERCIEMENTS = ["merci", "thanks", "chokran", "merc"]
    MOTS_COURTS = ["ok", "d'accord", "oui", "non", "parfait", "super", "bien"] # NOUVEAU !

    if any(msg_normalise.startswith(s) for s in SALUTATIONS) and len(msg_normalise.split()) < 4:
        return jsonify({"reponse": "Bonjour ! Je suis l'assistant de l'EMI. Comment puis-je vous aider ?"})

    if any(r in msg_normalise for r in REMERCIEMENTS) and len(msg_normalise.split()) < 5:
        return jsonify({"reponse": "Je vous en prie ! Avez-vous d'autres questions sur nos produits ?"})
        
    # On intercepte les petits mots d'approbation
    if msg_normalise in MOTS_COURTS:
        return jsonify({"reponse": "Très bien ! Puis-je vous aider avec autre chose ?"})

    # --- ÉTAPE 2 : APPEL DE L'AGENT AVEC MÉMOIRE PYTHON ---
    try:
        texte_historique = "\n".join([f"Client: {q}\nAssistant: {r}" for q, r in historique_conversation[-3:]])

        # L'astuce "Panneau Fluo" améliorée pour bloquer le charabia
        if texte_historique:
            question_enrichie = (
                f"CONTEXTE DES DERNIERS MESSAGES :\n{texte_historique}\n"
                f"---------------------------------\n"
                f"QUESTION ACTUELLE : {message_utilisateur}\n\n"
                f"INSTRUCTIONS VITALES :\n"
                f"1. Si la QUESTION ACTUELLE est incompréhensible (ex: suites de lettres comme 'mmporergh' ou 'hjfd'), "
                f"TU DOIS IGNORER le contexte et répondre EXACTEMENT : 'Final Answer: Je n'ai pas bien compris votre demande. Pourriez-vous reformuler ?'\n"
                f"2. Si la QUESTION utilise des pronoms ('son', 'le', 'il'), lis le CONTEXTE pour déduire le produit et fais ta requête SQL."
            )
        else:
            question_enrichie = message_utilisateur

        reponse_ia = agent_executor.invoke({
            "input": question_enrichie
        })
        
        texte_final = reponse_ia["output"]
        historique_conversation.append((message_utilisateur, texte_final))

    except Exception as e:
        erreur_str = str(e)
        if "Could not parse LLM output: `" in erreur_str:
            # CORRECTION DU BUG DE L'URL : On coupe bien avant la fin du backtick
            texte_brut = erreur_str.split("Could not parse LLM output: `")[1].split("`")[0]
            lignes = [l for l in texte_brut.splitlines() if not l.strip().startswith(("Thought:", "Action:", "Observation:"))]
            texte_final = " ".join(lignes).strip()
            
            # Si le texte est vide après nettoyage, on met un message par défaut
            if not texte_final:
                 texte_final = "Je n'ai pas bien compris votre demande. Pourriez-vous reformuler ?"
                 
            historique_conversation.append((message_utilisateur, texte_final))
        else:
            texte_final = "Désolé, j'ai rencontré une petite difficulté technique."
            print(f"Erreur : {e}")

    return jsonify({"reponse": texte_final})

if __name__ == '__main__':
    app.run(debug=True, port=5000)