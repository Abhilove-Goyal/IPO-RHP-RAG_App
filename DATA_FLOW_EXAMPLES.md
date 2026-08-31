# Data Flow Examples - Stage by Stage

This document shows exact data formats flowing through each pipeline stage.

## Upload Pipeline

### **Stage: PDF Ingestion**

**Input**: PDF file

```
sample_drhp.pdf (50 MB)
```

**Process**:
1. Open with pdfplumber
2. Extract table of contents
3. Parse pages, detect sections
4. Semantic chunking (400-600 tokens)
5. Generate embeddings
6. Store to Supabase

**Output after TOC extraction**:

```python
toc = [
    {
        "section_number": "I",
        "section_name": "Cover Page",
        "start_page": 1,
        "end_page": 5,
        "subsections": []
    },
    {
        "section_number": "II",
        "section_name": "Risk Factors",
        "start_page": 33,
        "end_page": 120,
        "subsections": [
            {
                "section_name": "Market Risk",
                "start_page": 33,
                "end_page": 50
            },
            {
                "section_name": "Regulatory Risk",
                "start_page": 51,
                "end_page": 72
            }
        ]
    },
    {
        "section_number": "III",
        "section_name": "Business",
        "start_page": 121,
        "end_page": 180,
        "subsections": []
    }
]
```

**Stored to Supabase**: Chunks table

```python
[
    {
        "id": "uuid-001",
        "ipo_id": "sample_drhp",
        "chunk_text": "The following are the principal factors that may materially affect our business...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 487,
        "embedding": [0.127, -0.034, 0.891, ...],  # 1024-dim for BAAI/bge-base
        "created_at": "2024-01-15T10:23:45"
    },
    {
        "id": "uuid-002",
        "ipo_id": "sample_drhp",
        "chunk_text": "Market volatility could impact our IPO pricing...",
        "page_number": 40,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 521,
        "embedding": [0.145, -0.028, 0.876, ...],
        "created_at": "2024-01-15T10:23:50"
    },
    # ... more chunks
]
```

---

## Query Pipeline - Detailed Data Flow

### **STAGE 1: Section Retrieval**

**Input**: Query string

```python
query = "What are the main risk factors facing this IPO?"
ipo_id = "sample_drhp"
sections = [
    {
        "section_name": "Risk Factors",
        "start_page": 33,
        "end_page": 120
    },
    {
        "section_name": "Business",
        "start_page": 121,
        "end_page": 180
    },
    {
        "section_name": "Financial Results",
        "start_page": 181,
        "end_page": 250
    }
]
```

**Process**: Keyword-based section scoring

```python
# Pseudocode
keywords = extract_keywords(query)  # ["risk", "factors", "ipo"]
for section in sections:
    score = 0
    section_name_lower = section["section_name"].lower()
    for keyword in keywords:
        if keyword in section_name_lower:
            score += 2.0
    # Also check subsections
    for subsection in section.get("subsections", []):
        sub_name_lower = subsection["section_name"].lower()
        for keyword in keywords:
            if keyword in sub_name_lower:
                score += 1.0
    section["score"] = score

# Scores:
# Risk Factors: 2.0 (keyword "risk")
# Business: 0.0
# Financial Results: 0.0
```

**Output**: Top 3 sections

```python
top_sections = [
    {
        "section_name": "Risk Factors",
        "start_page": 33,
        "end_page": 120,
        "score": 2.0,
        "pages": [33, 34, 35, ..., 120]
    }
]
```

**Logs**:
```
[MAIN] STAGE 1: Section-level retrieval
[SECTION_RETRIEVAL] Scoring sections for query: "What are..."
[SECTION_RETRIEVAL] Risk Factors: score 2.0 (keyword: risk)
[SECTION_RETRIEVAL] Selected top 3 sections: ['Risk Factors']
[MAIN] Stage 1 complete: 1 section selected
```

---

### **STAGE 2: Chunk Retrieval (Within Sections)**

**Input**: Query, section names, variations

```python
query = "What are the main risk factors facing this IPO?"
top_sections = ["Risk Factors"]
query_variations = [
    "What are the main risk factors?",
    "What risks could impact the IPO?",
    "Describe the risk profile"
]
ipo_id = "sample_drhp"
limit = 20
```

**Process**: For each query variation

