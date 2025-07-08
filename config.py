import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
API_KEY = os.environ.get('TL_API_KEY')
INDEX_ID = os.environ.get('TL_INDEX_ID')

# Validate environment variables
if not API_KEY or not INDEX_ID:
    raise ValueError("Please set TL_API_KEY and TL_INDEX_ID environment variables")

# API Endpoints
API_BASE = "https://api.twelvelabs.io/v1.3"

# Video Processing Settings
MAX_CHUNK_DURATION = 7200  # 120 minutes (2 hours) for Marengo
MAX_CHUNK_SIZE = 2.0 * 1024 * 1024 * 1024  # 2.0 GB (with safety buffer)

# Upload Settings
UPLOAD_WORKERS = 1  # Sequential uploads to avoid connection issues
INDEXING_WORKERS = 6  # Parallel indexing monitoring

# File Paths
UPLOAD_FOLDER = '/tmp'
PROGRESS_FILE = 'progress.json'
VIDEO_ID_MAP = 'video_id_map.json'

# Server Settings
SERVER_PORT = 5000
DEBUG_MODE = True