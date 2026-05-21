import os
import tempfile
from flask import Flask, request, jsonify
from flask_cors import CORS
import speech_recognition as sr
from pydub import AudioSegment

app = Flask(__name__)
CORS(app)

recognizer = sr.Recognizer()

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """
    Reçoit un fichier audio (multipart/form-data) et retourne le texte transcrit.
    Formats supportés : WebM, WAV, MP3, etc. (via pydub)
    """
    if 'audio' not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier audio reçu."}), 400

    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"success": False, "error": "Fichier vide."}), 400

    # Utilisation d'un dossier temporaire sécurisé
    temp_dir = tempfile.gettempdir()
    in_path = os.path.join(temp_dir, "audio_recv.webm")
    wav_path = os.path.join(temp_dir, "audio_conv.wav")

    try:
        # Sauvegarde du fichier brut
        audio_file.save(in_path)

        # Conversion en WAV (pydub gère automatiquement les formats)
        audio = AudioSegment.from_file(in_path)
        audio.export(wav_path, format="wav")

        # Transcription via Google Speech Recognition
        with sr.AudioFile(wav_path) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="fr-FR")

        return jsonify({
            "success": True,
            "text": text
        })

    except sr.UnknownValueError:
        return jsonify({"success": False, "error": "Le fichier audio n'a pas pu être compris."}), 400
    except sr.RequestError as e:
        return jsonify({"success": False, "error": f"Erreur du service de reconnaissance : {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        # Nettoyage des fichiers temporaires (ignorer les erreurs)
        for path in (in_path, wav_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "transcription"})

if __name__ == '__main__':
    print("🎙️ Agent 2 (Transcription) démarré sur http://localhost:5002")
    print("   Endpoint : POST /transcribe")
    app.run(debug=True, port=5002)