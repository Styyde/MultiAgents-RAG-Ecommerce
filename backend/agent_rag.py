import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import re
import hashlib
import tempfile
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import chromadb
from chromadb.utils import embedding_functions
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter
import docx
import fitz  # PyMuPDF pour PDF
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Agent 3 (RAG Légal)")

# Configuration CORS pour FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION
# ============================================
UPLOAD_FOLDER = "uploads/legal_documents"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CHROMA_DB_PATH = os.path.join(os.path.dirname(__file__), "chroma_store")
collection_name = "legal_documents"

try:
    # Client embarqué (pas de serveur Chroma externe à lancer/configurer).
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )

    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"}
    )
except Exception as e:
    # Une erreur ici ne doit pas empêcher le reste de l'app (health check, etc.) de démarrer.
    print(f"⚠️  Impossible d'initialiser ChromaDB au démarrage: {e}")
    collection = None

llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0)


# ============================================
# UTILITAIRES DE TRAITEMENT DE DOCUMENTS (Inchangé)
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
@app.post('/admin/upload')
async def upload_document(document: UploadFile = File(...)):
    if collection is None:
        return JSONResponse(status_code=503, content={"error": "ChromaDB indisponible"})
    if not document.filename:
        return JSONResponse(status_code=400, content={"error": "Fichier vide"})

    suffix = os.path.splitext(document.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        # Lecture asynchrone du flux de fichier sous FastAPI
        content = await document.read()
        tmp.write(content)
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
            return JSONResponse(status_code=400, content={"error": f"Format non supporté: {ext}"})

        if not text.strip():
            return JSONResponse(status_code=400, content={"error": "Aucun texte extractible"})

        chunks = processor.chunk_text(text)

        doc_id = hashlib.md5(document.filename.encode()).hexdigest()
        timestamp = datetime.now().isoformat()

        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_chunk_{i}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({
                "source": document.filename,
                "doc_id": doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "upload_date": timestamp,
                "file_type": ext[1:]
            })

        # Supprime les anciens chunks du même fichier avant de ré-indexer (évite les doublons).
        existing = collection.get(where={"doc_id": doc_id})
        if existing['ids']:
            collection.delete(ids=existing['ids'])

        collection.add(ids=ids, documents=documents, metadatas=metadatas)

        return {
            "success": True,
            "message": f"Votre fichier « {document.filename} » a été téléchargé avec succès !",
            "filename": document.filename
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        os.unlink(tmp_path)


@app.get('/admin/documents')
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
    return {"documents": list(unique.values())}


@app.delete('/admin/delete/{doc_id}')
def delete_document(doc_id: str):
    all_data = collection.get()
    ids_to_delete = [all_data['ids'][i] for i, meta in enumerate(all_data['metadatas']) if meta['doc_id'] == doc_id]
    
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
    return {"success": True, "deleted_chunks": len(ids_to_delete)}


# ============================================
# SEARCH ENDPOINT
# ============================================
@app.post('/search')
def search(data: dict = Body(...)):
    if collection is None:
        return JSONResponse(status_code=503, content={"error": "ChromaDB indisponible"})

    question = data.get('question', '')
    top_k = data.get('top_k', 3)
    if not question:
        return JSONResponse(status_code=400, content={"error": "Question vide"})

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
    return {"contexts": contexts}


# ============================================
# ASK ENDPOINT (Version FastAPI)
# ============================================
@app.post('/ask')
def ask(data: dict = Body(None)):
    """Endpoint robuste pour le gateway"""
    if data is None:
        return JSONResponse(status_code=400, content={"error": "JSON invalide ou vide"})

    question = data.get('question', '').strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "Le champ 'question' est obligatoire et ne doit pas être vide"})

    if collection is None:
        return JSONResponse(status_code=503, content={"error": "ChromaDB indisponible"})

    # 2. Recherche dans ChromaDB
    try:
        results = collection.query(
            query_texts=[question],
            n_results=6,
            include=["documents", "metadatas"]
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Erreur ChromaDB: {str(e)}"})

    contexts = []
    if results.get('documents') and results['documents'][0]:
        for i, doc in enumerate(results['documents'][0]):
            source = results['metadatas'][0][i].get('source', 'Document inconnu')
            chunk_idx = results['metadatas'][0][i].get('chunk_index', i)
            contexts.append(f"[Document: {source} - Extrait {chunk_idx}]\n{doc}")

    if not contexts:
        return {
            "response": "Je n'ai trouvé aucune information correspondante dans les documents juridiques chargés.",
            "sources": []
        }

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

        return {
            "response": answer,
            "sources": list(set(m.get('source', '') for m in results['metadatas'][0]))
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Erreur LLM: {str(e)}"})


@app.get('/health')
def health():
    if collection is None:
        return JSONResponse(status_code=503, content={"status": "chromadb_unavailable"})
    return {"status": "ok", "collection": collection_name, "count": collection.count()}


if __name__ == '__main__':
    print("=" * 50)
    print("📚 Agent 3 (RAG Légal) démarré sur http://localhost:5003")
    print("=" * 50)
    # Exécution via uvicorn sur le port 5003
    uvicorn.run(app, host="0.0.0.0", port=5003)
