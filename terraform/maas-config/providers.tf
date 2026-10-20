terraform {
  # `import` blocks with an expression id need Terraform >= 1.6.
  required_version = ">= 1.6.0"

  required_providers {
    maas = {
      source  = "canonical/maas"
      version = "~> 2.10"
    }
  }
}

# Connection details, machines and LXD power credentials come straight from the `maas` root's
# local state, so no secret has to travel through the command line.
data "terraform_remote_state" "maas" {
  backend = "local"

  config = {
    path = "${path.module}/../maas/terraform.tfstate"
  }
}

locals {
  deployed = data.terraform_remote_state.maas.outputs
}

provider "maas" {
  api_url             = local.deployed.maas_url
  api_key             = local.deployed.maas_api_key
  api_version         = "2.0"
  installation_method = "snap"
}
