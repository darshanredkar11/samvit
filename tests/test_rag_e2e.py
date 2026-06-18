"""E2E: RAG — ingest a document → search_docs."""
from __future__ import annotations
import os
import tempfile
import pytest
from samvit.tools import rag
from samvit.tools.memory import recall, remember


@pytest.mark.asyncio
async def test_ingest_and_search_markdown(agent_rec):
    """Ingest a .md file, then search for its content."""
    content = """
    # Product Documentation

    Samvit uses PostgreSQL for primary storage and Redpanda for event streaming.
    Authentication is done via bearer tokens hashed with SHA256 + bcrypt.

    ## Security Policy
    All tokens must be rotated quarterly.
    API keys are stored in environment variables, never in code.
    """

    result = await rag.ingest(
        agent_rec,
        content=content,
        filename="product_docs.md",
        namespace="e2e-test",
        description="Product documentation for E2E test",
    )
    assert result["document_id"] is not None
    assert result["chunk_count"] > 0

    searches = await rag.search_docs(
        agent_rec,
        query="How are tokens stored?",
        namespace="e2e-test",
        limit=3,
    )
    assert len(searches["results"]) > 0
    combined = " ".join(r["content"] for r in searches["results"]).lower()
    assert "sha256" in combined or "bcrypt" in combined or "bearer" in combined


@pytest.mark.asyncio
async def test_ingest_and_search_python_source(agent_rec):
    """Ingest a .py file, search for function definitions."""
    content = """
def calculate_discount(price: float, percentage: float) -> float:
    '''Apply a discount percentage to a price.'''
    return price * (1 - percentage / 100)

def apply_tax(price: float, tax_rate: float) -> float:
    '''Add tax to a base price.'''
    return price * (1 + tax_rate / 100)
"""

    result = await rag.ingest(
        agent_rec,
        content=content,
        filename="pricing.py",
        namespace="e2e-test",
    )
    assert result["chunk_count"] > 0

    searches = await rag.search_docs(
        agent_rec,
        query="price calculation with discount",
        namespace="e2e-test",
        limit=3,
    )
    assert len(searches["results"]) > 0


@pytest.mark.asyncio
async def test_rag_and_memory_workflow(agent_rec):
    """Ingest a policy doc, then store findings about it in memory."""
    content = "Data retention policy: customer data retained for 90 days, then anonymized."

    await rag.ingest(
        agent_rec,
        content=content,
        filename="policy.md",
        namespace="e2e-test",
    )

    await remember(
        agent_rec,
        "Checked data retention policy — 90 days, then anonymized",
        namespace="global",
    )

    found = await recall(agent_rec, query="data retention policy")
    assert len(found["results"]) > 0
