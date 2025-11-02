"""
FastAPI main application for Atlas Photogrammetry Service

This service accepts photo sets from Unity, processes them through a 
photogrammetry + segmentation pipeline, and returns 3D models (GLB/GLTF).

Architecture:
- FastAPI: REST API for job submission and status checking
- Redis: Message broker for Celery tasks
- Celery: Async task processing for the photogrammetry pipeline
- NGINX: Reverse proxy for TLS, chunked uploads, rate limiting
"""

from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import uuid
import shutil
import os
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

from .auth import require_auth
from .settings import settings


# Lifespan context for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources on startup/shutdown."""
    # Create necessary directories
    os.makedirs(os.path.join(settings.STORAGE_ROOT, "workspaces"), exist_ok=True)
    os.makedirs(os.path.join(settings.STORAGE_ROOT, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(settings.STORAGE_ROOT, "status"), exist_ok=True)
    yield
    


app = FastAPI(
    title="Atlas Photogrammetry API",
    description="3D reconstruction service for Unity clients",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.DEBUG
)

# CORS middleware (configurable via settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)


def save_job_status(job_id: str, status_data: Dict[str, Any]):
    """Save job status to persistent storage."""
    status_file = os.path.join(settings.STORAGE_ROOT, "status", f"{job_id}.json")
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, "w") as f:
        json.dump(status_data, f, indent=2)


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve job status from persistent storage."""
    status_file = os.path.join(settings.STORAGE_ROOT, "status", f"{job_id}.json")
    if not os.path.exists(status_file):
        return None
    with open(status_file, "r") as f:
        return json.load(f)


# Save reference to helper function before endpoint definition shadows it
_get_job_status_helper = get_job_status


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"service": "Atlas Photogrammetry API", "status": "running", "version": "1.0.0"}


@app.post("/api/v1/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    images: list[UploadFile] = File(...),
    options: str = Form("{}"),
    _=Depends(require_auth)
):
    """
    Create a new photogrammetry job.
    
    Accepts a list of images and optional configuration, stages them in a workspace,
    and queues them for processing via Celery.
    
    Args:
        images: List of uploaded image files
        options: JSON string with processing options (resolution, quality, etc.)
    
    Returns:
        Job ID and task ID for tracking progress
    """
    try:
        opts = json.loads(options or "{}")
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in options parameter"
        )
    
    n = len(images)
    if n < settings.MIN_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {settings.MIN_IMAGES} images for reconstruction"
        )
    if n > settings.MAX_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {settings.MAX_IMAGES} images allowed"
        )
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Create workspace directory structure
    job_dir = os.path.join(settings.STORAGE_ROOT, "workspaces", job_id)
    images_dir = os.path.join(job_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    
    # Save uploaded images
    saved_paths = []
    total_size = 0
    
    for i, upload_file in enumerate(images):
        # Preserve original filename but ensure unique naming
        original_filename = upload_file.filename or f"{i}.jpg"
        ext = os.path.splitext(original_filename)[1].lower() or ".jpg"
        path = os.path.join(images_dir, f"{i:04d}{ext}")
        
        # Stream file to disk
        with open(path, "wb") as out:
            shutil.copyfileobj(upload_file.file, out)
        
        file_size = os.path.getsize(path)
        total_size += file_size
        
        saved_paths.append({
            "index": i,
            "original_name": original_filename,
            "saved_path": path,
            "size": file_size
        })
        
        if total_size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            # Clean up partial upload
            shutil.rmtree(job_dir)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Total upload size exceeds {settings.MAX_UPLOAD_MB}MB limit"
            )
    
    # Initialize job status
    status_data = {
        "job_id": job_id,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "num_images": n,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "options": opts,
        "progress": 0,
        "message": "Job queued for processing"
    }
    save_job_status(job_id, status_data)
    
    # Import here to avoid circular dependency
    import sys
    
    # Add worker directory to path
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "worker"))
    
    from tasks import run_reconstruction
    
    # Enqueue Celery task (runs asynchronously on worker)
    task = run_reconstruction.delay(job_id, opts)
    
    return {
        "job_id": job_id,
        "task_id": task.id,
        "status": "queued",
        "message": "Job created successfully"
    }


@app.get("/api/v1/jobs/{job_id}")
async def get_job_status(job_id: str, _=Depends(require_auth)):
    """
    Get the current status of a photogrammetry job.
    
    Returns detailed progress information including processing stage,
    percentage complete, and any error messages.
    
    Args:
        job_id: Unique job identifier
    
    Returns:
        Job status object with progress and metadata
    """
    # Validate job_id format
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format"
        )
    
    status_data = _get_job_status_helper(job_id)
    
    if not status_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # The status_data from get_job_status() should already be a dict from JSON
    # But ensure it's properly formatted for FastAPI response
    if not isinstance(status_data, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid status data format"
        )
    
    return status_data


@app.get("/api/v1/jobs/{job_id}/artifact")
async def get_artifact(job_id: str, _=Depends(require_auth)):
    """
    Download the processed 3D model artifact (GLB file).
    
    Only available when job status is 'completed'. Returns the GLB file
    that can be directly loaded into Unity.
    
    Args:
        job_id: Unique job identifier
    
    Returns:
        GLB file as binary response
    """
    # Validate job_id format
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format"
        )
    
    # Check job status
    status_data = _get_job_status_helper(job_id)
    if not status_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if status_data.get("status") != "completed":
        current_status = status_data.get("status", "unknown")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job not completed yet. Current status: {current_status}"
        )
    
    # Check if artifact exists
    artifact_path = os.path.join(settings.STORAGE_ROOT, "artifacts", f"{job_id}.glb")
    if not os.path.exists(artifact_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact file not found"
        )
    
    return FileResponse(
        artifact_path,
        media_type="model/gltf-binary",
        filename=f"atlas-{job_id}.glb",
        headers={
            "Content-Disposition": f'attachment; filename="atlas-{job_id}.glb"'
        }
    )


@app.delete("/api/v1/jobs/{job_id}")
async def delete_job(job_id: str, _=Depends(require_auth)):
    """
    Delete a job and all its associated data.
    
    Removes workspace files, artifacts, and status data.
    
    Args:
        job_id: Unique job identifier
    
    Returns:
        Deletion confirmation
    """
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID format"
        )
    
    # Remove workspace
    workspace_dir = os.path.join(settings.STORAGE_ROOT, "workspaces", job_id)
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    
    # Remove artifact
    artifact_path = os.path.join(settings.STORAGE_ROOT, "artifacts", f"{job_id}.glb")
    if os.path.exists(artifact_path):
        os.remove(artifact_path)
    
    # Remove status file
    status_file = os.path.join(settings.STORAGE_ROOT, "status", f"{job_id}.json")
    if os.path.exists(status_file):
        os.remove(status_file)
    
    return {"message": f"Job {job_id} deleted successfully"}
