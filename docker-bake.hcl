# docker buildx bake [target]   — see Makefile `build`
variable "REGISTRY" { default = "spoldzielnia" }
variable "VERSION" { default = "dev" }
variable "PLATFORMS" { default = "" } # e.g. "linux/amd64,linux/arm64" in CI; empty = host arch

group "default" {
  targets = ["api"]
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
