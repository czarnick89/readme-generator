variable "role_name" {
  description = "nick-week-23"
  type        = string
}

variable "policy_arns" {
  description = "A list of policy ARNs to attach to the role."
  type        = list(string)
  default     = []
}

variable "service_principals" {
  description = "List of AWS services allowed to assume this role."
  type        = list(string)
}