```python
# Variation 1: "What are the main risk factors?"
embedding_1 = embed(query_variations[0])  # 1024-dim vector

# Supabase query:
results_1 = supabase.table("ipo_chunks") \
    .select("*") \
    .eq("ipo_id", "sample_drhp") \
    .in_("section", ["Risk Factors"]) \
    .limit(100) \
    .execute()

# Vector similarity search (Supabase pgvector)
# Find chunks with embedding closest to embedding_1
# Returns chunks sorted by similarity

# Similar for variations 2 and 3
```

**Output**: Retrieved & deduplicated chunks

```python
retrieved_chunks = [
    {
        "id": "uuid-001",
        "chunk_text": "The following are the principal factors that may materially...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 487,
        "similarity_score": 0.89,  # From embedding comparison
        "source": "variation_0"
    },
    {
        "id": "uuid-002",
        "chunk_text": "Market volatility could impact our IPO pricing significantly...",
        "page_number": 40,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 521,
        "similarity_score": 0.87,
        "source": "variation_1"
    },
    # ... 18 more chunks (deduped)
]
```

**Logs**:
```
[MAIN] STAGE 2: Chunk-level retrieval (within sections)
[MULTI_QUERY] Generated query variations:
  - Variation 0: "What are the main risk factors?"
  - Variation 1: "What risks could impact the IPO?"
  - Variation 2: "Describe the risk profile"
[RETRIEVER] Querying chunks in sections: ['Risk Factors']
[RETRIEVER] Variation 0: 23 results
[RETRIEVER] Variation 1: 19 results
[RETRIEVER] Variation 2: 21 results
[RETRIEVER] After deduplication: 20 unique chunks
[MAIN] Stage 2 complete: 20 chunks selected
```

---

### **STAGE 3: Cross-Encoder Reranking**

**Input**: Query, 20 chunks

```python
query = "What are the main risk factors?"
chunks_to_rank = [
    {
        "chunk_text": "The following are the principal factors...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf"
    },
    # ... 19 more chunks
]
```

**Process**: Cross-encoder scoring

```python
# For each chunk, create input pair:
# [query, chunk_text]

reranker = CrossEncoder("BAAI/bge-reranker-large")

# Scores (0-1 scale, higher = more relevant to query)
pair_list = [
    [query, chunk_0["chunk_text"]],
    [query, chunk_1["chunk_text"]],
    # ... 18 more pairs
]

scores = reranker.predict(pair_list)
# scores = [0.92, 0.88, 0.85, 0.81, 0.79, 0.75, 0.72, 0.71, 0.69, 0.68, ...]
```

**Output**: Top 5 reranked chunks

```python
final_chunks = [
    {
        "rank": 1,
        "chunk_text": "The following are the principal factors...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 487,
        "reranker_score": 0.92
    },
    {
        "rank": 2,
        "chunk_text": "Market volatility could impact our IPO...",
        "page_number": 40,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "chunk_tokens": 521,
        "reranker_score": 0.88
    },
    # ... 3 more chunks
]
```

**Logs**:
```
[MAIN] STAGE 3: Cross-encoder reranking
[RERANKER] Creating 20 query-chunk pairs...
[RERANKER] Scoring with BAAI/bge-reranker-large...
[RERANKER] Scores: [0.92, 0.88, 0.85, 0.81, 0.79, 0.75, 0.72, 0.71, 0.69, 0.68, ...]
[RERANKER] Selected top 5 chunks
[MAIN] Stage 3 complete: 5 chunks reranked
```

---

### **STAGE 4: Answer Generation**

**Input**: Query, 5 final chunks

```python
query = "What are the main risk factors?"
context_chunks = [
    {
        "chunk_text": "The following are the principal factors that may materially affect our business and the market price of our ordinary shares...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "reranker_score": 0.92
    },
    # ... 4 more chunks
]
```

**Process**: Prompt construction & LLM call

