# TL-Slicer

An intelligent video processing tool for TwelveLabs API that automatically handles large video files by splitting them based on duration and file size constraints.

## Overview

TL-Slicer is a web-based application that simplifies video uploads to TwelveLabs by automatically chunking large videos that exceed API limits. It provides a clean interface for uploading, processing, and verifying video indexing status through search functionality.

## Key Features

- **Smart Video Chunking**: Automatically splits videos based on duration (2 hours) and file size (2.0 GB) thresholds, both configurable in config.py.
- **No Unnecessary Processing**: Videos within limits are uploaded as-is without renaming or chunking
- **Real-time Progress Tracking**: Live upload progress with detailed status messages
- **Dual Upload Methods**: Choose between SDK or direct API uploads
- **Indexing Verification**: Built-in search functionality to verify successful video indexing
- **Clean Web Interface**: Modern, responsive UI with drag-and-drop support
- **Parallel Processing**: Efficient handling of multiple chunks with concurrent indexing monitoring

## How It Works

### Intelligent Splitting Algorithm

The application analyzes each video to determine optimal chunk duration:

1. **Calculates video bitrate** from file size and duration
2. **Determines size-based limit**: How long a chunk can be before hitting 2.1 GB
3. **Uses the smaller of**: 2-hour duration limit OR size-based limit
4. **Skips chunking entirely** if the video is already within both limits

Example scenarios:
- **High bitrate 4K video (4.2 Mbps)**: 3.5 GB, 2 hours → Split into 3 chunks
- **Normal bitrate video (1 Mbps)**: 900 MB, 3 hours → Split into 2 chunks based on duration
- **Small video**: 500 MB, 30 minutes → Uploaded as-is, no chunking

### Search Functionality

The search feature is designed specifically for **verifying indexing status**, not for production search:
- Searches across all chunks of a video automatically
- Shows timecodes relative to each chunk
- Supports both visual and audio search
- Results indicate successful indexing completion

## Requirements

### System Requirements
- Python 3.7+
- FFmpeg (for video processing)
- 2x available disk space for largest video (temporary chunk storage)

### Python Dependencies
```
flask
flask-cors
requests
twelvelabs
tqdm
python-dotenv
```

Optional:
- `requests-toolbelt` - For real-time upload progress in API mode

## Installation

1. **Clone the repository**
```bash
git clone https://github.com/bricepenven/TL_Slicer.git
cd TL-slicer
```

2. **Install Python dependencies**
```bash
pip install -r requirements.txt
```

3. **Install FFmpeg**
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt update && sudo apt install ffmpeg`
- Windows: Download from [ffmpeg.org](https://ffmpeg.org)

4. **Set up environment variables**
Create a `.env` file in the project root:
```env
TL_API_KEY=tlk_YOUR_API_KEY_HERE
TL_INDEX_ID=YOUR_INDEX_ID_HERE
```

Get your credentials from:
- API Key: https://playground.twelvelabs.io/dashboard/api-key
- Index ID: https://playground.twelvelabs.io/

5. **Run the application**
```bash
python server.py
```

6. **Open your browser**
Navigate to `http://localhost:5000`

## Usage

### Web Interface

1. **Select a video** using the file picker or drag-and-drop
2. **Choose processing method**:
   - **SDK**: Uses Twelve Labs Python SDK (recommended)
   - **API**: Direct REST API calls
3. **Monitor progress** as the video uploads and processes
4. **Wait for completion** - Status will show "Upload & Indexing successful"

### Command Line Interface (CLI)

You can also upload videos directly from the command line without the web interface:

#### Using SDK uploader:
```bash
python uploader_sdk.py /path/to/your/video.mp4
```

#### Using API uploader:
```bash
python uploader_API.py /path/to/your/video.mp4
```

