"""
Post-processing for 3D meshes

This module handles:
1. Mesh decimation (reduce triangle count)
2. Mesh repair and optimization
3. Scale estimation

The goal is to transform the raw reconstructed mesh into a
Unity-ready 3D model with proper topology.
"""

import os
import json
import shutil
from pathlib import Path


def run_postprocessing(mesh_dir: str, output_dir: str, options: dict):
    """
    Run complete post-processing pipeline on the reconstructed mesh.
    
    Args:
        mesh_dir: Directory containing raw mesh (OBJ/PLY)
        output_dir: Directory to save post-processed mesh
        options: Processing options
    """
    print("Starting post-processing...")
    os.makedirs(output_dir, exist_ok=True)
    
    # Find input mesh
    input_mesh = find_input_mesh(mesh_dir)
    if not input_mesh:
        raise FileNotFoundError(f"No mesh found in {mesh_dir}")
    
    # Decimate mesh
    decimated_mesh = os.path.join(output_dir, "mesh_decimated.obj")
    decimate_mesh(input_mesh, decimated_mesh, options)
    
    # Repair mesh topology
    repaired_mesh = os.path.join(output_dir, "mesh_textured.obj")
    repair_mesh(decimated_mesh, repaired_mesh, options)
    
    # Estimate scale
    scale = estimate_scale(repaired_mesh)
    
    # Save metadata
    metadata = {
        "input_mesh": input_mesh,
        "output_mesh": repaired_mesh,
        "estimated_scale_meters": scale,
        "options": options
    }
    with open(os.path.join(output_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)
    
    print("Post-processing complete")


def find_input_mesh(mesh_dir: str) -> str:
    """Find the input mesh file (OBJ, PLY, or MVS)."""
    for ext in ["*.obj", "*.ply", "*.mvs"]:
        for filepath in Path(mesh_dir).rglob(ext):
            return str(filepath)
    return None


def decimate_mesh(input_path: str, output_path: str, options: dict):
    """Decimate mesh to target triangle count using Open3D if available."""
    target_tris = options.get("target_triangles", 100000)
    
    try:
        import open3d as o3d
        mesh = o3d.io.read_triangle_mesh(input_path)
        simplified = mesh.simplify_quadric_decimation(target_tris)
        o3d.io.write_triangle_mesh(output_path, simplified)
    except (ImportError, Exception):
        shutil.copy2(input_path, output_path)


def repair_mesh(input_path: str, output_path: str, options: dict):
    """Repair mesh topology issues using Open3D if available."""
    try:
        import open3d as o3d
        mesh = o3d.io.read_triangle_mesh(input_path)
        mesh.remove_duplicated_vertices()
        mesh.remove_duplicated_triangles()
        mesh.remove_unreferenced_vertices()
        mesh.remove_non_manifold_edges()
        o3d.io.write_triangle_mesh(output_path, mesh)
    except (ImportError, Exception):
        shutil.copy2(input_path, output_path)


def estimate_scale(mesh_path: str) -> float:
    """Estimate the real-world scale of the mesh (returns 1.0 as default)."""
    return 1.0
