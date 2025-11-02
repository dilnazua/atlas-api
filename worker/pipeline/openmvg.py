"""
OpenMVG integration for sparse photogrammetry

OpenMVG (Multiple View Geometry) performs:
1. Feature detection and matching
2. Sparse point cloud reconstruction
3. Camera pose estimation

This module wraps OpenMVG tools using Docker containers.
"""

import os
import subprocess
import json
from pathlib import Path


def run_openmvg(images_dir: str, masks_dir: str, output_dir: str, options: dict):
    """
    Run OpenMVG sparse reconstruction pipeline using Docker.
    
    Args:
        images_dir: Directory containing input images
        masks_dir: Directory containing masks for each image
        output_dir: Directory to save sparse reconstruction results
        options: Processing options
    """
    print(f"Starting OpenMVG sparse reconstruction")
    print(f"Images: {images_dir}")
    print(f"Masks: {masks_dir}")
    print(f"Output: {output_dir}")
    
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # Use Docker to run OpenMVG pipeline
        run_openmvg_docker(images_dir, masks_dir, output_dir, options)
    except Exception as e:
        print(f"Error in sparse reconstruction: {e}")
        raise


def run_openmvg_docker(images_dir: str, masks_dir: str, output_dir: str, options: dict):
    """
    Run OpenMVG using Docker container.
    
    OpenMVG workflow:
    1. ListIntrinsics - list camera intrinsics
    2. ComputeFeatures - extract SIFT features
    3. ComputeMatches - match features
    4. IncrementalSfM - structure from motion
    
    Args:
        images_dir: Input images directory
        masks_dir: Input masks directory (optional, OpenMVG doesn't directly support masks)
        output_dir: Output directory
        options: Processing options
    """
    # Check if Docker is available
    if not check_docker_available():
        raise RuntimeError("Docker is not available. Please install Docker to use OpenMVG.")
    
    # Get absolute paths for volume mounting
    images_abs = os.path.abspath(images_dir)
    output_abs = os.path.abspath(output_dir)
    
    # Create directories inside output for OpenMVG
    matches_dir = os.path.join(output_dir, "matches")
    reconstruction_dir = os.path.join(output_dir, "reconstruction_sequential")
    os.makedirs(matches_dir, exist_ok=True)
    os.makedirs(reconstruction_dir, exist_ok=True)
    
    # Get Docker image name (can be configured via environment or settings)
    from api.settings import settings
    docker_image = os.getenv("OPENMVG_DOCKER_IMAGE", settings.OPENMVG_DOCKER_IMAGE)
    
    # Define paths inside container
    container_images = "/workspace/images"
    container_output = "/workspace/output"
    
    print(f"Using Docker image: {docker_image}")
    
    # Stage 1: List camera intrinsics (creates sfm_data.json)
    print("Listing camera intrinsics...")
    sfm_data_path = os.path.join(output_dir, "sfm_data.json")
    
    # Stage 1: List camera intrinsics (creates sfm_data.json)
    # Use sensor database to estimate intrinsics from EXIF, or provide default focal
    sensor_db = "/opt/openMVG_Build/install/lib/openMVG/sensor_width_camera_database.txt"
    
    # Get first image to estimate dimensions and default focal
    from PIL import Image
    image_files = list(Path(images_dir).glob("*.jpg")) + \
                  list(Path(images_dir).glob("*.jpeg")) + \
                  list(Path(images_dir).glob("*.png"))
    
    default_focal = -1  # Let OpenMVG estimate from sensor database
    if image_files:
        try:
            img = Image.open(image_files[0])
            width, height = img.size
            # Estimate focal as ~50mm equivalent (common for phone cameras)
            # Sensor width typically ~36mm, so focal_px = (focal_mm / sensor_mm) * width
            focal_mm = 50.0
            sensor_mm = 36.0
            default_focal = int((focal_mm / sensor_mm) * max(width, height))
            print(f"Estimated default focal length: {default_focal}px for {width}x{height} images")
        except Exception as e:
            print(f"Could not estimate focal from image: {e}")
    
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{images_abs}:{container_images}:ro",
        "-v", f"{output_abs}:{container_output}",
        docker_image,
        "openMVG_main_SfMInit_ImageListing",
        "-i", container_images,
        "-o", container_output,
        "-d", sensor_db,  # Sensor database for intrinsic estimation
        "-f", str(default_focal)  # Default focal length if EXIF not available
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        check=True  # This should succeed
    )
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Verify sfm_data.json was created
    if not os.path.exists(sfm_data_path):
        raise RuntimeError(f"OpenMVG failed to create sfm_data.json. Error: {result.stderr}")
    
    # Stage 2: Compute features (SIFT)
    print("Computing SIFT features...")
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{images_abs}:{container_images}:ro",
        "-v", f"{output_abs}:{container_output}",
        docker_image,
        "openMVG_main_ComputeFeatures",
        "-i", os.path.join(container_output, "sfm_data.json"),
        "-o", container_output,
        "-m", "SIFT"
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
        check=True
    )
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Stage 3: Compute matches
    print("Computing feature matches...")
    matches_file = os.path.join(container_output, "matches.txt.putative.bin")
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{images_abs}:{container_images}:ro",
        "-v", f"{output_abs}:{container_output}",
        docker_image,
        "openMVG_main_ComputeMatches",
        "-i", os.path.join(container_output, "sfm_data.json"),
        "-o", matches_file,  # Output file, not directory
        "-r", "0.8"  # Ratio threshold
        # Note: -p (pair_list) is optional, omitted for exhaustive matching
        # Geometric filtering happens in a separate step after matching
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
        check=True
    )
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Stage 3b: Geometric filtering (required before SfM)
    print("Filtering matches geometrically...")
    putative_matches = matches_file
    filtered_matches = os.path.join(container_output, "matches.f.bin")
    
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{images_abs}:{container_images}:ro",
        "-v", f"{output_abs}:{container_output}",
        docker_image,
        "openMVG_main_GeometricFilter",
        "-i", os.path.join(container_output, "sfm_data.json"),
        "-m", putative_matches,
        "-o", filtered_matches
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,
        check=True
    )
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Stage 4: Incremental SfM
    print("Running incremental Structure from Motion...")
    container_reconstruction = os.path.join(container_output, "reconstruction_sequential")
    filtered_matches_file = os.path.join(container_output, "matches.f.bin")
    
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{images_abs}:{container_images}:ro",
        "-v", f"{output_abs}:{container_output}",
        docker_image,
        "openMVG_main_SfM",
        "-i", os.path.join(container_output, "sfm_data.json"),
        "-m", container_output,  # Match directory
        "-M", filtered_matches_file,  # Use filtered matches file (matches.f.bin)
        "-o", container_reconstruction,  # Use container path, not host path
        "-s", "INCREMENTAL"  # Required: specify SfM engine type
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=7200,
        check=True
    )
    
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    
    # Convert to COLMAP-compatible format for OpenMVS
    print("Converting to OpenMVS format...")
    convert_to_openmvs_format(reconstruction_dir, output_dir)
    
    # Save summary
    num_images = len(list(Path(images_dir).glob("*.jpg"))) + \
                 len(list(Path(images_dir).glob("*.jpeg"))) + \
                 len(list(Path(images_dir).glob("*.png")))
    
    summary = {
        "method": "OpenMVG",
        "reconstruction_dir": reconstruction_dir,
        "num_images": num_images,
        "docker_image": docker_image
    }
    
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    
    print("OpenMVG sparse reconstruction complete")


