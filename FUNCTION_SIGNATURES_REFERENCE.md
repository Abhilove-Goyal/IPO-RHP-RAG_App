# Function Signatures & Integration Reference

Complete reference for all functions in the hierarchical pipeline.

## Core Pipeline Functions

### **main.py::run(query: str) → tuple[str, float]**

**Purpose**: Main entry point for query processing. Orchestrates 4-stage hierarchical retrieval.

**Signature**:
```python
def run(query: str) -> tuple[str, float]:
    """
    Process query through hierarchical retrieval pipeline.
    
    Args:
        query: User question (e.g., "What are the main risk factors?")
    
    Returns:
        tuple: (answer_text, faithfulness_score)
        - answer_text: Generated answer with citations
        - faithfulness_score: Float 0-1 indicating confidence
    
    Raises:
        Exception: If no IPO loaded or pipeline fails
    """
```

**Execution Flow**:
```python
# Stage 1: Section Retrieval
sections = get_sections_for_ipo(ipo_id)
top_sections = retrieve_top_sections(query, ipo_id, sections, top_k=3)

# Stage 2: Chunk Retrieval
query_variations = generate_queries(query)
retrieved_chunks = retrieve_chunks_in_sections(
    query, query_variations, ipo_id, top_sections, limit=20
)

# Stage 3: Reranking
final_chunks = rerank(query, retrieved_chunks, top_k=5)

# Stage 4: Answer Generation
answer, faithfulness = generate_answer(query, final_chunks)

# Logging
log_result(query, answer, faithfulness, ipo_id)

return answer, faithfulness
```

**Example**:
```python
from main import run

answer, score = run("What are the main risk factors?")
print(f"Answer: {answer}")
print(f"Confidence: {score:.2%}")
```

---

### **main.py::get_sections_for_ipo(ipo_id: str) → list[dict]**

**Purpose**: Retrieve hierarchical sections for an IPO from cache or extract from PDF.

**Signature**:
```python
def get_sections_for_ipo(ipo_id: str) -> list[dict]:
    """
    Get table of contents sections for an IPO.
    
    Args:
        ipo_id: IPO identifier (e.g., "sample_drhp")
    
    Returns:
        list: Sections with format:
        [
            {
                "section_name": "Risk Factors",
                "section_number": "II",
                "start_page": 33,
                "end_page": 120,
                "subsections": [...]
            },
            ...
        ]
    """
```

**Returns Format**:
```python
[
    {
        "section_name": "Cover",
        "section_number": "I",
        "start_page": 1,
        "end_page": 5,
        "subsections": []
    },
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
]
```

---

## Stage 1: Section Retrieval

### **section_retriever.py::retrieve_top_sections(...) → list[dict]**

**Purpose**: Identify most relevant sections to user query using keyword matching.

**Signature**:
```python
def retrieve_top_sections(
    query: str,
    ipo_id: str,
    sections: list[dict],
    top_k: int = 3
) -> list[dict]:
    """
    Retrieve top relevant sections for a query.
    
    Args:
        query: User question (e.g., "What are the risks?")
        ipo_id: IPO identifier
        sections: Full section list from get_sections_for_ipo()
        top_k: Number of top sections to return (default 3)
    
    Returns:
        list: Top sections, each containing:
        {
            "section_name": str,
            "start_page": int,
            "end_page": int,
            "score": float  # Keyword match confidence
        }
    """
```

**Execution**:
```python
# Keyword extraction
keywords = ["risk", "factors"]

# Score each section
for section in sections:
    score = 0
    # Direct match: section_name contains keywords
    for keyword in keywords:
        if keyword.lower() in section["section_name"].lower():
            score += 2.0
    
    # Subsection match
    for subsection in section.get("subsections", []):
        for keyword in keywords:
            if keyword.lower() in subsection["section_name"].lower():
                score += 1.0
    
    section["score"] = score

# Return top 3
return sorted(sections, key=lambda x: x["score"], reverse=True)[:top_k]
```

**Example**:
```python
sections = get_sections_for_ipo("sample_drhp")
top_sections = retrieve_top_sections(
    "What are the main risks?",
    "sample_drhp",
    sections,
    top_k=3
)
# Returns: [{"section_name": "Risk Factors", "start_page": 33, ...}]
```

---

## Stage 2: Chunk Retrieval

### **multi_query.py::generate_queries(query: str) → list[str]**

**Purpose**: Generate semantic variations of the query for better recall.

**Signature**:
```python
def generate_queries(query: str) -> list[str]:
    """
    Generate alternative formulations of the query.
    
    Args:
        query: Original question
    
    Returns:
        list: 3 query variations capturing different angles
    """
```

**Returns**:
```python
# Input: "What are the main risk factors?"
[
    "What are the main risk factors?",
    "What risks could impact the IPO?",
    "Describe the risk profile"
]
```

---

### **retriever.py::retrieve_chunks_in_sections(...) → list[dict]**

