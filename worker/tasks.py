"""
Celery worker task definitions

This module defines the main async task that runs the photogrammetry pipeline.
Celery is a distributed task queue that allows FastAPI to offload long-running
jobs to background workers.

How it works:
1. FastAPI receives a job request and calls run_reconstruction.delay()
2. Celery broker (Redis) queues the task
3. A Celery worker picks up the task from the queue
4. Worker runs the pipeline: segmentation → photogrammetry → post-processing
5. Worker updates job status throughout
6. Worker saves the final GLB artifact
"""

import os
import json
import traceback
from datetime import datetime
from pathlib import Path

from celery import Celery
from celery.signals import task_postrun

from api.settings import settings

# Initialize Celery app
# Celery needs: broker (message queue), backend (result storage)
# Both point to Redis in our case
celery_app = Celery(
    "atlas_photogrammetry",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["worker.tasks"]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,  # Track when task starts, not just queued
    task_time_limit=7200,  # 2 hour hard limit
    task_soft_time_limit=6600,  # 1h50min soft limit (can catch and cleanup)
    worker_prefetch_multiplier=1,  # Process one task at a time per worker
)


def update_job_status(job_id: str, status: str, progress: int, message: str, **kwargs):
    """
    Update the persistent job status file.
    
    This is how the API can track progress even if the worker crashes.
    
    Args:
        job_id: Unique job identifier
        status: Current status (queued, processing, completed, failed)
        progress: Percentage complete (0-100)
        message: Human-readable status message
        **kwargs: Additional metadata to save
    """
    status_file = os.path.join(settings.STORAGE_ROOT, "status", f"{job_id}.json")
    
    # Read existing status or create new
    if os.path.exists(status_file):
        with open(status_file, "r") as f:
            status_data = json.load(f)
    else:
        status_data = {}
    
    # Update fields
    status_data.update({
        "status": status,
        "progress": progress,
        "message": message,
        "updated_at": datetime.utcnow().isoformat(),
        **kwargs
    })
    
    # Write back
    os.makedirs(os.path.dirname(status_file), exist_ok=True)
    with open(status_file, "w") as f:
        json.dump(status_data, f, indent=2)


@celery_app.task(name="worker.tasks.run_reconstruction", bind=True)
def run_reconstruction(self, job_id: str, options: dict):
    """
    Main Celery task that runs the full photogrammetry pipeline.
    
    This is called asynchronously by FastAPI using:
        task = run_reconstruction.delay(job_id, options)
    
    Args:
        self: Celery task instance (bound task)
        job_id: Unique job identifier
        options: Processing options from client
    
    Returns:
        dict: Final job status and metadata
    """
    try:
        update_job_status(
            job_id,
            status="processing",
            progress=0,
            message="Pipeline started"
        )
        
        # Import pipeline modules
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        
        from worker.pipeline.sam_mask import run_sam_segmentation
        from worker.pipeline.openmvg import run_openmvg
        from worker.pipeline.openmvs import run_openmvs_pipeline
        from worker.pipeline.postproc import run_postprocessing
        from worker.pipeline.export import export_glb, compute_qa_metrics
        
        # Define workspace paths
        job_dir = os.path.join(settings.STORAGE_ROOT, "workspaces", job_id)
        images_dir = os.path.join(job_dir, "images")
        masks_dir = os.path.join(job_dir, "masks")
        sparse_dir = os.path.join(job_dir, "sparse")
        dense_dir = os.path.join(job_dir, "dense")
        mesh_dir = os.path.join(job_dir, "mesh")
        output_dir = os.path.join(job_dir, "output")
        artifact_path = os.path.join(settings.STORAGE_ROOT, "artifacts", f"{job_id}.glb")
        
        os.makedirs(masks_dir, exist_ok=True)
        os.makedirs(sparse_dir, exist_ok=True)
        os.makedirs(dense_dir, exist_ok=True)
        os.makedirs(mesh_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        
        # Stage 1: Segmentation (SAM-based)
        if settings.ENABLE_SAM_SEGMENTATION:
            update_job_status(
                job_id,
                status="processing",
                progress=10,
                message="Running SAM segmentation..."
            )
            run_sam_segmentation(images_dir, masks_dir)
        else:
            # Create empty masks (no segmentation)
            import glob
            for img in glob.glob(os.path.join(images_dir, "*")):
                basename = os.path.basename(img)
                mask_path = os.path.join(masks_dir, basename)
                # Create white mask (no masking)
                from PIL import Image
                img_obj = Image.open(img)
                white_mask = Image.new("L", img_obj.size, 255)
                white_mask.save(mask_path)
        
        # Stage 2: Photogrammetry - Sparse Reconstruction
        update_job_status(
            job_id,
            status="processing",
            progress=20,
            message="Running sparse reconstruction (OpenMVG)..."
        )
        run_openmvg(images_dir, masks_dir, sparse_dir, options)
        
        # Stage 3: Photogrammetry - Dense Reconstruction
        update_job_status(
            job_id,
            status="processing",
            progress=50,
            message="Running dense reconstruction (OpenMVS)..."
        )
        run_openmvs_pipeline(sparse_dir, dense_dir, mesh_dir, options)
        
        # Stage 4: Post-processing
        update_job_status(
            job_id,
            status="processing",
            progress=70,
            message="Post-processing mesh..."
        )
        run_postprocessing(mesh_dir, output_dir, options)
        
        # Stage 5: Export to GLB/GLTF
        update_job_status(
            job_id,
            status="processing",
            progress=90,
            message="Exporting to GLB format..."
        )
        export_glb(output_dir, artifact_path, options)
        
        # Stage 6: Compute QA metrics
        metrics = compute_qa_metrics(artifact_path, job_dir)
        
        # Update final status
        update_job_status(
            job_id,
            status="completed",
            progress=100,
            message="Reconstruction complete",
            completed_at=datetime.utcnow().isoformat(),
            metrics=metrics
        )
        
        return {
            "job_id": job_id,
            "status": "completed",
            "progress": 100,
            "metrics": metrics
        }
        
    except Exception as exc:
        # Log error
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        
        print(f"Error in reconstruction task {job_id}:")
        print(error_trace)
        
        update_job_status(
            job_id,
            status="failed",
            progress=self.request.retries * 10,
            message=f"Error: {error_msg}",
            error=error_msg,
            traceback=error_trace
        )
        
        # Re-raise to let Celery handle retries
        raise exc
