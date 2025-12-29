"""
Document ingestion - PDF extraction, OCR, chunking.

Usage:
    from ai.documents.ingestion import extract_pdf, extract_image, chunk_text
    
    # Extract text from PDF
    text = extract_pdf(pdf_bytes)
    
    # Extract text from image (OCR)
    text = extract_image(image_bytes)
    
    # Chunk text
    chunks = chunk_text(text, max_chars=800)
"""

import os
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class Page:
    """A document page."""
    page_num: int
    text: str


@dataclass
class ExtractedDocument:
    """Extracted document content."""
    filename: str
    pages: List[Page]
    
    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)
    
    @property
    def page_count(self) -> int:
        return len(self.pages)


def extract_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> ExtractedDocument:
    """
    Extract text from PDF.
    
    Args:
        pdf_bytes: PDF file bytes
        filename: Original filename
        
    Returns:
        ExtractedDocument with pages
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("Install PyMuPDF: pip install pymupdf")
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    
    try:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            pages.append(Page(page_num=page_num, text=text))
    finally:
        doc.close()
    
    return ExtractedDocument(filename=filename, pages=pages)


def extract_image(image_bytes: bytes, languages: List[str] = None) -> str:
    """
    Extract text from image using OCR.
    
    Args:
        image_bytes: Image file bytes
        languages: OCR languages (default: ['en', 'fr'])
        
    Returns:
        Extracted text
    """
    try:
        import easyocr
        import numpy as np
        from PIL import Image
        import io
    except ImportError:
        raise ImportError("Install easyocr: pip install easyocr pillow")
    
    languages = languages or ['en', 'fr']
    
    image = Image.open(io.BytesIO(image_bytes))
    image_np = np.array(image)
    
    reader = easyocr.Reader(languages)
    results = reader.readtext(image_np)
    
    return "\n".join(text for _, text, _ in results)


def extract_file(file_bytes: bytes, filename: str) -> str:
    """
    Extract text from file (auto-detect type).
    
    Args:
        file_bytes: File bytes
        filename: Filename (for type detection)
        
    Returns:
        Extracted text
    """
    ext = os.path.splitext(filename)[1].lower()
    
    if ext == ".pdf":
        doc = extract_pdf(file_bytes, filename)
        return doc.full_text
    
    elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"):
        return extract_image(file_bytes)
    
    elif ext in (".txt", ".md", ".csv", ".json", ".xml", ".html"):
        return file_bytes.decode("utf-8", errors="replace")
    
    else:
        # Try as text
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except:
            raise ValueError(f"Unsupported file type: {ext}")


def chunk_text(
    text: str,
    max_chars: int = 800,
    overlap_chars: int = 100,
) -> List[str]:
    """
    Split text into chunks.
    
    Tries to split on paragraph boundaries, falls back to word boundaries.
    
    Args:
        text: Text to chunk
        max_chars: Maximum chunk size
        overlap_chars: Overlap between chunks
        
    Returns:
        List of text chunks
    """
    if not text:
        return []
    
    # Split by paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    
    if not paragraphs:
        paragraphs = [text]
    
    chunks = []
    current = ""
    
    for para in paragraphs:
        # Would adding this paragraph exceed limit?
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip()
        else:
            # Save current chunk
            if current:
                chunks.append(current)
            
            # Handle long paragraphs
            if len(para) > max_chars:
                # Split by words
                words = para.split()
                current = ""
                
                for word in words:
                    if len(current) + len(word) + 1 <= max_chars:
                        current = f"{current} {word}".strip()
                    else:
                        if current:
                            chunks.append(current)
                        current = word
            else:
                current = para
    
    # Don't forget last chunk
    if current:
        chunks.append(current)
    
    return chunks


def chunk_with_metadata(
    text: str,
    max_chars: int = 800,
    source: str = None,
) -> List[dict]:
    """
    Chunk text with metadata.
    
    Args:
        text: Text to chunk
        max_chars: Maximum chunk size
        source: Source identifier
        
    Returns:
        List of {"content": str, "metadata": dict}
    """
    chunks = chunk_text(text, max_chars)
    
    return [
        {
            "content": chunk,
            "metadata": {
                "source": source,
                "chunk_index": i,
                "char_start": sum(len(c) for c in chunks[:i]),
            },
        }
        for i, chunk in enumerate(chunks)
    ]
