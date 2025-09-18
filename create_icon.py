#!/usr/bin/env python3
"""
Create a custom icon for the ZIP File Viewer application.
This script generates a .ico file with multiple resolutions.
"""

import os
from PIL import Image, ImageDraw, ImageFont

def create_icon_image(size):
    """Create an icon image of the specified size."""
    # Create a new image with transparent background
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Colors
    zip_color = (255, 193, 7)  # Golden yellow for ZIP
    folder_color = (52, 152, 219)  # Blue for folder
    text_color = (44, 62, 80)  # Dark blue-gray for text
    border_color = (52, 73, 94)  # Darker border
    
    # Calculate dimensions based on size
    margin = size // 8
    folder_width = size - 2 * margin
    folder_height = size - 2 * margin
    
    # Draw folder background
    folder_rect = [margin, margin + size//6, margin + folder_width, margin + folder_height]
    draw.rounded_rectangle(folder_rect, radius=size//20, fill=folder_color, outline=border_color, width=2)
    
    # Draw folder tab
    tab_width = folder_width // 3
    tab_height = size // 6
    tab_rect = [margin, margin, margin + tab_width, margin + tab_height]
    draw.rounded_rectangle(tab_rect, radius=size//30, fill=folder_color, outline=border_color, width=2)
    
    # Draw ZIP file representation (smaller rectangle inside)
    zip_margin = margin + size // 8
    zip_width = folder_width - size // 4
    zip_height = folder_height // 2
    zip_rect = [zip_margin, margin + size//3, zip_margin + zip_width, margin + size//3 + zip_height]
    draw.rounded_rectangle(zip_rect, radius=size//40, fill=zip_color, outline=border_color, width=1)
    
    # Try to add text if size is large enough
    if size >= 32:
        try:
            # Try to use a system font
            font_size = max(size // 8, 8)
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except OSError:
                try:
                    font = ImageFont.truetype("calibri.ttf", font_size)
                except OSError:
                    font = ImageFont.load_default()
            
            # Add "ZIP" text
            text = "ZIP"
            # Get text bounding box
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            # Center text in the ZIP rectangle
            text_x = zip_margin + (zip_width - text_width) // 2
            text_y = margin + size//3 + (zip_height - text_height) // 2
            
            draw.text((text_x, text_y), text, fill=text_color, font=font)
            
        except Exception:
            # If font loading fails, draw simple shapes instead
            # Draw small circles to represent files
            circle_size = size // 20
            for i in range(3):
                cx = zip_margin + (i + 1) * zip_width // 4
                cy = margin + size//3 + zip_height // 2
                draw.ellipse([cx - circle_size, cy - circle_size, 
                            cx + circle_size, cy + circle_size], 
                           fill=text_color)
    
    return img

def create_ico_file(output_path):
    """Create a .ico file with multiple resolutions."""
    print(f"Creating icon file: {output_path}")
    
    # Common icon sizes for Windows
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    
    for size in sizes:
        print(f"  Generating {size}x{size} icon...")
        img = create_icon_image(size)
        images.append(img)
    
    # Save as ICO file
    images[0].save(
        output_path,
        format='ICO',
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:]
    )
    
    print(f"✅ Icon created successfully: {output_path}")
    return output_path

def main():
    """Main function to create the icon."""
    print("🎨 Creating ZIP File Viewer Icon")
    print("=" * 40)

    # Create the icon
    icon_path = "zip-browser-icon.ico"
    create_ico_file(icon_path)

    # Also create a PNG version for preview
    png_path = "zip-browser-icon.png"
    preview_img = create_icon_image(256)
    preview_img.save(png_path, format='PNG')
    print(f"📷 Preview PNG created: {png_path}")

    print("\n🎉 Icon creation completed!")
    print(f"📁 Icon file: {os.path.abspath(icon_path)}")
    print(f"👁️  Preview: {os.path.abspath(png_path)}")

if __name__ == "__main__":
    main()
