terraform {
  required_version = ">= 1.6"
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# The demo uses the local and random providers so the pipeline runs anywhere
# without cloud credentials. Swap in a cloud provider and the review gate works
# the same way, because it reads the plan, not the provider.

variable "environment" {
  description = "Target environment name."
  type        = string
  default     = "staging"
}

variable "service_name" {
  description = "Name of the service whose config is rendered."
  type        = string
  default     = "orders-api"
}

resource "random_id" "deployment" {
  byte_length = 4
  keepers = {
    environment = var.environment
  }
}

resource "local_file" "service_config" {
  filename = "${path.module}/out/${var.service_name}-${var.environment}.json"

  content = jsonencode({
    service       = var.service_name
    environment   = var.environment
    deployment_id = random_id.deployment.hex

    slo = {
      availability_target = 99.9
      latency_p95_ms      = 300
    }
  })
}

output "config_file" {
  value = local_file.service_config.filename
}
