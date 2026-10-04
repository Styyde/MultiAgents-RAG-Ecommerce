# backend/agent_neo4j.py
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# Port: 5005

from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from graphtools import TOOLS_LIST
from agent_common import construire_reponse, est_salutation_seule, parse_history

load_dotenv()
app = FastAPI(title="Agent Neo4j")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0)

# Tous les outils Neo4j depuis graphtools.py
tools = TOOLS_LIST

SYSTEM_PROMPT = """
Tu es un expert en exploration de Knowledge Graph produit. Tu as accès à un large panel d'outils.
Voici le guide STRICT de décision pour choisir tes outils. Tu ne dois utiliser qu'un seul outil adapté au besoin principal :

### 🎯 1. RECHERCHES & FILTRES
- `trouver_produits_par_traversabilite_dynamique` : À utiliser QUAND le client cherche un produit en se basant sur la caractéristique d'un AUTRE produit (ex: "Je veux un PC avec le même processeur que le Dell XPS", "Même RAM que l'iPhone").
- `rechercher_par_caracteristiques` : À utiliser QUAND le client donne plusieurs spécifications techniques claires (ex: "Un PC avec 16GB de RAM et 1To de stockage").
- `rechercher_produits_par_caracteristique` : À utiliser QUAND le client cherche à partir d'une SEULE valeur technique globale (ex: "Montre-moi tout ce qui a un port USB-C" ou "Quels produits ont la puce M3 ?").
- `recherche_semantique_produits` : À utiliser QUAND le client décrit un cas d'usage vague ou un besoin abstrait SANS caractéristiques précises (ex: "un ordinateur pour faire du montage vidéo", "pour écouter de la musique").
- `recherche_hybride` : Outil de secours pour des requêtes très complexes qui mélangent un besoin abstrait et un mot-clé précis, si la sémantique échoue.

### 🤝 2. RECOMMANDATIONS & ALTERNATIVES
- `trouver_alternatives_produit` : À utiliser QUAND le client demande un "plan B", une alternative, un équivalent, ou un remplaçant à un produit (notamment un produit en rupture de stock). Renvoie uniquement des produits EN STOCK avec leur prix et leur stock réels.
- `recommander_produits_similaires_multi_criteres` : À utiliser QUAND le client demande des recommandations poussées ou "les produits qui ressemblent le plus" à un produit de référence (analyse les specs, le vecteur et les graphes).
- `trouver_produits_similaires` : À utiliser pour trouver des jumeaux textuels basés uniquement sur la similarité des descriptions.
- `verifier_compatibilite_produit` : À utiliser EXCLUSIVEMENT pour les questions d'accessoires ou de fonctionnement croisé (ex: "Qu'est-ce qui est compatible avec mon iPhone ?", "Quels accessoires fonctionnent avec...").

### 📊 3. EXPLORATION & INFORMATIONS GLOBALES
- `get_informations_produit` : À utiliser QUAND le client demande les détails, la fiche technique complète ou les caractéristiques d'un produit précis dont tu connais déjà l'existence.
- `lister_produits_par_categorie` : À utiliser QUAND le client veut explorer un segment global (ex: "Affiche-moi tous vos écrans", "Quels sont les smartphones disponibles ?").
- `lister_produits_par_marque` : À utiliser QUAND le client cible un constructeur (ex: "Montre-moi tout ce que vous avez chez Apple ou Samsung").
- `get_statistiques_catalogue` : À utiliser QUAND le client pose des questions sur le magasin lui-même (ex: "Combien de produits avez-vous ?", "Quelles catégories vendez-vous ?").

### 📦 4. STOCK ET CONTEXTE
- Les outils de recommandation et d'alternatives ne renvoient que des produits EN STOCK : cite le prix HT (`prix_ht`) et le stock (`stock`) qu'ils fournissent, n'en invente jamais. Si aucun produit n'est renvoyé, dis qu'aucune alternative n'est disponible actuellement.
- Les outils qui attendent `product_sku` acceptent aussi le nom du produit (ex: "Dell XPS 15").
- Les messages précédents de la conversation te sont fournis : sers-t'en pour comprendre les références (« celui-là », « le premier », « une alternative à ce produit ») et retrouver le produit exact déjà évoqué."""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

agent = create_tool_calling_agent(llm, tools, prompt)
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=False,
    max_iterations=6,
    handle_parsing_errors=True,
    return_intermediate_steps=True,
)

try:
    agent_executor.invoke({"input": "Bonjour"})
    print("✅ Agent Neo4j prêt sur port 5005")
except Exception as e:
    print(f"⚠️  Warm-up de l'agent Neo4j échoué (l'agent démarre quand même): {e}")

# L'agent est sans état : la mémoire conversationnelle est détenue par la gateway,
# qui transmet l'historique récent de la session dans le champ "history".

def traiter_message(msg: str, history: list) -> dict:
    if est_salutation_seule(msg):
        return {"success": True, "data": "Bonjour ! Je suis l'agent spécialiste du graphe de connaissances."}
    try:
        resultat = agent_executor.invoke({"input": msg, "chat_history": history})
        return construire_reponse(resultat)
    except Exception as e:
        return {"success": False, "error_code": "AGENT_ERROR", "message": str(e)}

@app.post('/process')
def process(data: dict = Body(None)):
    if not data or 'message' not in data:
        return JSONResponse(status_code=400, content={"success": False, "error_code": "NO_MESSAGE", "message": "Message requis"})
    msg = str(data['message']).strip()
    if not msg:
        return JSONResponse(status_code=400, content={"success": False, "error_code": "EMPTY_MESSAGE", "message": "Message vide"})

    return JSONResponse(content=traiter_message(msg, parse_history(data.get("history"))))

if __name__ == '__main__':
    print("🚀 Agent Neo4j démarré sur http://localhost:5005")
    uvicorn.run(app, host="0.0.0.0", port=5005)
