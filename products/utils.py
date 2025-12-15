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
    from PIL import Image, ImageDraw, ImageFont
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
    Ensure the generated barcode is scanner-friendly while maintaining quality.
    Preserves barcode structure and quiet zones for reliable scanning.
    """
    if not PIL_AVAILABLE:
        source_buffer.seek(0)
        return source_buffer
    
    source_buffer.seek(0)
    image = Image.open(source_buffer)
    
    # Preserve original mode if already grayscale or 1-bit
    if image.mode == '1':
        # Already 1-bit, just ensure DPI is set
        processed_buffer = io.BytesIO()
        image.save(processed_buffer, format='PNG', dpi=(dpi, dpi), optimize=False)
        processed_buffer.seek(0)
        return processed_buffer
    
    # Convert to grayscale for better scanning
    if image.mode != 'L':
        image = image.convert('L')
    
    # Use a more conservative threshold to preserve barcode structure
    # Lower threshold (128) ensures bars remain distinct but not too harsh
    image = image.point(lambda x: 0 if x < 128 else 255, '1')
    
    # Ensure minimum width for scanner readability (but don't over-scale)
    target_min_width = 600
    if image.width < target_min_width:
        scale_factor = max(1, int(target_min_width / max(1, image.width)))
        if scale_factor > 1 and scale_factor <= 3:  # Limit scaling to avoid distortion
            image = image.resize(
                (image.width * scale_factor, image.height * scale_factor),
                resample=Image.NEAREST
            )
    
    processed_buffer = io.BytesIO()
    # Save without optimization to preserve barcode structure
    image.save(processed_buffer, format='PNG', dpi=(dpi, dpi), optimize=False)
    processed_buffer.seek(0)
    return processed_buffer


def _get_barcode_writer_options() -> dict:
    """
    Centralized writer options tuned for scanner readability and Zebra thermal label printers.
    Optimized for high-quality output at 600 DPI with proper quiet zones and bar dimensions.
    """
    return {
        'module_width': 0.5,        # Slightly wider modules for high-DPI Zebra printing
        'module_height': 25.0,      # Taller bars for better scanning on 1.25" labels
        'quiet_zone': 12.0,         # Increased quiet zone for reliable scanning (minimum 10x module width)
        'font_size': 20,            # Text size for SKU below barcode (bold)
        'text_distance': 10.0,       # Minimal gap between barcode and SKU text
        'dpi': 600,                 # High DPI for Zebra thermal printer quality
        'background': 'white',
        'foreground': 'black',
        'write_text': True,
    }


def _load_font_with_fallback(font_size: int):
    """
    Attempt to load a bold font with comprehensive fallback options.
    Prioritizes bold fonts, then falls back to regular fonts.
    Tries multiple common font paths across different operating systems.
    Returns a font object that will work even if no system fonts are found.
    """
    # Prioritize bold fonts first, then regular fonts
    font_paths = [
        # Windows paths - Bold first
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/ARIALBD.TTF",
        "C:/Windows/Fonts/arialbi.ttf",  # Arial Bold Italic
        "C:/Windows/Fonts/calibrib.ttf",  # Calibri Bold
        "C:/Windows/Fonts/calibriz.ttf",  # Calibri Bold Italic
        "C:/Windows/Fonts/timesbd.ttf",   # Times Bold
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/ARIAL.TTF",
        "arialbd.ttf",
        "arial.ttf",
        
        # Linux common paths - Bold first (Debian/Ubuntu)
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/ttf-dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/ttf-dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        
        # Linux alternative paths - Bold first (CentOS/RHEL)
        "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
        
        # macOS paths - Bold first
        "/System/Library/Fonts/Helvetica-Bold.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        
        # Additional Linux paths - Bold first
        "/usr/local/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/usr/local/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/local/share/fonts/liberation/LiberationSans-Regular.ttf",
        "/usr/local/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    
    # Try each font path
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, font_size)
        except Exception:
            continue
    
    # If no font file found, try to use fontconfig to find a bold font (Linux)
    try:
        import subprocess
        # Try bold first
        result = subprocess.run(
            ['fc-match', '-f', '%{file}', 'sans-serif:style=bold'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            font_file = result.stdout.strip()
            if os.path.exists(font_file):
                return ImageFont.truetype(font_file, font_size)
        
        # Fallback to regular if bold not found
        result = subprocess.run(
            ['fc-match', '-f', '%{file}', 'sans-serif'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            font_file = result.stdout.strip()
            if os.path.exists(font_file):
                return ImageFont.truetype(font_file, font_size)
    except Exception:
        pass
    
    # Last resort: try to load any available truetype font (prioritize bold)
    try:
        # Try to find fonts in common system directories
        common_dirs = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            "/System/Library/Fonts",
            "C:/Windows/Fonts",
        ]
        
        # First pass: look for bold fonts
        bold_keywords = ['bold', 'bd', 'black', 'heavy', 'semibold', 'demibold']
        for font_dir in common_dirs:
            if os.path.exists(font_dir):
                for root, dirs, files in os.walk(font_dir):
                    for file in files:
                        if file.lower().endswith(('.ttf', '.otf', '.ttc')):
                            file_lower = file.lower()
                            if any(keyword in file_lower for keyword in bold_keywords):
                                try:
                                    font_path = os.path.join(root, file)
                                    return ImageFont.truetype(font_path, font_size)
                                except Exception:
                                    continue
        
        # Second pass: any font if bold not found
        for font_dir in common_dirs:
            if os.path.exists(font_dir):
                for root, dirs, files in os.walk(font_dir):
                    for file in files:
                        if file.lower().endswith(('.ttf', '.otf', '.ttc')):
                            try:
                                font_path = os.path.join(root, file)
                                return ImageFont.truetype(font_path, font_size)
                            except Exception:
                                continue
    except Exception:
        pass
    
    # Final fallback: use default font but we'll handle scaling differently
    # The default font is bitmap and doesn't scale, so we'll create a larger image
    return ImageFont.load_default()


def _add_price_text_to_buffer(buffer: io.BytesIO, price_text: Optional[str]) -> io.BytesIO:
    """
    Add price text at the top of the barcode image.
    Always writes the price text, even if the image already contains price information.
    Moves barcode down to provide space for price while preserving scanability.
    Returns a new buffer with the modified image; falls back to the original buffer on errors.
    """
    if not price_text:
        return buffer

    try:
        buffer.seek(0)
        # Keep barcode in its original mode (1-bit or L) for better scanning
        img = Image.open(buffer)
        original_mode = img.mode
        
        # If the image already has price text at the top (indicated by extra height),
        # we need to extract just the barcode portion to avoid duplicating the price.
        # This ensures we always write a fresh price, even if the image already contains one.
        
        # Padding constants for price text area
        top_padding = 150  # Increased padding for larger, bold font
        price_barcode_gap = 35  # Gap between price text and barcode
        bottom_padding = 100  # Padding at bottom for better scanning
        
        # Estimate if image already has price text (heuristic: if height > 400px, likely has price)
        # Extract just the barcode portion by cropping from the expected barcode start position
        if img.height > 400:  # Heuristic: barcode with price is typically taller
            # Try to extract the barcode portion (skip the top padding area)
            # This is a conservative approach - we'll crop from where barcode likely starts
            barcode_start_y = top_padding + price_barcode_gap
            if barcode_start_y < img.height:
                # Crop to get just the barcode portion (removes any existing price text)
                img = img.crop((0, barcode_start_y, img.width, img.height))
        
        # Convert to RGB only for drawing text, but preserve barcode quality
        if img.mode == '1':
            # Convert 1-bit to RGB for text overlay, but keep barcode crisp
            img_rgb = img.convert("RGB")
        else:
            img_rgb = img.convert("RGB")
        
        new_width = img.width
        new_height = img.height + top_padding + price_barcode_gap + bottom_padding
        new_img = Image.new("RGB", (new_width, new_height), "white")
        
        # Paste the barcode image below the price text area with additional gap
        # This preserves the barcode's original quality and adds clear separation
        new_img.paste(img_rgb, (0, top_padding + price_barcode_gap))

        draw = ImageDraw.Draw(new_img)
        # Font size for price text - large and bold for visibility
        font_size = 120  # Large, bold font for clear price display
        
        # Load font with comprehensive fallback (prioritizes bold fonts)
        font = _load_font_with_fallback(font_size)
        
        # Check if we're using the default font (bitmap font)
        is_default_font = isinstance(font, ImageFont.ImageFont) and not hasattr(font, 'path')
        
        if is_default_font:
            # Default font is very small (bitmap font ~8-10px), so we need to scale it up
            # Get actual text size first
            try:
                bbox = draw.textbbox((0, 0), price_text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                text_width, text_height = draw.textsize(price_text, font=font)
            
            # Calculate scale factor to achieve desired font size
            # Default font height is typically 8-10px, we want ~120px
            if text_height > 0:
                scale_factor = font_size / text_height
            else:
                scale_factor = 15  # Default scale if height is 0
            
            # Limit scale factor to reasonable range
            scale_factor = max(10, min(20, scale_factor))
            
            # Render text on a temporary canvas
            temp_width = int(text_width * scale_factor * 1.2)  # Extra padding
            temp_height = int(text_height * scale_factor * 1.2)
            temp_img = Image.new("RGB", (temp_width, temp_height), "white")
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((0, 0), price_text, fill="black", font=font)
            
            # Scale up the text image using nearest neighbor to keep it crisp
            scaled_width = int(text_width * scale_factor)
            scaled_height = int(text_height * scale_factor)
            scaled_img = temp_img.resize(
                (scaled_width, scaled_height),
                resample=Image.NEAREST
            )
            
            # Paste scaled text onto main image, centered
            x = (new_width - scaled_width) // 2
            y = (top_padding - scaled_height) // 2
            new_img.paste(scaled_img, (x, y))
        else:
            # Use the loaded TrueType font normally
            # Get text dimensions (using textbbox for newer PIL versions, fallback to textsize)
            try:
                bbox = draw.textbbox((0, 0), price_text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                # Fallback for older PIL versions
                text_width, text_height = draw.textsize(price_text, font=font)
            
            # Center the text horizontally at the top with some margin
            x = (new_width - text_width) // 2
            y = (top_padding - text_height) // 2  # Center vertically in top padding area
            draw.text((x, y), price_text, fill="black", font=font)

        # Convert back to 1-bit if original was 1-bit, for better scanning
        new_buffer = io.BytesIO()
        if original_mode == '1':
            # Convert to 1-bit for optimal scanning
            final_img = new_img.convert('L').point(lambda x: 0 if x < 128 else 255, '1')
            final_img.save(new_buffer, format='PNG', optimize=False)
        else:
            new_img.save(new_buffer, format='PNG', optimize=False)
        
        new_buffer.seek(0)
        return new_buffer
    except Exception as e:
        print(f"Error adding price text to barcode: {str(e)}")
        return buffer


def generate_barcode_image(barcode_value: str, price_text: Optional[str] = None) -> Optional[InMemoryUploadedFile]:
    """
    Generate a barcode image from a barcode value using CODE128 format.
    Uses python-barcode library to create barcode images.
    Always writes the price text if provided, overwriting any existing price on the image.
    
    Args:
        barcode_value: The value to encode in the barcode (e.g., product SKU)
        price_text: Optional price text to display at the top of the barcode (e.g., "USD $10.00")
        
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
        processed_buffer = _add_price_text_to_buffer(processed_buffer, price_text)
        
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


def generate_barcode_image_file(barcode_value: str, output_path: str = None, price_text: Optional[str] = None) -> Optional[str]:
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
                
                # Add price text if provided
                if price_text:
                    buffer = io.BytesIO()
                    image.save(buffer, format='PNG', dpi=(writer_options['dpi'], writer_options['dpi']), optimize=True)
                    buffer = _add_price_text_to_buffer(buffer, price_text)
                    with open(saved_path, 'wb') as f:
                        f.write(buffer.getbuffer())
                else:
                    image.save(saved_path, format='PNG', dpi=(writer_options['dpi'], writer_options['dpi']), optimize=True)
        
        return saved_path
        
    except Exception as e:
        print(f"Error generating barcode image file: {str(e)}")
        return None
