.PHONY: proto lint format install install-dev

# Regenerate gRPC stubs from proto/search_service.proto.
# Requires dev deps: uv sync --group dev (or pip install "betterproto[compiler]>=2.0.0b7" grpcio-tools)
proto:
	python -m grpc_tools.protoc \
		-I proto \
		--python_betterproto_out=tendermate_shared/grpc \
		search_service.proto

install:
	uv sync

install-dev:
	uv sync --group dev

lint:
	uv run ruff check src/

format:
	uv run ruff format src/
