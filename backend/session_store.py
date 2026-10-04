# backend/session_store.py
"""
Mémoire conversationnelle par session, détenue par la gateway.

Les agents restent sans état : à chaque appel, la gateway leur transmet l'historique
récent de la session. Une question de suivi (« et une alternative à celui-là ? »)
fonctionne donc même si elle est routée vers un autre agent que la question initiale.

Implémentation en mémoire, valable pour UN processus gateway. Pour plusieurs réplicas,
remplacer par un store partagé (ex. Redis : une liste par session + EXPIRE) en gardant
la même interface publique.
"""
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass
class _Session:
    tours: deque
    dernier_agent: Optional[str] = None
    derniere_activite: float = field(default=0.0)


class ConversationStore:
    def __init__(
        self,
        max_tours: int = 6,
        ttl_secondes: float = 1800,
        max_sessions: int = 1000,
        max_caracteres: int = 2000,
        horloge: Callable[[], float] = time.monotonic,
    ):
        self.max_tours = max_tours
        self.ttl_secondes = ttl_secondes
        self.max_sessions = max_sessions
        self.max_caracteres = max_caracteres
        self._horloge = horloge
        # Ordre = activité la plus ancienne en tête (LRU) : la purge s'arrête au 1er actif.
        self._sessions: "OrderedDict[str, _Session]" = OrderedDict()
        self._verrou = threading.Lock()

    @staticmethod
    def normaliser_id(session_id: object) -> str:
        """Renvoie l'identifiant s'il est bien formé, sinon en génère un nouveau."""
        if isinstance(session_id, str) and SESSION_ID_PATTERN.match(session_id):
            return session_id
        return uuid.uuid4().hex

    def _session_active(self, session_id: str) -> Optional[_Session]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._horloge() - session.derniere_activite > self.ttl_secondes:
            del self._sessions[session_id]
            return None
        return session

    def _purger(self) -> None:
        maintenant = self._horloge()
        while self._sessions:
            session_id, session = next(iter(self._sessions.items()))
            expiree = maintenant - session.derniere_activite > self.ttl_secondes
            if not expiree and len(self._sessions) <= self.max_sessions:
                break
            del self._sessions[session_id]

    def historique(self, session_id: str) -> list:
        """[{"role": "user"|"assistant", "content": str}, ...] du plus ancien au plus récent."""
        with self._verrou:
            session = self._session_active(session_id)
            if session is None:
                return []
            messages = []
            for question, reponse in session.tours:
                messages.append({"role": "user", "content": question})
                messages.append({"role": "assistant", "content": reponse})
            return messages

    def dernier_agent(self, session_id: str) -> Optional[str]:
        with self._verrou:
            session = self._session_active(session_id)
            return session.dernier_agent if session else None

    def enregistrer_tour(self, session_id: str, question: str, reponse: str, agent: str) -> None:
        with self._verrou:
            session = self._session_active(session_id)
            if session is None:
                session = _Session(tours=deque(maxlen=self.max_tours))
                self._sessions[session_id] = session
            session.tours.append((question[: self.max_caracteres], reponse[: self.max_caracteres]))
            session.dernier_agent = agent
            session.derniere_activite = self._horloge()
            self._sessions.move_to_end(session_id)
            self._purger()

    def effacer(self, session_id: str) -> bool:
        with self._verrou:
            return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        with self._verrou:
            return len(self._sessions)
