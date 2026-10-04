import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Chemin absolu : la base est trouvée quel que soit le répertoire de lancement
# (ex. `python backend/agent_sql.py` depuis la racine du projet).
# DATABASE_URL permet de pointer vers une autre base (tests, copie de démo...).
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ecommerce.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
Session = sessionmaker(bind=engine)
