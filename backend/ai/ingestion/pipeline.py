"""Document ingestion pipeline."""

import os
import hashlib
from typing import List, Dict, Any, Optional, Callable, Union
from dataclasses import dataclass, field

from .extractors.pdf import PDFExtractor, ImageExtractor, ExtractedDocument
from .chunkers.text import ChunkingStrategy, SentenceChunker, TokenChunker, Chunk


@dataclass
class IngestedDocument:
    """Result of ingestion pipeline."""
    file_id: str
    filename: str
    chunks: List[Dict[str, Any]]
    page_count: int
    chunk_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class IngestionPipeline:
    """
    Orchestrates document ingestion: extract → chunk → embed → prepare for storage.
    
    Usage with Embedder (recommended - ensures consistency):
        from embeddings import Embedder
        
        embedder = Embedder("bge-m3")
        
        pipeline = IngestionPipeline(embedder=embedder)
        
        result = pipeline.ingest_file(
            "document.pdf",
            metadata={"entity_id": "property_123"},
        )
        
    Legacy usage (still works):
        pipeline = IngestionPipeline(
            embed_fn=embed,
            chunker=SentenceChunker(max_chars=900),
        )
    """
    
    def __init__(
        self,
        embedder = None,  # Embedder instance (recommended)
        embed_fn: Callable[[List[str]], List[List[float]]] = None,
        chunker: ChunkingStrategy = None,
        pdf_extractor: PDFExtractor = None,
        image_extractor: ImageExtractor = None,
        classify_fn: Callable[[str], str] = None,
    ):
        """
        Args:
            embedder: Embedder instance (recommended for consistency)
            embed_fn: Function to embed text (legacy, use embedder instead)
            chunker: Chunking strategy (default: auto-configured based on embedder)
            pdf_extractor: PDF extractor (default: PDFExtractor)
            image_extractor: Image/OCR extractor (optional)
            classify_fn: Optional function to classify document type from filename
        """
        # Use embedder if provided
        if embedder is not None:
            self.embedder = embedder
            self.embed_fn = embedder.embed
            
            # Auto-configure chunker based on embedder's token limit
            if chunker is None:
                # Use ~80% of max tokens to leave room for special tokens
                max_tokens = int(embedder.max_tokens * 0.8)
                chunker = TokenChunker(
                    max_tokens=max_tokens,
                    overlap_tokens=50,
                    tokenizer=embedder.count_tokens,
                )
        else:
            self.embedder = None
            self.embed_fn = embed_fn
            
        if self.embed_fn is None:
            raise ValueError("Either embedder or embed_fn is required")
        
        self.chunker = chunker or SentenceChunker()
        self.pdf_extractor = pdf_extractor or PDFExtractor()
        self.image_extractor = image_extractor
        self.classify_fn = classify_fn or self._default_classify
    
    def _default_classify(self, filename: str) -> str:
        """Default document classification based on filename."""
        filename_lower = filename.lower()
        
        if "invoice" in filename_lower:
            return "invoice"
        elif "lease" in filename_lower or "contract" in filename_lower:
            return "lease"
        elif "gas" in filename_lower and "cert" in filename_lower:
            return "gas_certificate"
        elif "electric" in filename_lower:
            return "electrical_certificate"
        elif "inventory" in filename_lower:
            return "inventory"
        else:
            return "document"
    
    def _generate_file_id(self, file_path: str) -> str:
        """Generate unique file ID from path and modification time."""
        stat = os.stat(file_path)
        content = f"{file_path}:{stat.st_mtime}:{stat.st_size}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _generate_chunk_id(self, file_id: str, chunk_index: int) -> str:
        """Generate chunk ID."""
        return f"{file_id}_{chunk_index:04d}"
    
    def ingest_file(
        self,
        file_path: str,
        metadata: Dict[str, Any] = None,
        use_ocr: bool = False,
    ) -> IngestedDocument:
        """
        Ingest a file: extract, chunk, embed.
        
        Args:
            file_path: Path to file
            metadata: Additional metadata to attach to chunks
            use_ocr: Whether to use OCR for images in PDFs
            
        Returns:
            IngestedDocument with chunks ready for storage
        """
        metadata = metadata or {}
        filename = os.path.basename(file_path)
        file_id = self._generate_file_id(file_path)
        
        # Determine file type and extract
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == ".pdf":
            extracted = self.pdf_extractor.extract(file_path)
            
            # OCR images if requested
            if use_ocr and self.image_extractor:
                for page in extracted.pages:
                    if page.images:
                        ocr_text = []
                        for img_bytes in page.images:
                            try:
                                ocr_text.append(self.image_extractor.extract_bytes(img_bytes))
                            except:
                                pass
                        if ocr_text:
                            page.text += "\n\n" + "\n".join(ocr_text)
        
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
            if not self.image_extractor:
                raise ValueError("Image extractor required for image files")
            text = self.image_extractor.extract(file_path)
            extracted = ExtractedDocument(
                filename=filename,
                pages=[{"page_num": 1, "text": text}],
            )
        
        elif ext in (".txt", ".md"):
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            extracted = ExtractedDocument(
                filename=filename,
                pages=[{"page_num": 1, "text": text}],
            )
        
        else:
            raise ValueError(f"Unsupported file type: {ext}")
        
        # Chunk all pages
        all_chunks = []
        for page in extracted.pages:
            page_chunks = self.chunker.chunk(page.text, page_num=page.page_num)
            all_chunks.extend(page_chunks)
        
        if not all_chunks:
            return IngestedDocument(
                file_id=file_id,
                filename=filename,
                chunks=[],
                page_count=extracted.page_count,
                chunk_count=0,
                metadata=metadata,
            )
        
        # Extract texts for batch embedding
        chunk_texts = [c.text for c in all_chunks]
        
        # Batch embed
        embeddings = self.embed_fn(chunk_texts)
        
        # Build final chunks with all metadata
        doc_type = self.classify_fn(filename)
        
        final_chunks = []
        for i, (chunk, embedding) in enumerate(zip(all_chunks, embeddings)):
            chunk_id = self._generate_chunk_id(file_id, i)
            
            final_chunks.append({
                "id": chunk_id,
                "content": chunk.text,
                "embedding": embedding,
                "metadata": {
                    **metadata,
                    "file_id": file_id,
                    "filename": filename,
                    "doc_type": doc_type,
                    "page_num": chunk.page_num,
                    "chunk_index": i,
                },
            })
        
        return IngestedDocument(
            file_id=file_id,
            filename=filename,
            chunks=final_chunks,
            page_count=extracted.page_count,
            chunk_count=len(final_chunks),
            metadata=metadata,
        )
    
    def ingest_bytes(
        self,
        file_bytes: bytes,
        filename: str,
        metadata: Dict[str, Any] = None,
    ) -> IngestedDocument:
        """Ingest from bytes (for file uploads)."""
        import tempfile
        
        ext = os.path.splitext(filename)[1]
        
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(file_bytes)
            temp_path = f.name
        
        try:
            return self.ingest_file(temp_path, metadata)
        finally:
            os.unlink(temp_path)
    
    def ingest_text(
        self,
        text: str,
        doc_id: str,
        metadata: Dict[str, Any] = None,
    ) -> IngestedDocument:
        """Ingest raw text directly."""
        metadata = metadata or {}
        
        # Chunk
        chunks = self.chunker.chunk(text)
        
        if not chunks:
            return IngestedDocument(
                file_id=doc_id,
                filename=f"{doc_id}.txt",
                chunks=[],
                page_count=1,
                chunk_count=0,
                metadata=metadata,
            )
        
        # Embed
        chunk_texts = [c.text for c in chunks]
        embeddings = self.embed_fn(chunk_texts)
        
        # Build chunks
        final_chunks = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{doc_id}_{i:04d}"
            
            final_chunks.append({
                "id": chunk_id,
                "content": chunk.text,
                "embedding": embedding,
                "metadata": {
                    **metadata,
                    "file_id": doc_id,
                    "chunk_index": i,
                },
            })
        
        return IngestedDocument(
            file_id=doc_id,
            filename=f"{doc_id}.txt",
            chunks=final_chunks,
            page_count=1,
            chunk_count=len(final_chunks),
            metadata=metadata,
        )
