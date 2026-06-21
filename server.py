from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp, os, uuid, threading, time

app = Flask(__name__)
CORS(app, origins="*")

DOWNLOAD_FOLDER = "/tmp/downloads/"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
download_jobs = {}

def format_bytes(size):
    if not size: return "Unknown"
    for unit in ['B','KB','MB','GB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"

@app.route('/')
def home():
    return jsonify({"status": "ok", "app": "SnapLoad Pro"})

@app.route('/api/info', methods=['POST'])
def get_info():
    try:
        url = request.json.get('url', '')
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        platform = 'youtube'
        if 'instagram' in url: platform = 'instagram'
        elif 'tiktok' in url: platform = 'tiktok'
        elif 'facebook' in url: platform = 'facebook'
        elif 'twitter' in url or 'x.com' in url: platform = 'twitter'
        video_formats = []
        audio_formats = []
        for f in info.get('formats', []):
            size = f.get('filesize') or f.get('filesize_approx', 0)
            if f.get('vcodec','none') != 'none' and f.get('height'):
                video_formats.append({
                    "format_id": f['format_id'],
                    "quality": f"{f['height']}p",
                    "ext": f.get('ext','mp4'),
                    "filesize_str": format_bytes(size),
                    "type": "video"
                })
            elif f.get('acodec','none') != 'none' and f.get('vcodec') == 'none':
                audio_formats.append({
                    "format_id": f['format_id'],
                    "quality": f"{f.get('abr',128):.0f}kbps",
                    "ext": f.get('ext','m4a'),
                    "filesize_str": format_bytes(size),
                    "type": "audio"
                })
        seen = set()
        unique_video = []
        for f in sorted(video_formats, key=lambda x: int(x['quality'].replace('p','')), reverse=True):
            if f['quality'] not in seen:
                seen.add(f['quality'])
                unique_video.append(f)
        duration = info.get('duration', 0)
        mins = duration // 60
        secs = duration % 60
        return jsonify({
            "success": True,
            "title": info.get('title','Unknown'),
            "thumbnail": info.get('thumbnail',''),
            "duration_string": f"{mins}:{secs:02d}",
            "channel": info.get('uploader','Unknown'),
            "views": f"{info.get('view_count',0):,}",
            "platform": platform,
            "video_formats": unique_video[:5],
            "audio_formats": audio_formats[:3]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/download/start', methods=['POST'])
def start_download():
    try:
        url = request.json.get('url')
        format_id = request.json.get('format_id')
        title = request.json.get('title','video')
        job_id = str(uuid.uuid4())[:8]
        download_jobs[job_id] = {
            "status": "starting",
            "percent": 0,
            "speed": "Connecting...",
            "eta": "Calculating...",
            "filename": "",
            "filepath": "",
            "filesize": ""
        }
        def progress_hook(d):
            if d['status'] == 'downloading':
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
                percent = downloaded / total * 100
                speed = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0
                download_jobs[job_id].update({
                    "status": "downloading",
                    "percent": round(percent, 1),
                    "speed": format_bytes(speed) + "/s",
                    "eta": f"{eta//60}:{eta%60:02d}",
                    "filesize": format_bytes(total)
                })
            elif d['status'] == 'finished':
                download_jobs[job_id].update({
                    "status": "processing",
                    "percent": 99,
                    "filepath": d['filename']
                })
        def do_download():
            try:
                safe = ''.join(c for c in title if c.isalnum() or c in ' -_')[:40]
                out = DOWNLOAD_FOLDER + job_id + "_" + safe + ".%(ext)s"
                opts = {
                    'format': format_id,
                    'outtmpl': out,
                    'progress_hooks': [progress_hook],
                    'merge_output_format': 'mp4',
                    'quiet': True
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                for f in os.listdir(DOWNLOAD_FOLDER):
                    if f.startswith(job_id):
                        fp = DOWNLOAD_FOLDER + f
                        download_jobs[job_id].update({
                            "status": "complete",
                            "percent": 100,
                            "speed": "Done!",
                            "eta": "Ready",
                            "filename": f,
                            "filepath": fp,
                            "filesize": format_bytes(os.path.getsize(fp))
                        })
                        break
            except Exception as e:
                download_jobs[job_id].update({
                    "status": "error",
                    "error": str(e)
                })
        t = threading.Thread(target=do_download)
        t.daemon = True
        t.start()
        return jsonify({"success": True, "job_id": job_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/progress/<job_id>')
def get_progress(job_id):
    job = download_jobs.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"})
    return jsonify({"success": True, **job})

@app.route('/api/file/<job_id>')
def get_file(job_id):
    job = download_jobs.get(job_id)
    if not job or job['status'] != 'complete':
        return jsonify({"error": "Not ready"}), 404
    return send_file(
        job['filepath'],
        as_attachment=True,
        download_name=job['filename']
    )

def cleanup():
    while True:
        time.sleep(1800)
        now = time.time()
        for f in os.listdir(DOWNLOAD_FOLDER):
            fp = DOWNLOAD_FOLDER + f
            if now - os.path.getmtime(fp) > 3600:
                os.remove(fp)

t = threading.Thread(target=cleanup)
t.daemon = True
t.start()

port = int(os.environ.get('PORT', 5000))
app.run(host='0.0.0.0', port=port, debug=False)
