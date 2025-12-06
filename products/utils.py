"""
Utility functions for barcode scanning and processing.
NOTE: These functions are optional - frontend handles barcode scanning.
They are provided for backend validation or fallback scenarios only.
"""
import os
import io
from typing import Optional
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_GEN_AVAILABLE = True
except ImportError:
    BARCODE_GEN_AVAILABLE = False


def scan_barcode_from_image(image_path: str) -> Optional[str]:
    """
    Scan barcode from an image file.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Barcode value as string if found, None otherwise
    """
    if not os.path.exists(image_path):
        return None
    
    try:
        # Try using pyzbar first (supports most barcode types)
        if PYZBAR_AVAILABLE and PIL_AVAILABLE:
            try:
                # Open and process image
                image = Image.open(image_path)
                
                # Convert to RGB if necessary (pyzbar requires RGB)
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                
                # Scan for barcodes
                barcodes = pyzbar.decode(image)
                
                if barcodes:
                    # Return the first barcode found
                    return barcodes[0].data.decode('utf-8')
            except Exception as e:
                print(f"Pyzbar scanning error: {str(e)}")
        
        # Fallback: Try using opencv if pyzbar is not available or failed
        if OPENCV_AVAILABLE:
            try:
                # Read image
                img = cv2.imread(image_path)
                if img is None:
                    return None
                
                # Convert to grayscale
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
                # Try different barcode detection methods
                # Method 1: Using cv2.barcode.BarcodeDetector (OpenCV 4.5.1+)
                try:
                    detector = cv2.barcode.BarcodeDetector()
                    retval, decoded_info, decoded_type, points = detector.detectAndDecode(gray)
                    
                    if retval and decoded_info:
                        return decoded_info[0] if isinstance(decoded_info, list) else decoded_info
                except AttributeError:
                    # BarcodeDetector not available, try alternative methods
                    pass
            except Exception as e:
                print(f"OpenCV scanning error: {str(e)}")
        
        return None
        
    except Exception as e:
        # Log error but don't raise (allows graceful degradation)
        print(f"Error scanning barcode from image {image_path}: {str(e)}")
        return None


def scan_barcode_from_file(file) -> Optional[str]:
    """
    Scan barcode from a Django uploaded file.
    
    Args:
        file: Django UploadedFile or file-like object
        
    Returns:
        Barcode value as string if found, None otherwise
    """
    try:
        # Save to temporary location if needed
        if hasattr(file, 'temporary_file_path'):
            # File is already on disk
            return scan_barcode_from_image(file.temporary_file_path())
        else:
            # File is in memory, save to temp location
            import tempfile
            
            # Determine file extension
            if hasattr(file, 'name'):
                ext = os.path.splitext(file.name)[1] or '.png'
            else:
                ext = '.png'
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                # Read file content
                if hasattr(file, 'read'):
                    content = file.read()
                    file.seek(0)  # Reset file pointer
                else:
                    content = file
                
                tmp_file.write(content)
                tmp_path = tmp_file.name
            
            try:
                result = scan_barcode_from_image(tmp_path)
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
            
            return result
            
    except Exception as e:
        print(f"Error scanning barcode from file: {str(e)}")
        return None


def _create_high_contrast_barcode_buffer(source_buffer: io.BytesIO, dpi: int) -> io.BytesIO:
    """
    Ensure the generated barcode is high-contrast and printer-friendly.
    Converts to 1-bit monochrome with embedded DPI metadata so Zebra printers
    render crisp bars without fuzzy edges.
    """
    if not PIL_AVAILABLE:
        source_buffer.seek(0)
        return source_buffer
    
    source_buffer.seek(0)
    image = Image.open(source_buffer)
    
    # Convert to grayscale then threshold to pure black/white
    if image.mode != 'L':
        image = image.convert('L')
    
    image = image.point(lambda x: 0 if x < 200 else 255, '1')
    
    target_min_width = 700
    if image.width < target_min_width:
        scale_factor = max(1, int(target_min_width / max(1, image.width)))
        if scale_factor > 1:
            image = image.resize(
                (image.width * scale_factor, image.height * scale_factor),
                resample=Image.NEAREST
            )
    
    processed_buffer = io.BytesIO()
    image.save(processed_buffer, format='PNG', dpi=(dpi, dpi), optimize=True)
    processed_buffer.seek(0)
    return processed_buffer


