# backend/gateway.py
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
# Port: 5000

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool # <-- AJOUT POUR LA PERFORMANCE CONCURRENTE
import uvicorn
import requests

from semantic_router import semantic_router, AGENT_ENDPOINTS
from session_store import ConversationStore

app = FastAPI(title="Gateway Principal - Routeur Sémantique")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# MÉMOIRE CONVERSATIONNELLE (par session)
# ============================================
# La gateway est la seule à détenir l'historique : les agents restent sans état et
# reçoivent les derniers tours de la session à chaque appel.
conversations = ConversationStore(
    max_tours=int(os.getenv("SESSION_MAX_TURNS", "6")),
    ttl_secondes=float(os.getenv("SESSION_TTL_SECONDS", "1800")),
)

# ============================================
# CONFIGURATION DU FALLBACK
# ============================================
FALLBACK_RULES = {
    "sql_agent": {
        "allowed_errors": ["PRODUCT_NOT_FOUND", "NO_RESULT", "EMPTY_RESPONSE"],
        "fallback": "neo4j_agent"
    },
    "neo4j_agent": {
        # Neo4j est la dépendance externe la plus susceptible de tomber : en cas d'erreur
        # technique de l'agent graphe, l'agent SQL (catalogue local) prend le relais.
        "allowed_errors": ["PRODUCT_NOT_FOUND", "NO_RESULT", "EMPTY_RESPONSE", "TOOL_ERROR", "AGENT_ERROR"],
        "fallback": "sql_agent"
    }
}

# Réponses "métier" : utiles à l'utilisateur et à la suite de la conversation.
ERREURS_METIER = {"PRODUCT_NOT_FOUND", "NO_RESULT"}
# Réponses d'acheminement : leur texte est déjà rédigé pour l'utilisateur par call_agent.
ERREURS_TRANSPORT = {"HTTP_ERROR", "TIMEOUT", "CONNECTION_ERROR"}
MESSAGE_ERREUR_GENERIQUE = "Désolé, je rencontre des difficultés pour traiter votre demande. Veuillez contacter le support client."

# ============================================
# FONCTION D'APPEL AUX AGENTS (Rendue Asynchrone)
# ============================================
async def call_agent(endpoint: str, message: str, history: list) -> dict:
    """Appelle un agent de manière asynchrone pour ne pas bloquer le Gateway"""

    # Déterminer le payload selon l'agent
    if "5003" in endpoint or "legal" in endpoint.lower():
        payload = {"question": message}
    else:
        payload = {"message": message}
    payload["history"] = history

    try:
        # PERFORMANCE : Exécution dans un threadpool pour libérer l'Event Loop FastAPI
        response = await run_in_threadpool(
            requests.post, endpoint, json=payload, timeout=70
        )

        if response.status_code != 200:
            return {
                "success": False,
                "error_code": "HTTP_ERROR",
                "response": f"Erreur HTTP {response.status_code}"
            }

        data = response.json()

        # --- NORMALISATION ---
        resp_text = None
        if isinstance(data, dict):
            resp_text = (
                data.get("reponse") or
                data.get("response") or
                data.get("output") or
                data.get("text") or
                data.get("result") or
                data.get("data")
            )

        # Agents à statut structuré (agent SQL, agent graphe) : leur champ "success" est
        # calculé à partir des retours d'outils, on lui fait confiance sans analyser le texte
        # (une réponse de rupture contenant « aucun produit » n'est pas une erreur).
        if isinstance(data, dict) and "success" in data:
            if data["success"] is False:
                return {
                    "success": False,
                    "error_code": data.get("error_code", "AGENT_ERROR"),
                    "response": data.get("message") or resp_text or ""
                }
            if not resp_text:
                return {"success": False, "error_code": "EMPTY_RESPONSE", "response": ""}
            return {"success": True, "response": str(resp_text), "data": data}

        if resp_text is None:
            resp_text = str(data)

        # Agents sans statut structuré (ex. agent juridique) : détection par mots-clés
        error_keywords = ["erreur", "non trouvé", "aucun résultat", "aucun produit", "invalide"]
        if any(kw in str(resp_text).lower() for kw in error_keywords):
            return {
                "success": False,
                "error_code": "PRODUCT_NOT_FOUND",
                "response": resp_text
            }

        return {"success": True, "response": resp_text, "data": data}

    except requests.Timeout:
        return {"success": False, "error_code": "TIMEOUT", "response": "Le service met trop de temps à répondre."}
    except requests.ConnectionError:
        return {"success": False, "error_code": "CONNECTION_ERROR", "response": "Service hors ligne."}
    except Exception as e:
        return {"success": False, "error_code": "UNKNOWN_ERROR", "response": str(e)}

def get_fast_response(message: str) -> str:
    msg_lower = message.lower().strip()
    if any(w in msg_lower for w in ["merci", "thanks", "shukran"]):
        return "Je vous en prie ! Avez-vous d'autres questions ?"
    if any(w in msg_lower for w in ["au revoir", "bye", "bientot"]):
        return "Au revoir ! À bientôt."
    return "Bonjour ! Comment puis-je vous aider aujourd'hui ?"

