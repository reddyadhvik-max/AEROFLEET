import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database URL
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(os.path.dirname(__file__), 'aerofleet.db')}")

# Connect to database
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    DATABASE_URL, connect_args=connect_args, pool_size=10, max_overflow=20
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Driver(Base):
    __tablename__ = "drivers"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    assigned_truck = Column(String, nullable=True)
    encoding_path = Column(String, nullable=False)

class Alert(Base):
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    type = Column(String, index=True)
    severity = Column(String)
    description = Column(Text)
    truck_id = Column(String, index=True)
    driver_id = Column(String, index=True, nullable=True)
    driver_name = Column(String, nullable=True)
    time = Column(String)
    timestamp = Column(Float)
    clip_path = Column(String, nullable=True)

class User(Base):
    __tablename__ = "users"
    
    username = Column(String, primary_key=True, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)

class Truck(Base):
    __tablename__ = "trucks"
    
    id = Column(String, primary_key=True, index=True)
    status = Column(String, default="active")

class JourneyRecord(Base):
    __tablename__ = "journeys"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    truck_id = Column(String, index=True)
    driver_id = Column(String, index=True)
    start_time = Column(Float)
    end_time = Column(Float)
    distance_km = Column(Float)
    fuel_consumed = Column(Float)
    alerts_count = Column(Integer)

# Initialize database
def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Seed default trucks if none exist
    if db.query(Truck).count() == 0:
        for t_id in ["TRK-001", "TRK-002", "TRK-003"]:
            db.add(Truck(id=t_id))
        db.commit()
    db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