def _get_barcode_writer_options() -> dict:
    """
    Centralized writer options tuned for thermal label printers (e.g. Zebra).
    Wider module width + larger quiet zone improves readability when printed.
    """
    return {
        'module_width': 0.5,        # Wider bars for thermal transfer
        'module_height': 22.0,      # Taller bars for 1.25\" labels
        'quiet_zone': 7.0,
        'font_size': 16,            # Slightly smaller text
        'text_distance': 10.0,      # Move text further below bars
        'dpi': 600,                 # High DPI for crisp printing
        'background': 'white',
        'foreground': 'black',
        'write_text': True,
    }


def generate_barcode_image(barcode_value: str) -> Optional[InMemoryUploadedFile]:
    """
    Generate a barcode image from a barcode value using CODE128 format.
    Uses python-barcode library to create barcode images.
    
    Args:
        barcode_value: The value to encode in the barcode (e.g., product SKU)
        
    Returns:
        InMemoryUploadedFile with barcode image, or None if generation fails
    """
    if not BARCODE_GEN_AVAILABLE:
        print("Warning: python-barcode library not available. Install with: pip install python-barcode[images]")
        return None
    
    try:
        # Always use CODE128 format with tuned writer options
        writer_options = _get_barcode_writer_options()
        code_class = barcode.get_barcode_class('code128')
        code = code_class(barcode_value, writer=ImageWriter())
        
        buffer = io.BytesIO()
        code.write(buffer, options=writer_options)
        
        # Ensure crisp 1-bit output with embedded DPI metadata
        processed_buffer = _create_high_contrast_barcode_buffer(buffer, dpi=writer_options['dpi'])
        
        # Create Django InMemoryUploadedFile
        image_file = InMemoryUploadedFile(
            processed_buffer,
            None,
            f'{barcode_value}.png',
            'image/png',
            processed_buffer.getbuffer().nbytes,
            None
        )
        
        return image_file
        
    except Exception as e:
        print(f"Error generating barcode image: {str(e)}")
        return None


def generate_barcode_image_file(barcode_value: str, output_path: str = None) -> Optional[str]:
    """
    Generate a barcode image using CODE128 format and save to file.
    
    Args:
        barcode_value: The value to encode in the barcode
        output_path: Path to save the image (optional)
        
    Returns:
        Path to saved image file, or None if generation fails
    """
    if not BARCODE_GEN_AVAILABLE:
        return None
    
    try:
        import tempfile
        
        writer_options = _get_barcode_writer_options()
        code_class = barcode.get_barcode_class('code128')
        code = code_class(barcode_value, writer=ImageWriter())
        
        if not output_path:
            output_path = os.path.join(tempfile.gettempdir(), f'{barcode_value}.png')
        
        base_path = output_path.replace('.png', '')
        tmp_path = code.save(base_path, options=writer_options)
        saved_path = f"{base_path}.png"
        
        # Post-process for high-contrast thermal printing
        if PIL_AVAILABLE:
            with Image.open(saved_path) as image:
                if image.mode != 'L':
                    image = image.convert('L')
                image = image.point(lambda x: 0 if x < 200 else 255, '1')
                
                target_min_width = 700
                if image.width < target_min_width:
                    scale_factor = max(1, int(target_min_width / max(1, image.width)))
                    if scale_factor > 1:
                        image = image.resize(
                            (image.width * scale_factor, image.height * scale_factor),
                            resample=Image.NEAREST
                        )
                
                image.save(saved_path, format='PNG', dpi=(writer_options['dpi'], writer_options['dpi']), optimize=True)
        
        return saved_path
        
    except Exception as e:
        print(f"Error generating barcode image file: {str(e)}")
        return None

