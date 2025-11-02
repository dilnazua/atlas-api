"""
SAM-based image segmentation for background removal (production only)

This module uses Meta's Segment Anything Model (SAM) to automatically
create masks for each image, removing backgrounds and focusing on the
foreground object.

Requirements (no fallbacks):
- segment-anything installed
- A valid SAM checkpoint configured in settings
- GPU/CPU device configured via settings

Typical workflow:
1. Load each image from the input directory
2. Use SAM to detect and segment the main subject
3. Generate binary masks (255 = keep, 0 = remove)
4. Save masks for use in photogrammetry pipeline
"""

import os
import glob
import cv2
import numpy as np

# Lazy SAM initialization and singleton
_sam_loaded = False
_sam_mask_generator = None

def _load_sam_or_raise():
    global _sam_loaded, _sam_predictor, _sam_mask_generator
    if _sam_loaded:
        if _sam_mask_generator is None:
            raise RuntimeError("SAM failed to initialize earlier; cannot proceed without SAM.")
        return True

    from api.settings import settings
    from segment_anything import sam_model_registry
    # torch is required by segment-anything even if not referenced here
    import torch  # noqa: F401

    checkpoint = settings.SAM_CHECKPOINT_PATH
    model_type = settings.SAM_MODEL_TYPE
    device = settings.SAM_DEVICE

    if not checkpoint or not os.path.exists(checkpoint):
        raise FileNotFoundError("SAM checkpoint not configured or missing; set settings.SAM_CHECKPOINT_PATH.")

    sam = sam_model_registry[model_type](checkpoint=checkpoint)
    sam.to(device=device)

    # Configure high-quality automatic mask generator
    from segment_anything import SamAutomaticMaskGenerator as _SamAutoGen
    _sam_mask_generator = _SamAutoGen(
        model=sam,
        points_per_side=32,
        points_per_batch=64,
        pred_iou_thresh=0.88,
        stability_score_thresh=0.92,
        crop_n_layers=1,
        crop_n_points_downscale_factor=2,
        min_mask_region_area=0
    )
    _sam_loaded = True
    return True


def run_sam_segmentation(images_dir: str, masks_dir: str):
    """
    Run SAM-based segmentation on all images.
    
    Args:
        images_dir: Directory containing input images
        masks_dir: Directory to save generated masks
    """
    print(f"Starting SAM segmentation for images in {images_dir}")
    
    # Get all image files
    image_extensions = ["*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG"]
    image_files = []
    for ext in image_extensions:
        image_files.extend(glob.glob(os.path.join(images_dir, ext)))
    
    image_files.sort()
    print(f"Found {len(image_files)} images")
    
    if len(image_files) == 0:
        raise ValueError("No images found for segmentation")
    
    os.makedirs(masks_dir, exist_ok=True)
    
    # Ensure SAM is available before processing
    _load_sam_or_raise()

    # Process each image
    for i, image_path in enumerate(image_files):
        print(f"Processing image {i+1}/{len(image_files)}: {os.path.basename(image_path)}")
        
        try:
            # Create mask for this image
            mask = create_sam_mask(image_path)
            
            # Save mask
            basename = os.path.basename(image_path)
            mask_path = os.path.join(masks_dir, basename)
            cv2.imwrite(mask_path, mask)
            
        except Exception as e:
            # Fail fast in production; no heuristic/default masks
            raise RuntimeError(f"Failed to process {image_path}: {e}")
    
    print(f"SAM segmentation complete. Masks saved to {masks_dir}")


def create_sam_mask(image_path: str) -> np.ndarray:
    """
    Create a binary mask for an image using SAM.

    Args:
        image_path: Path to input image

    Returns:
        Binary mask (255 = keep, 0 = remove)
    """
    _load_sam_or_raise()

    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Could not load image: {image_path}")
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    masks = _sam_mask_generator.generate(image_rgb)
    if not masks:
        raise RuntimeError("SAM did not return any masks for the input image.")

    # Choose the largest mask by area as subject
    largest = max(masks, key=lambda m: m.get('area', 0))
    seg = largest['segmentation'].astype(np.uint8) * 255

    # Close small holes
    seg = cv2.morphologyEx(seg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return seg
