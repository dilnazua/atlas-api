# Atlas Photogrammetry API - Architecture Documentation

## Understanding the Stack

This document explains the key technologies and how they work together.

## Technology Stack

### 1. FastAPI (REST API)

**What it is:** A modern Python web framework for building APIs

**Why use it:**
- **Async by default**: Can handle many simultaneous requests efficiently
- **Automatic validation**: Uses Pydantic to validate request data
- **Auto-documentation**: Generates OpenAPI docs at `/docs`
- **Type hints**: Better IDE support and fewer bugs

**In our system:**
- Receives image uploads from Unity client
- Validates inputs (min/max images, file size)
- Saves images to disk workspace
- Queues task in Celery via Redis
- Returns immediately with job_id
- Provides status endpoints for polling

**Example:**
```python
@app.post("/api/v1/jobs")
async def create_job(images: list[UploadFile], ...):
    # Save images
    # Create Celery task
    task = run_reconstruction.delay(job_id, options)
    # Return job_id immediately (doesn't wait for processing)
    return {"job_id": job_id, "status": "queued"}
```

### 2. Redis (Message Broker)

**What it is:** An in-memory data store, used here as a message queue

**Why use it:**
- **Super fast**: Reads/writes are microseconds (vs milliseconds for databases)
- **Persistent**: Can survive restarts (optional)
- **Simple**: Just a key-value store, no schema

**In our system:**
- FastAPI enqueues tasks into Redis
- Celery workers pull tasks from Redis
- Also stores task results temporarily
- Acts as "middleman" between API and workers

**Visualization:**
```
FastAPI → Redis Queue → Celery Worker
             ↓
        [Task 1]
        [Task 2]
        [Task 3] ← Worker pulls this
```

**Without Redis:** The API server would have to wait hours for processing, blocking all other requests.

### 3. Celery (Task Queue)

**What it is:** A distributed task queue for Python

**Why use it:**
- **Async processing**: Long-running tasks don't block the API
- **Scalable**: Can run multiple workers in parallel
- **Reliable**: Handles failures, retries, timeouts
- **Progress tracking**: Can report status as it works

**In our system:**
- Runs the photogrammetry pipeline (takes 10-60 minutes)
- Updates job status throughout processing
- Can be scaled horizontally (add more workers)

**Example task:**
```python
@celery_app.task
def run_reconstruction(job_id: str, options: dict):
    # This runs in a separate process
    
    # Stage 1: Segmentation (10%)
    update_status(job_id, progress=10, message="Segmenting...")
    run_sam_segmentation()
    
    # Stage 2: Sparse reconstruction (20%)
    update_status(job_id, progress=20, message="Sparse recon...")
    run_openmvg()
    
    # ... etc
    
    # Done (100%)
    update_status(job_id, progress=100, status="completed")
```

