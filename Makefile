.PHONY: build up down logs ps scale build-gpu up-gpu down-gpu save load clean

# CPU-only stack (default): api, worker, postgres, minio. No GPU, no
# NVIDIA Container Toolkit needed — matches spec's "no cloud account, no
# cluster" MVP story.
COMPOSE = docker compose -f docker-compose.yml
# GPU override: same stack, worker runs Dockerfile.worker-gpu instead.
COMPOSE_GPU = docker compose -f docker-compose.yml -f docker-compose.gpu.yml

WORKERS ?= 2

## Build the CPU images (api, worker) without starting anything.
build:
	$(COMPOSE) build

## Bring up the full CPU stack, scaled to $(WORKERS) worker replicas.
up:
	$(COMPOSE) up -d --scale worker=$(WORKERS)

## Stop and remove the CPU stack's containers (keeps volumes).
down:
	$(COMPOSE) down

## Follow logs for all services.
logs:
	$(COMPOSE) logs -f

## List running services in this stack.
ps:
	$(COMPOSE) ps

## Rescale the worker replica count without restarting the rest of the stack.
scale:
	$(COMPOSE) up -d --scale worker=$(WORKERS)

## Build the GPU worker image (Dockerfile.worker-gpu) in addition to the CPU api image.
build-gpu:
	$(COMPOSE_GPU) build

## Bring up the stack with the GPU worker override, scaled to $(WORKERS) replicas.
## Requires an NVIDIA GPU + the NVIDIA Container Toolkit on the host.
up-gpu:
	$(COMPOSE_GPU) up -d --scale worker=$(WORKERS)

## Stop and remove the GPU stack's containers (keeps volumes).
down-gpu:
	$(COMPOSE_GPU) down

## Save built images to a tarball for transfer onto an offline/firewalled
## host (see docs/deploy-airgapped.md). Run `make build` first.
save:
	docker save ai-docolage-api:latest ai-docolage-worker:latest -o docolage-images.tar

## Load images from a tarball produced by `make save`, on the offline host.
load:
	docker load -i docolage-images.tar

## Remove containers, volumes and locally built images for this stack.
clean:
	$(COMPOSE) down -v --rmi local
