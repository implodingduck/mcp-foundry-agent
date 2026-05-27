variable "subscription_id" {
  type      = string
  sensitive = true
}

variable "location" {
  type    = string
  default = "EastUS2"
}

variable "gh_repo" {
  type = string
}

variable "redirect_uris" {
  description = "Redirect URIs for the app registration."
  type        = list(string)
  default     = []
}

variable "project_endpoint" {
  type = string
}

variable "agent_id" {
  type = string
}