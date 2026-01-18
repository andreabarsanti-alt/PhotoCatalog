"""
Image comparison functions for detecting duplicates and similar images.

Required packages:
    pip install scikit-image opencv-python numpy pillow pillow-heif
"""

from skimage.metrics import structural_similarity as ssim
import cv2
import numpy as np
from PIL import Image
import pillow_heif

# Register HEIF opener with PIL
pillow_heif.register_heif_opener()


def _load_image(image_path, grayscale=False):
    """
    Load an image supporting multiple formats including HEIC/HEIF.

    Args:
        image_path: Path to the image file
        grayscale: If True, convert to grayscale

    Returns:
        numpy array in OpenCV format (BGR or grayscale)
    """
    try:
        # Try to open with PIL (supports HEIC via pillow-heif)
        pil_img = Image.open(image_path)

        # Convert to RGB if necessary
        if pil_img.mode != 'RGB' and pil_img.mode != 'L':
            pil_img = pil_img.convert('RGB')

        # Convert PIL image to numpy array
        img_array = np.array(pil_img)

        # Convert RGB to BGR for OpenCV compatibility
        if len(img_array.shape) == 3:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

        # Convert to grayscale if requested
        if grayscale and len(img_array.shape) == 3:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_BGR2GRAY)

        return img_array

    except Exception as e:
        raise ValueError(f"Could not read image {image_path}: {e}")


def compare_ssim(image1_path, image2_path, threshold=0.95, verbose=False):
    """
    Compare images using Structural Similarity Index (SSIM).
    Supports HEIC/HEIF and all PIL-supported formats.

    Best for: Detecting compression artifacts, quality differences
    Accurate perceptual similarity measurement.

    Args:
        image1_path: Path to first image
        image2_path: Path to second image
        threshold: Minimum similarity score to consider similar (default: 0.95)
                  1.0 = identical, 0.0 = completely different
        verbose: If True, print comparison results (default: False)

    Returns:
        dict with:
            - 'similar': Boolean, True if images are similar
            - 'score': Float, similarity score (0.0 to 1.0)
            - 'threshold': Float, threshold used
    """
    try:
        # Read images in grayscale
        img1 = _load_image(image1_path, grayscale=True)
        img2 = _load_image(image2_path, grayscale=True)

        # Resize to same dimensions if needed
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

        # Calculate SSIM
        score, _ = ssim(img1, img2, full=True)

        result = {
            'similar': score >= threshold,
            'score': float(score),
            'threshold': threshold
        }

        if verbose:
            print(f"SSIM Comparison:")
            print(f"  Image 1: {image1_path}")
            print(f"  Image 2: {image2_path}")
            print(f"  Score: {score:.4f} (threshold: {threshold})")
            print(f"  Similar: {'Yes' if result['similar'] else 'No'}")

        return result

    except Exception as e:
        result = {
            'similar': False,
            'score': None,
            'threshold': threshold,
            'error': str(e)
        }

        if verbose:
            print(f"SSIM Comparison Error:")
            print(f"  Image 1: {image1_path}")
            print(f"  Image 2: {image2_path}")
            print(f"  Error: {e}")

        return result


def compare_mse(image1_path, image2_path, threshold=1000, verbose=False):
    """
    Compare images using Mean Squared Error (MSE).
    Supports HEIC/HEIF and all PIL-supported formats.

    Best for: Simple, fast pixel-by-pixel comparison
    Note: Sensitive to shifts and doesn't match human perception well.

    Args:
        image1_path: Path to first image
        image2_path: Path to second image
        threshold: Maximum MSE to consider similar (default: 1000)
                  0 = identical, higher values = more different
        verbose: If True, print comparison results (default: False)

    Returns:
        dict with:
            - 'similar': Boolean, True if images are similar
            - 'mse': Float, mean squared error (lower = more similar)
            - 'threshold': Float, threshold used
    """
    try:
        # Read images
        img1 = _load_image(image1_path, grayscale=False)
        img2 = _load_image(image2_path, grayscale=False)

        # Resize to same dimensions if needed
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

        # Calculate MSE
        mse = np.mean((img1.astype(float) - img2.astype(float)) ** 2)

        result = {
            'similar': mse <= threshold,
            'mse': float(mse),
            'threshold': threshold
        }

        if verbose:
            print(f"MSE Comparison:")
            print(f"  Image 1: {image1_path}")
            print(f"  Image 2: {image2_path}")
            print(f"  MSE: {mse:.2f} (threshold: {threshold})")
            print(f"  Similar: {'Yes' if result['similar'] else 'No'}")

        return result

    except Exception as e:
        result = {
            'similar': False,
            'mse': None,
            'threshold': threshold,
            'error': str(e)
        }

        if verbose:
            print(f"MSE Comparison Error:")
            print(f"  Image 1: {image1_path}")
            print(f"  Image 2: {image2_path}")
            print(f"  Error: {e}")

        return result


