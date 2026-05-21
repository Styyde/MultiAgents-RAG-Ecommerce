from flask import Flask, request, jsonify
from semantic_router import semantic_router, AGENT_ENDPOINTS, ROUTER_METADATA  # ← Import corrigé
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# ============================================
# FONCTION D'APPEL AUX AGENTS
# ============================================
def call_agent(endpoint: str, message: str) -> str:
    """Appelle l'agent correspondant avec le bon format de payload"""
    if endpoint == "fast_response_local":
        return get_fast_response(message)

    try:
        # Adaptation du payload selon l'agent
        if "5003" in endpoint or "legal_agent" in endpoint.lower():
            payload = {"question": message}          # Agent RAG Légal
        else:
            payload = {"message": message}           # Agent Catalogue

        print(f"📤 Appel à {endpoint} | Payload: {payload}")

        response = requests.post(endpoint, json=payload, timeout=60)

        if response.status_code == 200:
            data = response.json()
            # Gestion flexible des clés de réponse
            for key in ["response", "reponse", "output", "text", "result"]:
                if key in data:
                    return str(data[key])
            return str(data)  # fallback

        else:
            return f"Erreur agent ({response.status_code})"

    except requests.Timeout:
        return "Le service met trop de temps à répondre."
    except requests.ConnectionError:
        return "Impossible de contacter l'agent (service hors ligne)."
    except Exception as e:
        print(f"❌ Erreur call_agent: {e}")
        return f"Erreur interne : {str(e)}"


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
@app.route('/api/chat', methods=['POST'])
def chat():
    # --- Gestion Audio ---
    if 'audio' in request.files:
        audio_file = request.files['audio']
        try:
            files = {'audio': (audio_file.filename, audio_file.read(), audio_file.content_type)}
            trans_resp = requests.post(
                'http://localhost:5002/transcribe', 
                files=files, 
                timeout=30
            )
            trans_data = trans_resp.json()
            
            if not trans_data.get('success') or not trans_data.get('text'):
                return jsonify({"reponse": "Je n'ai pas pu comprendre l'audio."})
            
            user_message = trans_data['text']
        except Exception as e:
            print(f"Erreur transcription: {e}")
            return jsonify({"reponse": "Service de transcription indisponible."})
    
    # --- Gestion Texte ---
    else:
        if not request.is_json:
            return jsonify({"reponse": "Format JSON requis"}), 400
        
        data = request.get_json(silent=True) or {}
        user_message = data.get('message', '').strip()

    if not user_message:
        return jsonify({"reponse": "Message vide"}), 400

    # Routage
    agent_name, endpoint, confidence = semantic_router.route(user_message)

    print(f"📝 Message: {user_message[:60]}...")
    print(f"🎯 Agent: {agent_name} (confiance: {confidence:.2f})")
    print(f"🔗 Endpoint: {endpoint}")

    # Appel
    response_text = call_agent(endpoint, user_message)

    return jsonify({
        "reponse": response_text,
        "routing_info": {
            "agent": agent_name,
            "confidence": round(float(confidence), 3)
        }
    })


# ============================================
# ENDPOINT ADMIN
# ============================================
@app.route('/admin/agents', methods=['GET'])
def list_agents():
    return jsonify({
        "agents": semantic_router.get_all_agents(),
        "metadata": ROUTER_METADATA
    })


if __name__ == '__main__':
    print("=" * 60)
    print("🚪 Gateway Principal - Routeur Sémantique v2")
    print("   Port → 5000")
    print("   Chat → POST /api/chat")
    print("   Debug → GET /admin/agents")
    print("=" * 60)
    app.run(debug=True, port=5000)