import os
import subprocess
import tempfile
import shutil
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from twelvelabs import TwelveLabs
from twelvelabs.models.task import Task
from config import (
    API_KEY, INDEX_ID, MAX_CHUNK_DURATION, MAX_CHUNK_SIZE,
    UPLOAD_WORKERS, INDEXING_WORKERS, PROGRESS_FILE
)

client = TwelveLabs(api_key=API_KEY)

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

def get_video_info(filepath):
    """Get video duration and bitrate"""
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,bit_rate",
        "-of", "json",
        filepath
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    import json
    data = json.loads(result.stdout)
    duration = float(data['format']['duration'])
    
    # Get bitrate, fallback to file size calculation if not available
    bitrate = data['format'].get('bit_rate')
    if bitrate:
        bitrate = int(bitrate)
    else:
        # Estimate bitrate from file size
        file_size = os.path.getsize(filepath)
        bitrate = int((file_size * 8) / duration)  # bits per second
    
    return duration, bitrate

def should_chunk_video(duration, bitrate, file_size):
    """Determine if video needs chunking"""
    # Calculate size-based duration limit
    size_based_duration = (MAX_CHUNK_SIZE * 8) / bitrate
    optimal_chunk_duration = min(MAX_CHUNK_DURATION, size_based_duration)
    
    # If video duration is less than optimal chunk duration, no chunking needed
    return duration > optimal_chunk_duration, optimal_chunk_duration

def split_video_smart(input_path):
    """Split video respecting both duration and size constraints"""
    total_duration, bitrate = get_video_info(input_path)
    original_filename = Path(input_path).stem
    file_size = os.path.getsize(input_path)
    
    needs_chunking, optimal_chunk_duration = should_chunk_video(total_duration, bitrate, file_size)
    
    if not needs_chunking:
        print(f"✅ Video is within limits (duration: {total_duration:.1f}s, size: {file_size/(1024*1024*1024):.2f}GB)")
        print(f"📤 No chunking needed - uploading as single file")
        return [input_path], None  # Return original file, no temp directory
    
    # If we reach here, we need to chunk the video
    output_dir = tempfile.mkdtemp(prefix="tl_chunks_")
    chunk_paths = []
    
    print(f"📊 Video stats: Duration={total_duration:.1f}s, Bitrate={bitrate/1_000_000:.1f} Mbps")
    print(f"🧮 Optimal chunk duration: {optimal_chunk_duration:.1f}s (based on size/duration limits)")
    
    start = 0
    index = 1
    
    while start < total_duration:
        chunk_duration = min(optimal_chunk_duration, total_duration - start)
        output_path = os.path.join(output_dir, f"{original_filename}_chunk_{index:03d}.mp4")
        
        # Create chunk
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-ss", str(start), "-t", str(chunk_duration),
            "-c", "copy", "-movflags", "faststart",
            output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Verify chunk size
        chunk_size = os.path.getsize(output_path)
        chunk_size_gb = chunk_size / (1024 * 1024 * 1024)
        
        if chunk_size > MAX_CHUNK_SIZE:
            print(f"⚠️  Chunk {index} exceeds size limit ({chunk_size_gb:.2f} GB), re-splitting...")
            os.remove(output_path)
            
            # Reduce chunk duration by 20% and retry
            optimal_chunk_duration *= 0.8
            continue
        
        print(f"✅ Created chunk {index}: Duration={chunk_duration:.1f}s, Size={chunk_size_gb:.2f} GB")
        chunk_paths.append(output_path)
        start += chunk_duration
        index += 1
    
    return chunk_paths, output_dir

def wait_for_indexing(task_id: str, chunk_path: str):
    """Monitor indexing status in background"""
    print(f"👁️  Monitoring indexing for {task_id}")
    
    max_checks = 60  # Check for up to 5 minutes
    for i in range(max_checks):
        try:
            task = client.task.retrieve(task_id)
            status = task.status if hasattr(task, 'status') else 'unknown'
            
            if status in ['ready', 'completed']:
                print(f"✅ Indexing complete: {task_id}")
                break
            elif status == 'failed':
                print(f"❌ Indexing failed: {task_id}")
                break
            
            time.sleep(5)  # Check every 5 seconds
        except Exception as e:
            print(f"⚠️  Error checking status for {task_id}: {e}")
            break
    
    # Clean up the chunk file after indexing ONLY if it's a temp file
    if chunk_path and 'tl_chunks_' in chunk_path and os.path.exists(chunk_path):
        os.remove(chunk_path)
        print(f"🧹 Cleaned up: {chunk_path}")

