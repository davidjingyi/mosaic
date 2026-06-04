"""PDF file mapping API - group MD segments back to original PDFs."""
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, Request

from app.api.v1.deps import get_engine, require_admin

router = APIRouter(prefix="/knowledge-base", tags=["PDF映射"])


@router.get("/pdf-mapping")
async def get_pdf_mapping(request: Request, admin=Depends(require_admin)):
    """Group all MD segment files back to their original PDFs with chunk/index stats."""
    engine = get_engine(request)

    # Load registry
    from app.services.document_registry import get_documents
    docs_list = get_documents()

    # Query ChromaDB for chunk counts per doc_id
    vs = engine.vector_store
    doc_chunk_counts: dict[str, int] = {}
    try:
        for item in vs.get_all():
            doc_chunk_counts[item["doc_id"]] = item["chunk_count"]
    except Exception:
        pass

    # Parse and group by original PDF name
    pat = re.compile(r"^(\d+_)?(.+?)_(\d+-\d+)\.pdf-[a-f0-9-]+\.md$")
    pdf_groups: dict[str, dict] = {}

    for doc in docs_list:
        fp = doc.get("file_path", "") or doc.get("source", "")
        fname = fp.split("/")[-1] if "/" in fp else fp
        m = pat.match(fname)
        if not m:
            continue
        _, pdf_name, pages = m.groups()
        clean_name = re.sub(r"^\d+_", "", pdf_name)
        status = doc.get("status", "?")

        if clean_name not in pdf_groups:
            pdf_groups[clean_name] = {
                "pdf_name": clean_name,
                "B_md_files": 0,
                "C_has_chunks": 0,
                "D_needs_chunks": 0,
                "E_in_db": 0,
                "F_needs_index": 0,
                "total_chunks_in_db": 0,
                "page_range": pages,
            }

        g = pdf_groups[clean_name]
        g["B_md_files"] += 1
        g["total_chunks_in_db"] += doc_chunk_counts.get(doc.get("doc_id", ""), 0)

        if status in ("chunked", "indexing", "indexed"):
            g["C_has_chunks"] += 1
        else:
            g["D_needs_chunks"] += 1

        if status == "indexed":
            g["E_in_db"] += 1
        elif status in ("chunked", "indexing"):
            g["F_needs_index"] += 1

        # Track page range
        if pages:
            existing = g["page_range"]
            if "-" in existing:
                parts = existing.split(" ~ ") if " ~ " in existing else [existing]
                all_pages = []
                for p in parts:
                    if "-" in p:
                        a, b = p.split("-")
                        all_pages.extend([int(a), int(b)])
                if "-" in pages:
                    a2, b2 = pages.split("-")
                    all_pages.extend([int(a2), int(b2)])
                if all_pages:
                    g["page_range"] = f"{min(all_pages)} ~ {max(all_pages)}"

    # Sort by total segments descending
    result = sorted(pdf_groups.values(), key=lambda g: g["B_md_files"], reverse=True)

    # Summary
    total_B = sum(g["B_md_files"] for g in result)
    total_C = sum(g["C_has_chunks"] for g in result)
    total_D = sum(g["D_needs_chunks"] for g in result)
    total_E = sum(g["E_in_db"] for g in result)
    total_F = sum(g["F_needs_index"] for g in result)
    total_chunks = sum(g["total_chunks_in_db"] for g in result)

    return {
        "pdf_count": len(result),
        "total_md_files": total_B,
        "total_chunks_in_db": total_chunks,
        "summary": {
            "C_has_chunks": total_C,
            "D_needs_chunks": total_D,
            "E_in_db": total_E,
            "F_needs_index": total_F,
        },
        "relations_valid": {
            "C_plus_D_eq_B": total_C + total_D == total_B,
            "E_plus_F_eq_C": total_E + total_F == total_C,
        },
        "pdfs": result,
    }
