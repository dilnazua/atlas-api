"""
Application settings and configuration

Uses pydantic BaseSettings for validation and environment variable loading.
"""

import json
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    # App Environment
    APP_ENV: str  # development | production | test
    DEBUG: bool

    # API Authentication
    API_KEY: str
    
    # Redis Configuration (message broker for Celery)
    REDIS_URL: str
    
    # Storage Configuration
    STORAGE_ROOT: str
    WORKSPACE_TTL_HOURS: int  # How long to keep intermediate files
    
    # Upload Limits
    MAX_IMAGES: int
    MIN_IMAGES: int
    MAX_UPLOAD_MB: int
    
    # Processing Configuration
    ENABLE_SAM_SEGMENTATION: bool
    SAM_MODEL_TYPE: str  # vit_h | vit_l | vit_b
    SAM_CHECKPOINT_PATH: str   # e.g., /models/sam_vit_h_4b8939.pth
    SAM_DEVICE: str         # cpu | cuda
    PHOTOGRAMMETRY_QUALITY: str  # low, medium, high
    TARGET_TRIANGLE_COUNT: int
    MAX_TEXTURE_SIZE: int
    
    # Pipeline Configuration
    OPENMVG_DOCKER_IMAGE: str  # Docker image for OpenMVG
    OPENMVS_DOCKER_IMAGE: str  # Docker image for OpenMVS

    # CORS / Security
    CORS_ALLOW_ORIGINS: List[str]
    CORS_ALLOW_CREDENTIALS: bool
    CORS_ALLOW_METHODS: List[str]
    CORS_ALLOW_HEADERS: List[str]
    
    @field_validator("CORS_ALLOW_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from JSON string or list."""
        if v is None:
            return v
        if isinstance(v, str):
            try:
                # Try to parse as JSON array
                return json.loads(v)
            except json.JSONDecodeError:
                # If not valid JSON, treat as single origin
                return [v.strip()]
        return v
    
    @field_validator("CORS_ALLOW_METHODS", mode="before")
    @classmethod
    def parse_cors_methods(cls, v):
        """Parse CORS methods from JSON string or list."""
        if v is None:
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [v.strip()]
        return v
    
    @field_validator("CORS_ALLOW_HEADERS", mode="before")
    @classmethod
    def parse_cors_headers(cls, v):
        """Parse CORS headers from JSON string or list."""
        if v is None:
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [v.strip()]
        return v
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore"  # Ignore extra fields from .env (e.g., old GLOMAP_ENABLED)
    }

# Global settings instance (loads only from .env)
settings = Settings()
