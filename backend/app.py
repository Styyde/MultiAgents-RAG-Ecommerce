from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Produit
import os
import re
from dotenv import load_dotenv
import json
from langchain.tools import tool
from outils_pdf import generer_devis_pdf

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
CORS(app) # Ajout du CORS au cas où pour éviter les blocages du frontend

engine = create_engine('sqlite:///ecommerce.db', connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)

# 3. INITIALISATION DU CERVEAU IA ET DE LA MÉMOIRE
llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)

# On limite la vue à la table des produits et stocks
db = SQLDatabase.from_uri("sqlite:///ecommerce.db", include_tables=["T_Produits", "T_Stocks"])
toolkit = SQLDatabaseToolkit(db=db, llm=llm)

# Initialisation de la mémoire LangChain
historique_conversation = []

# ==========================================
# GUARDRAILS : SÉCURITÉ ANTI-INJECTION
# ==========================================
def est_requete_securisee(texte: str) -> bool:
    """
    Vérifie si le texte contient des mots-clés SQL destructeurs ou
    des tentatives de manipulation de prompt (Prompt Injection).
    """
    texte_min = texte.lower()
    
    # 1. Mots-clés SQL interdits (avec \b pour s'assurer que c'est le mot exact)
    mots_sql_dangereux = [
        r"\bdrop\b", r"\bdelete\b", r"\balter\b", r"\bupdate\b", 
        r"\binsert\b", r"\btruncate\b", r"\bgrant\b"
    ]
    
    # 2. Tentatives de manipulation du prompt (Jailbreak)
    motifs_jailbreak = [
        r"ignore.*règles", r"oublie.*instructions", r"passe outre", 
        r"nouveau prompt", r"tu es maintenant"
    ]
    
    # Vérification SQL
    for motif in mots_sql_dangereux:
        if re.search(motif, texte_min):
            print(f"[SÉCURITÉ] Tentative d'injection SQL détectée : {motif}")
            return False
            
    # Vérification Jailbreak
    for motif in motifs_jailbreak:
        if re.search(motif, texte_min):
            print(f"[SÉCURITÉ] Tentative de Jailbreak détectée : {motif}")
            return False
            
    return True

# --- L'OUTIL DE GÉNÉRATION DE DEVIS ---
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
        data = json.loads(parametres_json)
        nom_client = data.get("nom_client", "Client Anonyme")
        articles = data.get("articles", [])
        
        chemin = generer_devis_pdf(nom_client, articles)
        return f"SUCCÈS : Le devis a bien été généré ici : {chemin}"
        
    except json.JSONDecodeError:
        return "ERREUR : L'Action Input n'est pas un JSON valide. Tu dois envoyer un dictionnaire JSON avec 'nom_client' et 'articles'."
    except Exception as e:
        return f"ERREUR : Impossible de générer le devis. Détails : {str(e)}"

# 4. CRÉATION DE L'AGENT COMMERCIAL SQL AVEC LE PROMPT ANTI-BOUCLE
PREFIX_AGENT = """Tu es l'assistant commercial virtuel du magasin d'informatique de l'EMI. Tu parles TOUJOURS en français.

RÈGLES ABSOLUES :
1. Si l'utilisateur te salue ou demande ton nom, réponds directement sans utiliser d'outils.
2. Si le message de l'utilisateur est incompréhensible (ex: "hjfvn"), n'utilise AUCUN outil et réponds : "Je n'ai pas bien compris."
3. SÉCURITÉ : Tu n'as accès qu'au catalogue et aux stocks. Refuse toute autre demande.

RÈGLES SÉCURITÉ SQL & REQUÊTES :
- Pour les chaînes de caractères en SQL, utilise TOUJOURS des guillemets simples (ex: WHERE categorie = 'Smartphone') et JAMAIS de guillemets doubles.
- Si tu cherches un produit par son nom, utilise l'opérateur LIKE avec des jokers (ex: WHERE description LIKE '%iPhone%').
- Ne fais JAMAIS de "SELECT *" avec un JOIN. Sélectionne uniquement les colonnes utiles (description, prix_unitaire_ht, quantite).

RÈGLES MÉTIER (STOCKS ET ALTERNATIVES) :
- SI LE CLIENT DEMANDE UN DEVIS : Tu DOIS utiliser l'outil 'creer_devis_pdf'.
  * Vérifie le prix et la disponibilité en base de données avant.
  * Formate ton Action Input EXACTEMENT comme un JSON valide.
  * Dès que l'outil répond "SUCCÈS...", TU DOIS IMMÉDIATEMENT ARRÊTER ET RÉPONDRE EXACTEMENT : "Final Answer: Votre devis a bien été généré ! Je me tiens à votre disposition si vous souhaitez le valider."
- Pour vérifier le stock, fais une jointure SQL (JOIN) entre T_Produits (id) et T_Stocks (id_produit).
- SI LE PRODUIT N'EXISTE PAS OU EST EN RUPTURE (quantite = 0) : Trouve jusqu'à 3 alternatives de la MÊME catégorie ayant du stock (quantite > 0). Formule ta réponse : "Je suis désolé, le [Produit] n'est pas disponible. Cependant, je vous propose ces alternatives : [Liste avec prix et stock]".

FORMAT DE RÉPONSE OBLIGATOIRE (Respecte cette structure ReAct) :
Thought: Je dois analyser la demande et utiliser un outil si nécessaire.
Action: [Nom de l'outil]
Action Input: [L'entrée de l'outil]
Observation: [Le résultat de l'outil]
... (Répète si besoin)
Thought: Je connais la réponse.
Final Answer: [Ta réponse finale en français]
"""

