BUILDER_IMAGE=$(DOCKER_HUB_LOCAL)/python:3.10.8-slim

define docker-inside-python
	docker run \
		--rm -t \
		-u root \
		-e POETRY_REQUESTS_TIMEOUT=30 \
		-e PIP_TIMEOUT=30 \
		-e PIP_RETRIES=3 \
		-v $(PWD):/app \
		-v /etc/localtime:/etc/timezone \
		-v /etc/localtime:/etc/localtime \
		-v /var/run/docker.sock:/var/run/docker.sock \
		--add-host=art.lmru.tech:10.80.121.11 \
		-w /app \
		-e CI=true \
		$(BUILDER_IMAGE) \
		sh -x -c "$(strip $(1))"
endef

################################################################################
.PHONY: version
version:
	$(call docker-inside-python, echo ${VERSION} > version)
################################################################################
.PHONY: build-app
build-app:
	$(call docker-inside-python, rm -f poetry.lock && python3 -m pip install --upgrade pip && python3 -m pip install poetry && poetry config virtualenvs.create false && poetry source add --priority=primary art https://art.lmru.tech/artifactory/api/pypi/python-remote-pypi/simple && poetry source add --priority=supplemental dostovernost https://art.lmru.tech/artifactory/api/pypi/pypi-local-dostovernost/simple && poetry lock --no-interaction --no-ansi && poetry install --no-interaction --no-ansi)
################################################################################
.PHONY: build-image
build-image:
	docker build --add-host=art.lmru.tech:10.80.121.11 -t $(IMAGE_NAME):$(IMAGE_TAG) .
################################################################################
.PHONY: test
test:
	$(call docker-inside-python, rm -f poetry.lock && python3 -m pip install --upgrade pip && python3 -m pip install poetry && poetry config virtualenvs.create false && poetry source add --priority=primary art https://art.lmru.tech/artifactory/api/pypi/python-remote-pypi/simple && poetry source add --priority=supplemental dostovernost https://art.lmru.tech/artifactory/api/pypi/pypi-local-dostovernost/simple && poetry lock --no-interaction --no-ansi && poetry install --no-interaction --no-ansi && PYTHONPATH=. pytest)
################################################################################
.PHONY: lint
lint:
	$(call docker-inside-python, echo "hi")
################################################################################
