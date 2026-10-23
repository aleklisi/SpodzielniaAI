# docker buildx bake [target]   — see Makefile `build`
# Release (6.8, .github/workflows/publish.yml): multi-arch images with SBOM and provenance,
#   REGISTRY=ghcr.io/<owner>/spoldzielnia VERSION=v1.2.3 docker buildx bake release --push
variable "REGISTRY" { default = "spoldzielnia" }
variable "VERSION" { default = "dev" }
variable "PLATFORMS" { default = "" } # e.g. "linux/amd64,linux/arm64" in CI; empty = host arch
variable "SOURCE" { default = "local" } # org.opencontainers.image.source (the repository URL)
variable "REVISION" { default = "" }    # git commit; also tagged as sha-<commit> on release

group "default" {
  targets = ["api", "worker-media", "worker-core", "frontend"]
}

group "release" {
  targets = ["release-api", "release-worker-media", "release-worker-core", "release-frontend"]
}

function "tags" {
  params = [image]
  result = REVISION == "" ? ["${REGISTRY}/${image}:${VERSION}"] : [
    "${REGISTRY}/${image}:${VERSION}",
    "${REGISTRY}/${image}:sha-${substr(REVISION, 0, 12)}",
  ]
}

target "_backend" {
  context    = "."
  dockerfile = "docker/backend.Dockerfile"
  platforms  = PLATFORMS == "" ? [] : split(",", PLATFORMS)
  labels = {
    "org.opencontainers.image.source" = SOURCE
    "org.opencontainers.image.version" = VERSION
    "org.opencontainers.image.revision" = REVISION
  }
}

target "api" {
  inherits = ["_backend"]
  target   = "api"
  tags     = ["${REGISTRY}/api:${VERSION}"]
}

target "worker-media" {
  inherits = ["_backend"]
  target   = "worker-media"
  tags     = ["${REGISTRY}/worker-media:${VERSION}"]
}

target "worker-core" {
  inherits = ["_backend"]
  target   = "worker-core"
  tags     = ["${REGISTRY}/worker-core:${VERSION}"]
}

target "frontend" {
  context    = "frontend"
  dockerfile = "Dockerfile"
  platforms  = PLATFORMS == "" ? [] : split(",", PLATFORMS)
  tags       = ["${REGISTRY}/frontend:${VERSION}"]
  labels = {
    "org.opencontainers.image.source" = SOURCE
    "org.opencontainers.image.version" = VERSION
    "org.opencontainers.image.revision" = REVISION
  }
}

# Both architectures (amd64 servers, arm64 servers and Apple silicon), each image with an SBOM
# and SLSA provenance attached in the registry (trivy and `docker buildx imagetools` read them).
target "_release" {
  platforms = ["linux/amd64", "linux/arm64"]
  attest    = ["type=sbom", "type=provenance,mode=max"]
}

target "release-api" {
  inherits = ["api", "_release"]
  tags     = tags("api")
}

target "release-worker-media" {
  inherits = ["worker-media", "_release"]
  tags     = tags("worker-media")
}

target "release-worker-core" {
  inherits = ["worker-core", "_release"]
  tags     = tags("worker-core")
}

target "release-frontend" {
  inherits = ["frontend", "_release"]
  tags     = tags("frontend")
}
