import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import tempfile
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from pydub import AudioSegment
from groq import Groq
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv()

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Initialisation du client Groq (clé API lue depuis .env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY manquante dans le fichier .env")
    raise RuntimeError("GROQ_API_KEY non définie")

groq_client = Groq(api_key=GROQ_API_KEY)

# Configuration de la conversion audio
AUDIO_FORMATS_SUPPORTED = (".webm", ".mp3", ".m4a", ".ogg", ".wav")
MAX_AUDIO_SIZE_MB = 25  # Whisper accepte max 25 Mo

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """
    Reçoit un fichier audio (multipart/form-data) et retourne le texte transcrit.
    Utilise l'API Groq Whisper pour une rapidité optimale (< 500 ms).
    Formats supportés : WebM, MP3, WAV, M4A, OGG (via pydub).
    """
    # 1. Vérification de la présence du fichier
    if 'audio' not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier audio reçu."}), 400

    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"success": False, "error": "Fichier vide."}), 400

    # 2. Sauvegarde temporaire et vérification taille
    temp_dir = tempfile.gettempdir()
    in_path = os.path.join(temp_dir, "audio_recv_temp")
    wav_path = os.path.join(temp_dir, "audio_conv.wav")

    try:
        # Sauvegarde du fichier brut
        audio_file.save(in_path)

        # Vérification taille (optionnelle mais conseillée)
        file_size = os.path.getsize(in_path) / (1024 * 1024)
        if file_size > MAX_AUDIO_SIZE_MB:
            return jsonify({
                "success": False,
                "error": f"Fichier trop volumineux ({file_size:.1f} Mo > {MAX_AUDIO_SIZE_MB} Mo)"
            }), 400

        # Conversion en WAV mono, 16 kHz (format recommandé par Whisper)
        audio = AudioSegment.from_file(in_path)
        # Réduction à un canal et 16 kHz pour accélérer le traitement
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(wav_path, format="wav")

        # 3. Appel à l'API Groq Whisper (bloquant mais ultra rapide)
        with open(wav_path, "rb") as wav_file:
            transcription = groq_client.audio.transcriptions.create(
                file=(os.path.basename(wav_path), wav_file),
                model="whisper-large-v3",
                language="fr",
                response_format="json",
                temperature=0.0
            )

        transcribed_text = transcription.text.strip()
        if not transcribed_text:
            return jsonify({"success": False, "error": "La transcription n'a produit aucun texte."}), 400

        logger.info(f"Transcription réussie : {transcribed_text[:60]}...")
        return jsonify({"success": True, "text": transcribed_text})

    except Exception as e:
        logger.exception("Erreur lors de la transcription")
        return jsonify({"success": False, "error": f"Erreur interne : {str(e)}"}), 500

    finally:
        # Nettoyage systématique des fichiers temporaires
        for path in (in_path, wav_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as cleanup_err:
                    logger.warning(f"Impossible de supprimer {path} : {cleanup_err}")

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "transcription", "backend": "groq-whisper"})

if __name__ == '__main__':
    print("🎙️ Agent 2 (Transcription) démarré sur http://localhost:5002")
    print("   Endpoint : POST /transcribe (multipart/form-data, champ 'audio')")
    print("   Backend : Groq Whisper Large v3 – temps réponse < 1s")
    # threaded=True permet de gérer plusieurs requêtes simultanées (léger gain)
    app.run(debug=False, port=5002, threaded=True)
