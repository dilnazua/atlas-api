"""
GLB/GLTF export for Unity compatibility

This module handles:
1. Converting processed mesh to GLB format using trimesh
2. Optimizing for Unity (right-handed coords, scale, etc.)
3. Computing QA metrics (geometry quality, texture resolution, etc.)

GLB (GL Transmission Format Binary) is the binary form of GLTF,
optimized for web and Unity consumption.
"""

import os
import trimesh
from pathlib import Path


def export_glb(mesh_dir: str, output_path: str, options: dict):
    """
    Export mesh to GLB format ready for Unity.
    
    Args:
        mesh_dir: Directory containing post-processed mesh
        output_path: Path to save GLB file
        options: Processing options (may include 'scale', 'optimize', etc.)
    """
    print(f"Exporting to GLB: {output_path}")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Find input mesh
    textured_mesh = os.path.join(mesh_dir, "mesh_textured.obj")
    if not os.path.exists(textured_mesh):
        # Try to find any mesh
        input_mesh = find_mesh_file(mesh_dir)
        if not input_mesh:
            raise FileNotFoundError(f"No mesh found in {mesh_dir}")
        textured_mesh = input_mesh
    
    # Load mesh with trimesh
    mesh = trimesh.load(textured_mesh)
    
    # Handle empty meshes
    if mesh is None:
        raise ValueError(f"Failed to load mesh from {textured_mesh}")
    
    # Handle empty scenes
    if isinstance(mesh, trimesh.Scene):
        if len(mesh.geometry) == 0:
            raise ValueError(f"Empty scene loaded from {textured_mesh}")
        # Convert scene to a single mesh by merging all geometries
        mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
    
    # Apply optimizations if specified
    if isinstance(mesh, trimesh.Trimesh):
        # Check if mesh is actually empty
        if mesh.vertices.shape[0] == 0 or mesh.faces.shape[0] == 0:
            raise ValueError(f"Mesh is empty (no vertices or faces): {textured_mesh}")
        # Optional: Simplify mesh if requested
        if options.get('optimize', False):
            target_faces = mesh.faces.shape[0] // 2  # Reduce by 50%
            mesh = mesh.simplify_quadric_decimation(target_faces)
        
        # Optional: Scale adjustment
        if 'scale' in options:
            scale = float(options['scale'])
            mesh.apply_scale(scale)
        
        # Fix for Unity (Unity uses right-handed coordinate system)
        # OpenMVS outputs left-handed, so we may need to flip
        if options.get('flip_for_unity', True):
            # Flip Y and Z to convert to Unity's coordinate system
            transform = trimesh.transformations.scale_and_translate(
                scale=[1.0, -1.0, -1.0]
            )
            mesh.apply_transform(transform)
        
        # Center the mesh at origin for easier positioning in Unity
        if options.get('center_at_origin', True):
            mesh.vertices -= mesh.centroid
    print("almost done")
    # Export to GLB
    try:
        mesh.export(output_path, file_type='glb')
        print(f"GLB export complete: {output_path}")
    except Exception as e:
        # Fallback to GLTF if GLB fails
        gltf_path = output_path.replace('.glb', '.gltf')
        mesh.export(gltf_path, file_type='gltf')
        print(f"GLB export failed, saved as GLTF instead: {gltf_path}")
        print(f"Error: {e}")


def find_mesh_file(directory: str) -> str:
    """Find mesh file in directory."""
    for ext in ["*.obj", "*.ply", "*.OBJ", "*.PLY"]:
        for filepath in Path(directory).glob(ext):
            return str(filepath)
    
    # Recursive search
    for ext in ["*.obj", "*.ply"]:
        for filepath in Path(directory).rglob(ext):
            return str(filepath)
    
    return None


def compute_qa_metrics(glb_path: str, workspace_dir: str) -> dict:
    """
    Compute quality assurance metrics for the reconstructed model.
    
    Metrics include:
    - Triangle count
    - Vertex count
    - Texture resolution
    - Bounding box dimensions
    - Estimated reconstruction quality
    
    Args:
        glb_path: Path to output GLB file
        workspace_dir: Workspace directory
    
    Returns:
        dict: QA metrics
    """
    print("Computing QA metrics...")
    
    metrics = {
        "file_size_mb": 0,
        "triangle_count": 0,
        "vertex_count": 0,
        "texture_size": None,
        "bounding_box": None,
        "estimated_quality": "medium"
    }
    
    if not os.path.exists(glb_path):
        return metrics
    
    # Get file size
    metrics["file_size_mb"] = round(os.path.getsize(glb_path) / (1024 * 1024), 2)
    
    # Try to load and analyze the GLB
    try:
        mesh = trimesh.load(glb_path)
        
        if isinstance(mesh, trimesh.Trimesh):
            # Single mesh
            metrics["triangle_count"] = int(mesh.faces.shape[0])
            metrics["vertex_count"] = int(mesh.vertices.shape[0])
            
            # Bounding box
            bounds = mesh.bounds
            size = bounds[1] - bounds[0]
            metrics["bounding_box"] = {
                "min": bounds[0].tolist(),
                "max": bounds[1].tolist(),
                "size": size.tolist()
            }
            
            # Check if mesh has materials/textures
            if hasattr(mesh.visual, 'material'):
                mat = mesh.visual.material
                if hasattr(mat, 'image') and mat.image is not None:
                    metrics["texture_size"] = {
                        "width": mat.image.width,
                        "height": mat.image.height
                    }
            
            # Estimate quality based on triangle density
            if metrics["triangle_count"] > 50000:
                metrics["estimated_quality"] = "high"
            elif metrics["triangle_count"] > 10000:
                metrics["estimated_quality"] = "medium"
            else:
                metrics["estimated_quality"] = "low"
                
        elif isinstance(mesh, trimesh.Scene):
            # Scene with multiple meshes
            total_faces = 0
            total_vertices = 0
            for geometry in mesh.geometry.values():
                if isinstance(geometry, trimesh.Trimesh):
                    total_faces += geometry.faces.shape[0]
                    total_vertices += geometry.vertices.shape[0]
            
            metrics["triangle_count"] = total_faces
            metrics["vertex_count"] = total_vertices
            
            # Get scene bounding box
            bounds = mesh.bounds
            size = bounds[1] - bounds[0]
            metrics["bounding_box"] = {
                "min": bounds[0].tolist(),
                "max": bounds[1].tolist(),
                "size": size.tolist()
            }
            
            # Estimate quality
            if total_faces > 50000:
                metrics["estimated_quality"] = "high"
            elif total_faces > 10000:
                metrics["estimated_quality"] = "medium"
            else:
                metrics["estimated_quality"] = "low"
        
    except Exception as e:
        print(f"Could not fully analyze GLB file: {e}")
        # File exists but couldn't be analyzed
    
    return metrics
