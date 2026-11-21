IMAGE ?= rwx-core
SAMPLE ?= $(CURDIR)/kit/sample
OUTPUT ?= $(CURDIR)/.work/output.jsonl

RUN_FLAGS := --rm --platform linux/amd64 --network none --cpus 2 \
	--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
	--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
	--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
	--volume $(SAMPLE)/commits:/data/commits:ro \
	--volume $(SAMPLE)/tests.json:/data/tests.json:ro

.PHONY: build dev sample check maps

build:
	docker build --platform linux/amd64 --target run --tag $(IMAGE) .

dev:
	docker build --platform linux/amd64 --target dev --tag $(IMAGE)-dev .

sample: build
	mkdir -p $(dir $(OUTPUT))
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 kit/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT)

check: build
	mkdir -p $(dir $(OUTPUT))
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 kit/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT) > /dev/null && echo "format ok"

# Regenerate the maps from the pinned commit (takes a few minutes; see tools/build_maps.py).
maps: dev
	tools/run_maps.sh
