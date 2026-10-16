# docker buildx bake [target]   — see Makefile `build`
variable "REGISTRY" { default = "spoldzielnia" }
variable "VERSION" { default = "dev" }
variable "PLATFORMS" { default = "" } # e.g. "linux/amd64,linux/arm64" in CI; empty = host arch

group "default" {
  targets = ["api", "worker-media", "worker-core"]
}

target "_backend" {
  context    = "."
  dockerfile = "docker/backend.Dockerfile"
  platforms  = PLATFORMS == "" ? [] : split(",", PLATFORMS)
  labels = {
    "org.opencontainers.image.source" = "local"
    "org.opencontainers.image.version" = VERSION
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
