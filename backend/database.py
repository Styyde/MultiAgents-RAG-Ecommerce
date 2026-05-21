from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine('sqlite:///ecommerce.db', connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)