def validate_groups_by_comparison(input_json, output_json,
                                  group_key="matchID",
                                  source_key="SourceFile",
                                  method="ssim",
                                  threshold=0.95,
                                  verbose=False,
                                  specific_group_id=None):
    """
    Validate and refine match groups by pixel-level image comparison.
    Groups that contain visually different images will be split into subgroups.

    Args:
        input_json: Path to the input JSON catalog
        output_json: Path to save the validated catalog
        group_key: Key used to identify groups (default: "matchID")
        source_key: Key containing image file path (default: "SourceFile")
        method: Comparison method - 'ssim' or 'mse' (default: 'ssim')
        threshold: Similarity threshold (for SSIM: 0.95, for MSE: 1000)
        verbose: If True, print detailed comparison results (default: False)
        specific_group_id: If provided, only validate this specific group ID (default: None = validate all)
    """
    import json

    compare_func = compare_ssim if method == 'ssim' else compare_mse

    try:
        # Read JSON catalog
        print(f"Reading {input_json}...")
        with open(input_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} items")

        # Find the maximum existing group ID to generate unique new IDs
        max_id = 0
        for item in data:
            if group_key in item and item[group_key] is not None:
                if isinstance(item[group_key], (int, float)):
                    max_id = max(max_id, int(item[group_key]))

        next_id = max_id + 1

        # Group items by group_key
        groups = {}
        for item in data:
            if group_key in item and item[group_key] is not None:
                gid = item[group_key]
                if gid not in groups:
                    groups[gid] = []
                groups[gid].append(item)

        # Filter to specific group if requested
        if specific_group_id is not None:
            if specific_group_id in groups:
                groups = {specific_group_id: groups[specific_group_id]}
                print(f"Validating only group {specific_group_id}")
            else:
                print(f"Error: Group {specific_group_id} not found in catalog")
                return
        else:
            print(f"Found {len(groups)} groups to validate")

        print(f"Using {method.upper()} with threshold={threshold}")
        if verbose:
            print("Verbose mode enabled")

        # Statistics
        total_groups_split = 0
        total_new_groups = 0
        groups_unchanged = 0

        # Process each group
        for gid, items in groups.items():
            if len(items) < 2:
                groups_unchanged += 1
                continue

            print(f"\n{'='*60}")
            print(f"Validating group {gid} ({len(items)} images)")
            print(f"{'='*60}")

            # Get items with valid file paths
            valid_items = []
            for item in items:
                if source_key in item and item[source_key]:
                    valid_items.append(item)

            if len(valid_items) < 2:
                print(f"  Skipping: not enough valid file paths")
                groups_unchanged += 1
                continue

            # Build similarity matrix - compare all pairs
            n = len(valid_items)
            similarity_matrix = [[False for _ in range(n)] for _ in range(n)]

            # Mark diagonal as similar (image is similar to itself)
            for i in range(n):
                similarity_matrix[i][i] = True

            # Compare all pairs
            for i in range(n):
                for j in range(i + 1, n):
                    img1_path = valid_items[i][source_key]
                    img2_path = valid_items[j][source_key]

                    result = compare_func(img1_path, img2_path, threshold=threshold, verbose=verbose)

                    is_similar = result.get('similar', False)
                    similarity_matrix[i][j] = is_similar
                    similarity_matrix[j][i] = is_similar

            # Find connected components (groups of mutually similar images)
            assigned = [False] * n
            subgroups = []

            for i in range(n):
                if assigned[i]:
                    continue

                # Start a new subgroup
                subgroup = [i]
                assigned[i] = True

                # Find all images similar to everything in the current subgroup
                for j in range(i + 1, n):
                    if assigned[j]:
                        continue

                    # Check if j is similar to all items in subgroup
                    similar_to_all = all(similarity_matrix[j][k] for k in subgroup)

                    if similar_to_all:
                        subgroup.append(j)
                        assigned[j] = True

                subgroups.append(subgroup)

            # Assign new group IDs
            if len(subgroups) == 1:
                # No split needed - check if it's a single item
                if len(subgroups[0]) == 1:
                    # Single item group - remove the group key
                    valid_items[subgroups[0][0]][group_key] = None
                    print(f"  Removed group ID - only one image in group")
                    total_groups_split += 1  # Count as processed
                else:
                    # Multiple items, all similar
                    print(f"  Group unchanged - all images are similar")
                    groups_unchanged += 1
            else:
                # Split into multiple groups
                print(f"  Splitting into {len(subgroups)} subgroups:")
                total_groups_split += 1
                total_new_groups += len(subgroups)

                for sg_idx, subgroup in enumerate(subgroups):
                    # Single-item subgroups get None (no group)
                    if len(subgroup) == 1:
                        valid_items[subgroup[0]][group_key] = None
                        print(f"    Subgroup {sg_idx + 1} (ID=None): 1 image (removed from group)")
                        total_new_groups -= 1  # Don't count single items as groups
                        if verbose:
                            print(f"      - {valid_items[subgroup[0]][source_key]}")
                        continue

                    # First multi-item subgroup keeps the original ID
                    if sg_idx == 0:
                        new_gid = gid
                    else:
                        new_gid = next_id
                        next_id += 1

                    # Update items in this subgroup
                    for item_idx in subgroup:
                        valid_items[item_idx][group_key] = new_gid

                    print(f"    Subgroup {sg_idx + 1} (ID={new_gid}): {len(subgroup)} images")
                    if verbose:
                        for item_idx in subgroup:
                            print(f"      - {valid_items[item_idx][source_key]}")

        # Final statistics
        print(f"\n{'='*60}")
        print("VALIDATION RESULTS")
        print(f"{'='*60}")
        if specific_group_id is not None:
            print(f"Validated group: {specific_group_id}")
        else:
            print(f"Groups processed: {len(groups)}")
        print(f"Groups unchanged: {groups_unchanged}")
        print(f"Groups split: {total_groups_split}")
        if specific_group_id is None:
            print(f"Total groups after validation: {groups_unchanged + total_new_groups}")

        # Save updated catalog
        print(f"\nWriting validated catalog to {output_json}...")
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        print("Done!")

    except FileNotFoundError:
        print(f"Error: File not found - {input_json}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {input_json}")
    except Exception as e:
        print(f"Error: {e}")
        raise