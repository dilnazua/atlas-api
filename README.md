# Atlas Photogrammetry API

A production-ready photogrammetry service that processes photo sets from Unity and returns 3D models (GLB/GLTF) with textures.

## Architecture Overview

```
┌─────────────┐
│   Unity     │  ← Client (iOS/Android app)
│   Client    │
└──────┬──────┘
       │ HTTP/REST
       │
       ▼
┌─────────────┐
│   NGINX     │  ← Reverse proxy (TLS, rate limiting)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   FastAPI   │  ← REST API server
│   (Uvicorn) │
└──────┬──────┘
       │
       ├──────────┐
       │          │
       ▼          ▼
┌───────────┐  ┌───────────┐
│   Redis   │  │  Celery   │  ← Async task queue
└───────────┘  │  Worker   │
       │       └─────┬─────┘
       │             │
       └─────────────┘
                 │
                 ▼
       ┌──────────────────┐
       │ Photogrammetry    │
       │ Pipeline:         │
       │ 1. SAM Seg.       │
       │ 2. OpenMVG/Sparse  │
       │ 3. OpenMVS/Dense  │
       │ 4. Post-process   │
       │ 5. GLB Export     │
       └──────────────────┘
```

## How It Works

### Request Flow

1. **Unity client uploads photos** → `POST /api/v1/jobs`
   - Client sends array of images + processing options
   - FastAPI validates, saves images to disk in a workspace
   - Job status set to "queued"
   - Celery task enqueued via Redis

2. **Celery worker processes job** (asynchronously)
   - **Segmentation**: SAM removes backgrounds from each image
   - **Sparse reconstruction**: OpenMVG (Docker) extracts features, matches, estimates camera poses
   - **Dense reconstruction**: OpenMVS creates dense point cloud and mesh
   - **Post-processing**: Decimation, UV unwrapping, texture atlas generation
   - **Export**: Convert to GLB/GLTF format
   - Status updated throughout (progress: 0% → 100%)

3. **Client polls status** → `GET /api/v1/jobs/{id}`
   - Returns current stage, progress percentage, any errors
   - Unity shows progress bar to user

4. **Download result** → `GET /api/v1/jobs/{id}/artifact`
   - Returns GLB file when status is "completed"
   - Unity can directly import GLB file

## Components Explained

### FastAPI (REST API)

FastAPI is a modern Python web framework that:
- Handles HTTP requests and responses
- Validates input data automatically
- Provides automatic OpenAPI documentation at `/docs`
- Handles file uploads efficiently
- Runs with Uvicorn (ASGI server) for high performance

**Key endpoints:**
- `POST /api/v1/jobs` - Create a new photogrammetry job
- `GET /api/v1/jobs/{id}` - Get job status
- `GET /api/v1/jobs/{id}/artifact` - Download GLB result
- `DELETE /api/v1/jobs/{id}` - Delete job

### Redis (Message Broker)

Redis is used by Celery for:
- **Message broker**: Queue of tasks waiting to be processed
- **Result backend**: Store task results temporarily
- **Distributed coordination**: Multiple workers can pull from the same queue

Why not use a database? Databases are for permanent data. Redis is optimized for fast, transient message passing. Tasks are deleted after processing, so they don't need persistence.

### Celery (Task Queue)