Example output:
```
📽️ Video: PRAGUE_MAIN_STATION.mp4
⏱️  Duration: 02:07:58
💾 File size: 3.55 GB
📊 Bitrate: 4.0 Mbps

🔍 Checking if video needs splitting...
📊 Video stats: Duration=7678.6s, Bitrate=4.0 Mbps
🧮 Optimal chunk duration: 4329.6s (based on size/duration limits)
✅ Created chunk 1: Duration=3463.7s, Size=1.91 GB
✅ Created chunk 2: Duration=3463.7s, Size=1.27 GB
✅ Created chunk 3: Duration=751.2s, Size=0.36 GB

📦 Files to upload: 3
🚀 Starting upload...
⬆️ Uploading chunk 1/3 (1911.2 MB)...
video_id=686c7e8365c7568af194fb34
```

The CLI mode is useful for:
- Batch processing with shell scripts
- Integration into automated workflows
- Server environments without GUI
- Quick uploads without opening a browser

### Verifying Indexing

1. **Select your video** from the dropdown (shows chunk count if split)
2. **Enter a search query** (e.g., "person walking", "dog", "sunset")
3. **Choose search type**:
   - Visual: Search video content
   - Audio: Search speech/audio
4. **View results** with confidence scores and timecodes

## Configuration

All settings are centralized in `config.py`:

```python
# API Configuration
API_KEY = os.environ.get('TL_API_KEY')
INDEX_ID = os.environ.get('TL_INDEX_ID')

# Video Processing Settings
MAX_CHUNK_DURATION = 7200  # 2 hours in seconds
MAX_CHUNK_SIZE = 2.0 * 1024 * 1024 * 1024  # 2.0 GB

# Upload Settings
UPLOAD_WORKERS = 1  # Sequential uploads
INDEXING_WORKERS = 6  # Parallel indexing monitoring
```

## File Structure

```
tl-slicer/
├── server.py           # Flask web server
├── uploader_sdk.py     # SDK-based uploader
├── uploader_API.py     # API-based uploader
├── config.py           # Central configuration
├── index.html          # Web interface
├── .env               # Environment variables (create this)
├── requirements.txt    # Python dependencies
├── progress.json      # Upload progress tracking (auto-generated)
└── video_id_map.json  # Video-to-chunk mapping (auto-generated)
```

## API Limits

### TwelveLabs Platform Limits
- **Maximum video duration**: Marengo — 2 hours (7,200 seconds) per file; Pegasus — 1 hour (3,600 seconds) per file.
- **Maximum file size**: 2.1 GB per file
- **Supported formats**: MP4, AVI, MOV, MKV, and most video formats

### Application Behavior
- Videos exceeding limits are automatically split
- Each chunk gets a unique video_id in TwelveLabs
- Original filename is preserved in the UI
- Search aggregates results across all chunks

## Limitations

1. **Search is for verification only** - Not intended for production use
2. **No resume capability** - Failed uploads must restart
6. **Chunked videos** - Timecodes are relative to each chunk, not the original

## Troubleshooting

### Common Issues

1. **"Please set TL_API_KEY and TL_INDEX_ID"**
   - Ensure `.env` file exists with your credentials
   - Check environment variables are loaded

2. **FFmpeg not found**
   - Install FFmpeg and ensure it's in your PATH
   - Test with: `ffmpeg -version`

3. **Upload failures**
   - Check internet connection
   - Verify API key is valid
   - Ensure sufficient disk space

4. **No search results**
   - Wait for "Upload & Indexing successful" status
   - Indexing can take several minutes
   - Try broader search terms

### Debug Mode

Check console output for detailed information:
- Video analysis (bitrate, duration, size)
- Chunk creation progress
- Upload status for each chunk
- API responses

## Advanced Usage

### Custom Chunk Limits

Modify `config.py` to adjust limits:

```python
# For 1-hour chunks
MAX_CHUNK_DURATION = 3600

# For 1 GB chunks
MAX_CHUNK_SIZE = 1.0 * 1024 * 1024 * 1024
```

### Using Different Indexes

Update the `TL_INDEX_ID` in your `.env` file to upload to different indexes.