**Why not just use threads?**
- Threads are limited by Python's GIL (can't truly parallelize CPU-bound work)
- Celery runs separate processes (can use multiple CPU cores)
- Better isolation (one failed task doesn't crash the server)
- Can run workers on different machines

### 4. NGINX (Reverse Proxy)

**What it is:** A web server and reverse proxy

**Why use it:**
- **SSL/TLS termination**: Handles HTTPS encryption
- **Rate limiting**: Prevents abuse (X requests per second)
- **Chunked uploads**: Efficiently handles large file uploads
- **Load balancing**: Can distribute requests across multiple API instances

**In our system:**
- Sits in front of FastAPI
- Handles client connections
- Terminates TLS (HTTPS)
- Limits upload rate
- Buffers large uploads before forwarding to FastAPI

**Request flow:**
```
Client → NGINX → FastAPI → Redis → Celery
          ↓ SSL
         HTTPS
```

### 5. Pipeline Components

#### SAM Segmentation
- Uses Meta's Segment Anything Model
- Detects and isolates foreground object
- Generates binary masks for each image
- Improves photogrammetry by focusing on subject

#### Photogrammetry (OpenMVG)
- **Sparse reconstruction**: Uses OpenMVG Docker container to find features in images, match across images, estimate 3D positions
- Creates sparse point cloud (few thousand points)
- Estimates camera positions
- Outputs sfm_data.json format for OpenMVS conversion

#### Dense Reconstruction (OpenMVS)
- Converts OpenMVG sparse output to OpenMVS format
- Creates dense point cloud (millions of points) using Docker container
- Generates triangle mesh connecting points
- Refines mesh quality
- Applies textures to the mesh

#### Post-Processing
- Decimates mesh for Unity performance (100K triangles target)
- Creates UV coordinates for textures
- Generates texture atlas

#### Export
- Converts to GLB format (binary GLTF)
- Optimizes for Unity
- Computes QA metrics

## Data Flow

### Creating a Job

```
1. Unity client sends POST /api/v1/jobs with 12+ images
2. FastAPI validates inputs (number, size)
3. FastAPI saves images to storage/workspaces/{job_id}/images/
4. FastAPI creates job status JSON file
5. FastAPI enqueues Celery task via Redis
6. FastAPI returns job_id to client
7. Client can start polling status immediately
```

### Processing a Job

```
1. Celery worker pulls task from Redis queue
2. Worker updates status: "processing" (0%)
3. Worker runs SAM segmentation (progress: 10%)
4. Worker runs OpenMVG sparse reconstruction (progress: 30%)
5. Worker runs OpenMVS dense reconstruction (progress: 60%)
6. Worker runs post-processing (progress: 80%)
7. Worker exports GLB file (progress: 90%)
8. Worker saves GLB to storage/artifacts/{job_id}.glb
9. Worker updates status: "completed" (100%)
10. Worker task completes
```

### Downloading Result

```
1. Unity client polls GET /api/v1/jobs/{id}
2. Status is "completed" → ready to download
3. Unity client requests GET /api/v1/jobs/{id}/artifact
4. FastAPI serves GLB file
5. Unity imports GLB file directly
```

## Error Handling

### Failed Task
- Worker catches exception
- Updates status: "failed" with error message
- Logs error for debugging
- Celery can retry (configurable)

### Client Side
- Unity polls every few seconds
- If status is "failed", show error to user
- User can retry with different inputs

## Scaling

### Vertical Scaling (More Power)
```
docker-compose up --scale worker=4
```
- Runs 4 Celery workers on same machine
- Each pulls from same Redis queue
- Processes 4 jobs in parallel

### Horizontal Scaling (More Machines)
```
# Machine 1
- FastAPI
- NGINX
- Redis

# Machine 2
- Celery Worker 1
- Celery Worker 2

# Machine 3
- Celery Worker 3
- Celery Worker 4
```
- Workers can be on different machines
- All connect to same Redis instance
- NGINX load balances FastAPI requests

## Monitoring

### Status Tracking
- Each job has a JSON status file
- Updates throughout processing
- Visible via API endpoint

### Logs
```bash
# View all logs
docker-compose logs -f

# View specific service
docker-compose logs -f worker

# Follow in real-time
docker-compose logs -f --tail=100 worker
```

### Debugging
- Check job status: `GET /api/v1/jobs/{id}`
- Check Redis queue: `redis-cli LLEN celery`
- Check worker processes: `docker-compose ps`
- Check storage: `ls -lh storage/artifacts/`

## Security

### Authentication
- Bearer token in Authorization header
- Validate on every request
- Change `API_KEY` in production

### Rate Limiting
- NGINX limits requests per IP
- Prevents abuse and DoS

### File Validation
- Check file extensions
- Limit file size
- Scan for malicious content (in production)

## Performance Optimization

### FastAPI
- Multiple Uvicorn workers (`--workers 4`)
- Use async I/O for file operations

### Celery
- Multiple worker processes (`--concurrency=4`)
- Prefetch tasks to minimize overhead
- Use result backend for status tracking

### Redis
- Configure memory limits
- Enable persistence for reliability
- Use Redis Sentinel for HA

### Storage
- Use SSD for `/data` volume
- Monitor disk space (large uploads)
- Implement TTL cleanup for old jobs

## Future Enhancements

- GPU acceleration for SAM/COLMAP
- Distributed storage (DigitalOcean Spaces)
- WebSocket for real-time progress updates
- Multiple quality tiers (fast/standard/premium)
- Background job scheduling (Celery Beat)
- Monitoring dashboard (Prometheus)
