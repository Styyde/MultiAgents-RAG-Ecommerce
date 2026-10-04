# backend/agent_sql.py
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# Port: 5001

from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Importations nettoyées depuis notre nouveau module d'outils
from sqltools import SQL_TOOLS
from agent_common import construire_reponse, est_salutation_seule, parse_history

load_dotenv()

app = FastAPI(title="Agent Commercial Informatique - Core")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Initialisation du LLM de manière déterministe
llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0)

# ============================================
# PROMPT SYSTÈME AJUSTÉ & RENFORCÉ
# ============================================
SYSTEM_PROMPT = """
Tu es l'assistant commercial expert du magasin informatique EMI (section Base de Données Relationnelle SQL, Facturation et Gestion des Stocks).
Tu t'exprimes de façon courtoise, concise et professionnelle, exclusivement en français.

RÈGLES OPÉRATIONNELLES STRICTES :
1. Pour TOUTE interrogation portant sur le tarif (prix) ou la disponibilité (stock) d'un produit précis, invoque obligatoirement `get_realtime_price_stock` en premier recours. Si le client précise une quantité (« je veux 3 ... »), passe-la dans `quantite_souhaitee`.
2. Dans le cas unique où la recherche par mot-clé avec `get_realtime_price_stock` s'avère infructueuse, bascule immédiatement sur `get_product_price_stock_by_name`.
3. Lorsqu'un client exprime une contrainte budgétaire (ex: "à moins de...", "entre X et Y MAD"), utilise EXCLUSIVEMENT l'outil `get_produits_par_budget_sql` pour déléguer le tri à la base de données.
4. Lorsqu'on te demande quels produits sont bientôt en rupture, épuisés, ou un rapport sur l'état des stocks, appelle `get_alertes_stocks_sql`.
5. Si une action de vente se finalise ou si l'on te signale une entrée de marchandises, mets à jour la base SQL en utilisant `modifier_stock_produit_sql`.
6. Ne falsifie jamais et n'invente aucun code produit (SKU). Utilise à l'identique les codes SKU authentiques fournis par les retours d'outils.
7. Lorsque l'utilisateur demande explicitement la création d'un devis ou valide formellement sa sélection finale, compile les informations collectées et appelle l'outil `creer_devis_pdf_tool`.
8. Exemple de réponse attendue :
   "Voici les produits iPhone que nous disposons :
   1. iPhone 15 Pro - 13 000 MAD
   2. iPhone 15 Pro Max - 15 000 MAD"

RUPTURE DE STOCK ET ALTERNATIVES :
9. Si un outil renvoie `disponible: false` (statut RUPTURE ou STOCK_INSUFFISANT), annonce d'abord clairement la rupture ou la quantité réellement disponible, puis propose les produits de `alternatives_en_stock` avec leur nom, leur prix HT et leur stock. Ces alternatives sont déjà vérifiées en stock : ne les revérifie pas et n'en invente aucune autre. Si la liste est vide, dis qu'aucun produit équivalent n'est disponible actuellement.
10. Si le client demande explicitement une alternative, un équivalent ou un produit de remplacement, utilise `trouver_alternatives_en_stock`.

CONTEXTE DE CONVERSATION :
11. Les messages précédents de la conversation te sont fournis. Sers-t'en pour comprendre les références (« celui-là », « le deuxième », « ajoute-le au devis ») et retrouver le produit exact déjà évoqué, ainsi que pour compiler les articles d'un devis construit sur plusieurs messages.
12. Les prix et stocks cités dans l'historique peuvent être périmés : revérifie-les avec les outils avant de les confirmer au client.
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder("chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])

# Instanciation de l'agent avec notre liste d'outils cloisonnés
agent = create_tool_calling_agent(llm, SQL_TOOLS, prompt)
agent_executor = AgentExecutor(
    agent=agent,
    tools=SQL_TOOLS,
    verbose=True,  # Activé pour faciliter le débugging des appels d'outils
    max_iterations=5,
    handle_parsing_errors=True,
    # Les retours d'outils servent à déterminer le statut et à garantir les alternatives
    return_intermediate_steps=True,
)


# ============================================
# ENDPOINTS REST
# ============================================
# L'agent est sans état : la mémoire conversationnelle est détenue par la gateway,
# qui transmet l'historique récent de la session dans le champ "history".

def traiter_message(msg: str, history: list) -> dict:
    # Gestion simplifiée des salutations génériques hors boucle agent
    if est_salutation_seule(msg):
        return {"success": True, "data": "Bonjour ! Je suis l'assistant commercial de l'EMI. Que puis-je faire pour vous aujourd'hui ?"}

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
        return JSONResponse(status_code=400, content={"success": False, "error_code": "EMPTY_MESSAGE", "message": "Le contenu du message ne peut pas être vide."})

    return JSONResponse(content=traiter_message(msg, parse_history(data.get("history"))))


if __name__ == '__main__':
    print("🚀 Serveur d'API de l'Agent SQL démarré sur http://localhost:5001")
    uvicorn.run(app, host="0.0.0.0", port=5001)
