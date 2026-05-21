import os
import re
import hashlib
import tempfile
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import chromadb
from chromadb.utils import embedding_functions
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter
import docx
import fitz  # PyMuPDF pour PDF
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ============================================
# CONFIGURATION
# ============================================
UPLOAD_FOLDER = "uploads/legal_documents"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

chroma_client = chromadb.HttpClient(host="localhost", port=8000)
collection_name = "legal_documents"

embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)

collection = chroma_client.get_or_create_collection(
    name=collection_name,
    embedding_function=embedding_fn,
    metadata={"hnsw:space": "cosine"}
)

llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)

# ============================================
# UTILITAIRES DE TRAITEMENT DE DOCUMENTS
# ============================================
class DocumentProcessor:
    @staticmethod
    def extract_text_from_pdf(file_path: str) -> str:
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
        return text

    @staticmethod
    def extract_text_from_docx(file_path: str) -> str:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])

    @staticmethod
    def extract_text_from_txt(file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    @staticmethod
    def chunk_text(text: str, fallback_chunk_size: int = 500, fallback_overlap: int = 100):
        lines = text.split('\n')
        chunks = []
        current_chunk = []
       
        def is_title(line):
            l = line.strip()
            if len(l) < 4 or len(l) > 150:
                return False
           
            if re.match(r'(?i)^(article|chapitre|titre|section|partie)\s+[\dIVXLC]', l):
                return True
           
            if l.isupper() and re.search(r'[A-ZÀ-Ÿ]', l):
                return True
           
            if re.match(r'^([\d]+\.|[IVXLC]+\.)\s+[A-ZÀ-Ÿ]', l):
                return True
            return False

        has_titles = sum(1 for line in lines if is_title(line))
       
        if has_titles >= 2:
            for line in lines:
                if is_title(line) and current_chunk:
                    chunks.append("\n".join(current_chunk).strip())
                    current_chunk = [line]
                else:
                    current_chunk.append(line)
            if current_chunk:
                chunks.append("\n".join(current_chunk).strip())
           
            final_chunks = []
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=fallback_chunk_size,
                chunk_overlap=fallback_overlap,
                separators=["\n\n", "\n", " ", ""]
            )
            for c in chunks:
                if len(c) > fallback_chunk_size * 1.5:
                    final_chunks.extend(splitter.split_text(c))
                elif len(c) > 0:
                    final_chunks.append(c)
            return final_chunks
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=fallback_chunk_size,
                chunk_overlap=fallback_overlap,
                separators=["\n\n", "\n", " ", ""]
            )
            return splitter.split_text(text)


processor = DocumentProcessor()

