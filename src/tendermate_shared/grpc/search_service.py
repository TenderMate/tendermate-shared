# Generated from proto/search_service.proto — DO NOT EDIT manually.
# Regenerate: make proto
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional

import betterproto
import grpclib.client
import grpclib.const
import grpclib.server
from grpclib.const import Cardinality, Handler, Status
from grpclib.exceptions import GRPCError

if TYPE_CHECKING:
    from grpclib.metadata import Deadline, MetadataLike

# ─── Messages ─────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class Product(betterproto.Message):
    id: str = betterproto.string_field(1)
    name: str = betterproto.string_field(2)
    description: str = betterproto.string_field(3)
    quantity: int = betterproto.int32_field(4)
    unit: str = betterproto.string_field(5)


@dataclasses.dataclass
class SearchRequest(betterproto.Message):
    job_id: str = betterproto.string_field(1)
    products: List[Product] = betterproto.message_field(2)


@dataclasses.dataclass
class AnalysisRecord(betterproto.Message):
    candidate_url: str = betterproto.string_field(1)
    candidate_name: str = betterproto.string_field(2)
    matched: bool = betterproto.bool_field(3)
    confidence: float = betterproto.float_field(4)
    reason: str = betterproto.string_field(5)


@dataclasses.dataclass
class MatchResult(betterproto.Message):
    product_id: str = betterproto.string_field(1)
    matched: bool = betterproto.bool_field(2)
    etm_url: str = betterproto.string_field(3)
    etm_name: str = betterproto.string_field(4)
    etm_price: float = betterproto.float_field(5)
    etm_sku: str = betterproto.string_field(6)
    confidence: float = betterproto.float_field(7)
    ai_reasoning: str = betterproto.string_field(8)
    ai_analyses: List[AnalysisRecord] = betterproto.message_field(9)


@dataclasses.dataclass
class CancelJobRequest(betterproto.Message):
    job_id: str = betterproto.string_field(1)


@dataclasses.dataclass
class CancelJobResponse(betterproto.Message):
    was_running: bool = betterproto.bool_field(1)


# ─── Client stub (backend uses this) ─────────────────────────────────────────


class SearchServiceStub(betterproto.ServiceStub):
    """gRPC client stub.

    Usage::
        channel = grpclib.client.Channel(host, port)
        stub = SearchServiceStub(channel)
        async for result in stub.search_products(SearchRequest(...)):
            ...
        await channel.close()
    """

    async def search_products(
        self,
        search_request: SearchRequest,
        *,
        timeout: Optional[float] = None,
        deadline: Optional["Deadline"] = None,
        metadata: Optional["MetadataLike"] = None,
    ) -> AsyncIterator[MatchResult]:
        async for response in self._unary_stream(
            "/tendermate.agent.v1.SearchService/SearchProducts",
            search_request,
            MatchResult,
            timeout=timeout,
            deadline=deadline,
            metadata=metadata,
        ):
            yield response

    async def cancel_job(
        self,
        cancel_job_request: CancelJobRequest,
        *,
        timeout: Optional[float] = None,
        deadline: Optional["Deadline"] = None,
        metadata: Optional["MetadataLike"] = None,
    ) -> CancelJobResponse:
        return await self._unary_unary(
            "/tendermate.agent.v1.SearchService/CancelJob",
            cancel_job_request,
            CancelJobResponse,
            timeout=timeout,
            deadline=deadline,
            metadata=metadata,
        )


# ─── Server base (agent implements this) ─────────────────────────────────────


class SearchServiceBase:
    """gRPC server base — implement search_products and cancel_job.

    Register an instance with grpclib.server.Server::
        server = grpclib.server.Server([MySearchService()])
        await server.start(host, port)

    grpclib will call __mapping__() to discover the handlers.
    """

    async def search_products(
        self, request: SearchRequest
    ) -> AsyncIterator[MatchResult]:
        """Override this to stream MatchResult objects back to the client."""
        raise GRPCError(Status.UNIMPLEMENTED)
        yield MatchResult()  # marks this function as an async generator

    async def cancel_job(self, request: CancelJobRequest) -> CancelJobResponse:
        """Override this to cancel a running job."""
        raise GRPCError(Status.UNIMPLEMENTED)

    # ── Internal stream handlers (grpclib calls these) ─────────────────────────

    async def _handle_search_products(
        self, stream: grpclib.server.Stream[SearchRequest, MatchResult]
    ) -> None:
        request = await stream.recv_message()
        assert request is not None
        async for response in self.search_products(request):
            await stream.send_message(response)

    async def _handle_cancel_job(
        self, stream: grpclib.server.Stream[CancelJobRequest, CancelJobResponse]
    ) -> None:
        request = await stream.recv_message()
        assert request is not None
        response = await self.cancel_job(request)
        await stream.send_message(response)

    def __mapping__(self) -> Dict[str, grpclib.const.Handler]:
        return {
            "/tendermate.agent.v1.SearchService/SearchProducts": Handler(
                self._handle_search_products,
                Cardinality.UNARY_STREAM,
                SearchRequest,
                MatchResult,
            ),
            "/tendermate.agent.v1.SearchService/CancelJob": Handler(
                self._handle_cancel_job,
                Cardinality.UNARY_UNARY,
                CancelJobRequest,
                CancelJobResponse,
            ),
        }