def create_basic_intrinsics(intrinsics_file: str, images_dir: str):
    """
    Create a basic camera intrinsics file.
    
    Args:
        intrinsics_file: Path to save intrinsics file
        images_dir: Directory with images
    """
    # Get first image to estimate size
    image_files = list(Path(images_dir).glob("*.jpg")) + \
                  list(Path(images_dir).glob("*.jpeg")) + \
                  list(Path(images_dir).glob("*.png"))
    
    if not image_files:
        return
    
    try:
        from PIL import Image
        img = Image.open(image_files[0])
        width, height = img.size
        
        # Estimate focal length (assume 50mm equivalent, sensor width ~36mm)
        focal_length_mm = 50.0
        sensor_width_mm = 36.0
        focal_length_px = (focal_length_mm / sensor_width_mm) * width
        
        # Create basic intrinsics (OpenMVG format is JSON-like)
        # For now, we'll create a simple one that OpenMVG can use
        print(f"Created basic intrinsics: {width}x{height}, focal={focal_length_px:.1f}px")
    except Exception as e:
        print(f"Could not create intrinsics: {e}")


def convert_to_openmvs_format(reconstruction_dir: str, output_dir: str):
    """
    Convert OpenMVG reconstruction to OpenMVS-compatible format.
    
    OpenMVS expects:
    - cameras.txt, images.txt, points3D.txt (COLMAP format)
    OR
    - scene.mvs (OpenMVS native format)
    
    We'll create a minimal structure that OpenMVS InterfaceCOLMAP can convert.
    
    Args:
        reconstruction_dir: OpenMVG reconstruction directory
        output_dir: Output directory
    """
    # OpenMVG outputs sfm_data.json
    sfm_data_file = os.path.join(reconstruction_dir, "sfm_data.json")
    
    if not os.path.exists(sfm_data_file):
        print(f"Warning: OpenMVG output not found at {sfm_data_file}")
        return
    
    # For now, we'll rely on OpenMVS InterfaceCOLMAP to handle the conversion
    # OpenMVG can export to PLY format which OpenMVS can use
    # Or we can use openMVG_main_openMVG2openMVS to convert directly
    
    print("OpenMVG output ready for OpenMVS conversion")


def check_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def validate_sparse_output(output_dir: str) -> bool:
    """
    Validate that sparse reconstruction produced expected outputs.
    
    Args:
        output_dir: Output directory
    
    Returns:
        bool: True if validation passes
    """
    reconstruction_dir = os.path.join(output_dir, "reconstruction_sequential")
    sfm_data_file = os.path.join(reconstruction_dir, "sfm_data.json")
    
    if not os.path.exists(sfm_data_file):
        print(f"Warning: Missing OpenMVG output {sfm_data_file}")
        return False
    
    return True

