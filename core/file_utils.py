import os
from io import BytesIO
from PIL import Image
# import pikepdf  # Removed for stability
from django.core.files.base import ContentFile

def compress_image(image_file, quality=80, max_size=(2000, 2000)):
    """
    Compresses an image file using Pillow.
    - Resizes if dimensions exceed max_size.
    - Saves as JPEG with specified quality.
    """
    try:
        image_file.seek(0)
        img = Image.open(image_file)
        
        # Convert to RGB if needed
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        # Resize if too large
        if img.width > max_size[0] or img.height > max_size[1]:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        output.seek(0)
        
        return output
    except Exception as e:
        print(f"Image compression failed: {e}")
        image_file.seek(0) # Ensure pointer is reset
        return None

def compress_pdf(pdf_file):
    """
    Simplified PDF handling to ensure saving works perfectly.
    """
    try:
        pdf_file.seek(0)
        return None # Just return original by returning None here
    except Exception as e:
        print(f"PDF handling error: {e}")
        return None

def compress_file(file_field_item):
    """
    Detects file type and applies appropriate compression.
    Returns a ContentFile if compression was successful, otherwise the original file.
    """
    if not file_field_item:
        return file_field_item
        
    filename = file_field_item.name
    ext = os.path.splitext(filename)[1].lower()
    
    # Only compress if it's a new file (has 'file' attribute)
    if not hasattr(file_field_item, 'file'):
        return file_field_item

    compressed_output = None
    new_filename = filename

    if ext in ['.jpg', '.jpeg', '.png']:
        compressed_output = compress_image(file_field_item)
        if compressed_output:
            new_filename = os.path.splitext(filename)[0] + ".jpg"
            return ContentFile(compressed_output.read(), name=new_filename)
            
    return file_field_item
