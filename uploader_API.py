import os
import sys
import math
import time
import subprocess
import requests
import json
from tqdm import tqdm
from typing import List, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    API_KEY, INDEX_ID, API_BASE, MAX_CHUNK_DURATION, MAX_CHUNK_SIZE,
    UPLOAD_WORKERS, INDEXING_WORKERS, PROGRESS_FILE
)

# Progress tracking
progress_data = {}

def load_progress():
    global progress_data
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            progress_data = json.load(f)

def save_progress():
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress_data, f)

def get_video_info(path: str) -> Tuple[float, int]:
    """Get video duration and bitrate"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,bit_rate", "-of", "json", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    data = json.loads(result.stdout)
    duration = float(data['format']['duration'])
    
    # Get bitrate, fallback to file size calculation if not available
    bitrate = data['format'].get('bit_rate')
    if bitrate:
        bitrate = int(bitrate)
    else:
        # Estimate bitrate from file size
        file_size = os.path.getsize(path)
        bitrate = int((file_size * 8) / duration)  # bits per second
    
    return duration, bitrate

def should_chunk_video(duration: float, bitrate: int, file_size: int) -> Tuple[bool, float]:
    """Determine if video needs chunking"""
    # Calculate size-based duration limit
    size_based_duration = (MAX_CHUNK_SIZE * 8) / bitrate
    optimal_chunk_duration = min(MAX_CHUNK_DURATION, size_based_duration)
    
    # If video duration is less than optimal chunk duration, no chunking needed
    return duration > optimal_chunk_duration, optimal_chunk_duration

def chunk_video_smart(path: str) -> Tuple[List[str], bool]:
    """Split video respecting both duration and size constraints"""
    total_duration, bitrate = get_video_info(path)
    original_filename = Path(path).stem
    file_size = os.path.getsize(path)
    
    needs_chunking, optimal_chunk_duration = should_chunk_video(total_duration, bitrate, file_size)
    
    if not needs_chunking:
        print(f"✅ Video is within limits (duration: {total_duration:.1f}s, size: {file_size/(1024*1024*1024):.2f}GB)")
        print(f"📤 No chunking needed - uploading as single file")
        return [path], False  # Return original file, no chunking done
    
    # If we reach here, we need to chunk the video
    print(f"📊 Video stats: Duration={total_duration:.1f}s, Bitrate={bitrate/1_000_000:.1f} Mbps")
    print(f"🧮 Optimal chunk duration: {optimal_chunk_duration:.1f}s (based on size/duration limits)")
    
    chunks = []
    start = 0.0
    
    # Calculate chunks based on optimal duration
    while start < total_duration:
        length = min(optimal_chunk_duration, total_duration - start)
        chunks.append((start, length))
        start += length
    
    chunk_paths = []
    for i, (start, length) in enumerate(chunks):
        out_path = f"/tmp/{original_filename}_chunk_{i + 1:03d}.mp4"
        
        # Create chunk
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(length),
            "-i", path,
            "-c", "copy",
            "-movflags", "faststart",
            out_path
        ]
        
        print(f"🎬 Creating chunk {i + 1}/{len(chunks)} (Duration: {length:.1f}s)")
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Verify chunk size
        chunk_size = os.path.getsize(out_path)
        chunk_size_gb = chunk_size / (1024 * 1024 * 1024)
        
        if chunk_size > MAX_CHUNK_SIZE:
            print(f"⚠️  Chunk {i + 1} exceeds size limit ({chunk_size_gb:.2f} GB)")
            # In production, you might want to re-split this chunk
            # For now, we'll warn but continue
        else:
            print(f"✅ Chunk {i + 1}: Size={chunk_size_gb:.2f} GB")
        
        chunk_paths.append(out_path)
    
    return chunk_paths, True  # Return paths and indicate chunking was done

def upload_chunk_with_progress(path: str, chunk_index: int, total_chunks: int, original_filename: str, is_single_file: bool = False) -> str:
    """Upload chunk with progress tracking"""
    # Calculate progress
    chunk_percent = 80 / total_chunks  # 80% for uploads
    base_progress = 20 + (chunk_index * chunk_percent)
    
    file_size = os.path.getsize(path)
    file_size_mb = file_size / (1024 * 1024)
    
    # Update progress with appropriate message
    if is_single_file:
        status_msg = f"Uploading video ({file_size_mb:.1f} MB)..."
    else:
        status_msg = f"Uploading chunk {chunk_index + 1}/{total_chunks} ({file_size_mb:.1f} MB)..."
    
    progress_data[original_filename] = {
        "progress": int(base_progress),
        "status": status_msg
    }
    save_progress()
    
    headers = {"x-api-key": API_KEY}
    
    # Use requests-toolbelt for upload progress if available
    try:
        from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
        
        def upload_callback(monitor):
            progress = (monitor.bytes_read / monitor.len) * 100
            chunk_progress = base_progress + (progress * chunk_percent / 100)
            progress_data[original_filename] = {
                "progress": int(chunk_progress),
                "status": f"Uploading {'video' if is_single_file else f'chunk {chunk_index + 1}/{total_chunks}'}... {int(progress)}%"
            }
            save_progress()
        
        with open(path, "rb") as f:
            encoder = MultipartEncoder(
                fields={
                    'video_file': (os.path.basename(path), f, 'video/mp4'),
                    'index_id': INDEX_ID,
                    'language': 'en'
                }
            )
            monitor = MultipartEncoderMonitor(encoder, upload_callback)
            
            headers['Content-Type'] = monitor.content_type
            res = requests.post(f"{API_BASE}/tasks", headers=headers, data=monitor)
            
    except ImportError:
        # Fallback without progress monitoring
        print("ℹ️  Install requests-toolbelt for upload progress: pip install requests-toolbelt")
        with open(path, "rb") as f:
            files = {"video_file": f}
            data = {"index_id": INDEX_ID, "language": "en"}
            res = requests.post(f"{API_BASE}/tasks", headers=headers, files=files, data=data)

    if res.status_code not in [200, 201]:
        raise Exception(f"Upload failed: {res.status_code} - {res.text}")

    task_data = res.json()
    video_id = task_data.get("_id") or task_data.get("id")
    
    # Update progress after upload
    if is_single_file:
        status_msg = f"Upload complete, processing..."
    else:
        status_msg = f"Chunk {chunk_index + 1}/{total_chunks} uploaded, processing..."
    
    progress_data[original_filename] = {
        "progress": int(base_progress + chunk_percent),
        "status": status_msg
    }
    save_progress()
    
    return video_id

def wait_for_indexing(video_id: str, chunk_path: str, is_temp_file: bool):
    headers = {"x-api-key": API_KEY}
    url = f"{API_BASE}/tasks/{video_id}"

    while True:
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"⚠️  Error checking status for {video_id}: {res.text}")
            break

        status = res.json().get("status")
        if status in ["ready", "failed"]:
            print(f"ℹ️  Indexing done: {video_id} → {status}")
            break

        time.sleep(5)

    # Only remove temp files (chunks), not the original file
    if is_temp_file and os.path.exists(chunk_path):
        os.remove(chunk_path)
        print(f"🧹 Cleaned up: {chunk_path}")

def upload_all_sequential(paths: List[str], original_filename: str, is_chunked: bool):
    """Upload chunks sequentially with progress tracking"""
    video_ids = []
    
    with ThreadPoolExecutor(max_workers=INDEXING_WORKERS) as index_exec:
        for i, path in enumerate(paths):
            try:
                is_single_file = len(paths) == 1 and not is_chunked
                
                if is_single_file:
                    print(f"⬆️  Uploading video...")
                else:
                    print(f"⬆️  Uploading chunk {i + 1}/{len(paths)}...")
                    
                video_id = upload_chunk_with_progress(path, i, len(paths), original_filename, is_single_file)
                print(f"✅ Upload accepted: {video_id}")
                print(f"video_id={video_id}")
                
                video_ids.append(video_id)
                index_exec.submit(wait_for_indexing, video_id, path, is_chunked)
                
                # Small delay between chunks
                if i < len(paths) - 1:
                    time.sleep(2)
                    
            except Exception as e:
                print(f"❌ Upload failed for {path}: {e}")
                if os.path.exists(path) and is_chunked:
                    os.remove(path)
    
    # Update final status
    if len(video_ids) == len(paths):
        progress_data[original_filename] = {"progress": 100, "status": "Upload & Indexing successful"}
    else:
        progress_data[original_filename] = {
            "progress": 100,
            "status": f"Partial success: {len(video_ids)}/{len(paths)} {'chunks' if is_chunked else 'file'} uploaded"
        }
    save_progress()
    
    # Print all video IDs for the server to capture
    for vid in video_ids:
        print(f"video_id={vid}")

def main(input_path: str):
    if not os.path.isfile(input_path):
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    original_filename = os.path.basename(input_path)
    
    # Load existing progress
    load_progress()
    
    # Initialize progress
    progress_data[original_filename] = {"progress": 0, "status": "Starting..."}
    save_progress()
    
    print(f"📁 Processing: {input_path}")
    
    # Get video info
    duration, bitrate = get_video_info(input_path)
    file_size_gb = os.path.getsize(input_path) / (1024 * 1024 * 1024)
    
    h = int(duration // 3600)
    m = int((duration % 3600) // 60)
    s = int(duration % 60)
    
    print(f"📽️ Video: {original_filename}")
    print(f"⏱️  Duration: {h:02d}:{m:02d}:{s:02d}")
    print(f"💾 File size: {file_size_gb:.2f} GB")
    
    progress_data[original_filename] = {"progress": 5, "status": "Analyzing video..."}
    save_progress()
    
    print("\n🔍 Checking if video needs splitting...")
    chunks, is_chunked = chunk_video_smart(input_path)
    
    # Update progress message based on chunking
    if is_chunked:
        progress_data[original_filename] = {"progress": 20, "status": f"Created {len(chunks)} chunks, starting upload..."}
    else:
        progress_data[original_filename] = {"progress": 20, "status": "Starting upload (no chunking needed)..."}
    save_progress()
    
    print(f"\n📦 Files to upload: {len(chunks)}")
    upload_all_sequential(chunks, original_filename, is_chunked)
    print("🎉 Uploads and indexing triggered.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python uploader_API.py /path/to/video.mp4")
        sys.exit(1)

    main(sys.argv[1])