**Purpose**: Retrieve and score chunks from Supabase, filtering by section.

**Signature**:
```python
def retrieve_chunks_in_sections(
    query: str,
    query_variations: list[str],
    ipo_id: str,
    top_sections: list[dict],
    limit: int = 20
) -> list[dict]:
    """
    Retrieve chunks from Supabase using section filter.
    
    Args:
        query: Original user query
        query_variations: Alternative formulations
        ipo_id: IPO identifier
        top_sections: Sections from Stage 1
        limit: Max chunks to retrieve (default 20)
    
    Returns:
        list: Chunks with similarity scores:
        [
            {
                "id": "uuid",
                "chunk_text": "...",
                "page_number": 35,
                "section": "Risk Factors",
                "document_name": "sample_drhp.pdf",
                "chunk_tokens": 487,
                "similarity_score": 0.89
            },
            ...
        ]
    """
```

**Execution**:
```python
# Extract section names from top_sections
section_names = [s["section_name"] for s in top_sections]

# For each query variation
all_results = {}
for query_var in query_variations:
    # Embed query
    query_embedding = embed(query_var)
    
    # Query Supabase with section filter
    results = supabase.table("ipo_chunks") \
        .select("*") \
        .eq("ipo_id", ipo_id) \
        .in_("section", section_names) \
        .limit(100) \
        .execute()
    
    # Score and deduplicate
    for chunk in results.data:
        chunk_text = chunk["chunk_text"]
        if chunk_text not in all_results:
            all_results[chunk_text] = {
                **chunk,
                "similarity_score": cosine_similarity(
                    query_embedding, 
                    chunk["embedding"]
                )
            }

# Sort by similarity and return top `limit`
return sorted(
    all_results.values(),
    key=lambda x: x["similarity_score"],
    reverse=True
)[:limit]
```

**Example**:
```python
top_sections = retrieve_top_sections(query, ipo_id, sections)
query_variations = generate_queries(query)

chunks = retrieve_chunks_in_sections(
    query,
    query_variations,
    ipo_id,
    top_sections,
    limit=20
)
# Returns 20 chunks from relevant sections only
```

---

## Stage 3: Reranking

### **reranker.py::rerank(...) → list[dict]**

**Purpose**: Use cross-encoder to score and filter chunks.

**Signature**:
```python
def rerank(
    query: str,
    chunks: list[dict],
    top_k: int = 5,
    threshold: float = 0.0
) -> list[dict]:
    """
    Rerank chunks using cross-encoder model.
    
    Args:
        query: User question
        chunks: Chunks from Stage 2
        top_k: Return top K chunks (default 5)
        threshold: Min score to include (default 0.0)
    
    Returns:
        list: Top K reranked chunks with scores:
        [
            {
                "rank": 1,
                "chunk_text": "...",
                "page_number": 35,
                "section": "Risk Factors",
                "document_name": "sample_drhp.pdf",
                "reranker_score": 0.92
            },
            ...
        ]
    """
```

**Execution**:
```python
from sentence_transformers import CrossEncoder

# Initialize cross-encoder (lazy loaded)
encoder = CrossEncoder("BAAI/bge-reranker-large")

# Create pairs and score
pairs = [[query, chunk["chunk_text"]] for chunk in chunks]
scores = encoder.predict(pairs)

# Add scores and sort
for i, chunk in enumerate(chunks):
    chunk["reranker_score"] = float(scores[i])

# Filter and sort
reranked = [c for c in chunks if c["reranker_score"] >= threshold]
reranked = sorted(
    reranked,
    key=lambda x: x["reranker_score"],
    reverse=True
)[:top_k]

# Add rank
for i, chunk in enumerate(reranked):
    chunk["rank"] = i + 1

return reranked
```

**Example**:
```python
retrieved_chunks = retrieve_chunks_in_sections(...)
final_chunks = rerank(query, retrieved_chunks, top_k=5)
# Returns top 5 chunks most relevant to query
```

---

## Stage 4: Answer Generation

### **generator.py::generate_answer(...) → tuple[str, float]**

**Purpose**: Format context and invoke LLM to generate answer.

**Signature**:
```python
def generate_answer(
    query: str,
    context_chunks: list[dict]
) -> tuple[str, float]:
    """
    Generate answer using LLM with context.
    
    Args:
        query: User question
        context_chunks: Top chunks from Stage 3 (with metadata)
    
    Returns:
        tuple:
            - answer: Generated answer string
            - faithfulness: Float 0-1 indicating confidence
    """
```

**Expected Input Format**:
```python
context_chunks = [
    {
        "chunk_text": "The main risks include...",
        "page_number": 35,
        "section": "Risk Factors",
        "document_name": "sample_drhp.pdf",
        "reranker_score": 0.92
    },
    # ... 4 more chunks
]
```

