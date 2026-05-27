output "client_id" {
  description = "Application (client) ID for the MCP Foundry Agent app registration."
  value       = azuread_application.this.client_id
}

output "client_secret" {
  description = "Client secret for the MCP Foundry Agent app registration."
  value       = azuread_application_password.this.value
  sensitive   = true
}

output "tenant_id" {
  description = "Azure AD tenant ID."
  value       = data.azuread_client_config.current.tenant_id
}

output "scope" {
  description = "The user_impersonation scope URI for this app."
  value       = "api://${azuread_application.this.client_id}/user_impersonation"
}
