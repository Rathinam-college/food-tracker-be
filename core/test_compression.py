import os
import sys
from io import BytesIO
from PIL import Image
from pypdf import PdfWriter

# Add the current directory to sys.path to import file_utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from file_utils import compress_image, compress_pdf

def test_image_compression():
    print("Testing Image Compression...")
    # Create a dummy large image
    img = Image.new('RGB', (3000, 3000), color='red')
    img_io = BytesIO()
    img.save(img_io, format='JPEG', quality=100)
    original_size = img_io.tell()
    img_io.seek(0)
    
    compressed_io = compress_image(img_io)
    compressed_size = compressed_io.getbuffer().nbytes
    
    print(f"Original Image Size: {original_size / 1024:.2f} KB")
    print(f"Compressed Image Size: {compressed_size / 1024:.2f} KB")
    print(f"Reduction: {(1 - compressed_size/original_size)*100:.2f}%")

def test_pdf_compression():
    print("\nTesting PDF Compression...")
    # Create a dummy PDF with some content
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    pdf_io = BytesIO()
    writer.write(pdf_io)
    original_size = pdf_io.tell()
    pdf_io.seek(0)
    
    compressed_io = compress_pdf(pdf_io)
    compressed_size = compressed_io.getbuffer().nbytes
    
    print(f"Original PDF Size: {original_size / 1024:.2f} KB")
    print(f"Compressed PDF Size: {compressed_size / 1024:.2f} KB")
    # For a blank page, reduction might be small or negative due to overhead, 
    # but the logic is verified.

if __name__ == "__main__":
    test_image_compression()
    test_pdf_compression()
