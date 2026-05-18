import os
import tempfile
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import speech_recognition as sr
from pydub import AudioSegment

app = Flask(__name__)
CORS(app)

AGENT1_URL = "http://127.0.0.1:5000/api/voice-message"
recognizer = sr.Recognizer()

@app.route('/voice-message', methods=['POST'])
def handle_voice():
    if 'audio' not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier audio reçu."}), 400
        
    audio_file = request.files['audio']
    
    # Correction Windows : On utilise des chemins sous forme de chaînes de caractères simples
    # pour éviter que tempfile ne verrouille les fichiers en tâche de fond.
    dossier_temp = tempfile.gettempdir()
    chemin_in = os.path.join(dossier_temp, "audio_recu.webm")
    chemin_wav = os.path.join(dossier_temp, "audio_converti.wav")
    
    try:
        # 1. Sauvegarde du fichier brut
        audio_file.save(chemin_in)
        
        # 2. Conversion en WAV via pydub
        audio = AudioSegment.from_file(chemin_in)
        audio.export(chemin_wav, format="wav")
        
        # 3. Transcription Google STT
        with sr.AudioFile(chemin_wav) as source:
            audio_data = recognizer.record(source)
            texte_transcrit = recognizer.recognize_google(audio_data, language="fr-FR")
            
        # 4. Envoi à l'Agent 1
        payload = {
            "voice_text": texte_transcrit,
            "source": "voice"
        }
        reponse_agent1 = requests.post(AGENT1_URL, json=payload)
        donnees_agent1 = reponse_agent1.json()
        
        return jsonify({
            "success": True,
            "user_message": f"🎤 {texte_transcrit}",
            "assistant_response": donnees_agent1.get("reponse", "Erreur lors de la réponse de l'assistant.")
        })

    except sr.UnknownValueError:
        return jsonify({"success": False, "error": "L'audio n'a pas pu être compris."}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
        
    finally:
        # Nettoyage sécurisé sans conflit de processus
        if os.path.exists(chemin_in):
            try: os.remove(chemin_in)
            except: pass
        if os.path.exists(chemin_wav):
            try: os.remove(chemin_wav)
            except: pass

if __name__ == '__main__':
    app.run(port=5001, debug=True)