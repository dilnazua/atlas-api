"""
Pydantic models for API requests and responses

Defines the data structures for API validation and serialization.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class JobCreateResponse(BaseModel):
    """Response from job creation endpoint."""
    job_id: str
    task_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response from job status endpoint."""
    job_id: str
    status: str  # queued, processing, completed, failed
    created_at: str
    updated_at: Optional[str] = None
    num_images: int
    total_size_mb: float
    options: Dict[str, Any]
    progress: int  # 0-100
    message: str
    metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ProcessingOptions(BaseModel):
    """Options for photogrammetry processing."""
    quality: str = "medium"  # low, medium, high
    resolution_level: int = 1
    target_triangles: int = 100000
    max_texture_size: int = 2048
    enable_segmentation: bool = True