def upload_file_with_progress(path, chunk_index, total_chunks, original_filename, index_executor, is_single_file=False, max_retries=3):
    print(f"⬆️ Uploading: {path}")
    
    # Calculate base progress for this chunk
    chunk_size_percent = 80 / total_chunks  # 80% for uploads, 20% for other operations
    base_progress = 20 + (chunk_index * chunk_size_percent)  # Start at 20% after analysis
    
    # Get file size for progress tracking
    file_size = os.path.getsize(path)
    file_size_mb = file_size / (1024 * 1024)
    
    for attempt in range(max_retries):
        try:
            # Update progress at start of upload
            if is_single_file:
                status_msg = f"Uploading video ({file_size_mb:.1f} MB)..."
                print(f"📤 Uploading single file")
            else:
                status_msg = f"Uploading chunk {chunk_index + 1}/{total_chunks} ({file_size_mb:.1f} MB)..."
                print(f"📤 Starting upload of chunk {chunk_index + 1}/{total_chunks}")
            
            progress_data[original_filename] = {
                "progress": int(base_progress),
                "status": status_msg
            }
            save_progress()
            
            # Upload the file
            task = client.task.create(index_id=INDEX_ID, file=path)
            
            task_id = task.id if hasattr(task, 'id') else task._id
            print(f"video_id={task_id}")
            
            # Update progress after successful upload
            progress_after = base_progress + chunk_size_percent
            if is_single_file:
                status_msg = f"Upload complete, processing..."
            else:
                status_msg = f"Chunk {chunk_index + 1}/{total_chunks} uploaded, processing..."
            
            progress_data[original_filename] = {
                "progress": int(progress_after),
                "status": status_msg
            }
            save_progress()
            
            # Submit indexing monitoring to background thread pool
            index_executor.submit(wait_for_indexing, task_id, path)
            
            return path, task
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                print(f"⚠️  Upload failed for {path}: {e}. Retrying in {wait_time}s...")
                
                progress_data[original_filename] = {
                    "progress": int(base_progress),
                    "status": f"Retry {attempt + 1}/{max_retries} for {'video' if is_single_file else f'chunk {chunk_index + 1}'}..."
                }
                save_progress()
                
                import time
                time.sleep(wait_time)
            else:
                print(f"🔥 Upload error for {path} after {max_retries} attempts: {e}")
                return path, None
    
    return path, None

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python uploader_sdk.py <video_path>")
        return

    video_path = sys.argv[1]
    original_filename = os.path.basename(video_path)
    
    # Load existing progress
    load_progress()
    
    # Initialize progress for this file
    progress_data[original_filename] = {"progress": 0, "status": "Starting..."}
    save_progress()
    
    duration, bitrate = get_video_info(video_path)
    h = int(duration // 3600)
    m = int((duration % 3600) // 60)
    s = int(duration % 60)
    
    file_size_gb = os.path.getsize(video_path) / (1024 * 1024 * 1024)
    
    print(f"📽️ Video: {original_filename}")
    print(f"⏱️  Duration: {h:02d}:{m:02d}:{s:02d}")
    print(f"💾 File size: {file_size_gb:.2f} GB")
    print(f"📊 Bitrate: {bitrate/1_000_000:.1f} Mbps")
    
    progress_data[original_filename] = {"progress": 5, "status": "Analyzing video..."}
    save_progress()
    
    print("\n🔍 Checking if video needs splitting...")
    chunks, temp_dir = split_video_smart(video_path)
    
    # Update progress message based on chunking
    if len(chunks) == 1 and not temp_dir:
        progress_data[original_filename] = {"progress": 20, "status": "Starting upload (no chunking needed)..."}
    else:
        progress_data[original_filename] = {"progress": 20, "status": f"Created {len(chunks)} chunks, starting upload..."}
    save_progress()
    
    print(f"\n📦 Files to upload: {len(chunks)}")
    print(f"🚀 Starting upload...")
    
    # Create thread pool for indexing monitoring
    with ThreadPoolExecutor(max_workers=INDEXING_WORKERS) as index_executor:
        # Sequential upload with progress tracking
        successful_uploads = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                import time
                time.sleep(2)  # 2 second delay between chunks
            
            # Determine if this is a single file upload
            is_single_file = len(chunks) == 1 and not temp_dir
            path, task = upload_file_with_progress(chunk, i, len(chunks), original_filename, index_executor, is_single_file)
            
            if task:
                successful_uploads.append(task)
    
    # Final status
    if len(successful_uploads) == len(chunks):
        progress_data[original_filename] = {"progress": 100, "status": "Upload & Indexing successful"}
    else:
        progress_data[original_filename] = {
            "progress": 100, 
            "status": f"Partial success: {len(successful_uploads)}/{len(chunks)} chunks uploaded"
        }
    save_progress()

    print(f"\n🧹 All done. Uploaded {len(successful_uploads)}/{len(chunks)} {'chunks' if temp_dir else 'file'}.")
    
    # Clean up temp directory if it exists
    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            print("🧹 Cleaned up temporary files.")
        except:
            pass  # Directory might not be empty if indexing is still running

if __name__ == "__main__":
    main()