# ============================================
# ENDPOINTS ADMIN
# ============================================
@app.route('/admin/upload', methods=['POST'])
def upload_document():
    if 'document' not in request.files:
        return jsonify({"error": "Aucun document fourni"}), 400
    file = request.files['document']
    if file.filename == '':
        return jsonify({"error": "Fichier vide"}), 400

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.read())
        tmp_path = tmp.name

    try:
        ext = suffix.lower()
        if ext == '.pdf':
            text = processor.extract_text_from_pdf(tmp_path)
        elif ext == '.docx':
            text = processor.extract_text_from_docx(tmp_path)
        elif ext == '.txt':
            text = processor.extract_text_from_txt(tmp_path)
        else:
            return jsonify({"error": f"Format non supporté: {ext}"}), 400

        if not text.strip():
            return jsonify({"error": "Aucun texte extractible"}), 400

        chunks = processor.chunk_text(text)

        doc_id = hashlib.md5(file.filename.encode()).hexdigest()
        timestamp = datetime.now().isoformat()

        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_chunk_{i}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "source": file.filename,
                "doc_id": doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "upload_date": timestamp,
                "file_type": ext[1:]
            })

        collection.add(ids=ids, documents=documents, metadatas=metadatas)

        return jsonify({
            "success": True,
            "message": f"Votre fichier « {file.filename} » a été téléchargé avec succès !",
            "filename": file.filename
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


@app.route('/admin/documents', methods=['GET'])
def list_documents():
    all_data = collection.get()
    unique = {}
    for meta in all_data['metadatas']:
        doc_id = meta['doc_id']
        if doc_id not in unique:
            unique[doc_id] = {
                "filename": meta['source'],
                "upload_date": meta['upload_date'],
                "total_chunks": meta['total_chunks'],
                "file_type": meta['file_type']
            }
    return jsonify({"documents": list(unique.values())})


@app.route('/admin/delete/<doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    all_data = collection.get()
    ids_to_delete = [all_data['ids'][i] for i, meta in enumerate(all_data['metadatas']) if meta['doc_id'] == doc_id]
    
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
    return jsonify({"success": True, "deleted_chunks": len(ids_to_delete)})


# ============================================
# SEARCH ENDPOINT
# ============================================
@app.route('/search', methods=['POST'])
def search():
    data = request.json
    question = data.get('question', '')
    top_k = data.get('top_k', 3)
    if not question:
        return jsonify({"error": "Question vide"}), 400

    results = collection.query(
        query_texts=[question],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )

    contexts = []
    if results['documents'] and results['documents'][0]:
        for i, doc in enumerate(results['documents'][0]):
            similarity = 1 - results['distances'][0][i]
            contexts.append({
                "text": doc,
                "similarity": similarity,
                "source": results['metadatas'][0][i]['source'],
                "chunk_index": results['metadatas'][0][i]['chunk_index']
            })
    return jsonify({"contexts": contexts})


# ============================================
# ASK ENDPOINT (Version renforcée)
# ============================================
@app.route('/ask', methods=['POST'])
def ask():
    """Endpoint robuste pour le gateway"""
    # 1. Récupération sécurisée des données
    if not request.is_json:
        return jsonify({"error": "Content-Type doit être application/json"}), 400
    
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "JSON invalide ou vide"}), 400

    question = data.get('question', '').strip()
    if not question:
        return jsonify({"error": "Le champ 'question' est obligatoire et ne doit pas être vide"}), 400

    # 2. Recherche dans ChromaDB
    try:
        results = collection.query(
            query_texts=[question],
            n_results=6,
            include=["documents", "metadatas"]
        )
    except Exception as e:
        return jsonify({"error": f"Erreur ChromaDB: {str(e)}"}), 500

    contexts = []
    if results.get('documents') and results['documents'][0]:
        for i, doc in enumerate(results['documents'][0]):
            source = results['metadatas'][0][i].get('source', 'Document inconnu')
            chunk_idx = results['metadatas'][0][i].get('chunk_index', i)
            contexts.append(f"[Document: {source} - Extrait {chunk_idx}]\n{doc}")

    if not contexts:
        return jsonify({
            "response": "Je n'ai trouvé aucune information correspondante dans les documents juridiques chargés.",
            "sources": []
        })

    # 3. Prompt renforcé
    prompt = f"""
Tu es un assistant juridique marocain expert en droit commercial (Loi 15-95).
Réponds de façon claire, précise et professionnelle en français.

Règles :
- Base-toi uniquement sur les documents fournis ci-dessous.
- Cite l'article quand c'est possible.
- Ne refuse jamais de répondre si l'information est présente.

Documents :
{chr(10).join(contexts)}

Question : {question}

Réponse :
"""

    try:
        response = llm.invoke(prompt)
        answer = response.content.strip()

        return jsonify({
            "response": answer,
            "sources": list(set(m.get('source', '') for m in results['metadatas'][0]))
        })

    except Exception as e:
        return jsonify({"error": f"Erreur LLM: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "collection": collection_name, "count": collection.count()})


if __name__ == '__main__':
    print("=" * 50)
    print("📚 Agent 3 (RAG Légal) démarré sur http://localhost:5003")
    print("=" * 50)
    app.run(debug=True, port=5003)