```python
# build_prompt constructs:
prompt = """
You are an expert financial analyst. Using the provided evidence, 
answer the following question concisely and accurately.

Evidence:
[1] [Section: Risk Factors | Page: 35 | Document: sample_drhp.pdf]
The following are the principal factors that may materially affect our business...

[2] [Section: Risk Factors | Page: 40 | Document: sample_drhp.pdf]  
Market volatility could impact our IPO pricing significantly...

[3] [Section: Risk Factors | Page: 45 | Document: sample_drhp.pdf]
Regulatory changes in the financial sector could affect operations...

[4] [Section: Risk Factors | Page: 52 | Document: sample_drhp.pdf]
Competition from established players in the market...

[5] [Section: Risk Factors | Page: 60 | Document: sample_drhp.pdf]
Dependency on key personnel and potential talent loss...

Question: What are the main risk factors?

Answer: """

# LLM generates answer
llm_response = groq_client.chat.create(
    model="mixtral-8x7b-32768",
    messages=[{"role": "user", "content": prompt}],
    max_tokens=500
)

answer = llm_response.choices[0].message.content
# "The main risk factors facing this IPO include: (1) Market volatility impacting IPO pricing, 
# (2) Regulatory changes affecting operations, (3) Intense competition from established players, 
# (4) Dependency on key personnel, and (5) Various operational and financial risks outlined above."
```

**Output**: Answer with faithfulness score

```python
result = {
    "answer": "The main risk factors facing this IPO include: (1) Market volatility impacting IPO pricing, (2) Regulatory changes affecting operations, (3) Intense competition from established players, (4) Dependency on key personnel...",
    "faithfulness_score": 0.87,
    "citations": [
        {"rank": 1, "page": 35, "section": "Risk Factors"},
        {"rank": 2, "page": 40, "section": "Risk Factors"},
        {"rank": 3, "page": 45, "section": "Risk Factors"},
        {"rank": 4, "page": 52, "section": "Risk Factors"},
        {"rank": 5, "page": 60, "section": "Risk Factors"}
    ],
    "processing_time": 2.34
}
```

**Logs**:
```
[MAIN] STAGE 4: Answer generation
[GENERATOR] Formatting evidence with $N chunks...
[GENERATOR] Chunk 1: [Risk Factors | Page: 35] 
[GENERATOR] Chunk 2: [Risk Factors | Page: 40]
[GENERATOR] Chunk 3: [Risk Factors | Page: 45]
[GENERATOR] Chunk 4: [Risk Factors | Page: 52]
[GENERATOR] Chunk 5: [Risk Factors | Page: 60]
[GENERATOR] Invoking LLM (mixtral-8x7b-32768)...
[GENERATOR] Response received: 287 tokens
[GENERATOR] Faithfulness calculation complete: 0.87
[MAIN] Stage 4 complete: Answer generated
[MAIN] ====================================
[MAIN] Pipeline complete in 2.34 seconds
```

---

## API Request/Response Example

### **Request**:
```bash
curl -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the main risk factors facing this IPO?",
    "filters": {}
  }'
```

### **Response**:
```json
{
  "status": "success",
  "answer": "The main risk factors include market volatility, regulatory changes, competition, personnel dependency, and operational risks.",
  "faithfulness": 0.87,
  "processing_time_seconds": 2.34,
  "sources": [
    {
      "rank": 1,
      "section": "Risk Factors",
      "page": 35,
      "score": 0.92
    },
    {
      "rank": 2,
      "section": "Risk Factors",
      "page": 40,
      "score": 0.88
    }
  ]
}
```

---

## Key Data Format Contracts

### **Chunk from Supabase**
```python
{
    "id": "uuid",                      # Unique ID
    "ipo_id": "sample_drhp",          # IPO identifier
    "chunk_text": "...",               # Text (400-600 tokens)
    "page_number": 35,                 # Page in PDF (1-indexed)
    "section": "Risk Factors",         # Section name
    "document_name": "sample_drhp.pdf", # Source document
    "chunk_tokens": 487,               # Token count
    "embedding": [...],                # 1024-dim vector
    "created_at": "2024-01-15T10:23:45"
}
```

### **Section Dictionary**
```python
{
    "section_name": "Risk Factors",
    "section_number": "II",
    "start_page": 33,
    "end_page": 120,
    "subsections": [
        {
            "section_name": "Market Risk",
            "start_page": 33,
            "end_page": 50
        }
    ]
}
```

### **Pipeline Chunk (After Stage 2)**
```python
{
    "chunk_text": "...",              # Content
    "page_number": 35,                # Location
    "section": "Risk Factors",        # Section
    "document_name": "sample_drhp.pdf", # Source
    "chunk_tokens": 487,              # Size
    "similarity_score": 0.89,         # From Stage 2
    "reranker_score": 0.92,           # From Stage 3 (added later)
}
```

---

This demonstrates the exact data flowing through each stage of the pipeline!

