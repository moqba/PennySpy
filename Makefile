docker_build:
	@git_hash=$$(git rev-parse --short HEAD); \
    docker build -t "moqba/pennyspy:$$git_hash" .; \
    docker tag "moqba/pennyspy:$$git_hash" moqba/pennyspy:latest

docker_push:
	@git_hash=$$(git rev-parse --short HEAD); \
	echo "Pushing moqba/pennyspy:$$git_hash and latest..."; \
	docker push "moqba/pennyspy:$$git_hash"; \
	docker push moqba/pennyspy:latest