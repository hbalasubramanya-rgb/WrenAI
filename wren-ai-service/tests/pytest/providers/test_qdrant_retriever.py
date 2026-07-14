from types import SimpleNamespace

import pytest
from haystack import Document

from src.providers.document_store import qdrant as qdrant_module
from src.providers.document_store.qdrant import (
    AsyncQdrantDocumentStore,
    AsyncQdrantEmbeddingRetriever,
)


class FakeScrollClient:
    def __init__(self, points):
        self.points = points
        self.calls = []

    async def scroll(self, collection_name, offset, scroll_filter, limit):
        self.calls.append(
            {
                "collection_name": collection_name,
                "offset": offset,
                "scroll_filter": scroll_filter,
                "limit": limit,
            }
        )
        start = offset or 0
        end = min(start + limit, len(self.points))
        next_offset = end if end < len(self.points) else None
        return self.points[start:end], next_offset


@pytest.mark.asyncio
async def test_query_by_filters_enforces_total_top_k(monkeypatch):
    points = [SimpleNamespace(payload={"idx": idx}) for idx in range(5)]
    client = FakeScrollClient(points)
    store = AsyncQdrantDocumentStore.__new__(AsyncQdrantDocumentStore)
    store.async_client = client
    store.index = "schemas"
    store.scroll_size = 2
    store.use_sparse_embeddings = False

    monkeypatch.setattr(
        qdrant_module,
        "convert_filters_to_qdrant",
        lambda filters: filters,
    )
    monkeypatch.setattr(
        qdrant_module,
        "convert_qdrant_point_to_haystack_document",
        lambda point, use_sparse_embeddings: Document(
            content=str(point.payload["idx"]),
            meta=point.payload,
        ),
    )

    documents = await store._query_by_filters(filters={"field": "name"}, top_k=3)

    assert [document.meta["idx"] for document in documents] == [0, 1, 2]
    assert [call["limit"] for call in client.calls] == [2, 1]
    assert [call["offset"] for call in client.calls] == [None, 2]


@pytest.mark.asyncio
async def test_filter_only_retriever_uses_configured_filters_and_top_k():
    class Store:
        def __init__(self):
            self.filters = None
            self.top_k = None

        async def _query_by_filters(self, filters=None, top_k=None):
            self.filters = filters
            self.top_k = top_k
            return [Document(content="ok")]

    store = Store()
    retriever = AsyncQdrantEmbeddingRetriever.__new__(AsyncQdrantEmbeddingRetriever)
    retriever._document_store = store
    retriever._filters = {"operator": "AND", "conditions": []}
    retriever._top_k = 7

    result = await retriever.run(query_embedding=[])

    assert result["documents"][0].content == "ok"
    assert store.filters == {"operator": "AND", "conditions": []}
    assert store.top_k == 7
