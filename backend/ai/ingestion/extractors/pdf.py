"""PDF text extraction."""

import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class PageContent:
    """Content from a single page."""
    page_num: int
    text: str
    images: List[bytes] = None  # Raw image bytes if extracted


@dataclass
class ExtractedDocument:
    """Extracted content from a document."""
    filename: str
    pages: List[PageContent]
    metadata: Dict[str, Any] = None
    
    @property
    def full_text(self) -> str:
        """Get all text combined."""
        return "\n\n".join(p.text for p in self.pages if p.text)
    
    @property
    def page_count(self) -> int:
        return len(self.pages)


class PDFExtractor:
    """
    Extract text from PDF files.
    
    Usage:
        extractor = PDFExtractor()
        doc = extractor.extract("document.pdf")
        
        for page in doc.pages:
            print(f"Page {page.page_num}: {page.text[:100]}...")
    """
    
    def __init__(self, extract_images: bool = False):
        """
        Args:
            extract_images: Whether to extract images for OCR
        """
        self.extract_images = extract_images
    
    def extract(self, file_path: str) -> ExtractedDocument:
        """
        Extract text from PDF.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            ExtractedDocument with pages and text
        """
        import fitz  # PyMuPDF
        
        doc = fitz.open(file_path)
        pages = []
        
        try:
            for page_num, page in enumerate(doc, start=1):
                # Extract text
                text = page.get_text("text")
                
                # Optionally extract images
                images = None
                if self.extract_images:
                    images = self._extract_page_images(page)
                
                pages.append(PageContent(
                    page_num=page_num,
                    text=text.strip(),
                    images=images,
                ))
        finally:
            doc.close()
        
        return ExtractedDocument(
            filename=os.path.basename(file_path),
            pages=pages,
            metadata={
                "source_path": file_path,
                "page_count": len(pages),
            },
        )
    
    def _extract_page_images(self, page) -> List[bytes]:
        """Extract images from a page."""
        images = []
        
        for img_index, img in enumerate(page.get_images()):
            try:
                xref = img[0]
                base_image = page.parent.extract_image(xref)
                images.append(base_image["image"])
            except:
                continue
        
        return images
    
    def extract_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ExtractedDocument:
        """Extract from PDF bytes (for uploads)."""
        import fitz
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        
        try:
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")
                
                images = None
                if self.extract_images:
                    images = self._extract_page_images(page)
                
                pages.append(PageContent(
                    page_num=page_num,
                    text=text.strip(),
                    images=images,
                ))
        finally:
            doc.close()
        
        return ExtractedDocument(
            filename=filename,
            pages=pages,
            metadata={"page_count": len(pages)},
        )


class ImageExtractor:
    """Extract text from images using OCR."""
    
    def __init__(self, ocr_provider: str = "local"):
        """
        Args:
            ocr_provider: "local" (easyocr/tesseract) or "google" (Cloud Vision)
        """
        self.ocr_provider = ocr_provider
        self._ocr_reader = None
    
    def _get_local_ocr(self):
        """Get local OCR reader (lazy load)."""
        if self._ocr_reader is None:
            try:
                import easyocr
                self._ocr_reader = easyocr.Reader(['en', 'fr', 'de', 'es', 'it'])
            except ImportError:
                raise ImportError("Install easyocr: pip install easyocr")
        return self._ocr_reader
    
    def extract(self, image_path: str) -> str:
        """Extract text from image file."""
        if self.ocr_provider == "google":
            return self._extract_google(image_path)
        else:
            return self._extract_local(image_path)
    
    def extract_bytes(self, image_bytes: bytes) -> str:
        """Extract text from image bytes."""
        if self.ocr_provider == "google":
            return self._extract_google_bytes(image_bytes)
        else:
            return self._extract_local_bytes(image_bytes)
    
    def _extract_local(self, image_path: str) -> str:
        """Extract using local OCR."""
        reader = self._get_local_ocr()
        results = reader.readtext(image_path)
        return "\n".join(text for _, text, _ in results)
    
    def _extract_local_bytes(self, image_bytes: bytes) -> str:
        """Extract from bytes using local OCR."""
        import numpy as np
        from PIL import Image
        import io
        
        image = Image.open(io.BytesIO(image_bytes))
        image_np = np.array(image)
        
        reader = self._get_local_ocr()
        results = reader.readtext(image_np)
        return "\n".join(text for _, text, _ in results)
    
    def _extract_google(self, image_path: str) -> str:
        """Extract using Google Cloud Vision."""
        from google.cloud import vision
        
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as f:
            content = f.read()
        
        image = vision.Image(content=content)
        response = client.text_detection(image=image)
        
        if response.text_annotations:
            return response.text_annotations[0].description
        return ""
    
    def _extract_google_bytes(self, image_bytes: bytes) -> str:
        """Extract from bytes using Google Cloud Vision."""
        from google.cloud import vision
        
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.text_detection(image=image)
        
        if response.text_annotations:
            return response.text_annotations[0].description
        return ""