**Execution**:
```python
# Build prompt with citation format
prompt = build_prompt(query, context_chunks)
# Result:
# Question: What are the main risk factors?
# 
# Evidence:
# [1] [Section: Risk Factors | Page: 35 | Document: sample_drhp.pdf]
# The main risks include...
#
# [2] [Section: Risk Factors | Page: 40 | Document: sample_drhp.pdf]
# Market volatility could...
#
# Answer: """

# Call LLM
response = groq_client.chat.create(
    model="mixtral-8x7b-32768",
    messages=[{"role": "user", "content": prompt}],
    max_tokens=500
)

answer = response.choices[0].message.content

# Calculate faithfulness (relevance score)
faithfulness = calculate_faithfulness(answer, query, context_chunks)

return answer, faithfulness
```

**Example**:
```python
final_chunks = rerank(...)
answer, score = generate_answer(query, final_chunks)
print(f"Answer: {answer}")
print(f"Faithfulness: {score:.2%}")
```

---

### **prompt_builder.py::build_prompt(...) → str**

**Purpose**: Format chunks with citations for LLM prompt.

**Signature**:
```python
def build_prompt(
    query: str,
    chunks: list[dict],
    system_prompt: str = None
) -> str:
    """
    Build formatted prompt with evidence citations.
    
    Args:
        query: User question
        chunks: Context chunks with metadata
        system_prompt: Custom system instruction (optional)
    
    Returns:
        str: Formatted prompt ready for LLM
    """
```

**Output Format**:
```
You are an expert financial analyst...

Evidence:
[1] [Section: Risk Factors | Page: 35 | Document: sample_drhp.pdf]
The following are the principal factors that may materially affect...

[2] [Section: Risk Factors | Page: 40 | Document: sample_drhp.pdf]
Market volatility could impact our IPO pricing...

Question: What are the main risk factors?

Answer: """
```

---

## Utility Functions

### **toc_parser.py::extract_toc(pdf_path: str) → list[dict]**

**Purpose**: Extract table of contents structure from PDF.

**Signature**:
```python
def extract_toc(pdf_path: str) -> list[dict]:
    """
    Extract hierarchical table of contents from PDF.
    
    Args:
        pdf_path: Path to PDF file
    
    Returns:
        list[dict]: Sections with structure:
        [
            {
                "section_number": "I",
                "section_name": "Cover",
                "start_page": 1,
                "end_page": 5,
                "subsections": []
            },
            ...
        ]
    """
```

---

### **ingestion.py::load_chunk_documents(...) → dict**

**Purpose**: Upload and process PDF document into Supabase.

**Signature**:
```python
def load_chunk_documents(
    ipo_id: str,
    file_path: str,
    document_name: str
) -> dict:
    """
    Load PDF and store chunks in Supabase.
    
    Args:
        ipo_id: IPO identifier
        file_path: Path to PDF
        document_name: Display name
    
    Returns:
        dict:
        {
            "status": "completed",
            "chunks_created": 1247,
            "processing_time": 58.3,
            "message": "Success"
        }
    """
```

---

### **logger.py::log_result(...) → None**

**Purpose**: Store query and answer to logging database.

**Signature**:
```python
def log_result(
    query: str,
    answer: str,
    faithfulness: float = None,
    ipo_id: str = None,
    chunks_used: list = None
) -> None:
    """
    Log query result with optional metadata.
    
    Args:
        query: User question
        answer: Generated answer
        faithfulness: Confidence score (optional)
        ipo_id: IPO identifier (optional)
        chunks_used: List of chunk IDs (optional)
    
    Returns:
        None
    """
```

---

## Integration Points

### **From API to Pipeline** (api.py)

```python
from main import run
from rag.logger import log_result

@app.post("/ask")
async def ask(req: QueryRequest):
    try:
        # Call main pipeline
        answer, faithfulness = run(req.query)
        
        # Log result
        log_result(
            query=req.query,
            answer=answer,
            faithfulness=faithfulness,
            ipo_id=get_current_ipo_id()
        )
        
        return {
            "status": "success",
            "answer": answer,
            "faithfulness": faithfulness
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
```

---

## Data Dependencies

```
API Request
    ↓
main.run(query)
    ├─ get_sections_for_ipo(ipo_id)
    │  └─ toc_parser.extract_toc()
    │
    ├─ retrieve_top_sections(query, sections)
    │  └─ Keyword matching on section names
    │
    ├─ generate_queries(query)
    │  └─ LLM query expansion
    │
    ├─ retrieve_chunks_in_sections(query_variations, sections)
    │  ├─ embed() each variation
    │  └─ Query Supabase with section filter
    │
    ├─ rerank(query, chunks)
    │  └─ CrossEncoder scoring
    │
    ├─ generate_answer(query, final_chunks)
    │  ├─ build_prompt(query, chunks)
    │  └─ LLM invocation
    │
    └─ log_result(query, answer, faithfulness)
        └─ Store to Supabase
```

---

This is the complete function reference for the hierarchical pipeline!

