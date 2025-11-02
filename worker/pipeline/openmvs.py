"""
OpenMVS integration for dense reconstruction and meshing

OpenMVS (Multi-View Stereo) performs:
1. Dense point cloud reconstruction from sparse matches
2. Surface meshing from dense points
3. Mesh refinement and optimization

This module wraps OpenMVS CLI tools using Docker containers.
"""

import os
import subprocess
import json


def run_openmvs_pipeline(sparse_dir: str, dense_dir: str, mesh_dir: str, options: dict):
    """
    Run OpenMVS dense reconstruction and meshing pipeline using Docker.
    
    Args:
        sparse_dir: Directory with sparse reconstruction (from OpenMVG)
        dense_dir: Directory to save dense point cloud
        mesh_dir: Directory to save final mesh
        options: Processing options
    """
    # Find images directory
    workspace_dir = os.path.dirname(sparse_dir)
    images_path = os.path.join(workspace_dir, "images")
    if not os.path.exists(images_path):
        images_path = os.path.join(os.path.dirname(workspace_dir), "images")
    
    # Find reconstruction directory
    reconstruction_dir = os.path.join(sparse_dir, "reconstruction_sequential")
    if not os.path.exists(reconstruction_dir):
        reconstruction_dir = sparse_dir
    
    os.makedirs(dense_dir, exist_ok=True)
    os.makedirs(mesh_dir, exist_ok=True)
    
    # Get absolute paths
    sparse_abs = os.path.abspath(reconstruction_dir)
    dense_abs = os.path.abspath(dense_dir)
    mesh_abs = os.path.abspath(mesh_dir)
    images_abs = os.path.abspath(images_path) if os.path.exists(images_path) else None
    
    # Get Docker images
    from api.settings import settings
    openmvg_image = os.getenv("OPENMVG_DOCKER_IMAGE", settings.OPENMVG_DOCKER_IMAGE)
    openmvs_image = os.getenv("OPENMVS_DOCKER_IMAGE", settings.OPENMVS_DOCKER_IMAGE)
    
    # Container paths
    container_sparse = "/workspace/sparse"
    container_dense = "/workspace/dense"
    container_mesh = "/workspace/mesh"
    container_images = "/workspace/images"
    
    # Stage 1: Convert OpenMVG to OpenMVS format
    print("Converting OpenMVG format to OpenMVS...")
    sfm_data_json = os.path.join(reconstruction_dir, "sfm_data.json")
    
    # Convert binary to JSON if needed
    sfm_data_bin = os.path.join(reconstruction_dir, "sfm_data.bin")
    if os.path.exists(sfm_data_bin) and not os.path.exists(sfm_data_json):
        cmd = ["docker", "run", "--rm",
               "-v", f"{sparse_abs}:{container_sparse}",
               openmvg_image,
               "openMVG_main_ConvertSfM_DataFormat",
               "-i", os.path.join(container_sparse, "sfm_data.bin"),
               "-o", os.path.join(container_sparse, "sfm_data.json")]
        subprocess.run(cmd, check=True)
    
    # Convert to OpenMVS format
    if not os.path.exists(sfm_data_json):
        raise RuntimeError(f"No sfm_data found in {reconstruction_dir}")
    
    volumes = ["-v", f"{sparse_abs}:{container_sparse}:ro",
               "-v", f"{dense_abs}:{container_dense}"]
    if images_abs:
        volumes.extend(["-v", f"{images_abs}:{container_images}:ro"])
    
    cmd = ["docker", "run", "--rm"] + volumes + [
        openmvg_image,
        "openMVG_main_openMVG2openMVS",
        "-i", os.path.join(container_sparse, "sfm_data.json"),
        "-o", os.path.join(container_dense, "scene.mvs"),
        "-d", os.path.join(container_dense, "undistorted"),
        "-n", "1"
    ]
    subprocess.run(cmd, check=True)
    
    # Stage 2: Dense reconstruction
    print("Running dense point cloud reconstruction...")
    volumes = ["-v", f"{dense_abs}:{container_dense}",
               "-v", f"{mesh_abs}:{container_mesh}"]
    if images_abs:
        volumes.extend(["-v", f"{images_abs}:{container_images}:ro"])
    
    resolution_level = options.get("resolution_level", 1)
    cmd = ["docker", "run", "--rm"] + volumes + [
        openmvs_image,
        "DensifyPointCloud",
        "--working-folder", container_dense,
        "--input-file", os.path.join(container_dense, "scene.mvs"),
        "--resolution-level", str(resolution_level)
    ]
    subprocess.run(cmd, check=True)
    
    # Stage 3: Mesh reconstruction
    print("Reconstructing mesh...")
    volumes = ["-v", f"{dense_abs}:{container_dense}",
               "-v", f"{mesh_abs}:{container_mesh}"]
    if images_abs:
        volumes.extend(["-v", f"{images_abs}:{container_images}:ro"])
    
    cmd = ["docker", "run", "--rm"] + volumes + [
        openmvs_image,
        "ReconstructMesh",
        "--working-folder", container_mesh,
        "--input-file", os.path.join(container_dense, "scene_dense.mvs"),
        "--output-file", os.path.join(container_mesh, "scene_dense_mesh.mvs")
    ]
    subprocess.run(cmd, check=True)
    
    # Stage 4: Mesh refinement
    print("Refining mesh...")
    volumes = ["-v", f"{mesh_abs}:{container_mesh}"]
    if images_abs:
        volumes.extend(["-v", f"{images_abs}:{container_images}:ro"])
    
    cmd = ["docker", "run", "--rm"] + volumes + [
        openmvs_image,
        "RefineMesh",
        "--working-folder", container_mesh,
        "--input-file", os.path.join(container_mesh, "scene_dense_mesh.mvs"),
        "--max-face-area", str(options.get("max_face_area", 16)),
        "--process-local-memory", "0"
    ]
    subprocess.run(cmd, check=True)
    
    # Stage 5: Texture mapping
    print("Applying textures...")
    cmd = ["docker", "run", "--rm"] + volumes + [
        openmvs_image,
        "TextureMesh",
        "--working-folder", container_mesh,
        "--input-file", os.path.join(container_mesh, "scene_dense_mesh_refine.mvs"),
        "--export-type", "obj"
    ]
    subprocess.run(cmd, check=True)
    
    # Save summary
    summary = {
        "method": "OpenMVS-Docker",
        "dense_dir": dense_dir,
        "mesh_dir": mesh_dir,
        "docker_image": openmvs_image
    }
    with open(os.path.join(dense_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    
    print("OpenMVS pipeline complete")

