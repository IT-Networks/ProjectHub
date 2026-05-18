"""Retrieval layer for the Brain (P2, P3).

Pluggable components that knowledge.py + chat.py compose for the search /
context-build flow:

    contextual.py     T2.4 — generate one-sentence context snippets per
                              KnowledgeItem before FTS5 indexing
                              (Anthropic-Contextual-Retrieval pattern).
    hybrid.py         T2.7 — fuse FTS5 + cosine-on-embeddings via
                              Reciprocal-Rank-Fusion; returns Top-K
                              ranked items for the chat-context builder.
    reranker.py       T3.x — (planned) LLM-as-Judge cross-encoder for
                              the final reorder step before injection.

All three respect their respective master flags
(``brain_contextual_retrieval_enabled``, ``brain_embedding_enabled``,
``brain_reranker_enabled``) and degrade cleanly to FTS5-only when off.
"""
