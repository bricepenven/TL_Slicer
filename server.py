from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import shutil
import subprocess
import threading
import uuid
import json
import time
import requests
from twelvelabs import TwelveLabs
from config import (
    API_KEY, INDEX_ID, API_BASE, 
    UPLOAD_FOLDER, PROGRESS_FILE, VIDEO_ID_MAP,
    SERVER_PORT, DEBUG_MODE
)

app = Flask(__name__)
CORS(app)

# Initialize SDK client
sdk_client = TwelveLabs(api_key=API_KEY)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Load or initialize progress
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE, 'r') as f:
        progress = json.load(f)
else:
    progress = {}

# Load or initialize video ID map
if os.path.exists(VIDEO_ID_MAP):
    with open(VIDEO_ID_MAP, 'r') as f:
        video_id_map = json.load(f)
else:
    video_id_map = {}

def save_progress():
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)

def save_video_id_map():
    with open(VIDEO_ID_MAP, 'w') as f:
        json.dump(video_id_map, f)

def run_script(filepath, filename, method='sdk'):
    try:
        # Update progress at start
        progress[filename] = {"progress": 0, "status": "Initializing..."}
        save_progress()
        
        # Choose the right script
        script_name = 'uploader_sdk.py' if method == 'sdk' else 'uploader_API.py'
        cmd = ['python3', script_name, filepath]
        
        print(f"Running command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        
        # Look for video_id(s) in stdout - can be multiple for chunked videos
        video_ids = []
        for line in result.stdout.splitlines():
            if 'video_id=' in line:
                video_id = line.split('video_id=')[1].strip()
                video_ids.append(video_id)

        if video_ids:
            # Store all video IDs associated with this original file
            video_id_map[filename] = video_ids
            save_video_id_map()
            print(f"Stored {len(video_ids)} video IDs for {filename}")
            
            # Check final progress status
            if filename not in progress or progress[filename].get('progress', 0) < 100:
                progress[filename] = {"progress": 100, "status": "Upload & Indexing successful"}
                save_progress()
        elif result.returncode == 0:
            progress[filename] = {"progress": 100, "status": "Processing completed"}
            save_progress()
        else:
            error_msg = result.stderr or 'Unknown error'
            progress[filename] = {"progress": 100, "status": f"Failed: {error_msg}"}
            save_progress()

    except Exception as e:
        print(f"❌ Script failed: {e}")
        progress[filename] = {"progress": 100, "status": f"Failed: {str(e)}"}
        save_progress()

@app.route('/')
def serve_index():
    # Serve from current directory
    return send_from_directory('.', 'index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part in request'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        filename = file.filename
        temp_path = os.path.join(UPLOAD_FOLDER, filename)
        
        # Save the file
        file.save(temp_path)
        
        # Get method (sdk or api)
        method = request.form.get('method', 'sdk')
        
        # Initialize progress
        progress[filename] = {"progress": 0, "status": "Upload received, starting processing..."}
        save_progress()
        
        # Start processing in background thread
        thread = threading.Thread(target=run_script, args=(temp_path, filename, method))
        thread.start()
        
        return jsonify({"message": "Upload started", "filename": filename})
        
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/progress/<filename>', methods=['GET'])
def get_progress(filename):
    # Reload progress file to get latest updates
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            current_progress = json.load(f)
            status = current_progress.get(filename, {"progress": 0, "status": "No progress information"})
    else:
        status = {"progress": 0, "status": "No progress information"}
    
    return jsonify(status)

@app.route('/search', methods=['POST'])
def search():
    try:
        # Get data from form
        query = request.form.get("query")
        method = request.form.get("method", "api")
        selected_file = request.form.get("selectedVideo")
        
        print(f"Search request: query='{query}', method='{method}', file='{selected_file}'")
        
        if not query or not selected_file:
            return jsonify({"error": "Missing query or file selection"}), 400

        # Load video_id_map
        with open(VIDEO_ID_MAP, "r") as f:
            video_id_map = json.load(f)

        video_ids = video_id_map.get(selected_file)
        if not video_ids:
            return jsonify({"error": f"No video_id(s) found for '{selected_file}'"}), 404

        # Ensure video_ids is a list
        if isinstance(video_ids, str):
            video_ids = [video_ids]

        print(f"Found video IDs: {video_ids}")

        # Get search options from form
        search_options_json = request.form.get("searchOptions", '["visual", "audio"]')
        search_options = json.loads(search_options_json)
        
        print(f"Search options received: {search_options}")
        print(f"Search options type: {type(search_options)}")

        aggregated_results = []

        # Direct API call using v1.3 with multipart/form-data
        headers = {
            "x-api-key": API_KEY
            # Do NOT set Content-Type - requests will set it automatically with boundary
        }
        
        # Prepare payload - start with basic fields
        payload = {
            "index_id": INDEX_ID,
            "query_text": query,
            "page_limit": "50",
            "operator": "or",
            "sort_option": "score"
        }
        
        # Create files dict with search options as form fields
        files = {
            "dummy": (None, ""),  # Force multipart encoding
        }
        
        # Add search options as multiple form fields with the same name
        # This is how arrays are sent in multipart/form-data
        for option in search_options:
            files[f"search_options"] = (None, option)
        
        print(f"\n=== API SEARCH REQUEST ===")
        print(f"URL: {API_BASE}/search")
        print(f"Basic payload: {payload}")
        print(f"Search options: {search_options}")
        
        # For multipart with multiple values of same field, we need to use tuples
        data = []
        for key, value in payload.items():
            data.append((key, value))
        
        # Add search options
        for option in search_options:
            data.append(("search_options", option))
        
        res = requests.post(
            f"{API_BASE}/search",
            headers=headers,
            data=data,  # Use list of tuples for multiple values
            files={"dummy": (None, "")}  # Still need this for multipart
        )
        
        print(f"\n=== SEARCH RESPONSE ===")
        print(f"Status: {res.status_code}")
        print(f"Response headers: {dict(res.headers)}")
        
        if res.status_code == 200:
            try:
                search_data = res.json()
                all_results = search_data.get('data', [])
                print(f"Total results from API: {len(all_results)}")
                
                # Filter for our video IDs
                for clip in all_results:
                    if clip.get('video_id') in video_ids:
                        start = clip.get('start', 0)
                        end = clip.get('end', 0)
                        confidence = clip.get('confidence', clip.get('score', 0))
                        aggregated_results.append({
                            "start": start,
                            "end": end,
                            "confidence": confidence
                        })
                
                print(f"Filtered results for our videos: {len(aggregated_results)}")
            except json.JSONDecodeError:
                print(f"Failed to parse JSON response: {res.text[:200]}")
                return jsonify({"error": "Invalid JSON response from API"}), 500
        else:
            print(f"Response body: {res.text}")
            return jsonify({"error": f"API error: {res.status_code} - {res.text}"}), res.status_code

        # Format results - just timecodes and confidence
        if aggregated_results:
            # Sort by confidence score (handle both numeric and string values)
            def get_sort_value(item):
                conf = item.get('confidence', 0)
                if isinstance(conf, str):
                    # Map string confidence to numeric values for sorting
                    confidence_map = {'high': 0.9, 'medium': 0.5, 'low': 0.1}
                    return confidence_map.get(conf.lower(), 0)
                return float(conf)
            
            aggregated_results.sort(key=get_sort_value, reverse=True)
            
            result_text = f"Found {len(aggregated_results)} matches for '{query}':\n\n"
            for i, r in enumerate(aggregated_results[:10], 1):
                start_time = float(r.get('start', 0))
                end_time = float(r.get('end', 0))
                confidence = r.get('confidence', 0)
                
                # Handle both numeric and string confidence values
                if isinstance(confidence, str):
                    conf_display = confidence.upper()
                else:
                    conf_display = f"{float(confidence):.3f}"
                
                result_text += f"{i}. {start_time:.2f}s - {end_time:.2f}s (confidence: {conf_display})\n"
                
            if len(aggregated_results) > 10:
                result_text += f"\n... and {len(aggregated_results) - 10} more results"
            
            # Prepare all results for client-side pagination
            all_results_clean = []
            for r in aggregated_results:
                all_results_clean.append({
                    "start": float(r.get('start', 0)),
                    "end": float(r.get('end', 0)),
                    "confidence": r.get('confidence', 0)
                })
            
            return jsonify({"result": result_text, "allResults": all_results_clean})
        else:
            result_text = f"No matches found for '{query}' in the selected videos"
            return jsonify({"result": result_text})
        
    except Exception as e:
        print(f"Search error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/videos', methods=['GET'])
def get_videos():
    """Get list of uploaded videos"""
    try:
        videos = []
        for filename, ids in video_id_map.items():
            video_count = len(ids) if isinstance(ids, list) else 1
            videos.append({
                "filename": filename,
                "video_ids": ids if isinstance(ids, list) else [ids],
                "chunk_count": video_count
            })
        return jsonify({"videos": videos})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=DEBUG_MODE, port=SERVER_PORT)