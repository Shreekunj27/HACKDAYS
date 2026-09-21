from fastapi import FastAPI
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

# --- Configuration ---
# Demo Safety Net Toggle
USE_MOCK_DATA = True

# --- Database Setup ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./agent_memory.db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AgentState(Base):
    __tablename__ = "AgentState"
    id = Column(Integer, primary_key=True, index=True)
    memory = Column(String, index=True)

Base.metadata.create_all(bind=engine)

# --- FastAPI App ---
app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Backend is running and database is connected!"}

@app.get("/api/carbon")
def get_carbon_data():
    """Fetches Carbon data, using mock data if USE_MOCK_DATA is True."""
    if USE_MOCK_DATA:
        return {"data": "Iceland: 90% Renewable", "source": "mock"}
    else:
        # TODO: Add real WattTime/ElectricityMaps API call here
        return {"error": "Live API not implemented yet"}

@app.get("/api/llm")
def get_llm_response(prompt: str = "Hello"):
    """Fetches LLM response, using mock data if USE_MOCK_DATA is True."""
    if USE_MOCK_DATA:
        return {"response": f"Mock AI says: This is a safe fallback response for '{prompt}'.", "source": "mock"}
    else:
        # TODO: Add real OpenAI API call here
        return {"error": "Live API not implemented yet"}




    # --- STEP 4: DEMO SAFETY NET ---

# Toggle this to False when you want to use the real APIs
USE_MOCK_DATA = True

@app.get("/carbon-data")
def get_carbon_data():
    if USE_MOCK_DATA:
        return {"country": "Iceland", "renewable_percentage": "90%", "status": "Mock Data"}
    else:
        # TODO: Add real WattTime API call here later
        return {"error": "Real Carbon API not implemented yet"}

@app.get("/llm-response")
def get_llm_response(prompt: str = ""):
    if USE_MOCK_DATA:
        return {"response": "This is a mock LLM response. The grid is currently clean in Iceland.", "status": "Mock Data"}
    else:
        # TODO: Add real OpenAI API call here later
        return {"error": "Real LLM API not implemented yet"}