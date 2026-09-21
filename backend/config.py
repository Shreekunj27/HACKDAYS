import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WATT_TIME_API_KEY = os.getenv("WATT_TIME_API_KEY")