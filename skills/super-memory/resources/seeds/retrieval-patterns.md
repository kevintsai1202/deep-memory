# Knowledge Retrieval Strategy & RAG Patterns

> **Sources:** LangChain RAG best practices, Cohere Reranker docs, ChromaDB official guide, BAAI/bge model paper

---

## 🔧 Basic RAG Flow & Common Pitfalls

**Date:** 2025-01-01 (seed knowledge)
**Context:** Understanding the full pipeline when building a semantic retrieval system
**Best Practices:**

```
Query input → Embedding → Vector store retrieval → Candidate results
    → Reranker reranking → Take Top-K → Inject into context → Generate answer
```

**Common failure modes:**
- ❌ **Top-K too large**: Injecting too many results dilutes the important content (recommended K=3~5)
- ❌ **No reranker**: Vector similarity ≠ semantic relevance, which makes it easy to pull in irrelevant results
- ❌ **Chunks too short**: Chunks that are too small lack sufficient context; aim for 512~1024 tokens
- ❌ **Chunks too long**: Chunks that are too large dilute the vector representation, reducing semantic precision

---

## 🔧 Hybrid Search Architecture

**Date:** 2025-01-01 (seed knowledge)
**Context:** Needing both precise keyword matching and semantic relevance at the same time
**Best Practices:**

Hybrid search = fusing **keyword search (BM25) + vector search (Dense)** results

| Method | Strengths | Weaknesses |
|---|---|---|
| BM25 (keyword) | Exact term hits, fast | Can't understand synonyms or cross-language queries |
| Dense (vector) | Semantic understanding, cross-language | May lose precision on rare terms/jargon |
| **Hybrid** | Combines the strengths of both | Requires tuning a fusion weight (alpha/α) |

**Alpha parameter guidance:**
- `alpha=0.5`: Balanced fusion (default, suited to general scenarios)
- `alpha=0.7`: Favors vector search (suited to open-ended semantic questions)
- `alpha=0.3`: Favors keyword search (suited to code/exact-term queries)

---

## 🔧 Reranker Usage Principles (BGE-Reranker)

**Date:** 2025-01-01 (seed knowledge)
**Context:** Refining initial retrieval results to improve the quality of the final answer
**Best Practices:**
- **Two-stage strategy**: Stage one uses vector search to get the Top-20, stage two uses the reranker to refine down to the Top-3
- **Threshold filtering**: Discard results with `rerank_score < 0.35`, treating them as having no relevant content
- **BGE-Reranker-base model**: Well-suited to mixed Traditional Chinese/English corpora, with fast inference
- **Avoid empty injection**: If all results fall below the threshold, tell the user directly that "no relevant records were found" instead of forcing in low-scoring content

**super-memory configuration:**
```bash
# Use hybrid search + reranker, taking the Top-3 results
# Windows:        .venv\Scripts\python skills/chroma-hybrid-search/scripts/search.py --query "question" --limit 3
# Linux / macOS:  .venv/bin/python   skills/chroma-hybrid-search/scripts/search.py --query "question" --limit 3
# Only adopt results where rerank_score > 0.35
```

---

## 🔧 Multi-Vector Indexing Strategy

**Date:** 2025-01-01 (seed knowledge)
**Context:** Improving retrieval precision when knowledge-base documents are long
**Best Practices:**

For the same document, you can build multiple vector representations at once:
1. **Summary vector**: Represents the document's overall topic, used for coarse filtering
2. **Chunk vector**: Represents specific passages, used for precise matching
3. **Hypothetical Question vector**: Pre-generates "what questions can this document answer," improving recall

**When to use:** Consider a multi-vector strategy once a single `.md` file exceeds 50KB

---

## 🔧 6 RAG Failure Modes & Countermeasures

**Date:** 2025-01-01 (seed knowledge)
**Context:** Diagnosing why a RAG system is underperforming
**Best Practices:**

| Failure Mode | Symptom | Countermeasure |
|---|---|---|
| Missed recall | Data that exists can't be found | Increase Top-K, switch to hybrid search |
| Noisy recall | Irrelevant content gets retrieved | Add a reranker filter, lower alpha |
| Context dilution | The answer gets buried under a flood of irrelevant content | Reduce Top-K, raise the rerank threshold |
| Knowledge conflict | The knowledge base contains outdated/contradictory content | Clean up periodically, add date markers |
| Cross-language drift | Hit rate drops when mixing Chinese and English | Use a multilingual embedding model (e.g., multilingual-e5) |
| Query drift | The user's query is vague and the vector can't align precisely | HyDE (Hypothetical Document Embeddings) or query rewriting |
