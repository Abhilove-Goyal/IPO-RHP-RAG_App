# Quick Start Verification Checklist

Use this checklist to verify the hierarchical pipeline is working correctly.

## Pre-Flight Checks

### **1. Import Verification**
Run this in Python to check all modules load:

```python
# In Python terminal
from rag.toc_parser import extract_toc
from rag.section_retriever import retrieve_top_sections
from rag.retriever import retrieve_chunks_in_sections
from rag.reranker import rerank
from rag.generator import generate_answer
from rag.multi_query import generate_queries
import main

print("✅ All imports successful")
```

**Expected**: All imports complete without errors
**If fails**: Check for syntax errors in modified files

---

### **2. Database Connection**
```python
# In Python terminal
from core.supabase_client import get_supabase
supabase = get_supabase()

# Check if chunks table exists
result = supabase.table("ipo_chunks").select("*").limit(1).execute()
print(f"✅ Supabase connected, table exists")
```

**Expected**: No error, table accessible
**If fails**: Check SUPABASE_URL and SUPABASE_KEY in .env

---

### **3. Models Check**
```python
# In Python terminal - this may take a moment
from sentence_transformers import CrossEncoder

# This loads the reranker
encoder = CrossEncoder("BAAI/bge-reranker-large")
print("✅ Reranker model loads successfully")
```

**Expected**: Model downloads and loads (first time only)
**If fails**: Check internet connection for HuggingFace downloads

---

## API Testing

### **1. Health Endpoint**
```bash
# Terminal command
curl http://localhost:8000/health

# Expected response:
# {"status":"healthy","ipo_loaded":false,"current_ipo":null}
```

**Expected**: 200 status, healthy response
**If fails**: API server not running, start with: `uvicorn api:app --reload`

---

### **2. Upload Endpoint**
```bash
# Terminal - using sample PDF
curl -X POST "http://localhost:8000/upload" \
  -F "file=@test.pdf"

# Expected response shows:
# {
#   "status": "uploaded",
#   "ipo_id": "test",
#   "chunks_created": 24,
#   "processing_time_seconds": 12.5
# }
```

**Expected**: 200 status, chunk count > 0
**If fails**: 
- Check PDF exists
- Check /uploads directory exists
- Check logs for TOC extraction errors

---

### **3. Hierarchical Pipeline Test**

After uploading a PDF, test the full pipeline:

```bash
# Terminal - ask a question
curl -X POST "http://localhost:8000/ask" \
  -H "Content-Type: application/json" \
  -d '{"query": "What does the document say?"}'

# Expected response:
# {
#   "answer": "[Section: X | Page: Y] Answer text...",
#   "faithfulness": 0.85,
#   "status": "success"
# }
```

**Expected**: 200 status, answer generated, faithfulness between 0-1
**If fails**: 
- Check logs for each pipeline stage
- Verify Supabase has chunk data from upload
- Check for error messages in terminal

---

## Log Inspection

### **What to Look For**

After asking a question, your terminal should show:

```
[MAIN] ======================================
[MAIN] Processing query: What are...
[MAIN] IPO ID: test_drhp

[MAIN] STAGE 1: Section-level retrieval
[SECTION_RETRIEVAL] Scoring sections...
[SECTION_RETRIEVAL] Top sections: ['Risk Factors', 'Business', 'Financials']

[MAIN] STAGE 2: Chunk-level retrieval
[MULTI_QUERY] Generated variations: 
  - Query 1: ...
  - Query 2: ...
  - Query 3: ...
[RETRIEVER] Querying chunks in sections: ['Risk Factors', 'Business', 'Financials']
[RETRIEVER] Retrieved 20 chunks (deduped)

[MAIN] STAGE 3: Cross-encoder reranking
[RERANKER] Scoring 20 chunks...
[RERANKER] Top 5 scores: [0.92, 0.88, 0.85, 0.81, 0.79]

[MAIN] STAGE 4: Answer generation
[GENERATOR] Formatting prompt with 5 chunks...
[GENERATOR] Chunk 1: [Risk Factors | Page 23] ...
[GENERATOR] LLM invoked...
[GENERATOR] Faithfulness: 0.87

[MAIN] Pipeline complete in 2.3 seconds
```

**If you see this**: ✅ Pipeline working correctly

**If you see errors**: 🔴 Check the specific stage for errors

---

## Common Error Fixes

### Error: "ModuleNotFoundError: No module named 'rag.toc_parser'"
**Fix**: Verify `rag/__init__.py` exists (it should be empty)

### Error: "KeyError: 'section_name'"
**Fix**: TOC structure mismatch. Check toc_parser.py line 45-60 has correct dict keys

### Error: "psycopg2.errors.InsufficientPrivilege"
**Fix**: Supabase RLS policy issue. Admin must allow vector search on chunks table

### Error: "Connection timeout to HuggingFace"
**Fix**: Reranker downloading. First request may timeout - retry after 60 seconds

### Error: "No sections retrieved"
**Fix**: Normal if PDF has no TOC. Section filtering skipped, continues with full chunk search

---

## Performance Baseline

On a typical system:

| Stage | Time |
|-------|------|
| Section Retrieval | 10-50ms |
| Chunk Retrieval | 200-500ms |
| Reranking (5 chunks) | 100-200ms |
| Answer Generation | 1-3 seconds |
| **Total** | **1.5-4 seconds** |

---

## Success Indicators

✅ All 4 stages appear in logs
✅ Responses include section + page citations
✅ Faithfulness scores between 0.6-0.99
✅ Query processing < 5 seconds
✅ No import errors on startup

---

## If Something Still Fails

### Step 1: Check Syntax
```bash
python -m py_compile rag/toc_parser.py
python -m py_compile rag/section_retriever.py
python -m py_compile rag/retriever.py
python -m py_compile main.py
```

### Step 2: Check File Consistency
```bash
# Verify these functions exist
grep -n "def retrieve_top_sections" rag/section_retriever.py
grep -n "def retrieve_chunks_in_sections" rag/retriever.py
grep -n "def extract_toc" rag/toc_parser.py
grep -n "def run" main.py
```

### Step 3: Check API Integration
```bash
# Verify /ask uses correct import
grep "from main import" api.py
grep "run(" api.py
```

### Step 4: Enable Debug Logging
In `main.py` at top:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Contact Points for Further Help

If issues persist after these checks, verify:

1. **Supabase Tables**: 
   - ipo_chunks table exists with columns: ipo_id, chunk_text, section, page_number, embedding, chunk_tokens, document_name
   
2. **Environment Variables**:
   - SUPABASE_URL set
   - SUPABASE_KEY set
   - GROQ_API_KEY set (for LLM)
   - HF_TOKEN set (for embeddings)

3. **File Permissions**:
   - /uploads directory exists and is writable
   - /logs directory exists and is writable
   - /data/chroma directory exists

4. **Dependencies**:
   - Run: `pip install -r requirements.txt`
   - Restart kernel/API server

---

✅ Complete this checklist before reporting issues!