# ============================================
# ENDPOINT PRINCIPAL
# ============================================
@app.post('/api/chat')
async def chat(request: Request):
    content_type = request.headers.get("content-type", "")
    user_message = ""
    transcribed_text = None
    session_brut = None

    # --- Gestion Audio ---
    if "multipart/form-data" in content_type:
        form = await request.form()
        session_brut = form.get("session_id")
        if 'audio' in form:
            audio_file = form['audio']
            try:
                audio_bytes = await audio_file.read()
                files = {'audio': (audio_file.filename, audio_bytes, audio_file.content_type)}

                # PERFORMANCE : Transcription asynchrone non-bloquante
                trans_resp = await run_in_threadpool(
                    requests.post, 'http://localhost:5002/transcribe', files=files, timeout=70
                )
                trans_data = trans_resp.json()

                if not trans_data.get('success') or not trans_data.get('text'):
                    return JSONResponse(status_code=400, content={"reponse": "Je n'ai pas pu comprendre l'audio."})

                user_message = trans_data['text']
                transcribed_text = user_message
            except Exception as e:
                print(f"Erreur transcription: {e}")
                return JSONResponse(status_code=500, content={"reponse": "Service de transcription indisponible."})
        else:
            return JSONResponse(status_code=400, content={"reponse": "Fichier audio manquant"})

    # --- Gestion Texte ---
    else:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"reponse": "Format JSON requis"})
        if not isinstance(data, dict):
            return JSONResponse(status_code=400, content={"reponse": "Format JSON requis"})

        user_message = str(data.get('message') or '').strip()
        session_brut = data.get('session_id')

    # Identifiant de session : repris du client s'il est valide, sinon créé et renvoyé.
    session_id = conversations.normaliser_id(session_brut)

    if not user_message:
        return JSONResponse(status_code=400, content={"reponse": "Message vide", "session_id": session_id})

    historique = conversations.historique(session_id)

    # --- Routage Sémantique ---
    agent_name, endpoint, confidence = semantic_router.route(
        user_message, dernier_agent=conversations.dernier_agent(session_id)
    )
    original_agent = agent_name

    print(f"📝 Message: {user_message[:60]}... (session {session_id[:8]}, {len(historique) // 2} tour(s) en mémoire)")
    print(f"🎯 Agent: {agent_name} (confiance: {confidence:.2f})")
    print(f"🔗 Endpoint: {endpoint}")

    # PERFORMANCE : Coupe-circuit (Short-Circuit) pour les réponses locales rapides
    if endpoint == "fast_response_local":
        response_data = {
            "reponse": get_fast_response(user_message),
            "session_id": session_id,
            "routing_info": {
                "agent": agent_name,
                "fallback_used": False,
                "original_agent": original_agent,
                "confidence": round(confidence, 3)
            }
        }
        if transcribed_text: response_data["transcribed"] = transcribed_text
        return JSONResponse(content=response_data)

    # --- Exécution de l'Agent et Fallback ---
    result = await call_agent(endpoint, user_message, historique)

    if not result.get("success"):
        error_code = result.get("error_code", "UNKNOWN")

        # Vérification et exécution du fallback
        if agent_name in FALLBACK_RULES and error_code in FALLBACK_RULES[agent_name]["allowed_errors"]:
            fallback_agent = FALLBACK_RULES[agent_name]["fallback"]
            fallback_endpoint = semantic_router.agent_embeddings[fallback_agent]["endpoint"]
            print(f"⚠️ Fallback: {agent_name} → {fallback_agent} (erreur: {error_code})")

            fallback_result = await call_agent(fallback_endpoint, user_message, historique)

            if fallback_result.get("success"):
                result = fallback_result
                agent_name = fallback_agent
            # Sinon on conserve le résultat de l'agent d'origine : un « produit introuvable »
            # est plus utile à l'utilisateur qu'un message d'erreur générique.

    # --- Construction de la réponse finale ---
    error_code = result.get("error_code")
    if result.get("success") or error_code in ERREURS_METIER:
        final_response = result.get("response") or "Je n'ai pas trouvé de réponse à votre question."
        status_code = 200
        # Seules les réponses porteuses de sens alimentent la mémoire de la session
        conversations.enregistrer_tour(session_id, user_message, final_response, agent_name)
    elif error_code in ERREURS_TRANSPORT:
        final_response = result.get("response") or MESSAGE_ERREUR_GENERIQUE
        status_code = 502
    else:
        # AGENT_ERROR, TOOL_ERROR... : le texte brut d'exception n'est pas montré au client
        print(f"❌ Échec agent {agent_name} ({error_code}): {str(result.get('response'))[:200]}")
        final_response = MESSAGE_ERREUR_GENERIQUE
        status_code = 502

    response_data = {
        "reponse": final_response,
        "session_id": session_id,
        "routing_info": {
            "agent": agent_name,
            "fallback_used": agent_name != original_agent,
            "original_agent": original_agent,
            "confidence": round(confidence, 3)
        }
    }
    if status_code != 200:
        response_data["routing_info"]["error"] = error_code

    if transcribed_text:
        response_data["transcribed"] = transcribed_text

    return JSONResponse(status_code=status_code, content=response_data)

# ============================================
# ENDPOINTS SESSION
# ============================================
@app.get('/api/session/{session_id}')
def get_session(session_id: str):
    return {
        "session_id": session_id,
        "dernier_agent": conversations.dernier_agent(session_id),
        "historique": conversations.historique(session_id)
    }

@app.delete('/api/session/{session_id}')
def delete_session(session_id: str):
    return {"success": conversations.effacer(session_id)}

# ============================================
# ENDPOINT ADMIN
# ============================================
@app.get('/admin/agents')
def list_agents():
    return {
        "agents": semantic_router.get_all_agents(),
        "fallback_rules": FALLBACK_RULES,
        "sessions_actives": len(conversations)
    }

if __name__ == '__main__':
    print("=" * 60)
    print("🚪 Gateway Principal - Routeur Sémantique v3")
    print("   Port → 5000")
    print("   Chat → POST /api/chat")
    print("   Session → GET/DELETE /api/session/{session_id}")
    print("   Debug → GET /admin/agents")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=5000)
