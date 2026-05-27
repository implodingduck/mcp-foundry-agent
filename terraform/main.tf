terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "=4.67.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    azapi = {
      source  = "azure/azapi"
      version = "=2.8.0"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
    storage {
      data_plane_available = false
    }
  }

  storage_use_azuread = true

  subscription_id = var.subscription_id
}

provider "azuread" {}

data "azuread_client_config" "current" {}

resource "random_uuid" "scope_id" {}

resource "random_string" "unique" {
  length  = 8
  special = false
  upper   = false
}

# ---------------------------------------------------------
# App registration for the MCP Foundry Agent server
# ---------------------------------------------------------
resource "azuread_application" "this" {
  display_name = local.func_name
  owners       = [data.azuread_client_config.current.object_id]

  # Expose an API so users can request tokens scoped to this app
  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Allow the application to access the MCP Foundry Agent on behalf of the signed-in user."
      admin_consent_display_name = "Access MCP Foundry Agent"
      id                         = random_uuid.scope_id.result
      type                       = "User"
      value                      = "user_impersonation"
      enabled                    = true
    }
  }

  # Request delegated permission to Azure Cognitive Services for the OBO flow
  required_resource_access {
    resource_app_id = "7c33bfcb-8d33-48d6-8e2e-4b4d5459e3f4" # Azure Cognitive Services

    resource_access {
      id   = "4149d18b-cf58-4496-862f-0a9700390a1a" # user_impersonation (delegated)
      type = "Scope"
    }
  }

  web {
    implicit_grant {
      access_token_issuance_enabled = false
      id_token_issuance_enabled = false
    }
  }


  lifecycle {
    ignore_changes = [
      web[0].redirect_uris,
      identifier_uris
    ]
  }
}

resource "azuread_application_password" "this" {
  application_id = azuread_application.this.id
}

resource "azuread_application_redirect_uris" "this" {
  application_id = azuread_application.this.id
  type           = "Web"

  redirect_uris = [
    #"https://${azurerm_container_app.mcp.ingress[0].fqdn}/.auth/login/aad/callback",
    "http://localhost:8000/auth/callback"
  ]
}

resource "azuread_service_principal" "this" {
  client_id = azuread_application.this.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

resource "azuread_application_identifier_uri" "this" {
  application_id = azuread_application.this.id
  identifier_uri = "api://${azuread_application.this.client_id}"
}

data "azurerm_client_config" "current" {}

data "azurerm_log_analytics_workspace" "default" {
  name                = "DefaultWorkspace-${data.azurerm_client_config.current.subscription_id}-${local.loc_short}"
  resource_group_name = "DefaultResourceGroup-${local.loc_short}"
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.gh_repo}-${random_string.unique.result}-${local.loc_for_naming}"
  location = var.location
  tags     = local.tags
}

# App Insight instance for monitoring
resource "azurerm_application_insights" "this" {
  name                = "appi${local.func_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  workspace_id        = data.azurerm_log_analytics_workspace.default.id

  application_type    = "other"
}

resource "azurerm_container_app_environment" "this" {
  name                       = "ace-${local.func_name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = data.azurerm_log_analytics_workspace.default.id

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = local.tags
  lifecycle {
    ignore_changes = [
     infrastructure_resource_group_name,
     log_analytics_workspace_id
    ]
  }
}

resource "azurerm_container_app" "mcp" {
  name                         = "aca-${local.func_name}"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  template {
    container {
      name   = "mcp"
      image  = "ghcr.io/${var.gh_repo}:latest"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name = "CLIENT_ID"
        value = azuread_application.this.client_id
      }
      env {
        name = "CLIENT_SECRET"
        value = azuread_application_password.this.value
      }
      env {
        name = "TENANT_ID"
        value = data.azurerm_client_config.current.tenant_id
      }
      env {
        name = "BASE_URL"
        value = "${azurerm_container_app_environment.this.default_domain}"
      }
      env {
        name = "PROJECT_ENDPOINT"
        value = var.project_endpoint
      }

    }

    http_scale_rule {
      name                = "http-1"
      concurrent_requests = "100"
    }
    
    min_replicas = 1
    max_replicas = 1
  }

  ingress {
    allow_insecure_connections = false
    external_enabled           = true
    target_port                = 80
    transport                  = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  identity {
    type = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.agent.id]
  }

  tags = local.tags
}

resource azurerm_user_assigned_identity "agent" {
  name                = "uai-agent-${local.func_name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

# give the agent identity access to the AI Foundry project with Azure AI User role
resource "azurerm_role_assignment" "agent_foundry_access" {
  scope                = "/subscriptions/${var.subscription_id}"
  role_definition_name = "Foundry User"
  principal_id         = azurerm_user_assigned_identity.agent.principal_id
}
