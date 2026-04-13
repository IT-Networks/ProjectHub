"""
Document chunker — merges small sections and splits large ones into LLM-friendly chunks.
"""
import logging
from dataclasses import dataclass, field
from services.doc_parser import DocSection

logger = logging.getLogger("projecthub.doc_chunker")

# Target chunk size in characters
MIN_CHUNK_CHARS = 300
MAX_CHUNK_CHARS = 4000
IDEAL_CHUNK_CHARS = 2000


@dataclass
class DocChunk:
    heading_path: str
    text: str
    tables: list[str] = field(default_factory=list)
    has_diagrams: bool = False
    source_headings: list[str] = field(default_factory=list)
    chunk_index: int = 0  # Position within document

    @property
    def full_text(self) -> str:
        """Combined text + tables for LLM input."""
        parts = [self.text]
        for t in self.tables:
            parts.append(f"\n[Tabelle]\n{t}")
        return "\n".join(parts)

    @property
    def char_count(self) -> int:
        return len(self.full_text)


def chunk_sections(sections: list[DocSection]) -> list[DocChunk]:
    """Convert document sections into optimally-sized chunks for LLM processing."""
    if not sections:
        return []

    chunks: list[DocChunk] = []
    buffer_text_parts: list[str] = []
    buffer_tables: list[str] = []
    buffer_headings: list[str] = []
    buffer_path = ""
    buffer_has_images = False
    buffer_chars = 0

    def _flush_buffer():
        nonlocal buffer_text_parts, buffer_tables, buffer_headings, buffer_path, buffer_has_images, buffer_chars
        if not buffer_text_parts and not buffer_tables:
            return

        text = "\n\n".join(buffer_text_parts)
        chunk = DocChunk(
            heading_path=buffer_path,
            text=text,
            tables=buffer_tables[:],
            has_diagrams=buffer_has_images,
            source_headings=buffer_headings[:],
            chunk_index=len(chunks),
        )
        chunks.append(chunk)

        buffer_text_parts = []
        buffer_tables = []
        buffer_headings = []
        buffer_path = ""
        buffer_has_images = False
        buffer_chars = 0

    for section in sections:
        section_text = section.text
        section_chars = section.char_count

        # If section is too large, split it
        if section_chars > MAX_CHUNK_CHARS:
            _flush_buffer()
            sub_chunks = _split_large_section(section)
            chunks.extend(sub_chunks)
            # Update chunk indices
            for i, sc in enumerate(sub_chunks):
                sc.chunk_index = len(chunks) - len(sub_chunks) + i
            continue

        # If adding this section would exceed max, flush first
        if buffer_chars + section_chars > MAX_CHUNK_CHARS and buffer_chars > 0:
            _flush_buffer()

        # Add to buffer
        if section_text:
            buffer_text_parts.append(f"### {section.heading}\n{section_text}")
        buffer_tables.extend(section.tables)
        buffer_headings.append(section.heading)
        if not buffer_path:
            buffer_path = section.heading_path
        buffer_has_images = buffer_has_images or section.has_images
        buffer_chars += section_chars

        # If buffer is at ideal size, flush
        if buffer_chars >= IDEAL_CHUNK_CHARS:
            _flush_buffer()

    # Flush remaining
    _flush_buffer()

    # Post-process: merge tiny chunks with neighbors
    merged = _merge_small_chunks(chunks)

    logger.info("Chunked %d sections into %d chunks", len(sections), len(merged))
    return merged


def _split_large_section(section: DocSection) -> list[DocChunk]:
    """Split a large section into multiple chunks at paragraph boundaries."""
    paragraphs = section.text.split("\n")
    chunks: list[DocChunk] = []
    current_parts: list[str] = []
    current_chars = 0

    for para in paragraphs:
        para_len = len(para)

        if current_chars + para_len > MAX_CHUNK_CHARS and current_parts:
            chunks.append(DocChunk(
                heading_path=section.heading_path,
                text="\n".join(current_parts),
                has_diagrams=section.has_images and len(chunks) == 0,
                source_headings=[section.heading],
            ))
            current_parts = []
            current_chars = 0

        current_parts.append(para)
        current_chars += para_len

    # Remainder
    if current_parts:
        chunk = DocChunk(
            heading_path=section.heading_path,
            text="\n".join(current_parts),
            tables=section.tables if not chunks else [],  # Tables go to last chunk
            has_diagrams=section.has_images and len(chunks) == 0,
            source_headings=[section.heading],
        )
        chunks.append(chunk)

    return chunks


def _merge_small_chunks(chunks: list[DocChunk]) -> list[DocChunk]:
    """Merge chunks that are too small with their neighbors."""
    if len(chunks) <= 1:
        return chunks

    merged: list[DocChunk] = []
    i = 0

    while i < len(chunks):
        chunk = chunks[i]

        # If this chunk is too small and there's a next chunk, merge
        if chunk.char_count < MIN_CHUNK_CHARS and i + 1 < len(chunks):
            next_chunk = chunks[i + 1]
            combined = DocChunk(
                heading_path=chunk.heading_path,
                text=chunk.text + "\n\n" + next_chunk.text,
                tables=chunk.tables + next_chunk.tables,
                has_diagrams=chunk.has_diagrams or next_chunk.has_diagrams,
                source_headings=chunk.source_headings + next_chunk.source_headings,
                chunk_index=len(merged),
            )
            merged.append(combined)
            i += 2
        else:
            chunk.chunk_index = len(merged)
            merged.append(chunk)
            i += 1

    return merged