agent_executor = create_sql_agent(
    llm=llm,
    toolkit=toolkit, 
    extra_tools=[creer_devis_pdf],
    agent_type="zero-shot-react-description",
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=5, # Réduit pour éviter la consommation excessive de tokens
    early_stopping_method="generate", # CORRECTION ANTI-BOUCLE
    prefix=PREFIX_AGENT
)

# --- ROUTES FLASK ---

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
    if not est_requete_securisee(message_utilisateur):
        message_alerte = "🚨 Alerte de sécurité : Votre requête contient des termes non autorisés ou dangereux. L'action a été bloquée."
        historique_conversation.append((message_utilisateur, message_alerte))
        return jsonify({"reponse": message_alerte})
    msg_normalise = message_utilisateur.strip().lower().rstrip("?!.")

    # --- ÉTAPE 1 : PRÉ-FILTRAGE ---
    SALUTATIONS = ["bonjour", "salut", "hello", "hi", "cc", "coucou", "ca va", "comment vas-tu"]
    REMERCIEMENTS = ["merci", "thanks", "chokran", "merc"]
    MOTS_COURTS = ["ok", "d'accord", "oui", "non", "parfait", "super", "bien"]

    if any(msg_normalise.startswith(s) for s in SALUTATIONS) and len(msg_normalise.split()) < 4:
        return jsonify({"reponse": "Bonjour ! Je suis l'assistant de l'EMI. Comment puis-je vous aider ?"})

    if any(r in msg_normalise for r in REMERCIEMENTS) and len(msg_normalise.split()) < 5:
        return jsonify({"reponse": "Je vous en prie ! Avez-vous d'autres questions sur nos produits ?"})
        
    if msg_normalise in MOTS_COURTS:
        return jsonify({"reponse": "Très bien ! Puis-je vous aider avec autre chose ?"})

    # --- ÉTAPE 2 : APPEL DE L'AGENT AVEC MÉMOIRE PYTHON ---
    try:
        texte_historique = "\n".join([f"Client: {q}\nAssistant: {r}" for q, r in historique_conversation[-3:]])

        if texte_historique:
            question_enrichie = (
                f"CONTEXTE DES DERNIERS MESSAGES :\n{texte_historique}\n"
                f"---------------------------------\n"
                f"QUESTION ACTUELLE : {message_utilisateur}\n\n"
                f"INSTRUCTIONS VITALES :\n"
                f"1. Si la QUESTION ACTUELLE est incompréhensible, réponds : 'Final Answer: Je n'ai pas bien compris votre demande. Pourriez-vous reformuler ?'\n"
                f"2. Si la QUESTION utilise des pronoms, lis le CONTEXTE pour déduire le produit."
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
            texte_brut = erreur_str.split("Could not parse LLM output: `")[1].split("`")[0]
            lignes = [l for l in texte_brut.splitlines() if not l.strip().startswith(("Thought:", "Action:", "Observation:"))]
            texte_final = " ".join(lignes).strip()
            if not texte_final:
                 texte_final = "Je n'ai pas bien compris votre demande. Pourriez-vous reformuler ?"
            historique_conversation.append((message_utilisateur, texte_final))
        else:
            texte_final = "Désolé, j'ai rencontré une petite difficulté technique."
            print(f"Erreur : {e}")

    return jsonify({"reponse": texte_final})


@app.route('/api/voice-message', methods=['POST'])
def discuter_voix():
    donnees = request.json
    texte_transcrit = donnees.get('voice_text', '')
    
    message_utilisateur_formatte = f"🎤 {texte_transcrit}"
    # --- 🛡️ ÉTAPE 0 : GUARDRAIL DE SÉCURITÉ ---
    if not est_requete_securisee(texte_transcrit):
        message_alerte = "🚨 Alerte de sécurité : Votre message vocal contient des termes non autorisés. L'action a été bloquée."
        historique_conversation.append((message_utilisateur_formatte, message_alerte))
        return jsonify({"reponse": message_alerte})
    
    try:
        texte_historique = "\n".join([f"Client: {q}\nAssistant: {r}" for q, r in historique_conversation[-3:]])
        
        if texte_historique:
            question_enrichie = (
                f"CONTEXTE DES DERNIERS MESSAGES :\n{texte_historique}\n"
                f"---------------------------------\n"
                f"QUESTION ACTUELLE : {texte_transcrit}\n\n"
                f"INSTRUCTIONS VITALES : Reste concentré sur la vente et les stocks."
            )
        else:
            question_enrichie = texte_transcrit

        reponse_ia = agent_executor.invoke({"input": question_enrichie})
        texte_final = reponse_ia["output"]
        
        historique_conversation.append((message_utilisateur_formatte, texte_final))

    except Exception as e:
        texte_final = "Désolé, j'ai rencontré une petite difficulté technique lors du traitement de votre message vocal."
        print(f"Erreur Agent 1 (Voice) : {e}")

    return jsonify({"reponse": texte_final})

if __name__ == '__main__':
    app.run(debug=True, port=5000)