Celery is a distributed task queue that:
- Runs long-running tasks asynchronously (photogrammetry takes minutes/hours)
- Keeps the FastAPI server responsive (doesn't block on uploads)
- Can scale horizontally (add more workers)
- Provides task status tracking
- Handles retries on failure

**Why use Celery?**
If we didn't use Celery, the FastAPI server would be tied up for hours processing each job. With Celery, the server responds immediately ("job queued"), and workers handle the actual processing in the background.

### Pipeline Stages

#### 1. SAM Segmentation (`worker/pipeline/sam_mask.py`)
- Uses Meta's Segment Anything Model to detect and isolate the main subject
- Generates binary masks (keep/remove) for each image
- Improves photogrammetry results by focusing on the subject

#### 2. Photogrammetry - Sparse (`worker/pipeline/openmvg.py`)
- Uses OpenMVG Docker container to perform sparse reconstruction
- **Feature detection**: Find distinctive points in each image (corners, edges)
- **Feature matching**: Match features across images to find correspondences
- **Sparse reconstruction**: Estimate 3D positions of matched features + camera poses
- Output: OpenMVG sfm_data.json with sparse point cloud (few thousand points) and camera positions

#### 3. Photogrammetry - Dense (`worker/pipeline/openmvs.py`)
- Uses OpenMVS Docker container for dense reconstruction
- **Format conversion**: Converts OpenMVG sfm_data.json to OpenMVS format
- **Dense matching**: Fill in gaps with dense point cloud (millions of points)
- **Mesh generation**: Connect points into a triangle mesh
- **Mesh refinement**: Smooth, repair topology, fill holes
- **Texture mapping**: Applies textures from original images to the mesh
- Output: High-resolution mesh (millions of triangles)

#### 4. Post-Processing (`worker/pipeline/postproc.py`)
- **Decimation**: Reduce triangle count for Unity performance (100K target)
- **UV unwrapping**: Create texture coordinates for the mesh
- **Texture atlas**: Pack all textures into a single atlas
- **Scale estimation**: Estimate real-world scale

#### 5. Export (`worker/pipeline/export.py`)
- Convert to GLB format (binary GLTF)
- Optimize for Unity (coordinate system, scale)
- Compute QA metrics (triangle count, file size, etc.)
- Return downloadable artifact

## Setup and Deployment

### Local Development

```bash
# Clone repository
git clone <repo-url>
cd atlas-api

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install redis-server docker.io

# Pull OpenMVG and OpenMVS Docker images (if using official images)
# docker pull openmvg/openmvg:latest
# docker pull openmvs/openmvs:latest

# Set up environment
cp .env.example .env
# Edit .env with your settings

# Start Redis
redis-server

# Run API (in one terminal)
uvicorn api.main:app --reload

# Run Celery worker (in another terminal)
celery -A worker.tasks worker --loglevel=info

# Or use the convenience script
chmod +x scripts/run_local.sh
./scripts/run_local.sh
```

### Production (Docker)

```bash
# Pull required Docker images first
docker pull openmvg/openmvg:latest  # Verify image name
docker pull openmvs/openmvs:latest  # Verify image name

# Build and start all services
docker-compose -f infra/docker-compose.yaml --profile prod up -d

# Check logs
docker-compose -f infra/docker-compose.yaml logs -f

# Stop services
docker-compose -f infra/docker-compose.yaml down
```

### Testing

See [docs/RUNNING_AND_TESTING.md](docs/RUNNING_AND_TESTING.md) for detailed testing instructions.

### DigitalOcean Deployment

```bash
# 1. Deploy to VPS
ssh user@your-do-vps
git clone <repo-url>
cd atlas-api

# 2. Set up environment
cp .env.example .env
nano .env  # Set API_KEY and other vars

# 3. Start with Docker
docker-compose -f infra/docker-compose.yaml up -d

# 4. Set up NGINX with SSL (Let's Encrypt)
sudo certbot --nginx -d your-domain.com
```

## API Usage

### Authentication

All endpoints require Bearer token authentication:

```bash
curl -H "Authorization: Bearer changeme_in_production" \
  http://localhost:8000/api/v1/jobs
```

### Create a Job

```python
import requests

url = "http://localhost:8000/api/v1/jobs"
headers = {"Authorization": "Bearer changeme_in_production"}

files = [("images", open(f"image_{i}.jpg", "rb")) for i in range(12)]
data = {"options": json.dumps({"quality": "medium"})}

response = requests.post(url, files=files, data=data, headers=headers)
job = response.json()

print(f"Job ID: {job['job_id']}")
```

### Poll Status

```python
job_id = "abc123..."
url = f"http://localhost:8000/api/v1/jobs/{job_id}"
headers = {"Authorization": "Bearer changeme_in_production"}

response = requests.get(url, headers=headers)
status = response.json()

print(f"Status: {status['status']} ({status['progress']}%)")
print(f"Message: {status['message']}")
```

### Download Result

```python
url = f"http://localhost:8000/api/v1/jobs/{job_id}/artifact"
headers = {"Authorization": "Bearer changeme_in_production"}

response = requests.get(url, headers=headers)

with open("output.glb", "wb") as f:
    f.write(response.content)
```

## Configuration

Key environment variables (see `.env.example`):

- `API_KEY`: Authentication token (change in production!)
- `REDIS_URL`: Redis connection string
- `STORAGE_ROOT`: Data directory path
- `MAX_IMAGES`: Maximum images per job (default: 150)
- `MIN_IMAGES`: Minimum images required (default: 12)
- `MAX_UPLOAD_MB`: Max upload size (default: 2048 MB)

## Architecture Deep Dive

### Why This Stack?

1. **FastAPI**: Modern, fast, automatic validation, async support, built-in docs
2. **Celery**: Industry standard for async tasks in Python
3. **Redis**: Fast, simple, reliable message broker
4. **NGINX**: Battle-tested reverse proxy for production (TLS, rate limiting)
5. **Docker**: Consistent deployment across dev/prod

### Scaling Considerations

**Vertical scaling (more resources):**
- Increase `worker` container resources in `docker-compose.yaml`
- Add more Celery workers: `--concurrency=4`

**Horizontal scaling (more instances):**
- Add multiple `worker` services to `docker-compose.yaml`
- All pull from the same Redis queue
- FastAPI can be load-balanced behind NGINX

**Storage:**
- Current: Local disk (Docker volumes)
- Production: DigitalOcean Spaces (S3-compatible) or other object storage

### Monitoring

- FastAPI: Automatic OpenAPI docs at `/docs`
- Celery: Flower monitoring tool (optional)
- Redis: `redis-cli monitor` for debugging
- Logs: `docker-compose logs -f`

## Troubleshooting

**Job stuck at "queued":**
- Check if Celery worker is running: `docker-compose ps`
- Check worker logs: `docker-compose logs worker`

**Out of memory:**
- Reduce `MAX_IMAGES` or `concurrency`
- Increase Docker memory limits

**Slow processing:**
- Install GPU-accelerated tools (COLMAP-GPU, etc.)
- Use lower quality settings
- Reduce target triangle count


## Contributing

The codebase emphasizes:

- Error handling and logging
- Scalability (Celery workers)
- Security (authentication, validation)
- Observability (status tracking, metrics)
- Maintainability (clear structure, documentation)